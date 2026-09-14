"""WorkerProvider — the adapter that owns a child plane's whole lifetime.

``register`` is the only place the two halves meet: a Supervisor starts the
child, the proxy publishes its bare tool names, and only then does the adapter
take over teardown by swapping FastMCP's private ``_lifespan``. FastMCP 4 does
have a public ``mcp.lifespan`` — the inherited ``AggregateProvider.lifespan``,
which takes no server argument and combines the *mounted providers'* lifespans.
That is a different object from the ``FastMCP(lifespan=...)`` callable held in
``_lifespan``, so nothing here reads it; these tests enter
``mcp._lifespan_manager()`` instead.

The primary reaper is that swapped lifespan: entering and leaving
``mcp._lifespan_manager()`` must leave no child behind, with no ``shutdown()``
call from the test. ``shutdown()`` is the *explicit abort* — the failure path
and the last resort, never the thing that proves teardown works.

``create_plane`` is deliberately absent: these are unit tests of the adapter,
and whole-server assembly is a different question from whether this class
starts and reaps a child.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest
from fastmcp import FastMCP

import molmcp
from molmcp.provider_worker import WorkerProvider, supervisor, worker

_FIXTURES = Path(__file__).parent / "fixtures"
_ENTRYPOINT = "echo:EchoProvider"

_WORKER_FILE = Path(worker.__file__)
_WORKER_SOURCE = _WORKER_FILE.read_text(encoding="utf-8")
_PACKAGE_DIR = _WORKER_FILE.parent


@runtime_checkable
class _Reapable(Protocol):
    """The one part of ``Popen`` these tests need: is the child still alive?"""

    def poll(self) -> int | None: ...


def _members(value: object) -> list[object]:
    return list(vars(value).values()) if hasattr(value, "__dict__") else []


def _child_process(provider: WorkerProvider) -> _Reapable:
    """The live child, reached through the provider's own attributes.

    The private names are not frozen by the contract, so look for the object
    that answers ``poll`` — the process the Supervisor spawned.
    """
    for holder in _members(provider):
        if isinstance(holder, _Reapable):
            return holder
        for nested in _members(holder):
            if isinstance(nested, _Reapable):
                return nested
    raise AssertionError("no child process is reachable from the provider")


def _install_supervisor(monkeypatch: pytest.MonkeyPatch, factory: type) -> None:
    """Swap the Supervisor ``register`` builds, whichever import style it used."""
    monkeypatch.setattr(supervisor, "Supervisor", factory)
    monkeypatch.setattr(worker, "Supervisor", factory, raising=False)


def _recovery_node_ids(tree: ast.AST) -> set[int]:
    """Ids of every node inside an ``except`` handler or a ``finally`` block."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            ids.update(id(child) for child in ast.walk(node))
        elif isinstance(node, ast.Try):
            for statement in node.finalbody:
                ids.update(id(child) for child in ast.walk(statement))
    return ids


class TestWorkerProvider:
    """One WorkerProvider concern per test; no ``create_plane`` anywhere."""

    def test_probe_is_false_when_the_path_is_not_a_directory(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "not-a-checkout"
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=missing)

        assert provider.probe() is False

    def test_register_without_a_checkout_names_what_is_missing(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "not-a-checkout"
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=missing)

        with pytest.raises(RuntimeError) as excinfo:
            provider.register(FastMCP("echo"))

        message = str(excinfo.value)
        assert str(missing) in message
        assert _ENTRYPOINT in message
        # A missing checkout is not a missing wheel; do not send anyone to pip.
        assert "pip install" not in message

    def test_a_name_outside_the_provider_pattern_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            WorkerProvider(name="Echo_1", entrypoint=_ENTRYPOINT, path=_FIXTURES)

    async def test_register_publishes_the_bare_tool_name(self) -> None:
        mcp = FastMCP("echo")
        before = mcp._lifespan
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=_FIXTURES)

        try:
            provider.register(mcp)

            assert {tool.name for tool in await mcp.list_tools()} == {"echo"}
            # Teardown is now the adapter's; the swap is how it gets there.
            assert mcp._lifespan is not before
        finally:
            provider.shutdown()

    async def test_leaving_the_lifespan_reaps_the_child(self) -> None:
        mcp = FastMCP("echo")
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=_FIXTURES)
        provider.register(mcp)
        process = _child_process(provider)
        assert process.poll() is None

        async with mcp._lifespan_manager():
            pass

        # Reaped by the swapped lifespan alone — this test never calls
        # shutdown(), because shutdown() is the abort, not the reaper.
        assert process.poll() is not None

    def test_shutdown_aborts_the_child_and_is_idempotent(self) -> None:
        mcp = FastMCP("echo")
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=_FIXTURES)
        provider.register(mcp)
        process = _child_process(provider)

        provider.shutdown()
        provider.shutdown()

        assert process.poll() is not None

    def test_a_failed_register_reaps_and_leaves_the_lifespan_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[str] = []
        reaped: list[str] = []

        class WrongPlaneSupervisor:
            """A child greeting as a plane the adapter was not built for."""

            def __init__(
                self, *, entrypoint: str, path: object, **extra: object
            ) -> None:
                built.append(entrypoint)

            def start(self) -> dict[str, object]:
                return {
                    "type": "hello",
                    "protocol": 1,
                    "name": "other",
                    "tools": [],
                }

            def invoke(self, name: str, args: dict[str, object]) -> object:
                raise AssertionError("register must fail before any invoke")

            def shutdown(self) -> None:
                reaped.append("shutdown")

        _install_supervisor(monkeypatch, WrongPlaneSupervisor)
        mcp = FastMCP("echo")
        before = mcp._lifespan
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=_FIXTURES)

        with pytest.raises(ValueError):
            provider.register(mcp)

        assert built == [_ENTRYPOINT]
        assert reaped == ["shutdown"]
        # The swap never happened, so the server owes this adapter nothing.
        assert mcp._lifespan is before

    def test_the_adapter_has_no_close_and_no_public_export(self) -> None:
        assert not hasattr(WorkerProvider, "close")
        assert "WorkerProvider" not in molmcp.__all__

    def test_the_package_registers_no_atexit_hook(self) -> None:
        offenders = [
            path.name
            for path in sorted(_PACKAGE_DIR.rglob("*.py"))
            if "atexit.register" in path.read_text(encoding="utf-8")
        ]

        assert offenders == []

    def test_one_finalize_and_it_sits_on_the_success_path(self) -> None:
        assert _WORKER_SOURCE.count("weakref.finalize(") == 1
        tree = ast.parse(_WORKER_SOURCE)
        finalize_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "finalize"
        ]
        assert len(finalize_calls) == 1

        finalize = finalize_calls[0]
        assert id(finalize) not in _recovery_node_ids(tree), (
            "the fallback is for a registered server, not for a failed register"
        )

        swap_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute) and target.attr == "_lifespan"
        ]
        assert swap_lines, "register must swap mcp._lifespan"
        assert finalize.lineno > max(swap_lines)

    def test_worker_provider_satisfies_the_provider_protocol(self) -> None:
        provider = WorkerProvider(name="echo", entrypoint=_ENTRYPOINT, path=_FIXTURES)

        assert isinstance(provider, molmcp.Provider)
