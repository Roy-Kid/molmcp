# molmcp-molpack

MCP plugin exposing molpack's read-only packing-script inspector.

Three tools (all read-only):

- `list_restraints` — enumerate the restraint types `molpack` exposes,
  with constructor signatures and one-line summaries.
- `list_formats` — enumerate the structure-file formats molpack can read
  and write, with extension hints.
- `inspect_script` — parse a Packmol-compatible `.inp` script via
  `molpack.load_script` and return a summary (targets, per-target atom
  counts, output path, `nloop`).

The provider never executes a pack run — packing mutates files and is
compute-heavy, so it stays out of the MCP surface. Use the `molpack`
CLI or Python API directly to actually pack.

## Run standalone

```bash
uv run --package molmcp-molpack molmcp-molpack --transport stdio
```

## Mount under a gateway

```python
from fastmcp import FastMCP
from molmcp_molpack.server import mcp as molpack_mcp

gateway = FastMCP("my-gateway")
gateway.mount(molpack_mcp, namespace="molpack")
```
