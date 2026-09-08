"""FastMCP composition: core + namespaced provider mounts."""

from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from molmcp import CollectionIndex, create_plane, create_stack, runtime, server
from molmcp.components import (
    ALLOWED_REQUIRES,
    BundleSpec,
    ComponentKind,
    ComponentSpec,
    HarnessCatalog,
)
from molmcp.config import AppConfig, ConfigurationError
from molmcp.provider_worker.worker import WorkerProvider
from molmcp.settings import Settings, SettingsError


class _Vis:
    name = "molvis"

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
        def open() -> str:
            """Open a viewer session."""
            return "session"


async def test_stack_namespaces_provider_tools():
    stack = create_stack(
        collection=CollectionIndex([]),
        providers=[_Vis()],
        discover_entry_points=False,
    )
    assert stack.name == "molcrafts"
    names = {tool.name for tool in await stack.list_tools()}
    assert "packages" in names
    assert "open" in names
    assert "molvis_open" in names


async def test_stack_disable_skips_mount():
    stack = create_stack(
        collection=CollectionIndex([]),
        providers=[_Vis()],
        disable=["molvis"],
        discover_entry_points=False,
    )
    names = {tool.name for tool in await stack.list_tools()}
    assert "molvis_open" not in names
    assert "packages" in names


async def test_single_provider_plane_stays_bare():
    server = create_plane(
        "molvis",
        provider=_Vis(),
        discover_entry_points=False,
    )
    assert server.name == "molvis"
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"open"}


# --- autonomous harness wiring (spec 08) ----------------------------------
#
# Every outbound seam create_stack could reach for is faked here: no git, no
# network, no environment. The seams are patched on ``molmcp.server`` because
# that module is the single composition root the wiring has to live in.

_SHA = "0123456789abcdef0123456789abcdef01234567"
_LOCATOR = {"owner": "molcrafts", "repo": "harness", "ref": "main"}
_CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})
_SKILL = ComponentSpec(
    kind=ComponentKind.SKILL,
    name="daily",
    id="skill.daily",
    path="skills/daily.md",
)

#: One recorded call: positional arguments, then keyword arguments.
_Call = tuple[tuple[object, ...], dict[str, object]]


def _config(tmp_path: Path) -> AppConfig:
    """Config whose ``cache_dir`` is the already-resolved root of the store."""
    return AppConfig.from_dict(
        {"schema_version": "2", "cache_dir": str(tmp_path / "cache")},
        workspace_root=tmp_path,
    )


def _catalog(*components: ComponentSpec) -> HarnessCatalog:
    """A real catalog: the two required bundles plus *components*."""
    return HarnessCatalog(
        sha=_SHA,
        requires=(),
        components=(_SKILL, *components),
        bundles=(
            BundleSpec(name="daily", members=("skill.daily",)),
            BundleSpec(name="dev", members=("skill.daily",)),
        ),
    )


def _provider_component(path: str = "providers/demo/plane.py") -> ComponentSpec:
    """A checkout provider row whose ``id`` differs from its ``name``."""
    return ComponentSpec(
        kind=ComponentKind.PROVIDER,
        name="demo",
        id="provider.demo",
        path=path,
        entrypoint="demo.plane:DemoProvider",
    )


def _checkout(tmp_path: Path) -> Path:
    """A tree holding ``providers/demo/`` as a directory and a module in it."""
    tree = tmp_path / "tree"
    package = tree / "providers" / "demo"
    package.mkdir(parents=True)
    (package / "plane.py").write_text("", encoding="utf-8")
    return tree


class _Marker:
    """An in-tree provider that registers one identifiable tool."""

    def __init__(self, name: str, tool: str = "intree") -> None:
        self.name = name
        self._tool = tool

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(name=self._tool, annotations=ToolAnnotations(read_only_hint=True))
        def marker() -> str:
            """Prove which provider object was mounted under this namespace."""
            return self._tool


class _FakeWorker:
    """Stand-in for ``WorkerProvider`` recording the constructor mapping."""

    def __init__(self, *, name: str, entrypoint: str, path: str | Path) -> None:
        self.name = name
        self.entrypoint = entrypoint
        self.path = path

    def probe(self) -> bool:
        return True

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(name="worker", annotations=ToolAnnotations(read_only_hint=True))
        def worker() -> str:
            """Prove the checkout worker, not the in-tree plane, was mounted."""
            return self.name

    def __getattr__(self, item: str) -> object:
        if item == "close":
            raise AssertionError("spec 08 must not reach for WorkerProvider.close")
        raise AttributeError(item)


