# molmcp-molrs

MCP plugin exposing molrs's read-only catalogs and structure-file inspector.

Five tools (all read-only):

- `list_compute_ops` — enumerate molrs's trajectory-analysis operators
  (`RDF`, `MSD`, `Cluster`, `CenterOfMass`, `GyrationTensor`,
  `InertiaTensor`, `RadiusOfGyration`, `Pca2`, `KMeans`) with
  constructor signatures and one-line summaries.
- `list_neighbor_algos` — enumerate the spatial-neighbor primitives
  (`NeighborQuery`, `LinkedCell`, `NeighborList`).
- `list_readers` — enumerate molrs file readers (`read_xyz`,
  `read_pdb`, `read_lammps`, `read_lammps_traj`,
  `read_xyz_trajectory`, `read_chgcar_file`, `read_cube_file`,
  `parse_smiles`).
- `list_writers` — enumerate molrs file writers (`write_lammps`,
  `write_cube_file`).
- `inspect_structure` — open a single-frame structure file via
  `molrs.io` and return a summary (format, block names, atom/bond
  counts, simbox).

The provider never runs a compute pipeline — RDF/MSD/Cluster/etc.
are compute-heavy and return large arrays. Use the `molrs` Python
API directly (`import molrs`) when you actually want to compute.

## Run standalone

```bash
uv run --package molmcp-molrs molmcp-molrs --transport stdio
```

## Mount under a gateway

```python
from fastmcp import FastMCP
from molmcp_molrs.server import mcp as molrs_mcp

gateway = FastMCP("my-gateway")
gateway.mount(molrs_mcp, namespace="molrs")
```
