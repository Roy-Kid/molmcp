"""The activated harness checkout: bind the pointer, read the catalog, adapt.

Serving from a harness is a read of the activation pointer and of the
checkout's ``harness.toml`` — never a fetch, never a write. This module holds
the arms that do that reading; :mod:`molmcp.server` composes them and owns the
decision of when to run each one.

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
from .config import AppConfig, ConfigurationError, load_config
from .provider import Provider
from .provider_worker.worker import WorkerProvider
from .runtime import resolved_cache_dir

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


@dataclass(frozen=True, slots=True)
class Checkout:
    """The harness commit this process serves from, already on disk.

    Attributes:
        sha: Activated commit SHA, as the pointer file records it.
        tree: Root of that commit's tree — where ``harness.toml`` sits.
    """

    sha: str
    tree: Path


def _activated_checkout(config: AppConfig | str | Path | None) -> Checkout | None:
    """Bind the activation pointer and return the tree it points at.

    Serving is a read of the pointer, never a write to it: this binds, reads
    ``current``, and stops. Staging, promoting, and fetching a commit belong to
    the commands that were asked to change what is activated.

    Args:
        config: Application configuration — an :class:`AppConfig`, or anything
            :func:`~molmcp.config.load_config` accepts, which is resolved
            first; :func:`create_stack` passes one already resolved. Its
            resolved cache root — the same one discovery caches under, already
            resolved against the workspace and any ``--config`` override, and
            falling back to the default root when no ``cacheDir`` is set —
            holds the store at ``<cache>/harness`` and the pointer beside it
            at ``<cache>/harness.pointer``.

    Returns:
        The activated checkout, or ``None`` when nothing is activated yet.
        Nothing activated serves exactly like an unset locator.

    Raises:
        ConfigurationError: The pointer names a commit with no published tree.
            A missing tree is named, not silently re-fetched: serving a
            different commit than the one that was activated is the one
            outcome nobody asked for.
        ActivationVersionError: The pointer file exists and is not a version-1
            activation record (bad JSON, unknown version, missing fields).
            Raised by :meth:`Activation.bind`; a *missing* file is not an
            error, it is the empty record that returns ``None`` above.
    """
    root = resolved_cache_dir(_resolve_config(config))
    store = ImmutableGitStore(root=root / "harness", transport=GitHubTransport())
    activation = Activation.bind(
        root / "harness.pointer",
        store=store,
        supported_capabilities=SUPPORTED_CAPABILITIES,
    )
    current = activation.current
    if current is None:
        return None
    if not store.has(current):
        raise ConfigurationError(
            f"the activated harness commit {current} has no published tree "
            f"under {root / 'harness'}. Publish and activate it again, or "
            f"clear the activation pointer."
        )
    return Checkout(sha=current, tree=store.tree_path(current))


def _checkout_components(
    checkout: Checkout, kind: ComponentKind
) -> tuple[ComponentSpec, ...]:
    """Read the checkout's catalog and return every component of one *kind*.

    The catalog is the only inventory of the tree; the tree is never globbed,
    because a file nobody declared is not a component. Each arm reads it for
    itself — same tree, same SHA, same capability set — so an arm that does not
    run never pays for a catalog it would not use.

    Args:
        checkout: The activated checkout to read ``harness.toml`` from.
        kind: Component kind to keep.

    Returns:
        The matching components, in catalog order.

    Raises:
        CatalogError: The catalog is malformed, or requires a capability this
            runtime does not support.
    """
    catalog = load_harness_catalog(checkout.tree, checkout.sha, SUPPORTED_CAPABILITIES)
    return tuple(spec for spec in catalog.components if spec.kind is kind)


def _checkout_planes(checkout: Checkout | None) -> list[Provider]:
    """Adapt the checkout's provider components into mountable planes.

    Each one becomes a :class:`~molmcp.provider_worker.worker.WorkerProvider`
    named by the component's ``name`` — the plane id clients see and the name
    the entry-point comparison is made on. The component ``id``
    (``provider.demo``) is a catalog key, not a plane id; mounting under it
    would namespace the plane's tools as ``provider.demo_open``.

    Args:
        checkout: The activated checkout, or ``None`` when there is none.

    Returns:
        One plane per provider component; empty when nothing is activated.
    """
    if checkout is None:
        return []
    return [
        WorkerProvider(
            # A provider component always carries an entrypoint — ComponentSpec
            # refuses to be built without one — and it stays a string here: the
            # checkout is imported in the child process, never in this one.
            name=spec.name,
            entrypoint=str(spec.entrypoint),
            path=_import_root(checkout.tree, spec.path),
        )
        for spec in _checkout_components(checkout, ComponentKind.PROVIDER)
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


def _resolve_config(config: AppConfig | str | Path | None) -> AppConfig:
    if isinstance(config, AppConfig):
        return config
    return load_config(config)
