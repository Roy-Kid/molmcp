"""``molmcp init`` installs what the *activated* harness commit declares.

The read half of the harness chain, and the link that was missing from it.
``molmcp config harness set`` registers a source, ``molmcp harness sync``
publishes its ``HEAD`` and promotes that source's activation pointer, and
:func:`~molmcp.host.place_components` places one file per row — but nothing
turned a *pointer* into those rows, so an operator who had synced a harness
and run ``molmcp init`` got none of it.

Four obligations, and they are the whole of this module:

* read each configured source's activation pointer for its ``current`` SHA,
  and **skip** a source that has none — a configured source is not a synced
  one, and an operator who has not synced yet is not misconfigured;
* load that commit's catalog out of the published tree;
* keep the non-bundle rows, strip the kind's path prefix off each ``path``
  for the host-relative destination, and join ``component_root`` for the
  absolute source;
* resolve every row **under its own source's root**, so a multi-source
  install never reads one source's components out of another's tree.

One argument, ``host``, matching :func:`~molmcp.host.install_skill` and
:func:`~molmcp.host.write_adapter` beside it in ``cli._init``: this resolves
*and* places, so composing it costs that function one call. It reads the
configured sources and the cache root itself because ``molmcp init`` takes no
``--config`` flag and has no :class:`~molmcp.config.AppConfig` to be handed.

**This module stays off the worker stack.** :mod:`molmcp.harness` is the
other reader of these pointers, but it carries
``from .provider_worker.worker import WorkerProvider``, so importing it drags
the whole FastMCP-bearing worker stack into the importing process.
``molmcp init`` mounts no plane and must not pay for one, so the three names
needed here — :class:`~molmcp.components.Activation`,
:class:`~molmcp.components.ImmutableGitStore` and
:func:`~molmcp.components.load_harness_catalog` — are reached in the
stdlib-only :mod:`molmcp.components` leaf that owns them, and the path
spellings this must share with the serve and sync halves come from the light
:mod:`molmcp.harness_paths`. Neither :mod:`molmcp.harness` nor
:mod:`molmcp.provider_worker` may be imported here, however indirectly.
"""

from __future__ import annotations

from pathlib import Path

from .components import (
    KIND_PATH_PREFIX,
    Activation,
    ComponentSpec,
    GitHubTransport,
    ImmutableGitStore,
    load_harness_catalog,
)
from .config import AppConfig, ConfigurationError
from .harness_paths import SUPPORTED_CAPABILITIES, pointer_path, store_path
from .host import ComponentFile, Host, PlacementReport, place_components
from .runtime import resolved_cache_dir
from .settings import HarnessSource, load_settings


def install_harness_components(host: Host) -> PlacementReport:
    """Place every component the activated harness commits declare into *host*.

    One pass over the ``harness`` settings list, in the order it names its
    sources, then one :func:`~molmcp.host.place_components` call with
    everything they declared. A single call rather than one per source
    because that function checks every row before it writes the first byte:
    folded into one pass, a source whose tree is missing a declared file
    leaves nothing behind at all, where a call per source would have
    installed its predecessors already.

    Nothing here globs a tree. The catalog is the inventory, so a file
    sitting in a published commit that no row names is not a component and
    cannot reach a host.

    Args:
        host: One of the known hosts, as ``molmcp init`` names it. It is
            :func:`~molmcp.host.place_components` that validates it, and that
            happens even when nothing was declared.

    Returns:
        The report of that one placement run — what was written, what it
        replaced, and which rows were refused with which reason. Empty on an
        install that configures no harness source, and on one that has
        configured sources but has synced none of them: nothing activated is
        the ordinary state of a new install, not a broken one.

    Raises:
        ConfigurationError: A source's name cannot name a pointer file (see
            :func:`~molmcp.harness_paths.pointer_path`), or its pointer
            activates a commit that has no published tree.
        CatalogError: An activated commit's catalog is malformed, or requires
            a capability this build does not support. One bad catalog fails
            the install rather than being passed over in favour of its
            neighbours — carrying on would install a set the operator did not
            select.
        ActivationVersionError: A pointer file exists and is not a version-1
            activation record. Raised by
            :meth:`~molmcp.components.Activation.bind`; a *missing* file is
            not an error, it is the empty record that skips its source.
        FileNotFoundError: A catalog declares a file its own tree does not
            hold. Raised by :func:`~molmcp.host.place_components` before
            anything is written.
    """
    settings = load_settings(Path.cwd())
    root = resolved_cache_dir(AppConfig.default(Path.cwd(), settings=settings))
    store = ImmutableGitStore(root=store_path(root), transport=GitHubTransport())
    declared: list[ComponentFile] = []
    for source in settings.harness:
        declared.extend(_declared_files(source, root, store))
    return place_components(host, declared)


