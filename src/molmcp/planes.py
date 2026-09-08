"""MCP planes — one product domain per MCP server process.

``molcrafts`` is the **core** connection: knowledge pages plus
``list_planes`` / ``route``. It is always on and cannot be disabled.
Provider planes (``molvis`` / ``molq`` / ``molexp`` / …) are optional
MCP links from the ``molmcp.providers`` entry-point group.

There is no catalog plane. Default ``molmcp serve`` is the molcrafts core
with enabled providers FastMCP-mounted (namespaced tools).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .provider import Provider, discover_providers

#: Always-on knowledge + routing connection. Not a disableable plane.
CORE_PLANE_ID = "molcrafts"

#: Built-in ids that are not entry-point providers.
BUILTIN_PLANE_IDS = frozenset({CORE_PLANE_ID})

#: Retired plane id. Kept out of catalogs; serving it fails loudly.
GONE_PLANE_IDS = frozenset({"catalog"})

#: Intent routing table for the ``route`` tool.
#: Patterns are lowercase substrings matched against the task string.
#: Only **provider** planes appear here — the core is already connected.
_ROUTE_HINTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        (
            "draw",
            "viewer",
            "canvas",
            "visualize",
            "smiles",
            "molecule",
            "structure",
            "3d",
            "stage",
            "画",
            "可视化",
            "分子",
            "结构",
            "展示",
            "渲染",
            "多巴胺",
            "阿司匹林",
        ),
        "molvis",
        "Live molecular viewer session (open → exec Python → poll_events).",
    ),
    (
        (
            "job",
            "queue",
            "slurm",
            "submit",
            "sbatch",
            "cluster",
            "scancel",
            "作业",
            "队列",
            "提交",
            "集群",
        ),
        "molq",
        "Job store and cluster submit/cancel (mutations opt-in).",
    ),
    (
        (
            "experiment",
            "workspace",
            "molexp",
            "project",
            "run scaffold",
            "adopt",
            "metrics",
            "实验",
            "工作区",
            "整理数据",
        ),
        "molexp",
        "Experiment workspace layout, scaffold, and legacy-directory adoption.",
    ),
)


def gone_plane_message(plane_id: str) -> str:
    """Loud error when a retired plane id is used."""
    if plane_id == "catalog":
        return (
            "catalog is not a plane; list_planes and route live on molcrafts. "
            "Use `molmcp serve molcrafts`."
        )
    return f"{plane_id!r} is not a plane"


def core_disable_message() -> str:
    """Loud error when the caller tries to disable the core connection."""
    return "molcrafts is the core connection and cannot be disabled"


@dataclass(frozen=True, slots=True)
class PlaneInfo:
    """Public description of one connectable MCP server."""

    id: str
    kind: str  # "core" | "provider"
    purpose: str
    when_to_connect: str
    serve_command: str
    requires_config: bool
    tools_hint: tuple[str, ...]
    disableable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "purpose": self.purpose,
            "when_to_connect": self.when_to_connect,
            "serve_command": self.serve_command,
            "requires_config": self.requires_config,
            "tools_hint": list(self.tools_hint),
            "disableable": self.disableable,
        }


def _molcrafts_info() -> PlaneInfo:
    return PlaneInfo(
        id=CORE_PLANE_ID,
        kind="core",
        purpose=(
            "Always-on knowledge pages (packages → outline → open → compose) "
            "plus list_planes / route for optional provider planes."
        ),
        when_to_connect=(
            "Core connection — always on. Discover real symbols before writing "
            "code. Never invent APIs; miss means SYMBOL_NOT_FOUND."
        ),
        serve_command="molmcp serve",
        requires_config=True,
        tools_hint=(
            "list_planes",
            "route",
            "info",
            "packages",
            "outline",
            "open",
            "compose",
            "search",
            "suggest",
        ),
        disableable=False,
    )


#: Product copy for the planes molmcp itself ships, keyed by plane id.
#: **Not a membership table** — a key here is only ever looked up for a name
#: :func:`discover_providers` already reported. Holding a row for a plane the
#: entry-point group never registered must not put it in a catalog, because
#: ``molmcp serve`` could not start it.
_PROVIDER_COPY: dict[str, tuple[str, str]] = {
    "molvis": (
        "Live molvis viewer: persistent Python namespace + browser canvas.",
        "User wants to draw, load, select, or interact with a molecule in 3D.",
    ),
    "molq": (
        "molq job lifecycle: list/get/logs destinations; opt-in submit/cancel.",
        "User wants cluster jobs, queue status, or submission.",
    ),
    "molexp": (
        "molexp workspace navigation, idempotent scaffold, and adoption of a "
        "legacy data directory (not a run driver).",
        "User works with experiment workspaces, projects, FAIR layout, or has "
        "a folder of results to lift into one.",
    ),
}


def _provider_copy(name: str) -> tuple[str, str]:
    """Return the ``(purpose, when_to_connect)`` sentences for a member plane.

    Args:
        name: Plane id, as the entry-point group reported it.

    Returns:
        The catalog's own product copy when ``name`` has a row, otherwise a
        generic pair naming the plane and the group it came from.
    """
    return _PROVIDER_COPY.get(
        name,
        (
            f"Provider plane '{name}' (entry point molmcp.providers).",
            f"When work needs the '{name}' product surface.",
        ),
    )


def _tools_hint(provider: Provider) -> tuple[str, ...]:
    """Return the tool names *provider* publishes about itself.

    Duck-typed exactly like ``probe`` is in ``provider_available``: the
    instance already answers this, so the catalog keeps no parallel tool
    list that could drift away from what ``register`` actually attaches.

    Args:
        provider: A discovered provider instance.

    Returns:
        The wire names from ``provider.tool_specs()``, or an empty tuple
        when the instance does not publish specs (the Protocol minimum).
    """
    specs_fn = getattr(provider, "tool_specs", None)
    if not callable(specs_fn):
        return ()
    return tuple(spec.name for spec in specs_fn())


def list_plane_infos(*, include_unavailable_providers: bool = False) -> list[PlaneInfo]:
    """Return the core connection plus the provider planes the group reports.

    Membership has exactly one authority: the ``molmcp.providers``
    entry-point group, read through :func:`discover_providers`. By default
    only providers whose optional upstream package is installed appear
    (**silent omit** of missing science deps — not a test skip).

    Args:
        include_unavailable_providers: Widen discovery to providers whose
            ``probe()`` is false, for diagnostics. It widens availability
            only — a name the group never registered is still never listed.

    Returns:
        The core plane first, then one row per discovered provider, by id.
    """
    planes: list[PlaneInfo] = [_molcrafts_info()]
    discovered = discover_providers(only_available=not include_unavailable_providers)
    for provider in sorted(discovered, key=lambda member: member.name):
        purpose, when = _provider_copy(provider.name)
        planes.append(
            PlaneInfo(
                id=provider.name,
                kind="provider",
                purpose=purpose,
                when_to_connect=when,
                serve_command=f"molmcp serve {provider.name}",
                requires_config=False,
                tools_hint=_tools_hint(provider),
                disableable=True,
            )
        )
    return planes


def known_plane_ids(*, only_available: bool = False) -> frozenset[str]:
    """Ids that ``molmcp serve <id>`` may accept.

    Explicit serve still accepts discovered providers even when
    ``probe()`` is false (user gets a clear install error from
    ``register``). Catalogs use *only_available*.
    """
    provider_names = {p.name for p in discover_providers(only_available=only_available)}
    return frozenset(BUILTIN_PLANE_IDS | provider_names)


def route_task(task: str) -> dict[str, Any]:
    """Map a free-text task to optional provider planes to connect.

    ``molcrafts`` is the core and is never returned as a plane to add.
    Returns a structured routing answer — never executes science.
    """
    text = task.strip().lower()
    matched: list[dict[str, str]] = []
    seen: set[str] = set()
    for keywords, plane_id, reason in _ROUTE_HINTS:
        if any(k in text for k in keywords) and plane_id not in seen:
            seen.add(plane_id)
            matched.append({"plane": plane_id, "reason": reason})
    return {
        "ok": True,
        "task": task,
        "core": CORE_PLANE_ID,
        "planes": matched,
        "namespaces": [m["plane"] for m in matched],
        "serve_commands": ["molmcp serve"],
        "client_hint": (
            "Default `molmcp serve` already mounts these providers onto "
            "molcrafts (molvis_open, molq_list_jobs, …). Omit a mount with "
            "`molmcp init grok --disable …`. Science APIs are never MCP "
            "tools — discover them on molcrafts, then call them in agent "
            "Python or molvis_exec."
        ),
    }


__all__ = [
    "BUILTIN_PLANE_IDS",
    "CORE_PLANE_ID",
    "GONE_PLANE_IDS",
    "PlaneInfo",
    "core_disable_message",
    "gone_plane_message",
    "known_plane_ids",
    "list_plane_infos",
    "route_task",
]
