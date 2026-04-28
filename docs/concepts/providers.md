# Providers

A **Provider** is the unit of MCP functionality contributed by a MolCrafts package. Each package — `molpy`, `molpack`, `molexp`, `molq`, ... — that wants to expose tools over MCP ships a Provider class plus an entry point. molmcp discovers them at startup and mounts them onto a single server.

## The contract

```python
from typing import Protocol
from fastmcp import FastMCP

class Provider(Protocol):
    name: str
    def register(self, mcp: FastMCP) -> None: ...
```

That's the whole interface. Two requirements:

1. A class-level `name` attribute — used as the [mount prefix](#namespacing) and the dedup key. Use the MolCrafts package name (`molpy`, `molpack`, ...).
2. A `register(mcp)` method — called once at server-build time. The Provider attaches tools, resources, and prompts to `mcp`.

molmcp uses `typing.Protocol` with `@runtime_checkable`, so you do **not** need to inherit from `Provider` — any class with a matching shape qualifies.

## Two ways to register a Provider

### 1. Explicit injection

The host calls `create_server(...)` and passes Provider instances directly:

```python
from molmcp import create_server
from molpack_mcp import MolpackProvider

server = create_server(
    "molcrafts",
    providers=[MolpackProvider()],
    discover_entry_points=False,
)
```

### 2. Entry-point auto-discovery

The MolCrafts package declares its Provider in `pyproject.toml`:

```toml
[project.entry-points."molmcp.providers"]
molpack = "molpack_mcp:MolpackProvider"
```

When the host runs `python -m molmcp` (without `--no-discover`), molmcp enumerates the `molmcp.providers` group via `importlib.metadata.entry_points()` and instantiates each registered class with no arguments.

## Namespacing

Each Provider's tools are *not* automatically prefixed with `name`. If two MolCrafts packages register tools with the same simple name, they collide. To avoid that, mount a sub-server inside `register`:

```python
class MolpackProvider:
    name = "molpack"

    def register(self, parent_mcp):
        sub = FastMCP("molpack-sub")

        @sub.tool(annotations=ToolAnnotations(destructiveHint=True))
        def pack_box(spec: dict, workdir: str) -> dict:
            """Pack a simulation box from a MolCrafts pack spec."""
            from molpack import pack
            return pack(spec, workdir).to_dict()

        parent_mcp.mount(sub, prefix=self.name)
```

The tool now appears as `molpack_pack_box`. Without `mount`, it's just `pack_box` — fine if you only have one Provider, fragile across the ecosystem. molmcp's recommended convention: every MolCrafts Provider mounts under its package name.

molmcp's *Provider*-level dedup is name-based: a Provider whose `name` matches an already-registered Provider is logged and skipped. So if `molpack_mcp` is already loaded and another package also names itself `molpack`, the second is dropped.

## Annotation requirement

Every tool a Provider registers **must** declare either `readOnlyHint` or `destructiveHint` via `ToolAnnotations`:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_atom_count(filename: str) -> int:
    """Count atoms in a structure file."""
    ...

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def write_pdb(structure: dict, path: str) -> None:
    """Write structure to a PDB file (overwrites existing)."""
    ...
```

molmcp's [annotations validator](middleware.md#annotations-validator) walks every registered tool at server build time and refuses to start the server if any tool is missing this. Why so strict? MCP clients use these hints to decide whether to auto-approve a tool call — without them, the user gets prompted for every call (annoying) or the tool gets auto-approved (dangerous). Either choice degrades the UX for the whole MolCrafts ecosystem.

You can disable the check with `validate_annotations=False`, but don't.

## Discovery hygiene

Auto-discovery is a security boundary: any package the user has installed *and* that declares the entry point can register itself. molmcp:

- Logs every discovered Provider at startup so the user can see what's loaded.
- Skips Providers that fail to instantiate, with a warning instead of crashing.
- Skips Providers whose loaded class doesn't satisfy the `Provider` protocol.
- Honors `--no-discover` on the CLI to bypass discovery entirely.

For locked-down environments, prefer explicit `providers=[...]` injection.

## MolCrafts Provider naming convention

By convention, each MolCrafts package's Provider lives in a sibling package named `<pkg>_mcp`:

| MolCrafts package | Provider package | Provider class |
|-------------------|------------------|----------------|
| `molpy` | `molpy_mcp` | `MolPyProvider` |
| `molpack` | `molpack_mcp` | `MolpackProvider` |
| `molexp` | `molexp_mcp` | `MolexpProvider` |
| `molq` | `molq_mcp` | `MolqProvider` |
| `mollog` | `mollog_mcp` | `MollogProvider` |

The sibling-package layout keeps the MCP integration optional: `pip install molpy` does not pull in molmcp; `pip install molpy[mcp]` does.

## Read next

- **[Middleware](middleware.md)** — what wraps your Provider's tools
- **[Write a Provider](../guides/write-a-provider.md)** — step-by-step tutorial