def _declared_files(
    source: HarnessSource, root: Path, store: ImmutableGitStore
) -> tuple[ComponentFile, ...]:
    """Describe every component one source's activated commit declares.

    The pointer is *read*, never written: staging, promoting and fetching a
    commit belong to ``molmcp harness sync``, which was asked to change what
    is activated. A source with no pointer file, or with a pointer that
    activates nothing, contributes nothing and is not an error, and it does
    so per source — the neighbour still yields its own rows.

    Args:
        source: One entry of the ``harness`` settings list.
        root: The resolved cache root that source's pointer hangs off.
        store: The one shared store every source publishes into.

    Returns:
        One :class:`~molmcp.host.ComponentFile` per row of that commit's
        catalog, in catalog order, every one of them resolved under *this*
        source's own base. Bundles yield nothing: they are named groups of
        rows rather than files, and the catalog keeps them in a separate
        collection. The empty tuple when the source is not activated.

    Raises:
        ConfigurationError: The source's name cannot name a pointer file, or
            its pointer names a commit with no published tree. The second is
            named rather than silently re-fetched: installing a different
            commit than the one that was activated is the one outcome nobody
            asked for.
    """
    pointer = pointer_path(root, source.name)
    activation = Activation.bind(
        pointer,
        store=store,
        supported_capabilities=SUPPORTED_CAPABILITIES,
    )
    sha = activation.current
    if sha is None:
        return ()
    if not store.has(sha):
        raise ConfigurationError(
            f"the harness source named {source.name!r} is activated at commit "
            f"{sha}, which has no published tree under {store_path(root)}. Run "
            f"`molmcp harness sync {source.name}` to publish it again, or "
            f"delete that source's activation pointer at {pointer}."
        )
    tree = store.tree_path(sha)
    catalog = load_harness_catalog(tree, sha, SUPPORTED_CAPABILITIES)
    base = tree / catalog.component_root if catalog.component_root else tree
    return tuple(_component_file(spec, base) for spec in catalog.components)


def _component_file(spec: ComponentSpec, base: Path) -> ComponentFile:
    """Translate one catalog row into the four plain values ``host/`` takes.

    The kind's path prefix is catalog grammar — ``skills/`` says which kind a
    row is, which its ``kind`` field already said — so it is stripped here
    and the remainder is what the host's own ``skills/`` directory holds. The
    prefix is guaranteed present and to have something after it:
    :class:`~molmcp.components.ComponentSpec` refuses a path without both.

    Args:
        spec: The catalog row, exactly as its catalog declared it.
        base: The directory *this* row's source resolves its paths under —
            the published tree, or the ``component_root`` inside it.

    Returns:
        The description :func:`~molmcp.host.place_components` places. The kind
        crosses as a plain string, not as the enum: which kinds have a host
        destination is that function's table, and handing it a catalog type
        would make it a second reader of catalog grammar.
    """
    return ComponentFile(
        id=spec.id,
        kind=str(spec.kind),
        relative=spec.path.removeprefix(KIND_PATH_PREFIX[spec.kind]),
        source=base / spec.path,
    )


__all__ = ["install_harness_components"]
