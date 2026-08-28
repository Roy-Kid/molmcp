"""Generate host MCP client configs — core always on, providers togglable."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .planes import (
    CORE_PLANE_ID,
    GONE_PLANE_IDS,
    core_disable_message,
    gone_plane_message,
    list_plane_infos,
)

Host = Literal["grok", "claude", "cursor", "codex"]

SKILL_NAME = "molcrafts"


@dataclass(frozen=True, slots=True)
class PlaneToggle:
    """Which planes are enabled for a client config."""

    enabled: tuple[str, ...]
    disabled: tuple[str, ...]
    all_planes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": list(self.enabled),
            "disabled": list(self.disabled),
            "all_planes": list(self.all_planes),
        }


def default_plane_ids() -> tuple[str, ...]:
    """Core plus provider planes with installed deps.

    Optional science packages that are not installed are omitted silently —
    no pytest-style skip; they simply never appear in client configs.
    ``molcrafts`` is always first.
    """
    infos = list_plane_infos(include_unavailable_providers=False)
    ids = [p.id for p in infos]
    tail = sorted(x for x in ids if x != CORE_PLANE_ID)
    return (CORE_PLANE_ID, *tail)


def _ensure_core(planes: tuple[str, ...]) -> tuple[str, ...]:
    if CORE_PLANE_ID in planes:
        tail = tuple(p for p in planes if p != CORE_PLANE_ID)
        return (CORE_PLANE_ID, *tail)
    return (CORE_PLANE_ID, *planes)


def resolve_plane_toggles(
    *,
    enable: list[str] | tuple[str, ...] = (),
    disable: list[str] | tuple[str, ...] = (),
    available: tuple[str, ...] | None = None,
) -> PlaneToggle:
    """Default: core + every provider on. Apply ``--disable`` then ``--enable``.

    ``molcrafts`` cannot be disabled. Retired ids such as ``catalog`` error.

    Raises:
        ValueError: unknown plane id, retired plane, or attempt to disable core.
    """
    all_planes = _ensure_core(
        available if available is not None else default_plane_ids()
    )
    known = set(all_planes)
    enabled = set(all_planes)

    def _norm(name: str) -> str:
        return name.strip().lower()

    def _check(plane: str) -> None:
        if plane in GONE_PLANE_IDS:
            raise ValueError(gone_plane_message(plane))
        if plane == CORE_PLANE_ID:
            return
        if plane not in known:
            raise ValueError(f"unknown plane {plane!r}; known: {', '.join(all_planes)}")

    for raw in disable:
        plane = _norm(raw)
        _check(plane)
        if plane == CORE_PLANE_ID:
            raise ValueError(core_disable_message())
        enabled.discard(plane)

    for raw in enable:
        plane = _norm(raw)
        _check(plane)
        enabled.add(plane)

    enabled.add(CORE_PLANE_ID)
    ordered = tuple(p for p in all_planes if p in enabled)
    disabled = tuple(p for p in all_planes if p not in enabled)
    return PlaneToggle(enabled=ordered, disabled=disabled, all_planes=all_planes)


def _molmcp_command() -> list[str]:
    """The command a *client* can launch, as an absolute path.

    Emitting the bare name assumed the client would resolve it on PATH.
    Desktop MCP hosts are started by the desktop session, whose PATH is the
    system default, so a virtualenv's bin directory is not on it — the
    config worked in the terminal that generated it and nowhere else.

    The fallback runs this very interpreter rather than ``python``, which on
    macOS frequently does not exist at all.

    Symlinks are deliberately left alone: ``molmcp`` is often a shim, and
    resolving through it would pin a path the installer may replace.
    """
    resolved = shutil.which("molmcp")
    if resolved:
        return [os.path.abspath(resolved)]
    return [sys.executable, "-m", "molmcp"]


def serve_argv(plane: str | None = None, *, disable: tuple[str, ...] = ()) -> list[str]:
    """Argv for one host spawn.

    ``plane is None`` is the composed stack (``molmcp serve``). Provider
    disables are forwarded as ``--disable`` so the child process omits those
    FastMCP mounts. A named *plane* is the single-plane debug server.
    """
    parts = [*_molmcp_command(), "serve"]
    if plane is not None:
        parts.append(plane)
        return parts
    for name in disable:
        parts.extend(["--disable", name])
    return parts


def render_mcp_json(toggle: PlaneToggle) -> dict[str, Any]:
    """One ``mcpServers`` entry: composed ``molmcp serve``.

    Disabled providers become ``--disable`` flags on that command. Every host
    molmcp targets reads this JSON shape.
    """
    cmd = serve_argv(disable=toggle.disabled)
    return {
        "mcpServers": {
            CORE_PLANE_ID: {"command": cmd[0], "args": cmd[1:]},
        }
    }


def render_init(
    host: Host | None = None,
    *,
    enable: list[str] | tuple[str, ...] = (),
    disable: list[str] | tuple[str, ...] = (),
    available: tuple[str, ...] | None = None,
) -> tuple[PlaneToggle, str]:
    """Return ``(toggle, config text)``.

    ``host`` selects only where the result is meant to go; the body is the
    same JSON for all of them.
    """
    if host is not None and host not in _HOST_PATHS:
        raise ValueError(
            f"unknown host {host!r}; known: {', '.join(sorted(_HOST_PATHS))}"
        )
    toggle = resolve_plane_toggles(enable=enable, disable=disable, available=available)
    return toggle, json.dumps(render_mcp_json(toggle), indent=2) + "\n"


#: Where each host expects to find the JSON, relative to home unless noted.
_HOST_PATHS: dict[str, tuple[str, ...]] = {
    "claude": (".claude.json",),
    "cursor": (".cursor", "mcp.json"),
    "grok": (".mcp.json",),
    "codex": (".codex", "mcp.json"),
}

#: User-level skill directory (under home) for the usage constitution.
_HOST_SKILL_DIRS: dict[str, tuple[str, ...]] = {
    "claude": (".claude", "skills", SKILL_NAME),
    "cursor": (".cursor", "skills", SKILL_NAME),
    "grok": (".grok", "skills", SKILL_NAME),
    "codex": (".codex", "skills", SKILL_NAME),
}


def default_write_path(host: Host) -> Path:
    """Conventional destination for *host*'s MCP config."""
    if host not in _HOST_PATHS:
        raise ValueError(
            f"unknown host {host!r}; known: {', '.join(sorted(_HOST_PATHS))}"
        )
    return Path.home().joinpath(*_HOST_PATHS[host])


def default_skill_dir(host: Host) -> Path:
    """User-level skill directory for *host* (``SKILL.md`` lives inside)."""
    if host not in _HOST_SKILL_DIRS:
        raise ValueError(
            f"unknown host {host!r}; known: {', '.join(sorted(_HOST_SKILL_DIRS))}"
        )
    return Path.home().joinpath(*_HOST_SKILL_DIRS[host])


def skill_template() -> str:
    """Usage constitution shipped with this molmcp version."""
    from importlib.resources import files

    return (files("molmcp.skill") / "SKILL.md").read_text(encoding="utf-8")


def install_skill(host: Host) -> Path:
    """Overwrite the managed usage skill for *host*. Only ``molmcp init`` calls this."""
    dest_dir = default_skill_dir(host)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "SKILL.md"
    dest.write_text(skill_template(), encoding="utf-8")
    return dest


__all__ = [
    "Host",
    "PlaneToggle",
    "SKILL_NAME",
    "default_plane_ids",
    "default_skill_dir",
    "default_write_path",
    "install_skill",
    "render_init",
    "render_mcp_json",
    "resolve_plane_toggles",
    "serve_argv",
    "skill_template",
]
