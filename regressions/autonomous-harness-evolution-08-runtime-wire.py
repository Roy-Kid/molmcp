#!/usr/bin/env python3
"""Regression example: empty-safe overlays and a serve that never reads harness.

Standalone (no pytest dependency). Builds one minimal ``AppConfig`` over a
throwaway directory and asks ``molmcp.runtime.build_collection`` for a
collection twice — once with no extras, once with one overlay-shaped fake —
then points ``Path.home()`` at a second throwaway directory and drives the
public settings surface and ``molmcp.create_stack`` with both arms injected.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-08-runtime-wire.md``, Testing
strategy -> 回归, and acceptance AC-011):

    build_collection(config, extras=()) -> the engine's overlays list
        is not None and == list(load_overlays())
    build_collection(config, extras=(fake,)) -> [*load_overlays(), fake]
    create_stack(collection=CollectionIndex([]), providers=[Demo()])
        returns a FastMCP named "molcrafts" carrying "packages" and
        "demo_ping", built with zero WorkerProvider constructions and
        without close() being owed to anything
    load_settings() over {"harness": {"dev": ...}} raises SettingsError
        naming "harness.dev"
    load_settings() over {"shareReceipts": ...} raises SettingsError
        naming "shareReceipts"
    set_value(user_settings_path(), "harness.owner", "molcrafts") then
        load_settings().harness == {"owner": "molcrafts"}

The first golden is a *relationship*, never a count. An empty overlay list is
a legal answer — this repository declares no ``molmcp.overlays`` entry points,
so both sides are ``[]`` today — and what is pinned is that the engine is
handed a list rather than ``None``: ``None`` would make it discover overlays
for itself and quietly ignore whatever a checkout contributed. Nothing here
requires the default to be non-empty.

Reading the engine's overlays means reaching through the returned
``CollectionIndex``'s public ``sources`` tuple to a ``SourceBinding.engine``
and then to its private ``_overlays``. That is deliberate and is the one
non-public read in this file: the concatenation has no public getter, and
asserting on a stand-in engine would prove the stand-in.

The dual-injection golden runs *after* the settings goldens, on purpose. The
user settings file at that point holds ``harness.owner`` alone — a partial
locator, which ``create_stack`` answers with ``ConfigurationError`` on any
path that consults it. Completing at all is therefore the first proof that
dual injection never reads the locator, and the WorkerProvider construction
counter is the second. ``discover_entry_points`` is left at its default
``True`` so the skip is shown to come from ``providers is not None`` alone.

Public surface only, with two named exceptions: ``molmcp`` (``create_stack``,
``CollectionIndex``, ``AppConfig``), ``molmcp.runtime``
(``build_collection`` / ``resolved_cache_dir``), ``molmcp.provider_sdk``,
``molmcp.settings``, and the FastMCP API ``create_plane`` callers already use
(``list_tools``). The exceptions are ``molmcp.discovery.overlay``, whose
``load_overlays`` the golden names outright, and
``molmcp.provider_worker.WorkerProvider``, patched only to count
constructions. Deliberately absent: real git, network, subprocesses,
environment variables, pytest, and every harness primitive this wiring would
reach for on the git path — ``ImmutableGitStore``, ``GitHubTransport``,
``Activation.bind``, ``load_harness_catalog``, and
``runtime._session_capability_overlays``.

``Path.home`` is patched by hand, because a standalone script has no pytest
monkeypatch, and is restored in a ``finally`` beside the temporary
directories and the ``WorkerProvider.__init__`` wrapper. The real
``~/.molmcp/settings.json`` is therefore never read or written; the settings
reads pass no project root, so a checkout's ``.molmcp/`` cannot change the
answer either. Both discovery cache roots stay inside a throwaway directory,
which the script asserts before building anything, and no absolute machine
path is printed.

Run directly::

    uv run python regressions/autonomous-harness-evolution-08-runtime-wire.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_08_runtime_wire``.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from fastmcp import FastMCP

from molmcp import CollectionIndex, create_stack
from molmcp.config import AppConfig
from molmcp.discovery.overlay import (
    CapabilityOverlay,
    OverlayContribution,
    load_overlays,
)
from molmcp.provider_sdk import READ_ONLY, ProviderBase, tool
from molmcp.provider_worker import WorkerProvider
from molmcp.runtime import build_collection, resolved_cache_dir
from molmcp.settings import (
    SettingsError,
    load_settings,
    set_value,
    user_settings_path,
    write_settings_file,
)

# In-repo goldens, 2026-09-07, no third-party oracle.
_FAKE_OVERLAY_NAME = "regression-08-overlay"
_PLANE_NAME = "demo"
_CORE_NAME = "molcrafts"
_CORE_TOOL = "packages"
_MOUNTED_TOOL = "demo_ping"
_NO_WORKERS: list[str] = []

_REJECTED_NESTED_KEY = "harness.dev"
_REJECTED_TOP_KEY = "shareReceipts"
# The value written and the value expected back are spelled out separately
# on purpose: deriving one from the other would make them agree by
# construction, and a round trip that cannot disagree proves nothing.
_HARNESS_OWNER = "molcrafts"
_EXPECTED_HARNESS = {"owner": "molcrafts"}

# The two settings documents that must not load, written verbatim.
_DEV_DOCUMENT: dict[str, object] = {"harness": {"dev": "on"}}
_SHARE_DOCUMENT: dict[str, object] = {_REJECTED_TOP_KEY: "true"}

_CACHE_DIR_NAME = "cache"
#: The engine attribute holding the assembled overlay list. Private on
#: purpose — see the module docstring.
_ENGINE_OVERLAYS = "_overlays"


class _RegressionOverlay:
    """Overlay-shaped stand-in: the Protocol's three members and nothing else.

    Neither method is ever called. ``build_collection`` only assembles the
    list; contributing to a graph would mean indexing a source, which this
    script deliberately never does.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def applies_to(self, snapshot: object) -> bool:
        """Refuse every snapshot; nothing here resolves one."""
        return False

    def contribute(self, graph: object) -> OverlayContribution:
        """Contribute nothing; unreachable while ``applies_to`` is False."""
        return OverlayContribution()


