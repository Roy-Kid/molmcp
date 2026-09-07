from __future__ import annotations

import ast
import inspect
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from molmcp import CollectionIndex, create_plane, runtime
from molmcp.components.models import ComponentKind, ComponentSpec
from molmcp.config import AppConfig, load_config
from molmcp.discovery.config import DEFAULT_EXCLUDES
from molmcp.discovery.overlay import CapabilityOverlay, OverlayContribution
from molmcp.environment import DiscoveredSource, EnvironmentReport


def _discovery_report() -> EnvironmentReport:
    return EnvironmentReport(
        locator="/envs/foo",
        is_self=False,
        site_paths=(Path("/envs/foo/lib/python3.12/site-packages"),),
        sources=(
            DiscoveredSource(
                name="molpy",
                spec="local:/envs/foo/lib/python3.12/site-packages/molpy",
                identified_by=("entry_point", "keyword"),
                distribution="molpy",
                version="1.2.3",
            ),
        ),
        skipped=("brokenpkg: no importable package directory found",),
        excluded=("moljunk",),
    )


def _load_discovered_config(tmp_path, monkeypatch) -> AppConfig:
    monkeypatch.chdir(tmp_path)
    report = _discovery_report()
    monkeypatch.setattr(
        "molmcp.environment.discover_sources", lambda locator=None, **kwargs: report
    )
    return load_config()


def _concept(identifier: str) -> dict:
    namespace = identifier.split("/", 1)[0].removeprefix("@")
    return {
        "schema_version": "1",
        "id": identifier,
        "kind": "concept",
        "title": "Example",
        "summary": "Example catalog item.",
        "package": namespace,
        "package_version": "1.2.3",
        "tags": [],
        "aliases": [],
        "examples": [],
        "provenance": {
            "source_uri": f"https://example.test/{namespace}",
            "revision": "abc123",
            "declarer": namespace,
        },
    }


def test_custom_excludes_extend_safety_defaults(tmp_path):
    config = AppConfig.from_dict(
        {"schema_version": "2", "excludes": ["generated"]},
        workspace_root=tmp_path,
    )
    collection = runtime.build_collection(config)
    excludes = collection.sources[0].engine.config.excludes
    assert set(DEFAULT_EXCLUDES).issubset(excludes)
    assert "generated" in excludes


