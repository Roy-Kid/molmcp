"""Standalone FastMCP server for the molrs plugin."""

from __future__ import annotations

from molmcp import create_server

from .provider import MolRsProvider

mcp = create_server(
    name="molmcp-molrs",
    providers=[MolRsProvider()],
    discover_entry_points=False,
    import_roots=None,
)