class _FakeTransport:
    """GitHub transport stand-in that refuses to touch the network."""

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        raise AssertionError("serve must not resolve a ref to a commit")

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        raise AssertionError("serve must not fetch an archive")


class _FakeStore:
    """Immutable store stand-in over a checkout that is already on disk."""

    def __init__(
        self,
        root: str | Path,
        transport: object,
        *,
        tree: Path | None,
        published: bool,
    ) -> None:
        self.root = Path(root)
        self.transport = transport
        self._tree = tree
        self._published = published

    def has(self, sha: str) -> bool:
        return self._published

    def tree_path(self, sha: str) -> Path:
        if not self._published or self._tree is None:
            raise AssertionError(f"tree_path asked for unpublished {sha!r}")
        return self._tree

    def publish(self, sha: str, *, owner: str, repo: str) -> Path:
        raise AssertionError("serve must not clone a missing sha")


class _FakeActivation:
    """Pointer view with a fixed ``current`` whose writes are all refused."""

    def __init__(self, current: str | None) -> None:
        self.current = current
        self.staged: str | None = None
        self.previous: str | None = None

    def stage(self, sha: str) -> None:
        raise AssertionError(f"serve must not stage {sha!r}")

    def promote(self) -> None:
        raise AssertionError("serve must not promote")

    def rollback(self) -> None:
        raise AssertionError("serve must not roll back")


class _ActivationSeam:
    """Stand-in for the ``Activation`` class; only ``bind`` is ever used."""

    def __init__(self, wiring: _Wiring, current: str | None) -> None:
        self._wiring = wiring
        self._current = current

    def bind(
        self,
        path: str | Path,
        *,
        store: object,
        supported_capabilities: object,
    ) -> _FakeActivation:
        self._wiring.binds.append(
            {
                "path": Path(path),
                "store": store,
                "supported_capabilities": supported_capabilities,
            }
        )
        return _FakeActivation(self._current)


class _RecordingCollection(CollectionIndex):
    """Collection that counts its own lifecycle calls."""

    def __init__(self) -> None:
        super().__init__([])
        self.starts = 0
        self.closes = 0

    def start(self) -> None:
        self.starts += 1
        super().start()

    def close(self) -> None:
        self.closes += 1
        super().close()


@dataclass
class _Wiring:
    """What the faked seams saw during one ``create_stack`` call."""

    settings: list[_Call] = field(default_factory=list)
    transports: list[_Call] = field(default_factory=list)
    stores: list[_FakeStore] = field(default_factory=list)
    binds: list[dict[str, object]] = field(default_factory=list)
    catalogs: list[dict[str, object]] = field(default_factory=list)
    workers: list[_FakeWorker] = field(default_factory=list)
    built: list[dict[str, object]] = field(default_factory=list)
    collections: list[_RecordingCollection] = field(default_factory=list)
    discoveries: list[dict[str, object]] = field(default_factory=list)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    harness: dict[str, str] | None = None,
    tree: Path | None = None,
    current: str | None = None,
    published: bool = True,
    catalog: HarnessCatalog | None = None,
    entry_points: tuple[object, ...] = (),
) -> _Wiring:
    """Fake every seam ``create_stack`` reaches out through and record it."""
    wiring = _Wiring()
    resolved_catalog = catalog if catalog is not None else _catalog()

    def load_settings(*args: object, **kwargs: object) -> Settings:
        wiring.settings.append((args, kwargs))
        return Settings(harness=dict(harness or {}))

    def github_transport(*args: object, **kwargs: object) -> _FakeTransport:
        wiring.transports.append((args, kwargs))
        return _FakeTransport()

    def immutable_git_store(root: str | Path, transport: object) -> _FakeStore:
        made = _FakeStore(root, transport, tree=tree, published=published)
        wiring.stores.append(made)
        return made

    def load_harness_catalog(
        root: str | Path,
        sha: str,
        supported_capabilities: object,
    ) -> HarnessCatalog:
        wiring.catalogs.append(
            {"root": Path(root), "sha": sha, "capabilities": supported_capabilities}
        )
        return resolved_catalog

    def worker_provider(*, name: str, entrypoint: str, path: str | Path) -> _FakeWorker:
        made = _FakeWorker(name=name, entrypoint=entrypoint, path=path)
        wiring.workers.append(made)
        return made

    def build_collection(
        config: object,
        registry: object = None,
        *,
        extras: Sequence[object] = (),
    ) -> _RecordingCollection:
        wiring.built.append(
            {"config": config, "registry": registry, "extras": tuple(extras)}
        )
        collection = _RecordingCollection()
        wiring.collections.append(collection)
        return collection

    def discover_providers(
        *,
        failures: list[dict[str, str]] | None = None,
        only_available: bool = False,
    ) -> list[object]:
        wiring.discoveries.append({"only_available": only_available})
        return list(entry_points)

    monkeypatch.setattr(server, "load_settings", load_settings)
    monkeypatch.setattr(server, "GitHubTransport", github_transport)
    monkeypatch.setattr(server, "ImmutableGitStore", immutable_git_store)
    monkeypatch.setattr(server, "Activation", _ActivationSeam(wiring, current))
    monkeypatch.setattr(server, "load_harness_catalog", load_harness_catalog)
    monkeypatch.setattr(server, "WorkerProvider", worker_provider)
    monkeypatch.setattr(server, "build_collection", build_collection)
    monkeypatch.setattr(server, "discover_providers", discover_providers)
    return wiring


