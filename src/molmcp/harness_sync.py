"""``molmcp harness sync`` and ``rollback``: the two verbs that move a pointer.

``molmcp config harness set`` writes a coordinate and ``molmcp serve`` reads an
activation pointer. This module is what runs in between — resolve the named
source's ref to a commit, publish that commit into the shared store, activate
it in that source's own pointer — and it is the first production caller of
:meth:`~molmcp.components.ImmutableGitStore.publish`,
:meth:`~molmcp.components.Activation.stage` and
:meth:`~molmcp.components.Activation.promote`.

:func:`rollback_source` is the other direction along that same pointer, and the
first production caller of :meth:`~molmcp.components.Activation.rollback`: a
sync records the SHA it displaced in ``previous`` for exactly one reason, and
this is that reason. It sits beside the sync rather than in a sibling module
because both verbs address a source by the same operator-chosen label out of
the same settings list, so both owe an unknown name the same sentence.
:func:`_named` and :func:`_bind` are theirs jointly, and a second module could
reach them only by importing a private name or by keeping a second copy of a
message whose whole value is that it does not depend on which verb was typed.

Its own module rather than more of :mod:`molmcp.harness`, whose stated identity
is that serving "is a read of the activation pointers and of each checkout's
``harness.toml`` — never a fetch, never a write". Fetching and writing are this
module's whole job, so folding them in there would make that sentence false.
What the two share is spelled once and imported: :func:`~molmcp.harness.
assert_servable` (which entries this install may reach), and from the light
:mod:`molmcp.harness_paths` leaf that ``molmcp init`` reads too,
:func:`~molmcp.harness_paths.local_checkout_path` (which directory a local
entry's ``path`` names), :func:`~molmcp.harness_paths.store_path` and
:func:`~molmcp.harness_paths.pointer_path` (where a commit and its activation
land).

**Transport is chosen by the source's shape, never by a flag.**
:class:`~molmcp.settings.HarnessSource` already refuses an entry carrying both
a ``path`` and a coordinate, so the entry itself is a total answer to "where
does this come from". A ``--local`` flag would be a second answer, and two
answers to one question is how an install ends up fetching from a repository
nobody named.

**No network is opened here.** Both transports are constructed here and neither
is spoken to except through :class:`~molmcp.components.GitTransport`; the local
one shells out to ``git`` in a checkout on disk and opens no socket at all.
:func:`rollback_source` opens nothing at all: it names a transport only because
:meth:`~molmcp.components.Activation.bind` requires a store and a store requires
one, exactly as the two read-only callers in :mod:`molmcp.harness` and
:mod:`molmcp.harness_install` do, and it never speaks to it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .components import (
    Activation,
    CatalogError,
    GitHubTransport,
    GitTransport,
    ImmutableGitStore,
    LocalGitTransport,
    load_harness_catalog,
)
from .components.activate import (
    ActivationVersionError,
    IneligibleShaError,
    NothingToRollbackError,
)
from .components.store import StoreError
from .config import AppConfig, ConfigurationError
from .harness import assert_servable
from .harness_paths import (
    SUPPORTED_CAPABILITIES,
    local_checkout_path,
    pointer_path,
    store_path,
)
from .runtime import resolved_cache_dir
from .settings import (
    HarnessSource,
    SettingsError,
    load_settings,
    match_harness_source,
    read_settings_file,
    set_harness_source,
)


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What one :func:`sync_source` call did, for the caller to print.

    Attributes:
        source: Name of the harness source that was synced.
        sha: Commit the source's ref resolved to, and the one now activated.
        tree: Published catalog root for that commit, under
            :func:`~molmcp.harness_paths.store_path`.
        pointer: Activation pointer file this source owns.
        promoted: ``True`` when the pointer moved, ``False`` when *sha* was
            already the activated commit and nothing was staged. The
            distinction is not cosmetic: promoting a commit that is already
            current would overwrite ``previous`` — the one SHA
            :meth:`~molmcp.components.Activation.rollback` returns to — with
            the SHA that is already current, and the install would silently
            lose its way back.
    """

    source: str
    sha: str
    tree: Path
    pointer: Path
    promoted: bool


