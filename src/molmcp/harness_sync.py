"""``molmcp harness sync``: the verb between a configured source and a served one.

``molmcp config harness set`` writes a coordinate and ``molmcp serve`` reads an
activation pointer. This module is what runs in between — resolve the named
source's ref to a commit, publish that commit into the shared store, activate
it in that source's own pointer — and it is the first production caller of
:meth:`~molmcp.components.ImmutableGitStore.publish`,
:meth:`~molmcp.components.Activation.stage` and
:meth:`~molmcp.components.Activation.promote`.

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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .components import (
    Activation,
    GitHubTransport,
    GitTransport,
    ImmutableGitStore,
    LocalGitTransport,
)
from .components.activate import ActivationVersionError, IneligibleShaError
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
from .settings import HarnessSource, load_settings


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
    source = _named(load_settings(Path.cwd()).harness, name)
    assert_servable(source)

    root = resolved_cache_dir(config)
    pointer = pointer_path(root, source.name)
    transport = _transport(source)
    sha = transport.resolve_commit(source.owner, source.repo, source.ref or None)
    store = ImmutableGitStore(root=store_path(root), transport=transport)
    tree = _publish(store, source, sha)

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


def _named(sources: Sequence[HarnessSource], name: str) -> HarnessSource:
    """Select the entry called *name*, or refuse and say what is configured.

    Naming the typo is only half the message. A source is addressed by an
    operator-chosen label, so "unknown source" on its own leaves them to go
    and read the settings file to find out what they should have typed.

    Args:
        sources: The ``harness`` list as the settings files resolved it.
        name: The label to match, compared exactly — ``pointer_path`` maps
            two casings onto one file on darwin, but that is a collision to
            report there rather than a licence to guess here.

    Returns:
        The one entry with that name.

    Raises:
        ConfigurationError: No entry carries that name.
    """
    for source in sources:
        if source.name == name:
            return source
    configured = ", ".join(repr(source.name) for source in sources) or "(none)"
    raise ConfigurationError(
        f"no harness source is named {name!r}. This install configures: "
        f"{configured}. Sync one of those, or add the entry first with "
        f"`molmcp config harness set --name {name} ...`."
    )


def _transport(source: HarnessSource) -> GitTransport:
    """Build the transport this entry's *shape* calls for.

    A ``path`` entry is a checkout on disk and gets
    :class:`~molmcp.components.LocalGitTransport` rooted at that path;
    anything else is a coordinate and gets
    :class:`~molmcp.components.GitHubTransport`. No flag participates — see
    the module docstring.

    ``assert_servable`` has already run, and what that buys is narrower than
    "the path is ready to use": the stored string does not follow the working
    directory, and it names a real checkout **once expanded**. The expansion
    is still this function's to do, and it is done by calling
    :func:`~molmcp.harness_paths.local_checkout_path` rather than by a second
    ``expanduser()`` here — a home-relative ``~/harness``, which that check
    accepts precisely because home is the same directory in every session,
    would otherwise root this transport at a *literal* ``~`` directory under
    whatever working directory the client that launched this process stood
    in. An empty ``path`` means the three coordinates are filled in.

    Args:
        source: The entry to build a transport for.

    Returns:
        The transport for that origin. No token is passed to the GitHub one:
        a credential belongs in the environment of whatever reads it, and
        nothing in this module reads the environment.
    """
    if source.path:
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
        source: The entry being synced, named in the failure message.

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


__all__ = ["SyncReport", "sync_source"]
