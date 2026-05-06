"""Standalone FastMCP server for the molpack plugin."""

from __future__ import annotations

from molmcp import create_server

from .provider import MolPackProvider

mcp = create_server(
    name="molmcp-molpack",
    providers=[MolPackProvider()],
    discover_entry_points=False,
    import_roots=None,
)
