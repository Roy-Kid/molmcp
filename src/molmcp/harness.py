"""The activated harness checkouts: bind the pointers, fold the catalogs, adapt.

Serving from a harness is a read of the activation pointers and of each
checkout's ``harness.toml`` — never a fetch, never a write. Every source the
operator named owns its own pointer file beside one shared store, and the
components those catalogs declare are folded into one served set, first source
in the settings list winning a contested id. This module holds the arms that do
that reading; :mod:`molmcp.server` composes them, owns the decision of when to
run each one, and owns resolving the :class:`~molmcp.config.AppConfig` they are
handed.

One name here is shared with :mod:`molmcp.harness_sync`, which does the
writing: :func:`assert_servable`, which entries an install may reach at all. It
lives on this side because a rule with two spellings is a rule two commands can
disagree about — a settings entry ``molmcp serve`` refuses cannot be one
``molmcp harness sync`` accepts.

The rest of what those commands share is spelled in :mod:`molmcp.harness_paths`
and imported from there: :data:`~molmcp.harness_paths.SUPPORTED_CAPABILITIES`,
:func:`~molmcp.harness_paths.local_checkout_path` (which directory a local entry
names), :func:`~molmcp.harness_paths.store_path` and
:func:`~molmcp.harness_paths.pointer_path` (where a commit and its activation
land). They sit below this module rather than in it because a third command
needs them — ``molmcp init``, which mounts no plane and must not pay the import
cost this module's next paragraph describes just to read a pointer.

This module sits on the **heavy** side of the child-safe import boundary, by
choice rather than by accident: it carries
``from .provider_worker.worker import WorkerProvider``, so importing it drags
the whole FastMCP-bearing worker stack into the importing process.
:mod:`molmcp.server` pays that cost already. Anything else reaching in here —
a CLI verb wanting the activated checkout, say — inherits it, and should know
that before reaching (``notes.md:worker-child-isolation``, which names
:mod:`molmcp.provider_sdk` and :mod:`molmcp.provider` as the boundary this
module is deliberately outside of).

The cache root arrives through :func:`molmcp.runtime.resolved_cache_dir`, the
same shield :mod:`molmcp.server` uses, so nothing here imports
:mod:`molmcp.discovery`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .components import (
    Activation,
    CatalogError,
    ComponentKind,
    ComponentSpec,
    GitHubTransport,
    ImmutableGitStore,
    load_harness_catalog,
)
from .config import AppConfig, ConfigurationError
from .harness_paths import (
    SUPPORTED_CAPABILITIES,
    local_checkout_path,
    pointer_path,
    store_path,
)
from .provider import Provider
from .provider_worker.worker import WorkerProvider
from .runtime import resolved_cache_dir
from .settings import HarnessSource

logger = logging.getLogger(__name__)

#: The three coordinates that locate one named harness repository on GitHub.
#: A *remote* entry carries all three or none of them; anything between is a
#: configuration error rather than a value to guess at. They are not the whole
#: completeness rule — :func:`assert_servable` reads them only after it has
#: found no ``path``, because a local entry's coordinates are empty by
#: construction rather than by omission.
#:
#: This is deliberately not ``molmcp.settings._HARNESS_ENTRY_KEYS``, which also
#: holds ``name`` and ``path``: that set is what a settings-file entry may
#: *write*, this one is what a *remote* entry must have filled in before it can
#: be fetched from.
HARNESS_COORDINATES = ("owner", "repo", "ref")

#: What a local origin must have at the root it names. Probed with ``exists``
#: rather than ``is_dir``: ``.git`` is a directory in an ordinary clone and a
#: file in a linked worktree, and both are checkouts.
_GIT_DIR_NAME = ".git"

#: The one shared pointer file this install bound before sources were
#: activated by name. It is *named* once when it is the only pointer on disk
#: and never read. Nothing writes it: ``molmcp harness sync`` is now the one
#: caller of :meth:`~molmcp.components.Activation.stage` and
#: :meth:`~molmcp.components.Activation.promote` in ``src/``, and it writes
#: only through :func:`pointer_path`, which always interpolates a non-empty
#: source name. No source name can therefore produce this file, so the probe
#: is unambiguous — and the population it can still mislead is whoever ran a
#: pre-``sync`` build by hand, which is why one notice is the whole budget.
_LEGACY_POINTER_NAME = "harness.pointer"


@dataclass(frozen=True, slots=True)
class Checkout:
    """The harness commit this process serves from, already on disk.

    Attributes:
        sha: Activated commit SHA, as the pointer file records it.
        tree: Root of that commit's tree — where ``harness.toml`` sits.
        source: Name of the harness source this commit was activated for.
            It rides here, beside ``tree``, so that ``source -> tree`` has
            exactly one owner: :class:`ComponentFold` carries these objects
            rather than a second mapping of the same fact. That ownership
            survives :attr:`ComponentFold.component_roots`, and the
            distinguishing fact is what that field maps to: it is
            ``source -> str``, not ``source -> tree``. It records the
            ``component_root`` string a catalog authored — a fact no
            ``Checkout`` holds, because :func:`activated_checkouts` reads
            no catalog — and :meth:`ComponentFold.root_for` joins it onto
            the tree found *here*. The tree is therefore still owned once,
            and a base built against some other source's tree is not a
            state that type can hold.
    """

    sha: str
    tree: Path
    source: str


@dataclass(frozen=True, slots=True)
class SourcedComponent:
    """One catalog component paired with the source it was declared in.

    The cross-source key is this *pair*, and ``spec.id`` is carried
    **unchanged**: a component out of a source named ``official`` still has
    ``spec.id == "provider.demo"``. The pair does not try to namespace the id,
    because a namespaced id is not constructible —
    :class:`~molmcp.components.ComponentSpec` pins ``id == f"{kind}.{name}"``
    and its member pattern admits only the five known kinds, so
    ``official.provider.demo`` is refused at construction.

    The shape is :class:`~molmcp.collection.models.SearchHit`'s, which keeps
    ``source`` as a field *beside* the ref and never folds one into the other
    — this repo's existing answer to "the same id from two origins".

    Attributes:
        source: Name of the harness source the spec came from, as the
            ``harness`` settings list spells it.
        spec: The catalog row, exactly as its catalog declared it.
    """

    source: str
    spec: ComponentSpec


@dataclass(frozen=True, slots=True)
class ComponentFold:
    """The result of folding one component kind over several checkouts.

    There is deliberately no ``displaced`` field. A component that lost a
    contested id is *reported* — one warning from this module's logger — not
    stored: nothing in production would read such a field, and this repo's
    other first-wins folds (``discovery/overlay/catalog.py``,
    ``discovery/overlay/conventions.py``) drop losers without recording them.

    ``__post_init__`` refuses any fold whose two collections disagree: source
    names are unique on **both** sides and exactly equal across them. Each
    clause earns its keep. Set equality alone admits one source named twice
    in ``component_roots``, and :meth:`root_for`'s scan would then answer
    with whichever entry it met first while a second entry said something
    else. Set equality *and* roots-side uniqueness together still admit two
    checkouts sharing one source name with different trees, where the scan
    answers with the first tree and the other source's components resolve
    nowhere. :func:`activated_checkouts` already refuses a duplicate source
    name, but this type is directly constructible and cannot rely on its own
    caller. The guard owns the **correspondence** between the two
    collections, which nothing else owns, and deliberately does *not*
    re-validate the ``component_root`` string: that **value**'s one home is
    :class:`~molmcp.components.HarnessCatalog`'s own ``__post_init__``, and a
    second guard here would be a second owner of one rule.

    Attributes:
        checkouts: The checkouts this fold was built from, in source order.
            The fold carries the objects themselves rather than a parallel
            ``source -> tree`` map, so a consumer that needs a kept spec's
            tree has one place to find it and cannot hold two arguments out
            of sync.
        component_roots: Each source's ``component_root`` exactly as its
            catalog authored it — the raw string, in source order, ``""``
            for a catalog declaring no key. This is not the parallel map the
            entry above forbids, and both halves of why are worth stating.
            The **string** is the fold's own datum: no ``Checkout`` holds
            it, because :func:`activated_checkouts` reads no catalog, and
            giving ``Checkout`` the field would force it to. The **join** is
            what would have been the duplicate — ``tree`` already lives on
            the checkouts, so storing ``tree / component_root`` would be a
            second copy of a fact those objects already hold, and the
            invariant above would then exist only to police the agreement
            between two copies of one fact. With the string stored and the
            join performed inside :meth:`root_for` against *that source's
            own* :attr:`Checkout.tree`, "the base belongs to the right tree"
            is a theorem rather than an assertion: there is no other tree
            :meth:`root_for` can reach.
        kept: The surviving components — source order outside, catalog order
            within a source.

    Raises:
        CatalogError: ``checkouts`` and ``component_roots`` disagree — a
            source name repeats on either side, or the two sets of names are
            not equal.
    """

    checkouts: tuple[Checkout, ...]
    component_roots: tuple[tuple[str, str], ...]
    kept: tuple[SourcedComponent, ...]

    def __post_init__(self) -> None:
        """Refuse a fold that cannot answer exactly one base per source.

        Raises:
            CatalogError: A source name repeats among ``checkouts``, or
                repeats among ``component_roots``, or the two collections
                do not name the same set of sources.
        """
        checked = tuple(checkout.source for checkout in self.checkouts)
        rooted = tuple(source for source, _ in self.component_roots)
        if len(set(checked)) != len(checked):
            raise CatalogError(
                f"a component fold cannot hold two checkouts of one harness "
                f"source: {sorted(checked)!r}. `root_for` would answer with "
                f"the first one's tree and the second's components would "
                f"resolve nowhere."
            )
        if len(set(rooted)) != len(rooted):
            raise CatalogError(
                f"a component fold cannot hold two component roots for one "
                f"harness source: {sorted(rooted)!r}. `root_for` would answer "
                f"with the first one and the second would be silently unused."
            )
        if set(checked) != set(rooted):
            raise CatalogError(
                f"a component fold must hold exactly one component root per "
                f"checkout: its checkouts name {sorted(checked)!r} and its "
                f"component roots name {sorted(rooted)!r}."
            )

    def root_for(self, source: str) -> Path:
        """Return the directory *source*'s component paths resolve under.

        This is the **one** place a checkout tree and a catalog's
        ``component_root`` are joined, and it joins them against that
        source's own :attr:`Checkout.tree` — so a base belonging to another
        source's tree is not reachable rather than merely untested. A
        catalog declaring no ``component_root`` answers the tree object
        itself, not another spelling of it: no ``.`` component, no trailing
        separator, so an install that has no key today resolves
        byte-identical paths.

        A linear scan, symmetric with :meth:`specs_from` — but deliberately
        **not** symmetric with its tolerance of an unknown source. There is
        no empty ``Path`` a caller could stand in with, and a wrong base is
        the half-applied failure this whole design exists to prevent.

        Args:
            source: Harness source name, as the ``harness`` settings list
                spells it.

        Returns:
            ``tree / component_root`` for a rooted source, and exactly
            ``tree`` for a rootless one.

        Raises:
            CatalogError: This fold was not built from that source. The
                message contains ``unknown-source`` and names it with
                ``repr`` — the register :meth:`HarnessCatalog.get
                <molmcp.components.HarnessCatalog.get>` and ``get_bundle``
                already use.
        """
        for name, component_root in self.component_roots:
            if name != source:
                continue
            for checkout in self.checkouts:
                if checkout.source != source:
                    continue
                if not component_root:
                    return checkout.tree
                return checkout.tree / component_root
        raise CatalogError(f"unknown-source: {source!r}")

    @property
    def names(self) -> frozenset[str]:
        """Component names of every kept component.

        For :attr:`~molmcp.components.ComponentKind.PROVIDER` these are the
        plane ids clients see and the names entry-point planes are XORed
        against. A contested id appears once, because only its winner is
        kept — which is what stops two planes mounting under one namespace.
        """
        return frozenset(sourced.spec.name for sourced in self.kept)

    def specs_from(self, source: str) -> tuple[ComponentSpec, ...]:
        """Return the specs *source* kept, in that catalog's own order.

        Grouping is per source because every consumer of a spec also needs
        the tree it came from: an import root, an overlay's seed path. A spec
        that lost a contested id is not kept, so its own source does not
        report it either.

        Args:
            source: Harness source name to select.

        Returns:
            That source's kept specs in catalog order, or the empty tuple
            when the source kept nothing — including when it was never
            folded at all. An unknown source is not an error; it is a source
            with nothing in it.
        """
        return tuple(sourced.spec for sourced in self.kept if sourced.source == source)


def assert_servable(source: HarnessSource) -> None:
    """Refuse one harness source that names no origin this install can reach.

    An entry names **one** origin, and which one is read off its shape rather
    than off a flag: ``path`` is a checkout already on disk, the three
    :data:`HARNESS_COORDINATES` are a GitHub repository, and
    :class:`~molmcp.settings.HarnessSource` refuses both at once. Reading
    completeness as "all three coordinates are filled in" would therefore
    report the one legal shape of a local source — three empty coordinates —
    as half-authored, which is how a ``path``-only entry could never serve.

    A local origin is checked against the filesystem here, beside the remote
    entry's missing ``ref``, because it is the same kind of mistake: the
    settings file is what is wrong, and the operator needs the entry name and
    the path in one sentence rather than a ``GitError`` out of a transport
    several steps later. The probe is ``.git`` under the named root, and it
    is ``exists`` rather than ``is_dir`` on purpose — ``.git`` is a directory
    in an ordinary clone and a *file* in a linked worktree.

    Before that probe, a ``path`` is refused for **working-directory
    dependence — deliberately not for relativeness**, and the difference is
    the whole rule rather than a shade of wording. The entry is read out of
    ``~/.molmcp/settings.json``, one file shared by every project on this
    machine, while ``molmcp serve`` inherits whatever working directory the
    client that launched it happened to stand in, so ``./checkout`` is one
    stored string naming a different repository per session. ``~/harness``
    fails ``Path.is_absolute()`` and carries none of that: home is the same
    directory in every session, so it is expanded — through
    :func:`local_checkout_path`, the one spelling of that expansion — and
    served. Narrowed to ``is_absolute()`` this test
    would refuse a spelling that already names one directory everywhere,
    which is why the refusal offers ``~`` as a way out beside the absolute
    path: a message naming only the second would send an operator to rewrite
    an entry this function accepts as it stands.

    The order is load-bearing, not incidental. A real checkout can sit
    exactly where ``./checkout`` points from *this* process's working
    directory, so a cwd check placed after the probe would accept the entry
    on the strength of a repository the next session does not resolve to.

    Expanding is not rewriting. The source is read and never modified: the
    stored string is the operator's, it may have been authored on another
    machine, and normalising it to this machine's absolute path is a bug in
    the same family as the one being refused. Every message here reports the
    path **as written**, because that is the string the operator will look
    for in the settings file.

    This is the single owner of the rule. ``molmcp serve`` reaches it through
    :func:`molmcp.server._harness_locator` and ``molmcp harness sync`` calls
    it on the one entry it was given, so an entry one command refuses cannot
    be one the other accepts. What a caller may then assume of a ``path`` it
    let through is exactly two things — that the string does not follow the
    working directory, and that it names a checkout **once expanded**. It is
    not a licence to open ``source.path`` as written: the caller expands it,
    which means calling :func:`local_checkout_path`.

    Args:
        source: One entry of the ``harness`` settings list, as written.

    Raises:
        ConfigurationError: The entry names no origin at all, names a
            partial GitHub coordinate, or names a ``path`` that follows the
            working directory or is not a git checkout. Each message names
            the entry, because under a list of sources the entry's name is
            the address an operator goes to fix it, and names the path as the
            settings file spells it. The partial-coordinate message
            deliberately does **not** offer ``path``: an entry already
            carrying an ``owner`` is a remote one, and telling its author to
            add a ``path`` beside it is an instruction
            ``HarnessSource.__post_init__`` raises on.
    """
    if source.path.strip():
        root = local_checkout_path(source)
        if not root.is_absolute():
            raise ConfigurationError(
                f"the harness source named {source.name!r} names a `path` "
                f"that is read against the working directory: {source.path}. "
                f"Your settings file is shared by every project on this "
                f"machine, and `molmcp serve` inherits the working directory "
                f"of whichever client launched it, so that one entry names a "
                f"different checkout in every session. Write it as an "
                f"absolute path, or as a `~/` path — home is the same "
                f"directory in every session — on that entry of the `harness` "
                f"list in your settings file, or remove the entry to serve "
                f"without it."
            )
        if (root / _GIT_DIR_NAME).exists():
            return
        raise ConfigurationError(
            f"the harness source named {source.name!r} names a `path` that is "
            f"not a git checkout: {source.path}. A local origin is pinned to a "
            f"commit exactly as a remote one is, so it must be the root of a "
            f"repository already on disk — the directory holding its `.git`. "
            f"Point that entry of the `harness` list at a checkout, or remove "
            f"the entry to serve without it."
        )
    missing = [key for key in HARNESS_COORDINATES if not getattr(source, key).strip()]
    if not missing:
        return
    if len(missing) == len(HARNESS_COORDINATES):
        raise ConfigurationError(
            f"the harness source named {source.name!r} names no origin: set "
            f"owner, repo and ref to fetch it from a GitHub repository, or "
            f"set path to a checkout already on disk. Fill one of those in on "
            f"that entry of the `harness` list in your settings file, or "
            f"remove the entry to serve without it."
        )
    named = ", ".join(missing)
    raise ConfigurationError(
        f"the harness source named {source.name!r} is incomplete: "
        f"{named} {'is' if len(missing) == 1 else 'are'} not set. Fill "
        f"{'it' if len(missing) == 1 else 'them'} in on that entry of the "
        f"`harness` list in your settings file, or remove the entry to "
        f"serve without it."
    )


def servable_sources(
    sources: Sequence[HarnessSource],
) -> tuple[HarnessSource, ...]:
    """Check every named source and hand the whole list back in file order.

    Every entry is checked and none is ever skipped. An entry that names no
    reachable origin is refused rather than passed over in favour of its
    neighbour, for the same reason no coordinate is defaulted: carrying on
    from the next entry would serve code from a repository the operator did
    not select.

    Args:
        sources: Every named harness source, in the order the settings list
            names them.

    Returns:
        The same sources, in the same order — that order is the operator's
        priority control over a component two sources both declare, and it is
        carried through :func:`activated_checkouts` into the fold.

    Raises:
        ConfigurationError: Any entry fails :func:`assert_servable`.
    """
    for source in sources:
        assert_servable(source)
    return tuple(sources)


def activated_checkouts(
    config: AppConfig, sources: Sequence[HarnessSource]
) -> tuple[Checkout, ...]:
    """Bind one activation pointer per named source and return what they point at.

    Serving is a read of the pointers, never a write to one: each is bound,
    its ``current`` is read, and that is the end of it. Staging, promoting and
    fetching a commit belong to the commands that were asked to change what is
    activated.

    One store and one transport are shared across every source, and only the
    *pointers* multiply. :class:`ImmutableGitStore` keys a commit on its SHA
    alone and records provenance per SHA, and :class:`GitHubTransport` takes
    ``(owner, repo)`` per call, so a second root would buy no isolation and
    would strand every already-published tree. A pointer is not shareable the
    same way: the record it holds carries one ``active`` SHA, so two sources
    folded into one file would overwrite each other's commit.

    A source with no pointer file, or with a pointer that activates nothing,
    contributes no checkout and is not an error. Nothing activated serves
    exactly like an unset locator, and it does so *per source*: the neighbour
    still yields its own checkout.

    No catalog is read here. This function binds pointers and hands back trees;
    what a tree declares is :func:`fold_components`' subject, and an arm that
    does not run never pays for a catalog it would not use.

    Args:
        config: **Already-resolved** application configuration.
            :func:`~molmcp.server.create_stack` resolves it and passes it in so
            that the store and every pointer land under the very same cache
            root the collection indexes under. Resolution is that caller's job
            and is deliberately not repeated here.
        sources: Every named harness source, in the order the ``harness``
            settings list names them. That order is carried through to the
            returned checkouts, and it is the operator's only priority
            control: :func:`fold_components` resolves a contested component id
            first-wins over this sequence.

    Returns:
        One checkout per *activated* source, in source order. The empty tuple
        when none of them is activated — the same answer as no source at all.

    Raises:
        ConfigurationError: A source cannot be served. Three ways: its name
            cannot name a pointer file (see :func:`pointer_path`); two entries
            share a name, compared with ``casefold`` because both spellings
            resolve to one file on darwin and on Windows; or its pointer names
            a commit with no published tree. That last one is named rather
            than silently re-fetched — serving a different commit than the one
            that was activated is the one outcome nobody asked for — and it
            names the *source*, because under N sources a SHA and a store root
            identify no entry of the settings file to go and fix.
        ActivationVersionError: A pointer file exists and is not a version-1
            activation record (bad JSON, unknown version, missing fields).
            Raised by :meth:`Activation.bind`; a *missing* file is not an
            error, it is the empty record that skips its source above.
    """
    root = resolved_cache_dir(config)
    # Every name is turned into a path before anything is bound, so a settings
    # file this function refuses is refused whole rather than half-served.
    pointers: list[tuple[HarnessSource, Path]] = []
    claimed: dict[str, str] = {}
    for source in sources:
        key = source.name.casefold()
        first = claimed.get(key)
        if first is not None:
            raise ConfigurationError(
                f"the `harness` list names two sources that own one "
                f"activation pointer file: {first!r} and {source.name!r}. "
                f"Names are compared case-insensitively because darwin and "
                f"Windows resolve both spellings to the same file, so the "
                f"second entry would silently serve whatever the first "
                f"activated. Rename or remove one of those two entries in "
                f"your settings file."
            )
        claimed[key] = source.name
        pointers.append((source, pointer_path(root, source.name)))

    legacy = root / _LEGACY_POINTER_NAME
    if legacy.exists() and not any(pointer.exists() for _, pointer in pointers):
        logger.warning(
            "the activation pointer %s is left over from before this install "
            "activated harness sources by name, and is never read: each "
            "source now owns a `harness.<name>.pointer` file beside it, and "
            "none of the named sources has one, so nothing is activated. "
            "Delete that file, and activate the sources you want under their "
            "own names.",
            legacy,
        )

    store_root = store_path(root)
    store = ImmutableGitStore(root=store_root, transport=GitHubTransport())
    checkouts: list[Checkout] = []
    for source, pointer in pointers:
        activation = Activation.bind(
            pointer,
            store=store,
            supported_capabilities=SUPPORTED_CAPABILITIES,
        )
        current = activation.current
        if current is None:
            continue
        if not store.has(current):
            raise ConfigurationError(
                f"the harness source named {source.name!r} is activated at "
                f"commit {current}, which has no published tree under "
                f"{store_root}. Publish and activate it again, or clear that "
                f"source's activation pointer at {pointer}."
            )
        checkouts.append(
            Checkout(sha=current, tree=store.tree_path(current), source=source.name)
        )
    return tuple(checkouts)


def fold_components(
    checkouts: Sequence[Checkout], kind: ComponentKind
) -> ComponentFold:
    """Fold one component kind over every checkout, first source wins.

    Each checkout's ``harness.toml`` is the only inventory of its tree — the
    tree is never globbed, because a file nobody declared is not a component —
    and the catalogs are read here, in the order the ``harness`` settings list
    names their sources. This is the one folder: each arm calls it for itself
    with the kind it wants, so an arm that does not run never pays for a
    catalog it would not use. When two sources declare the same ``spec.id``, **the
    first one in that list keeps it** — the ``setdefault`` idiom this repo
    already folds ordered streams with — and the later declaration is
    dropped with one warning naming the winning source, the losing source
    and the contested id. The order of the list is therefore the operator's
    priority control, and the only one: there is no per-source override.

    Keying on ``spec.id`` rather than on the component name is what closes
    the mount hazard for providers, where ``id == f"provider.{name}"`` makes
    an id collision a plane-name collision: two sources shipping
    ``provider.demo`` would otherwise build two planes named ``demo`` and
    mount both under one namespace.

    Args:
        checkouts: The activated checkouts, in source order.
        kind: The one component kind to fold; every other kind in every
            catalog is passed over.

    Returns:
        The fold: the checkouts it was built from, each source's
        ``component_root`` as its catalog authored it, and the components
        that survived. No checkout at all is not an error — it is the empty
        fold. The roots are recorded in the same loop iteration that reads
        the catalog, because that iteration is the only place both the
        source name and its catalog are in hand at once.

    Raises:
        CatalogError: A catalog is malformed, or requires a capability this
            runtime does not support. One bad catalog fails the serve rather
            than being skipped in favour of its neighbours, for the same
            reason an incomplete source is refused: carrying on would serve
            code the operator did not select. Also when *checkouts* names one
            source twice, which :class:`ComponentFold` refuses —
            :func:`activated_checkouts` cannot produce that, but this
            function is directly callable with a hand-built sequence.
    """
    kept: dict[str, SourcedComponent] = {}
    component_roots: list[tuple[str, str]] = []
    for checkout in checkouts:
        catalog = load_harness_catalog(
            checkout.tree, checkout.sha, SUPPORTED_CAPABILITIES
        )
        component_roots.append((checkout.source, catalog.component_root))
        for spec in catalog.components:
            if spec.kind is not kind:
                continue
            sourced = SourcedComponent(source=checkout.source, spec=spec)
            winner = kept.setdefault(spec.id, sourced)
            if winner is sourced:
                continue
            logger.warning(
                "the harness source named %r also declares %r, which the "
                "source named %r declares first; the earlier entry of the "
                "`harness` list wins, so %r's copy is served and this one "
                "is ignored. Reorder that list, or drop the component from "
                "one of the two catalogs.",
                checkout.source,
                spec.id,
                winner.source,
                winner.source,
            )
    return ComponentFold(
        checkouts=tuple(checkouts),
        component_roots=tuple(component_roots),
        kept=tuple(kept.values()),
    )


def checkout_planes(fold: ComponentFold) -> list[Provider]:
    """Adapt a provider fold's kept components into mountable planes.

    Each one becomes a :class:`~molmcp.provider_worker.worker.WorkerProvider`
    named by the component's ``name`` — the plane id clients see and the name
    the entry-point comparison is made on. The component ``id``
    (``provider.demo``) is a catalog key, not a plane id; mounting under it
    would namespace the plane's tools as ``provider.demo_open``.

    The fold is the *only* argument, deliberately. Every spec needs the base
    its source resolves under to find its import root, and the fold answers
    that itself through :meth:`ComponentFold.root_for` — so there is nothing
    for a caller to keep in sync, and a fold built from some other checkout
    list cannot be paired with a stale one here. Only kept components are
    built, which is what stops two sources' ``provider.demo`` from mounting
    twice under one namespace.

    Args:
        fold: A :data:`~molmcp.components.ComponentKind.PROVIDER` fold. Any
            other kind yields planes whose entrypoints were never meant to be
            run in a worker; folding the right kind is the caller's business,
            as it is the caller that named the kind.

    Returns:
        One plane per kept provider component — source order outside, catalog
        order within a source. Empty when nothing is activated.
    """
    return [
        WorkerProvider(
            # A provider component always carries an entrypoint — ComponentSpec
            # refuses to be built without one — and it stays a string here: the
            # checkout is imported in the child process, never in this one.
            name=spec.name,
            entrypoint=str(spec.entrypoint),
            path=_import_root(fold.root_for(checkout.source), spec.path),
        )
        for checkout in fold.checkouts
        for spec in fold.specs_from(checkout.source)
    ]


def _import_root(base: Path, path: str) -> Path:
    """Resolve a component path to the directory its module is imported from.

    A component may point at either the module file (``providers/demo/plane.py``)
    or the package directory that holds it (``providers/demo``). Both name the
    same import root, so a directory is used as it stands and a file hands back
    its parent.

    The overlay arm resolves its own import root the other way — always the
    parent, whatever the path names (``molmcp.runtime`` /
    ``_session_capability_overlays``). The two rules can only disagree when a
    component's ``path`` names a directory, and which one is right there
    depends on whether its ``entrypoint`` spells the module relative to that
    directory or to the directory above it, which the catalog grammar does not
    settle. If a real catalog's provider entrypoint ever fails to import, this
    difference is the first thing to check.

    Args:
        base: The directory this source's component paths resolve under —
            :meth:`ComponentFold.root_for`'s answer. Deliberately not named
            ``tree``: :attr:`Checkout.tree` means "where ``harness.toml``
            sits" in this same module, and the two stop being one directory
            the moment a catalog declares a ``component_root``. This
            function is not told which case it is in and does not need to
            be; it takes a base directory and knows nothing about where it
            came from.
        path: The component's POSIX path, exactly as its catalog authored
            it, resolved under *base*.

    Returns:
        The directory to import the component from.
    """
    candidate = base / path
    return candidate if candidate.is_dir() else candidate.parent
