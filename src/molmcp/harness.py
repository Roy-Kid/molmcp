"""The activated harness checkouts: bind the pointers, fold the catalogs, adapt.

Serving from a harness is a read of the activation pointers and of each
checkout's ``harness.toml`` — never a fetch, never a write. Every source the
operator named owns its own pointer file beside one shared store, and the
components those catalogs declare are folded into one served set, first source
in the settings list winning a contested id. This module holds the arms that do
that reading; :mod:`molmcp.server` composes them, owns the decision of when to
run each one, and owns resolving the :class:`~molmcp.config.AppConfig` they are
handed.

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
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .components import (
    Activation,
    ComponentKind,
    ComponentSpec,
    GitHubTransport,
    ImmutableGitStore,
    load_harness_catalog,
)
from .config import AppConfig, ConfigurationError
from .provider import Provider
from .provider_worker.worker import WorkerProvider
from .runtime import resolved_cache_dir
from .settings import HarnessSource

logger = logging.getLogger(__name__)

#: Capability tokens this runtime can honor, named once here and passed as
#: this object to :meth:`Activation.bind` and to every catalog load.
#:
#: A *harness* is a git repository holding the user's own agent tooling —
#: skills, agents, rules, provider planes, discovery overlays — that this
#: install can be pointed at. Its ``harness.toml`` *catalog* declares those
#: pieces, and the catalog (and each named bundle inside it) may list
#: *capability tokens*: machinery a piece needs from whatever process loads
#: it. The two this build honors are ``provider-sdk``, the public
#: :mod:`molmcp.provider_sdk` a checkout plane is written against, and
#: ``harness-catalog``, the catalog format read here.
#:
#: This set is deliberately not ``molmcp.components.ALLOWED_REQUIRES``. That
#: set is what a harness catalog is *allowed to declare* — the grammar. This
#: one is what this process can *deliver* — eligibility. They happen to hold
#: the same two tokens today; aliasing them would make a token added to the
#: grammar tomorrow claim runtime support that nothing here implements.
SUPPORTED_CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})

#: Source names :func:`pointer_path` refuses outright, kept for symmetry with
#: :data:`molmcp.components.store._RESERVED_SHA_KEYS` rather than because
#: either one escapes a directory — see that function's docstring.
_RESERVED_SOURCE_NAMES = frozenset({".", ".."})

#: The one shared pointer file this install bound before sources were
#: activated by name. It is *named* once when it is the only pointer on disk
#: and never read: nothing in the product writes it (there is no caller of
#: ``Activation.stage`` / ``promote`` / ``rollback`` anywhere in ``src/``), so
#: the population it can still mislead is very nearly empty, and one notice
#: where it can matter is the whole budget. No source name can produce this
#: file — :func:`pointer_path` always interpolates a non-empty name — so the
#: probe is unambiguous.
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
            rather than a second mapping of the same fact.
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

    Attributes:
        checkouts: The checkouts this fold was built from, in source order.
            The fold carries the objects themselves rather than a parallel
            ``source -> tree`` map, so a consumer that needs a kept spec's
            tree has one place to find it and cannot hold two arguments out
            of sync.
        kept: The surviving components — source order outside, catalog order
            within a source.
    """

    checkouts: tuple[Checkout, ...]
    kept: tuple[SourcedComponent, ...]

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


def pointer_path(root: Path, name: str) -> Path:
    """Name the activation pointer file of one harness source.

    Each source owns ``<root>/harness.<name>.pointer``, a direct child of the
    cache root and a sibling of the one shared store at ``<root>/harness``.

    The name is guarded here rather than on
    :class:`~molmcp.settings.HarnessSource`, because this is the only place
    that knows the name is about to become path *structure* instead of a
    label: that class governs it as "non-empty and whitespace-free" on
    purpose, so an operator who may name an index source ``MolCrafts`` may
    name a harness source ``MolCrafts``, and ``molmcp config`` must keep
    working on a settings file this function refuses.

    The guard is :meth:`ImmutableGitStore._sha_dir`'s, and **which half of it
    is load-bearing is worth stating**, so that nobody later "simplifies" it
    by dropping the half that matters. ``.`` and ``..`` are refused for
    symmetry with that method's reserved set, **not** because they traverse:
    interpolated into ``harness.{name}.pointer`` neither is a path segment at
    all — ``harness....pointer`` is one ordinary filename inside *root*. The
    separator, absolute-path and empty checks are the ones that close the
    hole, since ``a/b`` and ``../../evil`` do turn the name into structure
    and would write outside the cache root.

    Nothing is created, and nothing is created on the way to a refusal: this
    function computes a path and never touches the filesystem.

    Args:
        root: The resolved cache root the store already hangs off.
        name: The harness source's name, as the ``harness`` settings list
            spells it.

    Returns:
        The pointer file for that source.

    Raises:
        ConfigurationError: The name cannot be one path segment — it is
            empty, reserved, absolute, or contains a path separator. The
            message names it with ``repr``, this repo's register for a
            rejected value and the only form that can name the empty string
            at all.
    """
    if (
        not name
        or name in _RESERVED_SOURCE_NAMES
        or Path(name).is_absolute()
        or os.sep in name
        or "/" in name
        or "\\" in name
        or (os.altsep is not None and os.altsep in name)
    ):
        raise ConfigurationError(
            f"the harness source named {name!r} cannot name an activation "
            f"pointer file: a source name must be a single path segment, so "
            f"it may not be empty, `.`, `..`, absolute, or contain a path "
            f"separator. Rename that entry of the `harness` list in your "
            f"settings file."
        )
    return root / f"harness.{name}.pointer"


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

    store_root = root / "harness"
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
        The fold: the checkouts it was built from, and the components that
        survived. No checkout at all is not an error — it is the empty fold.

    Raises:
        CatalogError: A catalog is malformed, or requires a capability this
            runtime does not support. One bad catalog fails the serve rather
            than being skipped in favour of its neighbours, for the same
            reason an incomplete source is refused: carrying on would serve
            code the operator did not select.
    """
    kept: dict[str, SourcedComponent] = {}
    for checkout in checkouts:
        catalog = load_harness_catalog(
            checkout.tree, checkout.sha, SUPPORTED_CAPABILITIES
        )
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
    return ComponentFold(checkouts=tuple(checkouts), kept=tuple(kept.values()))


def checkout_planes(fold: ComponentFold) -> list[Provider]:
    """Adapt a provider fold's kept components into mountable planes.

    Each one becomes a :class:`~molmcp.provider_worker.worker.WorkerProvider`
    named by the component's ``name`` — the plane id clients see and the name
    the entry-point comparison is made on. The component ``id``
    (``provider.demo``) is a catalog key, not a plane id; mounting under it
    would namespace the plane's tools as ``provider.demo_open``.

    The fold is the *only* argument, deliberately. Every spec needs the tree
    it came from to resolve its import root, and the fold already carries the
    checkouts it was built from — so there is nothing for a caller to keep in
    sync, and a fold built from some other checkout list cannot be paired with
    a stale one here. Only kept components are built, which is what stops two
    sources' ``provider.demo`` from mounting twice under one namespace.

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
            path=_import_root(checkout.tree, spec.path),
        )
        for checkout in fold.checkouts
        for spec in fold.specs_from(checkout.source)
    ]


def _import_root(tree: Path, path: str) -> Path:
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
        tree: Root of the activated checkout.
        path: The component's tree-relative POSIX path.

    Returns:
        The directory to import the component from.
    """
    candidate = tree / path
    return candidate if candidate.is_dir() else candidate.parent