class Demo(ProviderBase):
    """Minimal public-SDK plane, injected so the provider arm never runs."""

    name = _PLANE_NAME

    @tool(READ_ONLY)
    def ping(self) -> str:
        """Return a fixed string so the mount is observable."""
        return "pong"


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _patch_home(home: Path) -> Callable[[], None]:
    """Point every ``Path.home()`` at *home* until the returned undo runs.

    Args:
        home: Throwaway directory to stand in for the user's home.

    Returns:
        A no-argument callable restoring the original ``Path.home``.
    """
    original = vars(Path).get("home")
    Path.home = classmethod(lambda cls: home)

    def restore() -> None:
        if original is None:  # pragma: no cover - stdlib always defines it
            delattr(Path, "home")
        else:
            Path.home = original

    return restore


def _watch_worker_provider() -> tuple[list[str], Callable[[], None]]:
    """Record every ``WorkerProvider`` construction until the undo runs.

    Wrapping the constructor rather than the module attribute catches the
    class wherever it is reached from, which is the whole claim: a stack
    assembled from two injected arms builds no checkout-backed plane, so
    nothing is left owing a subprocess teardown.

    Returns:
        The live list of constructed plane names, and a callable restoring
        the original ``__init__``.
    """
    constructed: list[str] = []
    original: Callable[..., None] = WorkerProvider.__init__

    def recording(self: WorkerProvider, *args: object, **kwargs: object) -> None:
        constructed.append(str(kwargs.get("name", "?")))
        original(self, *args, **kwargs)

    WorkerProvider.__init__ = recording

    def restore() -> None:
        WorkerProvider.__init__ = original

    return constructed, restore


def _config(root: Path) -> AppConfig:
    """Build the minimal config, and prove its cache root is throwaway.

    Args:
        root: Throwaway directory standing in for a workspace.

    Returns:
        A config with one source and a cache root inside *root*.
    """
    config = AppConfig.from_dict(
        {"schema_version": "2", "cache_dir": str(root / _CACHE_DIR_NAME)},
        workspace_root=root,
    )
    cache_root = resolved_cache_dir(config)
    _require(
        cache_root == (root / _CACHE_DIR_NAME).resolve(),
        "the resolved cache root is not the throwaway directory",
    )
    return config


def _engine_overlays(collection: CollectionIndex) -> list[object]:
    """Return the overlay list ``build_collection`` handed the engine.

    Args:
        collection: The collection ``build_collection`` just returned.

    Returns:
        The engine's overlays, in the order the engine will apply them.
    """
    bindings = collection.sources
    _require(bool(bindings), "the collection has no source binding to read")
    engine = bindings[0].engine
    _require(
        hasattr(engine, _ENGINE_OVERLAYS),
        f"the engine exposes no {_ENGINE_OVERLAYS} to read",
    )
    overlays = getattr(engine, _ENGINE_OVERLAYS)
    _require(
        overlays is not None,
        "the engine was handed overlays=None instead of a list",
    )
    _require(
        isinstance(overlays, list),
        f"the engine's overlays are a {type(overlays).__name__}, not a list",
    )
    return list(overlays)


def _check_default_overlays(root: Path, baseline: list[CapabilityOverlay]) -> None:
    """Golden 1: ``extras=()`` is the entry-point list, empty included.

    Args:
        root: Throwaway directory to build the collection under.
        baseline: ``list(load_overlays())``, read once.
    """
    overlays = _engine_overlays(build_collection(_config(root), extras=()))
    _require(
        overlays == baseline,
        f"extras=() produced {overlays!r}, not list(load_overlays())",
    )

    print(f"extras=() -> {len(overlays)} overlay(s) == list(load_overlays())")