async def _tool_names(stack: FastMCP) -> set[str]:
    return {tool.name for tool in await stack.list_tools()}


# -- arm gating -------------------------------------------------------------


def test_dual_injection_never_consults_the_harness_locator(tmp_path, monkeypatch):
    """Both arms injected: the locator is not read, bound, or catalogued."""
    wiring = _wire(monkeypatch, harness={"owner": "molcrafts"})
    create_stack(
        collection=CollectionIndex([]),
        providers=[_Vis()],
        config=_config(tmp_path),
    )
    assert wiring.settings == []
    assert wiring.binds == []
    assert wiring.catalogs == []


async def test_unset_locator_serves_exactly_like_today(tmp_path, monkeypatch):
    """Three keys unset: no bind, no extras, entry points then ``disable=``."""
    wiring = _wire(
        monkeypatch,
        harness={},
        entry_points=(_Marker("demo"), _Marker("other")),
    )
    stack = create_stack(config=_config(tmp_path), disable=["other"])
    assert wiring.binds == []
    assert wiring.catalogs == []
    assert wiring.built[0]["extras"] == ()
    assert wiring.discoveries == [{"only_available": True}]
    names = await _tool_names(stack)
    assert "demo_intree" in names
    assert "other_intree" not in names


@pytest.mark.parametrize(
    ("harness", "missing"),
    [
        ({"owner": "molcrafts"}, ("repo", "ref")),
        ({"owner": "molcrafts", "repo": "harness"}, ("ref",)),
    ],
)
def test_partial_locator_names_the_missing_keys(
    tmp_path,
    monkeypatch,
    harness: dict[str, str],
    missing: tuple[str, ...],
):
    """One or two of the three keys is a ConfigurationError, not a guess."""
    _wire(monkeypatch, harness=harness)
    with pytest.raises(ConfigurationError) as excinfo:
        create_stack(config=_config(tmp_path))
    message = str(excinfo.value)
    for key in missing:
        assert key in message
    assert not isinstance(excinfo.value, SettingsError)


async def test_absent_current_falls_back_without_resolving_or_promoting(
    tmp_path, monkeypatch
):
    """A complete locator with no current SHA serves the unset fallback."""
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=None,
        tree=_checkout(tmp_path),
        entry_points=(_Marker("demo"),),
    )
    stack = create_stack(config=_config(tmp_path))
    assert len(wiring.binds) == 1
    assert wiring.catalogs == []
    assert wiring.built[0]["extras"] == ()
    assert wiring.discoveries == [{"only_available": True}]
    assert "demo_intree" in await _tool_names(stack)


