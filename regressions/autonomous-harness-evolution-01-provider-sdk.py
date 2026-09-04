#!/usr/bin/env python3
"""Regression example: public Provider SDK through ``create_plane``.

Standalone (no pytest dependency). Declares a minimal ``demo`` Provider with
the public ``molmcp.provider_sdk`` surface (``ProviderBase``, ``tool``,
``READ_ONLY``), loads it through public ``create_plane(...,
discover_entry_points=False)``, and asserts the hard-coded goldens below.

Hard-coded goldens (in-repo, 2026-09-04, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-01-provider-sdk.md``, Testing
strategy -> Regression example):

    registered tool names == ["echo"]
    echo.read_only_hint is True
    call_tool("echo", {"text": "sdk-ok"}) content contains "sdk-ok"

Imports are this project plus the FastMCP API already used by ``create_plane``
callers (``list_tools`` / ``call_tool``). No live third-party oracle.

Run directly::

    uv run python regressions/autonomous-harness-evolution-01-provider-sdk.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via ``test_autonomous_harness_evolution_01_provider_sdk``.
"""

from __future__ import annotations

import asyncio
import sys

from molmcp import create_plane
from molmcp.provider_sdk import READ_ONLY, ProviderBase, tool

# In-repo goldens, 2026-09-04, no third-party oracle.
_EXPECTED_TOOL_NAMES = ["echo"]
_EXPECTED_READ_ONLY_HINT = True
_ECHO_TEXT = "sdk-ok"


class Demo(ProviderBase):
    """Minimal public-SDK plane used only by this regression."""

    name = "demo"

    @tool(READ_ONLY)
    def echo(self, text: str) -> str:
        """Return *text* unchanged."""
        return text


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


async def _exercise() -> None:
    server = create_plane(
        "demo",
        provider=Demo(),
        discover_entry_points=False,
    )
    tools = await server.list_tools()
    names = [item.name for item in tools]
    _require(
        names == _EXPECTED_TOOL_NAMES,
        f"registered tool names {names} != {_EXPECTED_TOOL_NAMES}",
    )

    echo = tools[0]
    hint = echo.annotations.read_only_hint if echo.annotations is not None else None
    _require(
        hint is _EXPECTED_READ_ONLY_HINT,
        f"echo.read_only_hint is {hint!r}, expected {_EXPECTED_READ_ONLY_HINT}",
    )

    result = await server.call_tool("echo", {"text": _ECHO_TEXT})
    text = result.content[0].text
    _require(
        _ECHO_TEXT in text,
        f"echo({_ECHO_TEXT!r}) content {text!r} does not contain {_ECHO_TEXT!r}",
    )
    print(f"tools={names}")
    print(f"read_only_hint={hint}")
    print(f"echo({_ECHO_TEXT!r}) -> {text}")


def main() -> int:
    asyncio.run(_exercise())
    print("\nOK: public Provider SDK plane registered echo; goldens match.")
    return 0


def test_autonomous_harness_evolution_01_provider_sdk() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