@dataclass(frozen=True, slots=True)
class RollbackReport:
    """What one :func:`rollback_source` call did, for the caller to print.

    No published tree is named, unlike :class:`SyncReport`. A rollback reads
    no tree at all, and :meth:`~molmcp.components.ImmutableGitStore.tree_path`
    raises on a commit whose directory has since been pruned — a field nothing
    needed would have turned a pointer move into a traceback.

    Attributes:
        source: Name of the harness source whose pointer moved.
        sha: Commit now activated — the one the last sync displaced and
            recorded as ``previous``.
        pointer: Activation pointer file this source owns, now naming *sha*.
    """

    source: str
    sha: str
    pointer: Path


def sync_source(config: AppConfig, name: str) -> SyncReport:
    """Fetch, publish and activate the commit one named harness source is at.

    The four steps, in the one order that leaves nothing half-done: resolve
    the ref to a commit, publish that commit's tree, then — only if it is not
    already the activated one — stage it and promote it. Publishing before
    reading the pointer is deliberate: publishing a commit that is already
    published is a no-op that touches no directory, and doing it first repairs
    an install whose store was pruned out from under a still-valid pointer.

    Args:
        config: **Already-resolved** application configuration. The caller
            resolves it so that the store and the pointer land under the very
            same cache root ``molmcp serve`` reads, and so this function never
            has to decide where a cache lives.
        name: The harness source to sync, matched exactly against the ``name``
            of an entry in the ``harness`` settings list.

    Returns:
        What was done, including whether the pointer actually moved.

    Raises:
        ConfigurationError: No entry is named *name* (the message lists the
            ones that are configured); the entry names no origin this install
            can reach; the source's pointer file is not a readable activation
            record; the store refuses the commit; or the commit's
            ``harness.toml`` is one this build cannot serve.
        GitError: The ref did not resolve, or the archive could not be
            fetched. Raised by the transport and deliberately not reworded —
            its message is the only place the reason is written down.
    """
    source = _named(_loaded_harness(), name)
    assert_servable(source)

    root = resolved_cache_dir(config)
    pointer = pointer_path(root, source.name)
    transport = _transport(source)
    sha = transport.resolve_commit(source.owner, source.repo, source.ref or None)
    store = ImmutableGitStore(root=store_path(root), transport=transport)
    tree = _publish(store, source, sha)
    try:
        catalog = load_harness_catalog(tree, sha, SUPPORTED_CAPABILITIES)
        catalog.enabled_components(source.enable)
    except CatalogError as exc:
        raise ConfigurationError(
            f"the harness source named {source.name!r} cannot be served: {exc}"
        ) from exc

    activation = _bind(pointer, store, source)
    if activation.current == sha:
        return SyncReport(
            source=source.name,
            sha=sha,
            tree=tree,
            pointer=pointer,
            promoted=False,
        )
    _stage_and_promote(activation, source, sha, tree)
    return SyncReport(
        source=source.name,
        sha=sha,
        tree=tree,
        pointer=pointer,
        promoted=True,
    )


def rollback_source(config: AppConfig, name: str) -> RollbackReport:
    """Activate the commit this source's last sync displaced.

    Two steps, and neither of them fetches: bind the named source's activation
    pointer, then move it back one level. ``previous`` is what
    :meth:`~molmcp.components.Activation.promote` recorded when it activated a
    commit over the one that was current, and this is the only thing that
    reads it.

    **One level, not a toggle.** :meth:`~molmcp.components.Activation.rollback`
    clears ``previous`` as it restores it, so the record left behind names a
    current commit and no way back: a second call refuses exactly as a
    never-synced source does. The way *forward* to the newer commit is
    ``molmcp harness sync``, and it re-fetches nothing, because a rollback
    prunes nothing and that commit's tree is still published.

    **The entry's origin is never consulted.**
    :func:`~molmcp.harness.assert_servable` is deliberately not called and no
    checkout is opened, because a rollback reaches no origin. Refusing an entry
    here for an origin this install can no longer reach would strand precisely
    the operator this verb exists for — the one whose checkout has since moved
    and whose good commit is still sitting published in the store.

    Args:
        config: **Already-resolved** application configuration, for the reason
            :func:`sync_source` takes one: the pointer this moves has to be
            the file under the very same cache root ``molmcp serve`` reads.
        name: The harness source to roll back, matched exactly against the
            ``name`` of an entry in the ``harness`` settings list.

    Returns:
        The source, the commit now activated, and the pointer that says so.

    Raises:
        ConfigurationError: No entry is named *name* (the message lists the
            ones that are configured); the source's pointer file is not a
            readable activation record; or that record names no previous
            commit, which is the state of a source synced once and of one
            already rolled back alike. Nothing is written on any of those
            paths — a source that was never synced still has no pointer file
            afterwards, since one written here is one ``molmcp serve`` and
            ``molmcp init`` would then have to read.
    """
    source = _named(_loaded_harness(), name)
    root = resolved_cache_dir(config)
    pointer = pointer_path(root, source.name)
    store = ImmutableGitStore(root=store_path(root), transport=GitHubTransport())
    activation = _bind(pointer, store, source)
    return RollbackReport(
        source=source.name,
        sha=_roll_back(activation, source, pointer),
        pointer=pointer,
    )