def test_current_missing_from_the_store_names_that_sha(tmp_path, monkeypatch):
    """An activated SHA with no tree is an error, never a silent re-clone."""
    _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
        published=False,
    )
    with pytest.raises(ConfigurationError) as excinfo:
        create_stack(config=_config(tmp_path))
    assert _SHA in str(excinfo.value)


async def test_injected_collection_still_runs_the_provider_git_arm(
    tmp_path, monkeypatch
):
    """Skip is per owner: an injected collection only skips the overlay arm."""
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert "demo_worker" in await _tool_names(stack)
    assert wiring.built == []
    assert len(wiring.catalogs) == 1


async def test_injected_providers_still_run_the_overlay_git_arm(tmp_path, monkeypatch):
    """Injected providers skip only the provider arm and still pass disable=."""
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(
        providers=[_Marker("demo")],
        config=_config(tmp_path),
        disable=["demo"],
    )
    assert "demo_intree" not in await _tool_names(stack)
    assert wiring.workers == []
    assert wiring.discoveries == []
    assert len(wiring.catalogs) == 1
    assert len(wiring.built) == 1


async def test_entry_point_discovery_off_is_not_a_provider_git_arm(
    tmp_path, monkeypatch
):
    """``discover_entry_points=False`` with no providers mounts nothing."""
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(
        collection=CollectionIndex([]),
        config=_config(tmp_path),
        discover_entry_points=False,
    )
    assert [name for name in await _tool_names(stack) if name.startswith("demo_")] == []
    assert wiring.settings == []
    assert wiring.binds == []
    assert wiring.workers == []


# -- named bind -------------------------------------------------------------


def test_named_store_and_pointer_hang_off_the_resolved_cache_root(
    tmp_path, monkeypatch
):
    """One store at ``<cache>/harness``, pointer beside it, token omitted."""
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    assert wiring.transports == [((), {})]
    assert len(wiring.stores) == 1
    store = wiring.stores[0]
    assert store.root == config.cache_dir / "harness"
    assert isinstance(store.transport, _FakeTransport)
    assert wiring.binds[0]["path"] == config.cache_dir / "harness.pointer"
    assert wiring.binds[0]["store"] is store


def test_unset_cache_dir_still_binds_under_the_resolved_default_root(
    tmp_path, monkeypatch
):
    """No ``cacheDir`` set is the common case, not a broken harness.

    ``AppConfig.cache_dir`` is ``None`` until somebody configures ``cacheDir``,
    so reading it raw turns "I set the three harness keys" into an error for
    the majority of users. The fallback to the default cache root already has
    one home in :mod:`molmcp.runtime`; the bind hangs off that resolved root.
    """
    config = AppConfig.from_dict({"schema_version": "2"}, workspace_root=tmp_path)
    assert config.cache_dir is None
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    resolved = runtime.resolved_cache_dir(config)
    assert len(wiring.stores) == 1
    store = wiring.stores[0]
    assert store.root == resolved / "harness"
    assert len(wiring.binds) == 1
    assert wiring.binds[0]["path"] == resolved / "harness.pointer"
    assert wiring.binds[0]["store"] is store


def test_server_module_imports_nothing_from_discovery():
    """The cache root is AppConfig's; server.py stays out of discovery."""
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    modules: list[str] = []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
            names |= {alias.name for alias in node.names}
    assert [name for name in modules if "discovery" in name] == []
    assert "DiscoveryConfig" not in names
    assert "default_cache_dir" not in names


def test_the_locator_is_read_once_with_the_project_root(tmp_path, monkeypatch):
    """``load_settings(Path.cwd())``: bare hides a project's harness keys."""
    config = _config(tmp_path)
    tree = _checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    wiring = _wire(monkeypatch, harness=_LOCATOR, current=_SHA, tree=tree)
    create_stack(config=config)
    assert len(wiring.settings) == 1
    args, kwargs = wiring.settings[0]
    assert len(args) + len(kwargs) == 1
    assert (args[0] if args else kwargs["project_root"]) == Path.cwd()


# -- capabilities -----------------------------------------------------------