def _check_extras_concat(root: Path, baseline: list[CapabilityOverlay]) -> None:
    """Golden 2: ``extras`` land after the entry-point overlays, in order.

    Args:
        root: Throwaway directory to build the collection under.
        baseline: ``list(load_overlays())``, read once.
    """
    fake = _RegressionOverlay(_FAKE_OVERLAY_NAME)
    _require(
        isinstance(fake, CapabilityOverlay),
        "the fake does not satisfy the CapabilityOverlay protocol",
    )

    overlays = _engine_overlays(build_collection(_config(root), extras=(fake,)))
    expected = [*baseline, fake]
    _require(
        overlays == expected,
        f"extras=(fake,) produced {overlays!r}, not load_overlays() then the fake",
    )
    _require(
        overlays[-1] is fake,
        "the checkout overlay is not last in the engine's list",
    )

    print(f"extras=(fake,) -> load_overlays() then {_FAKE_OVERLAY_NAME!r}")


def _check_rejected_setting(document: dict[str, object], key: str) -> None:
    """Goldens 4 and 5: *document* must not load, and must name *key*.

    Args:
        document: Settings JSON to write as the user layer.
        key: The offending key the refusal has to name.
    """
    write_settings_file(user_settings_path(), document)
    try:
        load_settings()
    except SettingsError as exc:
        message = str(exc)
    else:
        raise AssertionError(f"settings carrying {key!r} loaded instead of raising")
    _require(
        key in message,
        f"the SettingsError for {key!r} does not name it",
    )

    print(f"load_settings() over {key!r} -> SettingsError naming it")


def _check_owner_round_trip() -> None:
    """Golden 6: ``harness.owner`` is settable on its own and reads back."""
    path = user_settings_path()
    write_settings_file(path, {})
    set_value(path, "harness.owner", _HARNESS_OWNER)

    harness = load_settings().harness
    _require(
        harness == _EXPECTED_HARNESS,
        f"harness reads back as {harness!r}, not {_EXPECTED_HARNESS!r}",
    )

    print(f"harness after `config set harness.owner` -> {harness}")


async def _dual_injection_tool_names() -> set[str]:
    """Compose the stack with both arms injected and list what it serves.

    Returns:
        Every tool name the composed core exposes, namespaces included.
    """
    stack = create_stack(collection=CollectionIndex([]), providers=[Demo()])
    _require(
        isinstance(stack, FastMCP),
        f"create_stack returned a {type(stack).__name__}, not a FastMCP",
    )
    _require(
        stack.name == _CORE_NAME,
        f"the composed server is named {stack.name!r}, not {_CORE_NAME!r}",
    )
    return {item.name for item in await stack.list_tools()}


def _check_dual_injection(constructed: list[str]) -> None:
    """Golden 3: both arms injected, nothing fetched, nothing to close.

    Args:
        constructed: The live list of ``WorkerProvider`` plane names, which
            must still be empty once the stack is composed.
    """
    names = asyncio.run(_dual_injection_tool_names())
    _require(
        _CORE_TOOL in names,
        f"the composed core does not serve {_CORE_TOOL!r}",
    )
    _require(
        _MOUNTED_TOOL in names,
        f"the injected plane did not mount as {_MOUNTED_TOOL!r}",
    )
    _require(
        constructed == _NO_WORKERS,
        f"dual injection built WorkerProvider(s) {constructed!r}",
    )

    print(f"create_stack(collection=..., providers=[Demo()]) -> {_CORE_NAME!r}")
    print(f"tools include {_CORE_TOOL!r} and {_MOUNTED_TOOL!r}")
    print(f"WorkerProvider constructions={constructed}")


def main() -> int:
    baseline = list(load_overlays())

    with tempfile.TemporaryDirectory(prefix="molmcp-wire-regression-") as workspace:
        root = Path(workspace)
        _check_default_overlays(root, baseline)
        _check_extras_concat(root, baseline)

    home_dir = tempfile.TemporaryDirectory(prefix="molmcp-wire-regression-home-")
    restore_home = _patch_home(Path(home_dir.name))
    constructed, restore_worker = _watch_worker_provider()
    try:
        _check_rejected_setting(_DEV_DOCUMENT, _REJECTED_NESTED_KEY)
        _check_rejected_setting(_SHARE_DOCUMENT, _REJECTED_TOP_KEY)
        _check_owner_round_trip()
        # Runs with the partial locator the round-trip just wrote: consulting
        # it would be a ConfigurationError, so completing is half the proof.
        _check_dual_injection(constructed)
    finally:
        restore_worker()
        restore_home()
        home_dir.cleanup()

    print("\nOK: overlays stay empty-safe and dual injection never reads harness.")
    return 0


def test_autonomous_harness_evolution_08_runtime_wire() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
