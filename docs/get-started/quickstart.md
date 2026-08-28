# Quickstart

Stand up MolCrafts MCP: **`molmcp serve`** is the knowledge core with
enabled providers FastMCP-mounted (`molvis_open`, …). `molmcp init <host>`
writes that one MCP entry and the usage skill.

## 1. List planes

```bash
pip install molcrafts-molmcp
molmcp planes
molmcp route "draw dopamine"
```

`molcrafts` is the core connection. Provider planes appear when their
packages / entry points are available. `--disable molcrafts` is an error;
`--disable molvis` (and the other providers) is the supported toggle.

## 2. Serve

```bash
# Composed core + provider mounts (needs at least one configured source)
molmcp serve

# Debug one provider only (bare tool names)
molmcp serve molvis
```

On the composed server, clients see `molcrafts__packages` and
`molcrafts__molvis_open`.

## 3. Connect from Claude Code

```bash
molmcp init claude
# or:
claude mcp add molcrafts -- molmcp serve
```

JSON shape:

```json
{
  "mcpServers": {
    "molcrafts": {
      "command": "molmcp",
      "args": ["serve"]
    }
  }
}
```

Use absolute paths / `uv run --directory …` if the client’s PATH is thin.
`molmcp init grok` writes this map (one composed `serve`) and the usage
skill; drop mounts with `--disable`.

## 4. Knowledge plane tools

On **molcrafts**, the main path is hierarchical pages:

| Tool | Role |
|------|------|
| `list_planes` / `route` | Optional provider planes to connect |
| `packages` | L0 package directory — choose sources |
| `outline` | Module / symbol map for one source |
| `open` | Inject one symbol page (optional source body) |
| `search` / `suggest` | Index helpers (prefer after packages/outline) |
| `compose` | Budgeted multi-page pack for a task |
| `info` | Ops / health — not the primary discovery path |

Science methods are **discovered** here and **invoked** in agent Python or
via `molvis_exec` — they are never re-wrapped as MCP science tools.

## 5. HTTP instead of stdio

```bash
molmcp serve molcrafts --transport streamable-http --host 127.0.0.1 --port 8787
```

Non-loopback HTTP requires auth configuration — see [Deploy](deploy.md).

## What's next?

- **[Deploy](deploy.md)** — full local stdio layout and client wiring
- **[Architecture](../concepts/architecture.md)** — core + provider planes
- **[MolVis workbench](../guides/molvis-workbench.md)** — open / exec / poll_events
- **[Write a Provider](../guides/write-a-provider.md)** — add a product plane
