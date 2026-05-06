# molmcp

**The MCP foundation for the MolCrafts ecosystem.**

molmcp is the Model Context Protocol layer that MolCrafts packages share. Instead of every package — `molpy`, `molcfg`, `molexp`, `molpack`, `mollog`, `molq`, `molrec`, `molvis` — authoring its own MCP server, they all build on molmcp: same source-introspection tools, same security defaults, same Provider plugin contract. molmcp itself is pure infrastructure — it imports nothing from MolCrafts packages, so any of them can adopt it without dragging in the others.

## What molmcp gives the MolCrafts ecosystem

<div class="grid cards" markdown>

- :material-magnify: **Source introspection**

    Seven read-only tools — `list_modules`, `list_symbols`, `get_source`, `get_docstring`, `get_signature`, `read_file`, `search_source` — bound to any MolCrafts import root.

    [→ Quickstart](get-started/quickstart.md)

- :material-puzzle: **Provider plugin contract**

    Each MolCrafts package contributes domain tools via a `Provider` class plus an entry point. The host server discovers them at startup, mounts them under a namespace, and validates their tool annotations.

    [→ Writing a Provider](guides/write-a-provider.md)

- :material-shield-check: **Security defaults**

    `..` traversal blocked. Responses capped at 256 KB. Tools without `readOnlyHint`/`destructiveHint` refuse to start. `safe_subprocess` for shelling out to Packmol / LAMMPS / AmberTools.

    [→ Security model](guides/security.md)

- :material-layers: **Composition without coupling**

    Mount many Providers in one server with `mcp.mount(prefix=...)`. molmcp itself depends on no MolCrafts package — they each adopt molmcp on their own schedule.

    [→ Architecture](concepts/architecture.md)

</div>

## A quick taste

```bash
pip install molcrafts-molmcp
python -m molmcp --import-root molpy --name molpy
```

That's enough — seven tools online over MCP stdio, ready to wire into Claude Code or any MCP client. For the one-line `claude mcp add` recipe and the curated plugin servers (`molmcp-molpy`, `molmcp-molrs`, `molmcp-molpack`), see [Deploy](get-started/deploy.md).

When `molpack`, `molq`, or any other MolCrafts package wants to expose its own domain tools (pack a box, submit a job, run an experiment), it ships a Provider. See [Writing a Provider](guides/write-a-provider.md).

## Why this exists

When LLM agents work on a MolCrafts project they need exact, current API knowledge — what's in `molpy.core.atomistic`, what `molpack.pack` accepts, what `molexp.Experiment` returns. Re-implementing source introspection per package is wasted work; the code is identical regardless of which MolCrafts package it points at. molmcp factors out the common layer, with security defaults that no one wants to maintain in N copies, so MolCrafts packages can focus on the *interesting* part: exposing the simulations, the parsers, the I/O — the things only they can do.

[Get started →](get-started/installation.md){ .md-button .md-button--primary }
[See the architecture →](concepts/architecture.md){ .md-button }
