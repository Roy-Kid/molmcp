# Deploy locally (stdio)

Local **stdio** MCP: the host spawns **one** `molmcp serve` subprocess.
Providers are FastMCP-mounted onto the molcrafts core.

---

## What molmcp serves

| Command | What the agent sees |
|---------|---------------------|
| **`molmcp serve`** | Core: `list_planes`, `route`, `packages`, `outline`, `open`, … plus namespaced mounts `molvis_open`, `molq_list_jobs`, `molexp_list_projects`, … |
| **`molmcp serve molvis`** (debug) | Vis-only process, bare `open` / `exec` |

`molcrafts` cannot be disabled. `molmcp init grok --disable molq` omits that
mount. Tool ids on the composed server are `molcrafts__packages` and
`molcrafts__molvis_open`.

## Prerequisites

- **Python ≥ 3.12**
  ```bash
  pip install molcrafts-molmcp
  ```
- For the **molcrafts** knowledge plane, at least one configured source (see
  [Installation](installation.md)).
- Optional domain packages for provider planes:
  ```bash
  pip install molcrafts-molpy    # richer local graph + science in-agent
  pip install molcrafts-molq     # molq plane
  pip install molcrafts-molexp   # molexp plane
  # molvis plane: page host + molvis Python/bindings per that package’s docs
  ```

!!! tip "Use a venv"

    Clients spawn the server with whatever `python` / `molmcp` is on `PATH`.
    A dedicated venv keeps the tree predictable:

    ```bash
    uv venv && source .venv/bin/activate
    uv pip install molcrafts-molmcp molcrafts-molpy
    ```

## Multi-link client config

### Claude Code

```bash
claude mcp add molcrafts -- molmcp serve molcrafts
claude mcp add molvis -- molmcp serve molvis   # optional
claude mcp list
```

### Generic JSON

```json
{
  "mcpServers": {
    "molcrafts": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/molmcp", "molmcp", "serve", "molcrafts"]
    }
  }
}
```

## Recommended agent loop

1. `molcrafts.route("…")` → which optional provider planes to connect.
2. `molcrafts.packages` / `outline` / `open` → real APIs into context.
3. Call science from agent Python (or `molvis.exec` for a live canvas).
4. Never invent MCP tools that re-export molpy/molrs methods.

Full viewer dialogue: [MolVis workbench](../guides/molvis-workbench.md).

## HTTP (optional)

```bash
molmcp serve molcrafts --transport streamable-http --host 127.0.0.1 --port 8787
```

Non-loopback binds require `server.auth_token_env` in an explicit `--config` file. Prefer
stdio for local agents.

## Offline helpers

```bash
molmcp planes
molmcp route "submit a job"
molmcp search "Conformer"
molmcp index    # when configured
```

## Read next

- [Quickstart](quickstart.md)
- [Architecture](../concepts/architecture.md)
- [Provider design](../concepts/provider-design.md)
- [Security](../guides/security.md)
