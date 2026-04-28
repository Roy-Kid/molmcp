"""Provider protocol — the contract for downstream MCP plugins."""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Protocol, runtime_checkable

from fastmcp import FastMCP

ENTRY_POINT_GROUP = "molmcp.providers"

logger = logging.getLogger(__name__)


@runtime_checkable
class Provider(Protocol):
    """A unit of MCP functionality that can be registered onto a FastMCP server.

    Implementations must expose:

    * ``name`` — short identifier used as the mount prefix (e.g. ``"molpy"``).
      Tools registered by the provider become ``<name>__<tool>`` to avoid
      collisions across providers.
    * ``register(mcp)`` — called once at server-build time. The provider
      should attach tools, resources, and prompts to ``mcp``.

    Providers SHOULD set ``ToolAnnotations`` (at minimum ``readOnlyHint``)
    on every tool. The default :class:`AnnotationsValidator` middleware
    will reject the server at startup otherwise.
    """

    name: str

    def register(self, mcp: FastMCP) -> None: ...


def discover_providers() -> list[Provider]:
    """Enumerate Provider instances declared via the ``molmcp.providers`` entry point.

    Each entry point must resolve to a class; the class is instantiated with
    no arguments. Providers raising during instantiation are logged and skipped.
    """
    discovered: list[Provider] = []
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        eps = importlib.metadata.entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]

    for ep in eps:
        try:
            cls = ep.load()
            instance = cls()
        except Exception as e:
            logger.warning("Failed to load Provider %r: %s", ep.name, e)
            continue
        if not isinstance(instance, Provider):
            logger.warning(
                "Entry point %r resolved to %r which does not implement Provider",
                ep.name,
                type(instance).__name__,
            )
            continue
        discovered.append(instance)
    return discovered