def relocate_pointer(
    config: AppConfig,
    settings_path: Path,
    *,
    locator: str,
    name: str,
    enable: Sequence[str] = (),
    disable: Sequence[str] = (),
) -> None:
    """Rename one harness source and move its activation pointer with it.

    :func:`~molmcp.settings.set_harness_source` is the settings write;
    :func:`~molmcp.harness_paths.pointer_path` still names the file from
    the alias. This function is the one place an alias change also moves
    that file. The target pointer must not already exist unless it *is*
    the source file: refusing first is what leaves the settings file
    unchanged. No pointer on disk is settings-only.

    Args:
        config: Already-resolved application configuration, so the
            pointer paths land under the same cache root ``molmcp serve``
            reads.
        settings_path: The settings file that holds the entry.
        locator: Origin as the operator wrote it; identity is its origin
            key.
        name: The new alias.
        enable: Passed through to
            :func:`~molmcp.settings.set_harness_source`.
        disable: Passed through to
            :func:`~molmcp.settings.set_harness_source`.

    Raises:
        ConfigurationError: The new pointer file already exists under a
            different path, or the settings layer refuses the rename.
    """
    matched = match_harness_source(_file_harness(settings_path), locator)
    old_pointer: Path | None = None
    new_pointer: Path | None = None
    same_pointer = True
    if matched is not None:
        root = resolved_cache_dir(config)
        old_pointer = pointer_path(root, matched.name)
        new_pointer = pointer_path(root, name)
        same_pointer = old_pointer == new_pointer or (
            old_pointer.exists()
            and new_pointer.exists()
            and os.path.samefile(old_pointer, new_pointer)
        )
        if new_pointer.exists() and not same_pointer:
            raise ConfigurationError(
                f"cannot rename harness source {matched.name!r} to {name!r}: "
                f"the activation pointer {new_pointer} already exists"
            )
    _set_harness_source(
        settings_path,
        locator,
        alias=name,
        enable=enable,
        disable=disable,
    )
    if (
        old_pointer is not None
        and new_pointer is not None
        and old_pointer.exists()
        and not same_pointer
    ):
        os.replace(old_pointer, new_pointer)


def _named(sources: Sequence[HarnessSource], name: str) -> HarnessSource:
    """Select the entry matching *name* as an alias or a locator spelling.

    Naming the typo is only half the message. A source is addressed by an
    operator-chosen label or by any spelling of its origin, so "unknown
    source" on its own leaves them to go and read the settings file to
    find out what they should have typed.

    Args:
        sources: The ``harness`` list as the settings files resolved it.
        name: An alias, or any accepted locator spelling of an origin.

    Returns:
        The one matching entry.

    Raises:
        ConfigurationError: No entry matches *name*.
    """
    matched = match_harness_source(sources, name)
    if matched is not None:
        return matched
    configured = ", ".join(repr(source.name) for source in sources) or "(none)"
    raise ConfigurationError(
        f"no harness source is named {name!r}. This install configures: "
        f"{configured}. Sync one of those, or add the entry first with "
        f"`molmcp config harness set {name}`."
    )


def _loaded_harness() -> tuple[HarnessSource, ...]:
    """The resolved ``harness`` list, with settings failures as config errors."""
    try:
        return load_settings(Path.cwd()).harness
    except SettingsError as exc:
        raise ConfigurationError(str(exc)) from exc


def _file_harness(path: Path) -> tuple[HarnessSource, ...]:
    """The ``harness`` entries stored in one settings file."""
    try:
        raw = read_settings_file(path)
    except SettingsError as exc:
        raise ConfigurationError(str(exc)) from exc
    entries = raw.get("harness", [])
    if not isinstance(entries, list):
        return ()
    return tuple(HarnessSource(**entry) for entry in entries if isinstance(entry, dict))


