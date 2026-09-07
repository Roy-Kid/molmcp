#!/usr/bin/env python3
"""Regression example: a checkout plane served from its own process.

Standalone (no pytest dependency). Writes a throwaway ``echo.py`` into a
temporary directory — a plane this interpreter never imports — hands that
directory to the public ``WorkerProvider(name=, entrypoint=, path=)``, and
loads it through public ``create_plane(..., discover_entry_points=False)``.
The tools a client then sees came out of a child process over the worker's
own wire; this process only ever saw ``create_plane``. Asserts the
hard-coded goldens below.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-05-provider-worker.md``,
Testing strategy -> Regression, and acceptance AC-009):

    {tool.name for tool in await list_tools()} == {"echo"}
    call_tool("echo", {"text": "ping"}).structured_content == {"text": "ping"}

Public surface only: ``molmcp.create_plane`` and
``molmcp.provider_worker.WorkerProvider``, plus the FastMCP API every
``create_plane`` caller already uses (``list_tools`` / ``call_tool``).
Deliberately absent: ``Supervisor``, ``protocol``, ``proxy``, ``child.py``,
and ``provider_sdk`` — the generated ``echo.py`` imports the SDK, but it does
so in the *child* interpreter, which is the whole point. Also absent: pytest,
network, environment variables, and any third-party import or subprocess at
runtime. The one subprocess here is molmcp's own worker child.

Teardown is explicit. Production enters this plane's lifespan through the
composed core (spec 08's ``FastMCPProvider.lifespan``), which reaches the
``_lifespan`` that ``register`` wrapped; a script that never starts a server
never enters it, so ``shutdown()`` runs in a ``finally`` and no child outlives
the run.

Run directly::

    uv run python regressions/autonomous-harness-evolution-05-provider-worker.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_05_provider_worker``.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from molmcp import create_plane
from molmcp.provider_worker import WorkerProvider

# In-repo goldens, 2026-09-07, no third-party oracle.
_EXPECTED_TOOL_NAMES = {"echo"}
_ECHO_ARGS = {"text": "ping"}
_EXPECTED_ECHO_RESULT = {"text": "ping"}

_PLANE = "echo"
_ENTRYPOINT = "echo:EchoProvider"

# The plane, written to disk at runtime and imported only by the child. It is
# a string here, not an import: this interpreter must never hold it.
_ECHO_MODULE = """\
\"\"\"Echo plane for the worker regression — served from a temporary checkout.\"\"\"

from __future__ import annotations

from molmcp.provider_sdk import READ_ONLY, ProviderBase, tool


class EchoProvider(ProviderBase):
    \"\"\"Echo plane — one read-only tool.\"\"\"

    name = "echo"

    @tool(READ_ONLY)
    def echo(self, text: str) -> dict[str, str]:
        \"\"\"Echo text back.\"\"\"
        return {"text": text}
"""


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


async def _exercise(checkout: Path) -> None:
    """Serve *checkout* as the ``echo`` plane and check the goldens.

    Args:
        checkout: Directory holding the generated ``echo.py``.
    """
    provider = WorkerProvider(
        name=_PLANE,
        entrypoint=_ENTRYPOINT,
        path=checkout,
    )
    try:
        server = create_plane(
            _PLANE,
            provider=provider,
            discover_entry_points=False,
        )
        names = {tool.name for tool in await server.list_tools()}
        _require(
            names == _EXPECTED_TOOL_NAMES,
            f"published tool names {names} != {_EXPECTED_TOOL_NAMES}",
        )

        result = await server.call_tool("echo", _ECHO_ARGS)
        structured = result.structured_content
        _require(
            structured == _EXPECTED_ECHO_RESULT,
            f"echo({_ECHO_ARGS}) structured {structured!r} != {_EXPECTED_ECHO_RESULT}",
        )

        print(f"tools={sorted(names)}")
        print(f"echo({_ECHO_ARGS}) -> {structured}")
    finally:
        # The script runs no server, so nothing else will enter the lifespan
        # that register() wrapped; the explicit abort is what reaps the child.
        provider.shutdown()


def main() -> int:
    prefix = "molmcp-provider-worker-regression-"
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        checkout = Path(tmp)
        (checkout / "echo.py").write_text(_ECHO_MODULE, encoding="utf-8")
        asyncio.run(_exercise(checkout))

    print("\nOK: the echo plane answered from its own process; goldens match.")
    return 0


def test_autonomous_harness_evolution_05_provider_worker() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
