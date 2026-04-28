# Write a Provider

You're maintaining a MolCrafts package. You want LLM agents to be able to *do* things with it, not just read its source. This guide walks through writing the Provider that ships with your package.

We'll use `molpack` as the running example, building a Provider that exposes one tool: `pack_box(spec, workdir)`. The same pattern applies to any MolCrafts package.

## Step 1 — Make molmcp an optional dep of your package

In `molpack/pyproject.toml`:

```toml
[project.optional-dependencies]
mcp = ["molmcp >= 0.1, < 0.2"]
```

Don't make molmcp a hard dependency — users who don't need MCP shouldn't pull in the server framework.

## Step 2 — Decide where the Provider lives

The MolCrafts convention is a sibling package named `<pkg>_mcp`:

```
molpack/                 # the main package, no MCP knowledge
└── src/molpack/...

molpack_mcp/             # sibling package, the Provider
└── src/molpack_mcp/__init__.py
```

This keeps the MCP integration out of your main package's import graph. Users who don't run MCP never touch `molpack_mcp`.

For small packages it's fine to keep `molpack_mcp/` inside the same repo as a separate `[project.optional-dependencies]` install target, or as a second package in a workspace.

## Step 3 — Write the Provider class

Create `molpack_mcp/__init__.py`:

```python
"""MCP Provider for molpack."""
from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import ToolAnnotations


class MolpackProvider:
    name = "molpack"

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
        def pack_box(spec: dict, workdir: str) -> dict:
            """Pack a simulation box from a MolCrafts pack spec.

            Args:
                spec: Pack spec — components, densities, constraints.
                workdir: Output directory for generated files.

            Returns:
                Dict with keys ``status``, ``output_files``, ``log``.
            """
            from molpack import pack  # lazy import — keeps cold start fast
            result = pack(spec, workdir)
            return {
                "status": result.status,
                "output_files": result.output_files,
                "log": result.log,
            }
```

A few things worth calling out:

- **Class-level `name = "molpack"`.** This is both the dedup key and the recommended mount prefix.
- **`ToolAnnotations(destructiveHint=True)`.** Required — molmcp will refuse to start the server otherwise. `pack_box` writes files, so it's destructive (read-only would be `readOnlyHint=True`).
- **Lazy import of the heavy module.** Don't `import molpack` at module top — when molmcp's auto-discovery instantiates your Provider, you want it cheap. Defer the heavy work to the tool body.
- **Plain-dict return.** Don't return Pydantic models from tool functions; some MCP clients serialize them as JSON-strings instead of dicts. Stick to primitives, lists, dicts.

## Step 4 — Register the entry point

In `molpack_mcp/pyproject.toml` (or `molpack/pyproject.toml` if you ship them together):

```toml
[project.entry-points."molmcp.providers"]
molpack = "molpack_mcp:MolpackProvider"
```

The key (`molpack` here) is just a label — molmcp doesn't use it. The value is the dotted path to your Provider class.

## Step 5 — Test it

```python
# tests/test_mcp.py
import pytest
from molmcp import create_server


@pytest.fixture
def server():
    from molpack_mcp import MolpackProvider
    return create_server(
        "test",
        providers=[MolpackProvider()],
        discover_entry_points=False,  # skip entry-point lookup in tests
    )


async def test_pack_box(server, tmp_path):
    result = await server.call_tool(
        "pack_box",
        {"spec": {...}, "workdir": str(tmp_path)},
    )
    text = result.content[0].text
    assert "status" in text
```

Run with `pytest tests/test_mcp.py -v`. molmcp's introspection tools are absent because we didn't pass `import_roots=["molpack"]` — only the Provider's tool is registered.

## Step 6 — Use it from an MCP client

The user installs your package and starts the server:

```bash
pip install molpack[mcp]
python -m molmcp --import-root molpack --name molpack
```

Auto-discovery finds the entry point, so `MolpackProvider` is registered. The agent now sees:

- The seven introspection tools (because `--import-root molpack` enabled `IntrospectionProvider`)
- `pack_box` from your Provider

To wire into Claude Code:

```bash
claude mcp add molpack -- python -m molmcp \
    --import-root molpack \
    --name molpack
```

## Patterns worth knowing

### Mounting tools under your package name as a prefix

If your Provider registers many tools and you want them all prefixed (so they don't collide with other MolCrafts Providers in the same server), mount a sub-server:

```python
def register(self, parent_mcp):
    sub = FastMCP("molpack")

    @sub.tool(annotations=ToolAnnotations(destructiveHint=True))
    def pack_box(...): ...

    @sub.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def estimate_density(...): ...

    parent_mcp.mount(sub, prefix=self.name)
```

Now both tools appear as `molpack_pack_box` and `molpack_estimate_density`. This is the recommended pattern when multiple MolCrafts Providers will be loaded together.

### Long-running tools

```python
@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def expensive_minimization(structure: dict) -> dict:
    """Run a 30-second optimization."""
    ...
```

Sync tool functions are run in a threadpool automatically by the underlying server, so you don't need to make them `async`. But if your tool calls *async* code internally, declare it `async def` and `await` properly — don't block the event loop.

### Writing destructive tools

```python
@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def write_pdb(structure: dict, path: str) -> str:
    """Write structure to a PDB file."""
    ...
```

`destructiveHint=True` tells the MCP client this tool mutates external state. Most clients will prompt the user before each call. If your tool both reads and writes, set `destructiveHint=True` (it dominates).

### Shelling out to external tools

If your MolCrafts Provider calls Packmol, LAMMPS, AmberTools, or any other external CLI, **do not** use `subprocess.run` directly. Use molmcp's `run_safe`:

```python
from molmcp import run_safe

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def run_packmol(input_file: str, workdir: str) -> dict:
    """Run packmol against an input file in workdir."""
    result = run_safe(
        ["packmol"],
        cwd=workdir,
        timeout=120.0,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
```

`run_safe` enforces list-form args (no shell injection), no `shell=True`, mandatory timeout, output truncation. See **[Security](security.md)** for the full story.

## Read next

- **[Security](security.md)** — `run_safe`, `fence_untrusted`, what to validate
- **[Middleware](../concepts/middleware.md)** — how molmcp's defaults wrap your tools
