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

from dataclasses import dataclass
from pathlib import Path
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
        commands: Directory of one-line stubs, one per dev slash command
            such as ``/mol:spec``; the bodies stay under *molmcp_dev*.
        agents: Host agents root. Recorded so this table stays the single
            truth; no function in ``molmcp.host`` writes there, so a user's
            own files are left alone.
        rules: Host rules root. Recorded so this table stays the single
            truth; no function in ``molmcp.host`` writes there, so a user's
            own files are left alone.
        molmcp_dev: Tree that holds the full dev harness bodies once
            :func:`~molmcp.host.activate_dev` has copied them in.
    """

    mcp_json: tuple[str, ...]
    skill_dir: tuple[str, ...]
    adapter: tuple[str, ...]
    commands: tuple[str, ...]
    agents: tuple[str, ...]
    rules: tuple[str, ...]
    molmcp_dev: tuple[str, ...]


HOSTS: dict[Host, HostLayout] = {
    "grok": HostLayout(
        mcp_json=(".mcp.json",),
        skill_dir=(".grok", "skills", SKILL_NAME),
        adapter=(".grok", "molmcp-adapter.md"),
        commands=(".grok", "commands"),
        agents=(".grok", "agents"),
        rules=(".grok", "rules"),
        molmcp_dev=(".grok", "molmcp-dev"),
    ),
    "claude": HostLayout(
        mcp_json=(".claude.json",),
        skill_dir=(".claude", "skills", SKILL_NAME),
        adapter=(".claude", "molmcp-adapter.md"),
        commands=(".claude", "commands"),
        agents=(".claude", "agents"),
        rules=(".claude", "rules"),
        molmcp_dev=(".claude", "molmcp-dev"),
    ),
    "cursor": HostLayout(
        mcp_json=(".cursor", "mcp.json"),
        skill_dir=(".cursor", "skills", SKILL_NAME),
        adapter=(".cursor", "molmcp-adapter.md"),
        commands=(".cursor", "commands"),
        agents=(".cursor", "agents"),
        rules=(".cursor", "rules"),
        molmcp_dev=(".cursor", "molmcp-dev"),
    ),
    "codex": HostLayout(
        mcp_json=(".codex", "mcp.json"),
        skill_dir=(".codex", "skills", SKILL_NAME),
        adapter=(".codex", "molmcp-adapter.md"),
        commands=(".codex", "commands"),
        agents=(".codex", "agents"),
        rules=(".codex", "rules"),
        molmcp_dev=(".codex", "molmcp-dev"),
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
