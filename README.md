<h1 align="center">molmcp</h1>

<p align="center">
  <strong>The MCP foundation for the MolCrafts ecosystem</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/molcrafts-molmcp/"><img alt="PyPI" src="https://img.shields.io/pypi/v/molcrafts-molmcp?logo=python&logoColor=white&label=PyPI"></a>
  <a href="https://pypi.org/project/molcrafts-molmcp/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/molcrafts-molmcp.svg"></a>
  <a href="https://github.com/MolCrafts/molmcp/blob/master/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-BSD--3--Clause-blue"></a>
  <a href="https://github.com/MolCrafts/molmcp/actions"><img alt="CI" src="https://github.com/MolCrafts/molmcp/actions/workflows/ci.yml/badge.svg"></a>
</p>

<p align="center">
  <a href="https://molcrafts.github.io/molmcp/"><strong>Documentation</strong></a>
  &middot;
  <a href="https://molcrafts.github.io/molmcp/get-started/quickstart/"><strong>Quickstart</strong></a>
  &middot;
  <a href="https://molcrafts.github.io/molmcp/guides/write-a-provider/"><strong>Write a Provider</strong></a>
  &middot;
  <a href="https://github.com/MolCrafts/molmcp/issues"><strong>Issues</strong></a>
</p>

---

## Why molmcp

The MolCrafts ecosystem ships many packages — `molpy`, `molcfg`, `molexp`, `molpack`, `mollog`, `molq`, `molrec`, `molvis` — and each of them benefits from being callable by an LLM agent. Without coordination, every package would have to author its own MCP server, redo the same source-introspection plumbing, redo the same security defaults, redo the same plugin wiring. molmcp is the layer that the MolCrafts packages share so they don't have to.

It does two things:

1. Exposes seven read-only **source-introspection tools** for any MolCrafts package, so an agent can ask "what does `molpy.core.atomistic` contain?" and get an exact answer from the live source.
2. Defines a **Provider** plugin contract so each MolCrafts package can ship its own *domain* tools (`molpack` exposes "pack a box", `molq` exposes "submit a job", `molexp` exposes "run an experiment") under a single coordinated MCP server with shared security defaults.

molmcp itself imports nothing from the MolCrafts packages. That's the point — it's pure infrastructure, and any MolCrafts package can adopt it without dragging in the others.

## Features

- **Seven introspection tools** — `list_modules`, `list_symbols`, `get_source`, `get_docstring`, `get_signature`, `read_file`, `search_source` — pointed at any MolCrafts import root.
- **Provider plugin contract** — MolCrafts packages contribute domain tools via a `Provider` class plus an entry point. Auto-discovered, namespaced, version-able.
- **Security middleware** that's on by default — path-traversal guard, response-size cap (256 KB), and a startup-time check that refuses to serve any tool missing a `readOnlyHint`/`destructiveHint` annotation.
- **`safe_subprocess` helper** — for MolCrafts packages that wrap external CLIs (Packmol, LAMMPS, AmberTools): forced list args, no `shell=True`, mandatory timeout.
- **Three transports** — `stdio`, `streamable-http`, `sse`.

## Install

```bash
pip install molcrafts-molmcp
```

Requires Python ≥ 3.10. The PyPI distribution is `molcrafts-molmcp`; the import name is `molmcp`.

## 60-second quickstart

Expose a MolCrafts package as a set of MCP introspection tools:

```bash
python -m molmcp --import-root molpy --name molpy
```

Wire it into Claude Code:

```bash
claude mcp add molpy -- python -m molmcp --import-root molpy --name molpy
```

The agent now has `mcp__molpy__list_modules`, `mcp__molpy__get_source`, etc.

## Adding domain tools (for MolCrafts packages)

```python
# in your MolCrafts package, e.g. src/molpack_mcp/__init__.py
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

class MolpackProvider:
    name = "molpack"

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
        def pack_box(spec: dict, workdir: str) -> dict:
            """Pack a simulation box from a MolCrafts pack spec."""
            from molpack import pack
            return pack(spec, workdir).to_dict()
```

Declare the entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."molmcp.providers"]
molpack = "molpack_mcp:MolpackProvider"
```

Now `python -m molmcp` discovers it automatically. The new tool joins the introspection tools in the same server.

## Architecture

```
                ┌────────────────────────────────────┐
                │  MCP clients                       │
                │  (Claude Code, Claude Desktop, …)  │
                └──────────────┬─────────────────────┘
                               │   stdio / http / sse
                               ▼
                ┌────────────────────────────────────┐
                │  molmcp                            │
                │  • Provider contract               │
                │  • IntrospectionProvider           │
                │  • PathSafety / ResponseLimit      │
                │  • run_safe / fence_untrusted      │
                └──────────────┬─────────────────────┘
                               │
       ┌───────────┬───────────┼───────────┬───────────┐
       ▼           ▼           ▼           ▼           ▼
   molpy_mcp   molpack_mcp  molexp_mcp  molq_mcp   mollog_mcp
   (domain)    (domain)     (domain)    (domain)   (domain)
```

molmcp itself is library code — no MolCrafts package depends on any other through molmcp. Each package writes its Provider against the molmcp contract and ships the entry point; the host process composes them at runtime.

## Documentation

Full documentation lives at **[molcrafts.github.io/molmcp](https://molcrafts.github.io/molmcp/)**:

- [Installation & quickstart](https://molcrafts.github.io/molmcp/get-started/installation/)
- [Architecture](https://molcrafts.github.io/molmcp/concepts/architecture/)
- [Writing a Provider](https://molcrafts.github.io/molmcp/guides/write-a-provider/)
- [Security model](https://molcrafts.github.io/molmcp/guides/security/)
- [CLI reference](https://molcrafts.github.io/molmcp/reference/cli/)

To preview the docs locally:

```bash
pip install "molcrafts-molmcp[docs]"
zensical serve
```

## Status

Alpha. The Provider contract and middleware surface may shift before 1.0. Pin to `molcrafts-molmcp >= 0.1, < 0.2`.

## Contributing

```bash
git clone https://github.com/MolCrafts/molmcp.git
cd molmcp
pip install -e ".[dev]"
pytest
```

## License

BSD-3-Clause. See [LICENSE](LICENSE).

## Acknowledgements

molmcp is part of the [MolCrafts](https://github.com/MolCrafts) project. It implements the [Model Context Protocol](https://modelcontextprotocol.io/) using the [fastmcp](https://github.com/jlowin/fastmcp) server library.