def _set_harness_source(
    path: Path,
    locator: str,
    *,
    alias: str | None,
    enable: Sequence[str],
    disable: Sequence[str],
) -> None:
    """Call :func:`set_harness_source`, mapping a settings refusal up."""
    try:
        set_harness_source(path, locator, alias=alias, enable=enable, disable=disable)
    except SettingsError as exc:
        raise ConfigurationError(str(exc)) from exc


def _transport(source: HarnessSource) -> GitTransport:
    """Build the transport this entry's *shape* calls for.

    A local locator is a checkout on disk and gets
    :class:`~molmcp.components.LocalGitTransport` rooted at
    :func:`~molmcp.harness_paths.local_checkout_path`; a GitHub locator
    gets :class:`~molmcp.components.GitHubTransport`. No flag participates
    — see the module docstring.

    ``assert_servable`` has already run, and what that buys is narrower than
    "the path is ready to use": a local locator names a real checkout
    **once expanded**. The expansion is still this function's to do, and it
    is done by calling :func:`~molmcp.harness_paths.local_checkout_path`
    rather than by a second ``expanduser()`` here — a home-relative
    ``~/harness`` would otherwise root this transport at a *literal* ``~``
    directory under whatever working directory the client that launched
    this process stood in. A GitHub locator has an empty ``path``; its
    ``owner`` / ``repo`` / ``ref`` are derived, and ``ref or None`` is what
    :meth:`~molmcp.components.GitHubTransport.resolve_commit` is handed.

    Args:
        source: The entry to build a transport for.

    Returns:
        The transport for that origin. No token is passed to the GitHub one:
        a credential belongs in the environment of whatever reads it, and
        nothing in this module reads the environment.
    """
    if source.is_local:
        return LocalGitTransport(local_checkout_path(source))
    return GitHubTransport()


def _publish(store: ImmutableGitStore, source: HarnessSource, sha: str) -> Path:
    """Install *sha*'s tree in the shared store and return its catalog root.

    Provenance is the entry's own ``owner`` and ``repo``, passed through
    unchanged — including the two empty strings a local entry has. Inventing
    a coordinate for a local source (its path, say) would make two clones of
    one repository claim one SHA under two owners, and
    :class:`~molmcp.components.ShaConflictError` would then refuse the second
    sync of a commit whose tree is byte-for-byte the one already published.

    Args:
        store: The shared store, already rooted at :func:`store_path`.
        source: The entry being synced, read for provenance only.
        sha: The commit to publish.

    Returns:
        The published catalog root for *sha*.

    Raises:
        ConfigurationError: The store refused the commit — most reachably,
            *sha* is already published under a different repository, which
            happens when an entry is moved from one origin to another.
    """
    try:
        return store.publish(sha, owner=source.owner, repo=source.repo)
    except StoreError as exc:
        raise ConfigurationError(
            f"the harness source named {source.name!r} resolved to commit "
            f"{sha}, which this install's harness store will not publish: "
            f"{exc}. Nothing was activated, so the commit that was serving "
            f"still is."
        ) from exc


def _bind(pointer: Path, store: ImmutableGitStore, source: HarnessSource) -> Activation:
    """Bind this source's activation pointer, naming the file if it is broken.

    Args:
        pointer: The source's own ``harness.<name>.pointer`` file. A missing
            one is not an error — it binds an empty record, which is the
            never-synced install.
        store: The shared store the activation checks eligibility against.
        source: The entry being synced or rolled back, named in the failure
            message.

    Returns:
        The bound activation.

    Raises:
        ConfigurationError: The file exists and is not a version-1 activation
            record.
    """
    try:
        return Activation.bind(
            pointer,
            store=store,
            supported_capabilities=SUPPORTED_CAPABILITIES,
        )
    except ActivationVersionError as exc:
        raise ConfigurationError(
            f"the activation pointer of the harness source named "
            f"{source.name!r} is not a readable activation record: {exc}. "
            f"Delete {pointer} and sync again — a pointer holds names, not "
            f"trees, so nothing published is lost with it."
        ) from exc


