"""Proxy — a child's hello catalog becomes published FastMCP tools.

The child sends signature *facts*, never a schema. This module turns each fact
list back into an :class:`inspect.Signature`, hangs it on a callable, and hands
that callable to ``mcp.tool(...)`` — the same call an in-process provider's
``register`` makes. FastMCP therefore stays the only producer of JSON Schema in
the system, so a proxied tool and a local one are described to a client by the
same machinery rather than by two descriptions that have to be kept in step.

The docstring a client reads is the child's method docstring, and the
``ToolAnnotations`` are the child's declared ones: crossing a process boundary
must not quietly downgrade what a caller is told before it confirms a call.

Nothing here knows about subprocesses. ``invoke`` is a plain callable, so this
module is exercised with a recording stub and the Supervisor is exercised
separately.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from .protocol import rebuild_signature

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _build_call(
    fact: Mapping[str, Any],
    invoke: Callable[[str, dict[str, Any]], Any],
) -> Callable[..., Any]:
    """Build the parent-side callable standing in for one child tool.

    Arguments are bound against the rebuilt signature before they cross the
    pipe, so a bad call fails here — with the child's own parameter names in
    the message — rather than as an ``error`` frame from a process the caller
    cannot see. Defaults are applied for the same reason: the child receives
    the call the signature says it will, whether or not the client spelled
    every argument out.

    Args:
        fact: One hello catalog entry — ``name``, ``doc`` and ``parameters``.
        invoke: How a call reaches the child: bare tool name and arguments.

    Returns:
        A callable carrying the child's tool name, docstring, signature and
        per-parameter type annotations — what FastMCP reads off a function
        when it publishes it. The MCP ``ToolAnnotations`` (the hints a client
        uses to decide whether to confirm a call) are *not* on the callable;
        :func:`bind_tools` passes those to ``mcp.tool`` itself.
    """
    name: str = fact["name"]
    signature = rebuild_signature(fact["parameters"])

    def call(*args: object, **kwargs: object) -> Any:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return invoke(name, dict(bound.arguments))

    call.__name__ = name
    call.__doc__ = fact["doc"]
    call.__signature__ = signature  # type: ignore[attr-defined]
    call.__annotations__ = {
        parameter.name: parameter.annotation
        for parameter in signature.parameters.values()
        if parameter.annotation is not inspect.Parameter.empty
    }
    return call


def bind_tools(
    mcp: FastMCP,
    hello: Mapping[str, Any],
    invoke: Callable[[str, dict[str, Any]], Any],
) -> list[str]:
    """Publish every tool a child declared onto its FastMCP server.

    Names are registered **bare** (``echo``, never ``echo_echo``): a composed
    core adds the namespace when it mounts the plane, and a plane that
    prefixed its own names would be namespaced twice.

    Args:
        mcp: The server for this plane — the one whose name is the plane id.
        hello: The child's greeting; only its ``tools`` list is read.
        invoke: How a published tool reaches the child. Bound at publish time,
            so the tool holds the Supervisor rather than looking one up.

    Returns:
        The bare names bound, in the order the child declared them.

    Raises:
        ProtocolError: Raised by
            :func:`~molmcp.provider_worker.protocol.rebuild_signature` when a
            catalog entry names a parameter kind that is not an
            :class:`inspect.Parameter` kind. Tools declared before the bad
            entry are already bound when this happens, which is why the
            caller's failure path reaps the child instead of carrying on with
            a half-bound plane.
    """
    tools: Sequence[Mapping[str, Any]] = hello["tools"]
    bound: list[str] = []
    for fact in tools:
        annotations = ToolAnnotations(**fact["annotations"])
        mcp.tool(name=fact["name"], annotations=annotations)(_build_call(fact, invoke))
        bound.append(fact["name"])
    return bound
