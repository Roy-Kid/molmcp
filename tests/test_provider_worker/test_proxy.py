"""Proxy — hello signature facts become published FastMCP tools.

``bind_tools`` is the only place a worker plane's catalog turns into MCP
metadata. The hello frame here is written by hand (the shape the child
promises), and the callable it produces is bound to a real ``FastMCP``: the
JSON Schema in the assertions is FastMCP's, produced from the rebuilt
signature, never hand-rolled by the proxy.

The child is a fake ``invoke`` that records its calls, so nothing in this file
touches a subprocess.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools import Tool

from molmcp.provider_worker import proxy

#: The input schema FastMCP publishes for ``echo(text: str)``. Hard-coded:
#: the proxy is correct when FastMCP sees the same signature the child sent.
_ECHO_INPUT_SCHEMA: dict[str, object] = {
    "additionalProperties": False,
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "type": "object",
}


def _hello() -> dict[str, object]:
    """One hello frame, built by hand, carrying signature facts only."""
    return {
        "type": "hello",
        "protocol": 1,
        "name": "echo",
        "tools": [
            {
                "name": "echo",
                "attribute": "echo",
                "doc": "Echo text back.",
                "annotations": {
                    "read_only_hint": True,
                    "destructive_hint": False,
                    "idempotent_hint": True,
                    "open_world_hint": False,
                },
                "parameters": [
                    {
                        "name": "text",
                        "kind": "POSITIONAL_OR_KEYWORD",
                        "annotation": "str",
                        "has_default": False,
                    }
                ],
            }
        ],
    }


class _RecordingInvoke:
    """The Supervisor seam: records ``(name, args)`` and answers like echo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, name: str, args: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, dict(args)))
        return {"text": args["text"]}


async def _published(mcp: FastMCP, name: str) -> Tool:
    """The one published tool called *name*."""
    by_name = {tool.name: tool for tool in await mcp.list_tools()}
    assert name in by_name, f"{name!r} not published: {sorted(by_name)}"
    return by_name[name]


class TestProxy:
    """One ``bind_tools`` concern per test, against a real FastMCP."""

    async def test_bind_tools_publishes_the_bare_name(self) -> None:
        mcp = FastMCP("echo")

        bound = proxy.bind_tools(mcp, _hello(), _RecordingInvoke())

        assert bound == ["echo"]
        assert {tool.name for tool in await mcp.list_tools()} == {"echo"}

    async def test_description_and_schema_come_from_the_hello_facts(self) -> None:
        mcp = FastMCP("echo")
        proxy.bind_tools(mcp, _hello(), _RecordingInvoke())

        tool = await _published(mcp, "echo")

        assert tool.description == "Echo text back."
        assert tool.parameters == _ECHO_INPUT_SCHEMA

    async def test_annotations_survive_the_wire(self) -> None:
        mcp = FastMCP("echo")
        proxy.bind_tools(mcp, _hello(), _RecordingInvoke())

        annotations = (await _published(mcp, "echo")).annotations

        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True
        assert annotations.open_world_hint is False

    async def test_calling_the_tool_routes_through_invoke(self) -> None:
        mcp = FastMCP("echo")
        invoke = _RecordingInvoke()
        proxy.bind_tools(mcp, _hello(), invoke)

        result = await mcp.call_tool("echo", {"text": "ping"})

        assert invoke.calls == [("echo", {"text": "ping"})]
        assert result.structured_content == {"text": "ping"}
