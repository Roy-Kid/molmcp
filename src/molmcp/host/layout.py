"""The single host path table and its thin readers.

A *host* is the AI client ``molmcp init`` wires — a desktop app or a terminal
agent; :mod:`molmcp.host` introduces what gets written into it. One record per
host says where every file ``molmcp init`` may write belongs, so no second
table can drift from it. Values are path tuples relative to ``Path.home()``;
home is resolved only when a caller asks for a concrete
:class:`~pathlib.Path`, and no environment variable selects a destination.

This module is Layer 2 and imports the standard library only. Nothing under
``molmcp.host`` may import ``molmcp.client_config``, ``molmcp.cli``,
``molmcp.server``, ``molmcp.providers``, or ``molmcp.discovery``:
``client_config`` reads this table, not the other way round.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

Host = Literal["grok", "claude", "cursor", "codex"]
"""The hosts ``molmcp init`` knows how to wire."""

SKILL_NAME = "molcrafts"
"""Directory name of the managed usage skill inside a host's ``skills/``."""


@dataclass(frozen=True, slots=True)
class HostLayout:
    """Where one host keeps every file ``molmcp init`` may write.

    Each field is a path tuple relative to ``Path.home()``, which keeps a
    record immutable, hashable, and independent of the home directory in
    force when it is read.

    Attributes:
        mcp_json: The host's MCP (Model Context Protocol) client config —
            the list of servers it launches, and the only JSON
            ``molmcp init`` writes.
        skill_dir: Directory holding the managed usage constitution
            ``SKILL.md``. Its last part is always :data:`SKILL_NAME`.
        adapter: Stable pointer file ``molmcp-adapter.md``.
        agents: Host agents root. Only
            :func:`~molmcp.host.place.place_components` writes there, and
            only the ``agent`` components a catalog declares by name, so a
            user's own files are left alone.
        rules: Host rules root. Written on the same terms as *agents*, for
            ``rule`` components.
    """

    mcp_json: tuple[str, ...]
    skill_dir: tuple[str, ...]
    adapter: tuple[str, ...]
    agents: tuple[str, ...]
    rules: tuple[str, ...]


HOSTS: dict[Host, HostLayout] = {
    "grok": HostLayout(
        mcp_json=(".mcp.json",),
        skill_dir=(".grok", "skills", SKILL_NAME),
        adapter=(".grok", "molmcp-adapter.md"),
        agents=(".grok", "agents"),
        rules=(".grok", "rules"),
    ),
    "claude": HostLayout(
        mcp_json=(".claude.json",),
        skill_dir=(".claude", "skills", SKILL_NAME),
        adapter=(".claude", "molmcp-adapter.md"),
        agents=(".claude", "agents"),
        rules=(".claude", "rules"),
    ),
    "cursor": HostLayout(
        mcp_json=(".cursor", "mcp.json"),
        skill_dir=(".cursor", "skills", SKILL_NAME),
        adapter=(".cursor", "molmcp-adapter.md"),
        agents=(".cursor", "agents"),
        rules=(".cursor", "rules"),
    ),
    "codex": HostLayout(
        mcp_json=(".codex", "mcp.json"),
        skill_dir=(".codex", "skills", SKILL_NAME),
        adapter=(".codex", "molmcp-adapter.md"),
        agents=(".codex", "agents"),
        rules=(".codex", "rules"),
    ),
}
"""Layout per host, in the order ``molmcp init`` offers as ``--help`` choices."""


def layout_for(host: Host) -> HostLayout:
    """Return the layout record for *host*.

    Args:
        host: One of the keys of :data:`HOSTS`.

    Returns:
        The immutable :class:`HostLayout` describing that host's paths.

    Raises:
        ValueError: If *host* is not a known host.
    """
    if host not in HOSTS:
        known = ", ".join(sorted(HOSTS))
        raise ValueError(f"unknown host {host!r}; known: {known}")
    return HOSTS[host]


def default_write_path(host: Host) -> Path:
    """Conventional destination for *host*'s MCP config.

    Args:
        host: One of the keys of :data:`HOSTS`.

    Returns:
        ``Path.home()`` joined with the record's ``mcp_json`` parts.

    Raises:
        ValueError: If *host* is not a known host.
    """
    return Path.home().joinpath(*layout_for(host).mcp_json)


def default_skill_dir(host: Host) -> Path:
    """User-level skill directory for *host* (``SKILL.md`` lives inside).

    Args:
        host: One of the keys of :data:`HOSTS`.

    Returns:
        ``Path.home()`` joined with the record's ``skill_dir`` parts.

    Raises:
        ValueError: If *host* is not a known host.
    """
    return Path.home().joinpath(*layout_for(host).skill_dir)


_KEY_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]*):(.*)$")

_FRONTMATTER_MAPS: Mapping[Host, Mapping[str, str]] = MappingProxyType(
    {
        "grok": MappingProxyType(
            {
                "name": "name",
                "description": "description",
                "when-to-use": "when-to-use",
                "user-invocable": "user-invocable",
                "disable-model-invocation": "disable-model-invocation",
                "argument-hint": "argument-hint",
            }
        ),
        "claude": MappingProxyType(
            {
                "name": "name",
                "description": "description",
                "user-invocable": "user-invocable",
                "disable-model-invocation": "disable-model-invocation",
                "argument-hint": "argument-hint",
            }
        ),
        "cursor": MappingProxyType(
            {
                "name": "name",
                "description": "description",
                "disable-model-invocation": "disable-model-invocation",
            }
        ),
        "codex": MappingProxyType(
            {
                "name": "name",
                "description": "description",
            }
        ),
    }
)


def remap_frontmatter(text: str, host: Host) -> str:
    """Rewrite top-level YAML keys for *host*; drop unmapped keys.

    Line-oriented: values are not parsed. A document without a closed
    ``---`` fence is returned unchanged. Output uses ``\\n`` newlines.

    Args:
        text: File contents, typically a SKILL.md / agent / rule body.
        host: Destination host; validated via :func:`layout_for`.

    Returns:
        Remapped text, or *text* when there is no closed fence.

    Raises:
        ValueError: If *host* is not a known host.
    """
    layout_for(host)
    mapping = _FRONTMATTER_MAPS[host]
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return text
    close: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            close = index
            break
    if close is None:
        return text
    blocks: list[tuple[str, list[str]]] = []
    current_key: str | None = None
    current_lines: list[str] = []
    for line in lines[1:close]:
        match = None
        if not line.startswith((" ", "\t")):
            match = _KEY_LINE.match(line)
        if match is not None:
            if current_key is not None:
                blocks.append((current_key, current_lines))
            current_key = match.group(1)
            current_lines = [line]
            continue
        if current_key is None:
            continue
        current_lines.append(line)
    if current_key is not None:
        blocks.append((current_key, current_lines))
    out = ["---"]
    for key, block in blocks:
        dest = mapping.get(key)
        if dest is None:
            continue
        first = block[0]
        rest = first.split(":", 1)[1]
        out.append(f"{dest}:{rest}")
        out.extend(block[1:])
    out.append("---")
    out.extend(lines[close + 1 :])
    return "\n".join(out) + "\n"