def _stage_and_promote(
    activation: Activation, source: HarnessSource, sha: str, tree: Path
) -> None:
    """Stage *sha* past the eligibility gate, then make it the current commit.

    Both steps or neither: ``stage`` is what reads the commit's
    ``harness.toml`` and refuses one this build cannot honor, and ``promote``
    is what a served process would see. A staged SHA left unpromoted is a SHA
    nothing serves, which is indistinguishable from a sync that never ran.

    Args:
        activation: The bound pointer to move.
        source: The entry being synced, named in the failure message.
        sha: The commit to activate.
        tree: That commit's published catalog root, named in the failure
            message so there is a directory to go and look at.

    Raises:
        ConfigurationError: The commit's catalog is malformed, or requires a
            capability this build does not provide. The pointer is left
            exactly as it was.
    """
    try:
        activation.stage(sha)
    except IneligibleShaError as exc:
        # The catalog's filename is deliberately absent from this sentence.
        # `components/catalog.py` is the one module allowed to resolve it
        # (`tests/test_harness_catalog_fixture.py` enforces that), so this
        # names the published tree and lets the operator find the file in it.
        provided = ", ".join(sorted(SUPPORTED_CAPABILITIES))
        raise ConfigurationError(
            f"the harness source named {source.name!r} resolved to commit "
            f"{sha}, whose catalog this build cannot serve: it is malformed, "
            f"or it requires a capability beyond the ones this molmcp "
            f"provides ({provided}). The tree is published at {tree}. Nothing "
            f"was activated, so the commit that was serving still is."
        ) from exc
    activation.promote()


def _roll_back(activation: Activation, source: HarnessSource, pointer: Path) -> str:
    """Move *activation* back one level and return the commit now activated.

    The refusal is raised from two sites and the two are not redundant. The
    guard runs before anything is written, and it is the one an operator hits:
    a verb that reported "nothing to roll back" over a record it had already
    replaced would leave the install activating nothing at all, which is worse
    than the state it refused. The handler answers the same condition as seen
    by :meth:`~molmcp.components.Activation.rollback`'s own reload of the file,
    which is what another process moving the pointer in between looks like.
    Both spell one sentence, because it is one condition.

    Converting rather than leaving it to ``cli.main``'s funnel is the choice
    here: ``NothingToRollbackError`` is an ``ActivationError``, which is a
    plain ``Exception``, so it is caught by none of the types that funnel
    registers and would otherwise reach the operator as a traceback. The other
    way to close that is to register ``ActivationError`` there, but the raw
    message is ``nothing to rollback`` — it names neither the source nor the
    way forward, so it is not a sentence a CLI can hand over. That is why
    ``IneligibleShaError``, ``StoreError`` and ``ActivationVersionError`` are
    converted in this module too, and the opposite of ``GitError``, whose own
    message already names the ref or the checkout git could not answer for.

    Args:
        activation: The bound pointer to move.
        source: The entry being rolled back, named in the failure message.
        pointer: That source's pointer file, named in the failure message so
            there is a file to go and look at.

    Returns:
        The commit that is activated once the pointer has moved: the one
        ``previous`` named.

    Raises:
        ConfigurationError: The record names no previous commit. The pointer
            file is left exactly as it was, and a missing one is not created.
    """
    previous = activation.previous
    if previous is None:
        raise _nothing_to_roll_back(source, pointer)
    try:
        activation.rollback()
    except NothingToRollbackError as exc:
        raise _nothing_to_roll_back(source, pointer) from exc
    return previous


def _nothing_to_roll_back(source: HarnessSource, pointer: Path) -> ConfigurationError:
    """Build the refusal for an activation record with no previous commit.

    Returned rather than raised so that both sites in :func:`_roll_back` hand
    the operator the same sentence without a second copy of it.

    Args:
        source: The entry that was to be rolled back.
        pointer: That source's activation pointer file.

    Returns:
        The error to raise.
    """
    return ConfigurationError(
        f"the harness source named {source.name!r} has no commit to roll back "
        f"to: its activation pointer records no previous commit. That is the "
        f"state of a source synced only once, and of one already rolled back "
        f"— rollback clears the previous commit as it restores it, so it goes "
        f"back one level rather than toggling between two. To move forward "
        f"again, run `molmcp harness sync {source.name}`; the commit it "
        f"activates is still published, so nothing is re-fetched. Nothing was "
        f"written to {pointer}."
    )


__all__ = [
    "RollbackReport",
    "SyncReport",
    "relocate_pointer",
    "rollback_source",
    "sync_source",
]
