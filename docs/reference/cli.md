# CLI reference

```
molmcp [OPTIONS]
python -m molmcp [OPTIONS]
```

Both forms are equivalent. The `molmcp` script is installed by `pip install molcrafts-molmcp` via `[project.scripts]`; `python -m molmcp` works whenever the package is importable.

## Options

### `--name NAME`

Server name advertised to MCP clients. Default: `molmcp`.

This becomes the prefix in client-side tool naming (e.g., Claude Code's `mcp__<name>__<tool>`). Match it to the MolCrafts package you're exposing for clarity:

```bash
python -m molmcp --name molpy --import-root molpy
```

### `--import-root PACKAGE`

Top-level Python package whose source the built-in `IntrospectionProvider` should expose. Repeatable:

```bash
python -m molmcp \
    --import-root molpy \
    --import-root molpack \
    --import-root molexp
```

If `--import-root` is omitted, the introspection tools are not registered — useful if you only want auto-discovered Provider tools.

### `--no-discover`

Skip auto-discovery of Providers via the `molmcp.providers` entry point group. Use when you don't want any third-party Providers loaded, or when you only want `IntrospectionProvider` plus an explicit allowlist:

```bash
python -m molmcp --import-root molpy --no-discover
```

### `--no-validate-annotations`

Skip the startup-time check that every registered tool has `readOnlyHint` or `destructiveHint`. Use only when prototyping a new Provider; never in production.

### `--transport {stdio,streamable-http,sse}`, `-t`

Transport protocol. Default: `stdio`.

- `stdio` — default. The server reads MCP messages from stdin and writes to stdout. Right for local clients (Claude Code, Claude Desktop) that spawn the server as a subprocess.
- `streamable-http` — HTTP with streaming. Right for sharing a server across processes or machines.
- `sse` — Server-Sent Events. Legacy; prefer `streamable-http` for new deployments.

### `--host ADDRESS`

Bind address for HTTP and SSE transports. Default: `127.0.0.1`. Ignored for `stdio`.

### `--port PORT`, `-p`

Port for HTTP and SSE transports. Default: `8787`. Ignored for `stdio`.

### `--help`, `-h`

Show usage and exit.

## Common invocations

### Local stdio server for one MolCrafts package

```bash
python -m molmcp --import-root molpy --name molpy
```

### Local stdio server, multiple MolCrafts packages

```bash
python -m molmcp \
    --import-root molpy \
    --import-root molpack \
    --import-root molexp \
    --name molcrafts
```

### HTTP server on port 9000

```bash
python -m molmcp \
    --import-root molpy --name molpy \
    --transport streamable-http --host 0.0.0.0 --port 9000
```

### Locked-down: only explicit Providers, no auto-discovery

```bash
python -m molmcp --import-root molpy --name molpy --no-discover
```

This still registers `IntrospectionProvider` (because `--import-root molpy`) but ignores any third-party Providers installed via entry points.

### Domain tools only, no introspection

```bash
python -m molmcp --name molpack
```

No `--import-root` means `IntrospectionProvider` doesn't register. Whatever Providers are auto-discovered run alone.

## Wiring into Claude Code

```bash
claude mcp add <name> -- python -m molmcp [molmcp options...]
```

Example:

```bash
claude mcp add molpy -- python -m molmcp --import-root molpy --name molpy
```

Note the `--` separator: everything after it is the molmcp invocation Claude Code will spawn each session.

## Wiring into Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "molpy": {
      "command": "python",
      "args": ["-m", "molmcp", "--import-root", "molpy", "--name", "molpy"]
    }
  }
}
```

Restart Claude Desktop for the server to appear in the tools picker.

## Read next

- **[API reference](api.md)** — programmatic `create_server` API
- **[Quickstart](../get-started/quickstart.md)** — walkthrough using these flags