def test_explicit_config_is_not_ignored_with_injected_collection(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLMCP_TOKEN", "secret")
    config = AppConfig.from_dict(
        {
            "schema_version": "2",
            "server": {"auth_token_env": "MOLMCP_TOKEN"},
        },
        workspace_root=tmp_path,
    )
    server = create_plane(
        "molcrafts",
        collection=CollectionIndex([]),
        config=config,
        discover_entry_points=False,
    )
    assert server.auth is not None


async def test_environment_token_verifier_accepts_only_current_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOLMCP_TOKEN", "secret")
    config = AppConfig.from_dict(
        {
            "schema_version": "2",
            "server": {"auth_token_env": "MOLMCP_TOKEN"},
        },
        workspace_root=tmp_path,
    )
    server = create_plane(
        "molcrafts",
        collection=CollectionIndex([]),
        config=config,
        discover_entry_points=False,
    )
    assert await server.auth.verify_token("secret") is not None
    assert await server.auth.verify_token("wrong") is None


def test_build_collection_metadata_carries_discovery(tmp_path, monkeypatch):
    config = _load_discovered_config(tmp_path, monkeypatch)
    collection = runtime.build_collection(config)
    assert collection.metadata["discovery"] == _discovery_report().to_dict()


def test_info_configuration_surfaces_discovery(tmp_path, monkeypatch):
    config = _load_discovered_config(tmp_path, monkeypatch)
    collection = runtime.build_collection(config)
    discovery = collection.info()["configuration"]["discovery"]
    report = _discovery_report()
    assert discovery["site_paths"] == [str(path) for path in report.site_paths]
    identified = {
        source["name"]: source["identified_by"] for source in discovery["sources"]
    }
    assert identified["molpy"] == ["entry_point", "keyword"]
    assert discovery["skipped"] == list(report.skipped)
    assert discovery["excluded"] == list(report.excluded)


def test_config_summary_includes_secret_free_discovery(tmp_path, monkeypatch):
    config = _load_discovered_config(tmp_path, monkeypatch)
    text = runtime.config_summary(config)
    summary = json.loads(text)
    assert "discovery" in summary
    assert summary["discovery"]["sources"][0]["identified_by"] == [
        "entry_point",
        "keyword",
    ]
    lowered = text.lower()
    assert "secret" not in lowered
    assert "password" not in lowered
    assert "token" not in lowered


# --- session capability overlays / build_collection extras -----------------

_SESSION_OVERLAYS = "_session_capability_overlays"

_OVERLAY_FACTORY_MODULE = '''\
"""Checkout-side overlay module reached through ComponentSpec.entrypoint."""


class DemoOverlay:
    name = "demo"

    def applies_to(self, snapshot):
        return True

    def contribute(self, graph):
        return "demo-contribution"


def make_overlay():
    return DemoOverlay()
'''

_NON_OVERLAY_FACTORY_MODULE = '''\
"""Checkout-side module whose factory returns a non-overlay object."""


def make_overlay():
    return object()
'''


class _FakeOverlay:
    """Minimal in-process object satisfying ``CapabilityOverlay``."""

    def __init__(self, name: str) -> None:
        self.name = name

    def applies_to(self, snapshot: object) -> bool:
        return True

    def contribute(self, graph: object) -> OverlayContribution:
        return OverlayContribution()


class _RecordedEngine:
    """Stand-in for ``DiscoveryEngine`` that records the overlays argument."""

    def __init__(self, config: object = None, overlays: object = None) -> None:
        self.config = config
        self.overlays = overlays

    def query(self, spec: str) -> object:
        raise NotImplementedError("the recording engine is never queried")


def _record_engines(monkeypatch: pytest.MonkeyPatch) -> list[_RecordedEngine]:
    """Capture every engine ``build_collection`` constructs."""
    recorded: list[_RecordedEngine] = []

    def build(config: object = None, overlays: object = None) -> _RecordedEngine:
        engine = _RecordedEngine(config, overlays)
        recorded.append(engine)
        return engine

    monkeypatch.setattr(runtime, "DiscoveryEngine", build)
    return recorded


def _bare_config(tmp_path: Path) -> AppConfig:
    return AppConfig.from_dict({"schema_version": "2"}, workspace_root=tmp_path)


def _write_checkout(tmp_path: Path, module_name: str, source: str) -> Path:
    """Write one overlay module into a fake checkout tree; return the tree."""
    tree = tmp_path / "checkout"
    overlays = tree / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    (overlays / f"{module_name}.py").write_text(source, encoding="utf-8")
    return tree


def _overlay_seed(component_name: str, module_name: str) -> ComponentSpec:
    return ComponentSpec(
        kind=ComponentKind.OVERLAY,
        name=component_name,
        id=f"overlay.{component_name}",
        path=f"overlays/{module_name}.py",
        entrypoint=f"{module_name}:make_overlay",
    )


def _runtime_source() -> str:
    return Path(runtime.__file__).read_text(encoding="utf-8")


def _session_overlay_node(source: str) -> ast.FunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == _SESSION_OVERLAYS:
            return node
    raise AssertionError(f"runtime.{_SESSION_OVERLAYS} is not defined")


@pytest.fixture
def clean_import_state() -> Iterator[None]:
    """Undo any ``sys.path`` / ``sys.modules`` change a checkout import made."""
    saved_path = list(sys.path)
    saved_modules = set(sys.modules)
    yield
    sys.path[:] = saved_path
    for name in set(sys.modules) - saved_modules:
        del sys.modules[name]


def test_build_collection_accepts_extras_defaulting_to_empty_tuple():
    parameters = inspect.signature(runtime.build_collection).parameters
    assert "extras" in parameters
    default = parameters["extras"].default
    assert isinstance(default, tuple)
    assert default == ()


def test_build_collection_empty_extras_equal_load_overlays(tmp_path, monkeypatch):
    overlay = _FakeOverlay("from-entry-point")
    monkeypatch.setattr(runtime, "load_overlays", lambda: [overlay])
    recorded = _record_engines(monkeypatch)
    runtime.build_collection(_bare_config(tmp_path), extras=())
    assert recorded[0].overlays == [overlay]


def test_build_collection_concatenates_load_overlays_then_extras(tmp_path, monkeypatch):
    first = _FakeOverlay("first")
    second = _FakeOverlay("second")
    extra = _FakeOverlay("from-checkout")
    monkeypatch.setattr(runtime, "load_overlays", lambda: [first, second])
    recorded = _record_engines(monkeypatch)
    runtime.build_collection(_bare_config(tmp_path), extras=(extra,))
    assert recorded[0].overlays == [first, second, extra]


def test_build_collection_passes_an_empty_overlay_list_not_none(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "load_overlays", lambda: [])
    recorded = _record_engines(monkeypatch)
    runtime.build_collection(_bare_config(tmp_path), extras=())
    assert recorded[0].overlays is not None
    assert recorded[0].overlays == []


class TestSessionCapabilityOverlays:
    """``runtime._session_capability_overlays`` — the checkout overlay loader."""

    def test_imports_entrypoint_from_checkout_and_returns_the_instance(
        self, tmp_path, clean_import_state
    ):
        tree = _write_checkout(tmp_path, "demo_factory", _OVERLAY_FACTORY_MODULE)
        seed = _overlay_seed("demo", "demo_factory")
        loaded = list(runtime._session_capability_overlays((seed,), tree))
        assert len(loaded) == 1
        overlay = loaded[0]
        assert isinstance(overlay, CapabilityOverlay)
        assert type(overlay).__name__ == "DemoOverlay"
        assert overlay.name == "demo"
        assert overlay.contribute(None) == "demo-contribution"

    def test_factory_result_that_is_not_an_overlay_names_the_component(
        self, tmp_path, clean_import_state
    ):
        tree = _write_checkout(tmp_path, "faulty_factory", _NON_OVERLAY_FACTORY_MODULE)
        seed = _overlay_seed("broken-demo", "faulty_factory")
        with pytest.raises(ValueError) as excinfo:
            list(runtime._session_capability_overlays((seed,), tree))
        assert "broken-demo" in str(excinfo.value)

    def test_never_calls_load_overlays_and_never_globs_the_tree(self):
        source = _runtime_source()
        node = _session_overlay_node(source)
        called = {
            child.func.id if isinstance(child.func, ast.Name) else child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, (ast.Name, ast.Attribute))
        }
        assert "load_overlays" not in called
        assert not called & {"glob", "rglob", "iterdir", "walk"}
        segment = ast.get_source_segment(source, node)
        assert segment is not None
        assert "glob" not in segment

    def test_is_private_with_no_public_loader_alias(self):
        loader = getattr(runtime, _SESSION_OVERLAYS)
        assert loader.__name__.startswith("_")
        assert _SESSION_OVERLAYS not in getattr(runtime, "__all__", ())
        aliases = [
            name
            for name, value in vars(runtime).items()
            if value is loader and not name.startswith("_")
        ]
        assert aliases == []
        assert not hasattr(runtime, "overlay_loader")
