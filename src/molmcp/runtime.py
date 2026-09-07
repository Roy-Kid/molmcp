"""Application-layer assembly of the source collection.

:func:`build_collection` turns one resolved :class:`~molmcp.config.AppConfig`
into the :class:`~molmcp.collection.CollectionIndex` the molcrafts core
searches, and is the only place discovery's capability overlays are put in
order. Two helpers keep it company. ``_session_capability_overlays`` builds
the overlays an activated harness checkout contributes, and lives here
because that concatenation is its only consumer. :func:`resolved_cache_dir`
owns the fallback for an unset ``cacheDir``, so that no module beyond this
one and the CLI has to import :mod:`molmcp.discovery` to find a cache root.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .collection import CollectionIndex, SourceBinding
from .components.models import ComponentSpec
from .config import AppConfig
from .discovery import DiscoveryConfig, DiscoveryEngine
from .discovery.config import DEFAULT_EXCLUDES
from .discovery.overlay import CapabilityOverlay, load_overlays


class OverlayLoadError(ValueError):
    """Raised when a harness checkout component yields no usable overlay.

    A *harness checkout* is one commit of the user's own agent-tooling
    repository, unpacked on disk. Its ``harness.toml`` catalog may declare
    *capability overlays* — objects that layer domain knowledge onto the code
    graph discovery builds. This is what a catalog row promising one and
    delivering something else raises. Being a ``ValueError``, it is already
    caught by a caller that only wants "the stack could not be built".
    """


def _session_capability_overlays(
    seeds: Sequence[ComponentSpec], tree_path: Path
) -> tuple[CapabilityOverlay, ...]:
    """Construct the activated checkout's capability overlays in this process.

    Every seed names a ``module:object`` entrypoint. The directory holding
    the seed's ``path`` inside ``tree_path`` goes on ``sys.path``, the module
    half is imported, and the named object is called as a factory. The result
    must satisfy :class:`~molmcp.discovery.overlay.CapabilityOverlay`; one
    that does not is a named error rather than a skipped warning, because a
    catalog row that declares an overlay and delivers something else is a
    broken catalog, not a missing optional extra.

    That directory is the *parent* of the seed's path, taken unconditionally,
    and it is prepended to ``sys.path`` for the rest of the process — nothing
    takes it back off, so a checkout module that shadows an installed one goes
    on shadowing it long after the graph is built. The provider arm resolves
    its import root by a different rule (``molmcp.server`` / ``_import_root``
    uses a path that names a directory as it stands); that function is where
    the reason the two coexist is written down.

    This import runs in-process while a checkout *provider* runs in a
    subprocess of its own. The difference is session state, not trust. A
    provider owns a long-lived MCP session — an ``exec`` namespace and an
    event journal that outlive every individual call — so it needs a process
    to hold that state and to be torn down together with it. An overlay is
    called once, at graph-build time, and keeps nothing between calls. The
    trust model is single and covers both: the harness repository is the
    user's own code, on the same footing as the kernel a notebook executes.

    Args:
        seeds: Overlay ``ComponentSpec`` rows read from the harness catalog.
        tree_path: Root of the activated checkout the seed paths resolve under.

    Returns:
        One overlay instance per seed, in seed order.

    Raises:
        OverlayLoadError: If a seed declares no entrypoint, or its factory
            returns an object that is not a ``CapabilityOverlay``. The message
            names the offending component.
        ImportError: The entrypoint's module half is not importable from the
            checkout — a mistyped module name, or a dependency the checkout
            never declared. It reaches the caller as raised, unwrapped,
            because the interpreter's own message says more about which import
            failed than any rewrapping here could.
        AttributeError: The module imports, but carries no object under the
            entrypoint's second half. Whatever the factory itself raises
            propagates the same way.
    """
    overlays: list[CapabilityOverlay] = []
    for seed in seeds:
        entrypoint = seed.entrypoint
        if entrypoint is None:
            raise OverlayLoadError(f"overlay component {seed.name!r} has no entrypoint")
        module_name, _, attribute = entrypoint.partition(":")
        import_root = str((tree_path / seed.path).parent)
        if import_root not in sys.path:
            sys.path.insert(0, import_root)
        instance = getattr(importlib.import_module(module_name), attribute)()
        if not isinstance(instance, CapabilityOverlay):
            raise OverlayLoadError(
                f"overlay component {seed.name!r} entrypoint {entrypoint!r} "
                f"returned {type(instance).__name__}, not a CapabilityOverlay"
            )
        overlays.append(instance)
    return tuple(overlays)


def resolved_cache_dir(config: AppConfig) -> Path:
    """Return the one resolved cache root for this configuration.

    ``AppConfig.cache_dir`` is ``None`` until somebody configures ``cacheDir``,
    so every caller that wants a real directory needs the same fallback to the
    discovery default. That fallback lives here and nowhere else: one place
    decides the default, and the harness store can never land in a different
    directory than the discovery caches.

    :mod:`molmcp.server` reads the root from this function precisely so that it
    need not import :mod:`molmcp.discovery`. Discovery has exactly two
    importers — this module and the CLI — and the harness wiring is not a third.

    Args:
        config: Resolved application configuration.

    Returns:
        The configured ``cache_dir``, or the discovery default
        (:func:`molmcp.discovery.config.default_cache_dir`) when unset.
    """
    return config.cache_dir or DiscoveryConfig().cache_dir


def build_collection(
    config: AppConfig,
    registry: object | None = None,
    *,
    extras: Sequence[CapabilityOverlay] = (),
) -> CollectionIndex:
    """Build one collection over every named source in ``config``.

    ``registry`` is the duck-typed extension point :class:`CollectionIndex`
    documents — anything exposing ``search`` / ``get`` / ``info`` joins the
    search as one more channel. molmcp ships no implementation: the capability
    manifest it used to carry had no producer anywhere in the ecosystem, so
    the shape was guesswork. The seam stays; the guess does not.

    This is the only place overlays are assembled: the entry-point overlays
    come first, then ``extras``. The concatenation is always a list, empty
    included, so the engine never falls back to discovering overlays itself.

    Args:
        config: Resolved application configuration.
        registry: Optional duck-typed extra search channel.
        extras: Overlays contributed by an activated harness checkout,
            appended after the entry-point overlays. Empty is a legal answer.

    Returns:
        The assembled :class:`CollectionIndex`.
    """
    discovery = DiscoveryConfig(
        cache_dir=resolved_cache_dir(config),
        excludes=tuple(dict.fromkeys((*DEFAULT_EXCLUDES, *config.excludes))),
        watch=config.watch,
    )
    overlays = [*load_overlays(), *extras]
    engine = DiscoveryEngine(discovery, overlays=overlays)
    bindings = [
        SourceBinding(
            name=name,
            spec=spec,
            engine=engine,
            namespace=name,
            metadata={"configured": True},
        )
        for name, spec in sorted(config.sources.items())
    ]
    return CollectionIndex(
        bindings,
        registry,
        metadata={
            "workspace_root": str(config.workspace_root),
            "watch": config.watch,
            "discovery": config.discovery,
        },
    )


def config_summary(config: AppConfig) -> str:
    """Stable, secret-free configuration text for diagnostics."""
    return json.dumps(
        {
            "workspace_root": str(config.workspace_root),
            "sources": config.sources,
            "watch": config.watch,
            "transport": config.server.transport,
            "discovery": config.discovery,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