def test_one_capability_object_reaches_bind_and_both_catalog_calls(
    tmp_path, monkeypatch
):
    """One frozenset object: bind plus one catalog call per git arm."""
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component()),
    )
    create_stack(config=_config(tmp_path))
    assert server.SUPPORTED_CAPABILITIES == _CAPABILITIES
    assert wiring.binds[0]["supported_capabilities"] is server.SUPPORTED_CAPABILITIES
    assert len(wiring.catalogs) == 2
    for call in wiring.catalogs:
        assert call["capabilities"] is server.SUPPORTED_CAPABILITIES
        assert call["root"] == tree
        assert call["sha"] == _SHA


def test_supported_capabilities_is_a_subset_of_allowed_requires_not_an_alias():
    """Catalog grammar and runtime ability are two sets that happen to match."""
    assert server.SUPPORTED_CAPABILITIES <= ALLOWED_REQUIRES
    assert server.SUPPORTED_CAPABILITIES is not ALLOWED_REQUIRES


# -- worker provider mapping and XOR ---------------------------------------


def test_worker_provider_is_named_by_component_name_not_id(tmp_path, monkeypatch):
    """``name=`` is the EP name ``demo``; ``provider.demo`` is not a plane id."""
    spec = _provider_component()
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(spec),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert spec.id == "provider.demo"
    assert [worker.name for worker in wiring.workers] == ["demo"]
    assert wiring.workers[0].name != spec.id


def test_worker_provider_entrypoint_stays_an_unimported_string(tmp_path, monkeypatch):
    """The entrypoint is carried as a string; the checkout is not imported."""
    spec = _provider_component()
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(spec),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert wiring.workers[0].entrypoint == spec.entrypoint
    assert "demo.plane" not in sys.modules
    assert "demo" not in sys.modules


@pytest.mark.parametrize("path", ["providers/demo/plane.py", "providers/demo"])
def test_worker_provider_path_is_the_import_root_directory(
    tmp_path, monkeypatch, path: str
):
    """A file row gives its parent; a directory row gives that directory."""
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component(path=path)),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert Path(wiring.workers[0].path) == tree / "providers" / "demo"


async def test_checkout_wins_the_name_and_entry_point_only_planes_pass_through(
    tmp_path, monkeypatch
):
    """XOR against ``discover_providers(only_available=True)``, by EP name."""
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(_provider_component()),
        entry_points=(_Marker("demo"), _Marker("other")),
    )
    stack = create_stack(config=_config(tmp_path))
    names = await _tool_names(stack)
    assert "demo_worker" in names
    assert "demo_intree" not in names
    assert "other_intree" in names
    assert wiring.discoveries == [{"only_available": True}]


# -- lifecycle --------------------------------------------------------------


async def test_core_lifespan_closes_the_collection_and_never_closes_a_worker(
    tmp_path, monkeypatch
):
    """``coll.close()`` stays in the core finally; no worker teardown here."""
    wiring = _wire(
        monkeypatch,
        harness=_LOCATOR,
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(config=_config(tmp_path))
    collection = wiring.collections[0]
    async with stack._lifespan_manager():
        assert collection.starts == 1
        assert collection.closes == 0
    assert collection.closes == 1
    assert wiring.workers != []
    assert not hasattr(WorkerProvider, "close")


# -- frozen keyword surface (spec 14) ---------------------------------------


class TestCreateStackSignature:
    """``create_stack``'s keyword surface is a published contract.

    Every host adapter, the CLI, and every embedder calls this by keyword.
    A parameter renamed, reordered into a positional slot, or quietly added
    breaks callers this repository cannot see, so the tuple is pinned rather
    than described. ``create_plane`` grew ``extras``; ``create_stack`` did
    not, and this is where that stays true.
    """

    #: Exactly today's parameters, in today's order.
    PARAMETERS = (
        "collection",
        "config",
        "providers",
        "disable",
        "discover_entry_points",
        "enable_path_safety",
        "enable_response_limit",
        "response_limit_bytes",
        "validate_annotations",
        "instructions",
    )

    def test_the_parameter_names_are_exactly_the_frozen_tuple(self):
        assert tuple(inspect.signature(create_stack).parameters) == self.PARAMETERS

    def test_every_parameter_is_keyword_only(self):
        parameters = inspect.signature(create_stack).parameters
        kinds = {name: p.kind for name, p in parameters.items()}
        assert kinds == dict.fromkeys(self.PARAMETERS, inspect.Parameter.KEYWORD_ONLY)
