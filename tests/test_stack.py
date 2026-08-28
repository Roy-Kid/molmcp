"""FastMCP composition: core + namespaced provider mounts."""

from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from molmcp import CollectionIndex, create_plane, create_stack


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
