"""Host adapter layer: where ``molmcp init`` writes on each known host.

A *host* is the AI client a user runs — a desktop app or a terminal agent.
:data:`HOSTS` names the ones molmcp knows, and each keeps its configuration in
its own directory under the user's home. ``molmcp init <host>`` fills that
tree with five kinds of file:

* the **MCP JSON** — MCP (Model Context Protocol) is the wire protocol an AI
  client uses to call tools, and this file is the client's list of servers to
  launch;
* the **usage skill**, also called the *constitution* — the managed
  ``SKILL.md`` that teaches the agent how to drive molmcp;
* the **adapter** ``molmcp-adapter.md`` — a short pointer file saying where
  the others live, carrying no skill, agent, or rule body of its own;
* the **daily bundle** — extra skills for ordinary use, copied in beside the
  usage skill;
* the **dev bundle** — the harness a molmcp contributor uses: full bodies
  under ``molmcp-dev/`` plus one-line slash-command stubs under ``commands/``.

The daily and dev bundles come from a *checkout*: a directory the caller
passes in explicitly. Nothing here goes looking for one.

This package re-exports the public surface of :mod:`molmcp.host.layout`, the
single host path table, and of :mod:`molmcp.host.install`, the write
primitives that fill it. It imports the standard library only, so
``client_config`` can read it without an import cycle.
"""

from .install import (
    ADAPTER_TEXT,
    activate_dev,
    install_skill,
    materialize_daily,
    materialize_dev_index,
    resolve_bundle_source,
    write_adapter,
)
from .layout import (
    HOSTS,
    SKILL_NAME,
    Host,
    HostLayout,
    default_skill_dir,
    default_write_path,
    layout_for,
)

__all__ = [
    "ADAPTER_TEXT",
    "HOSTS",
    "SKILL_NAME",
    "Host",
    "HostLayout",
    "activate_dev",
    "default_skill_dir",
    "default_write_path",
    "install_skill",
    "layout_for",
    "materialize_daily",
    "materialize_dev_index",
    "resolve_bundle_source",
    "write_adapter",
]
