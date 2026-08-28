# Architecture

molmcp is FastMCP-composed MCP infrastructure for MolCrafts. **`molmcp serve`**
starts the **molcrafts core** (knowledge pages plus `list_planes` / `route`)
and **mounts** enabled providers into that process with official namespaces
(`molvis_open`). `molmcp init <host> --disable molq` omits a mount. Providers
can still be served alone for debugging (`molmcp serve molvis`).

**Protocol alignment:** servers run on **FastMCP 4 / MCP SDK v2**, which speak
MCP **2026-07-28** (sessionless `server/discover`) while still serving older
handshake-era clients.

```
  MCP clients (Claude, Grok, …)
       │
       │  one stdio: molmcp serve
       │
       └── molcrafts
              packages / open / route
              molvis_open / molvis_exec / …
              molq_list_jobs / …
              molexp_list_projects / …
```

There is **no** parent server that mounts every provider under `molmcp`, and
**no catalog plane** — routing lives on molcrafts.

## Responsibilities

### 1. Plane runtime

`create_plane(plane_id)` builds one FastMCP server whose **name is the plane
id**. Tool names are **bare** (`open`, `list_projects`). Clients see
`molcrafts__molvis_open` on the composed server. A focused
`molmcp serve molexp` process still uses bare `list_projects`. Startup
**rejects** registering `molexp_list_projects` on a server named `molexp`.

### 2. Knowledge core (`molcrafts`)

Always on. OKF-style pages over the discovery graph: packages → outline →
open → compose, plus `list_planes` / `route` for optional provider planes.
Codegraph ranks are evidence only. Science methods are discovered here and
invoked elsewhere. `--disable molcrafts` is an error.

### 3. Provider planes

`Provider` protocol + `molmcp.providers` entry points. Each provider is its own
plane and **can be disabled**. Four-condition tool rule still applies (stable
signature, read-only default, high frequency, single-shot). No upstream API
mirror.

## Request flow (example: draw a molecule)

1. `molcrafts.route("draw dopamine")` → connect `molvis`.
2. `molcrafts.search` / `open` → real molpy/molvis symbols.
3. `molvis.open` → browser session.
4. `molvis.exec` → agent-written Python (`parse_molecule`, `draw_frame`, …).

## What molmcp does not do

- Mega-server with every tool mounted.
- Hard-coded chemistry tools (`show_smiles`, `optimize`, …).
- Science-package imports outside provider planes.

## Read next

- [Provider design](provider-design.md)
- [Discovery engine](discovery.md)
- [MolVis workbench](../guides/molvis-workbench.md)
