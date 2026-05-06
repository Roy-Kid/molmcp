# Quickstart

Expose a MolCrafts package over MCP, in 60 seconds.

## 1. Run the server

```bash
python -m molmcp --import-root molpy --name molpy
```

This starts an MCP server over **stdio** that knows how to read `molpy`'s source code. The same shape works for any MolCrafts package: substitute `--import-root molcfg`, `--import-root molexp`, `--import-root molpack`, etc.

The server stays in the foreground waiting for an MCP client to connect over stdin/stdout. To stop it, press `Ctrl+C`.

## 2. Connect from Claude Code

In another terminal:

```bash
claude mcp add molpy -- python -m molmcp --import-root molpy --name molpy
```

The `--` separates Claude Code's args from molmcp's args; everything after `--` is the command Claude Code spawns each session.

After this, ask Claude:

> What modules does molpy expose? Show me the source of `molpy.core.atomistic.Atomistic`.

Behind the scenes Claude calls:

- `mcp__molpy__list_modules`
- `mcp__molpy__get_source`

For the full local-stdio walkthrough — including the curated plugin servers (`molmcp-molpy`, `molmcp-molrs`, `molmcp-molpack`), verifying with `claude mcp list`, multi-server setup, and per-client wiring — see [Deploy](deploy.md).

## 3. The seven tools

| Tool | What it does |
|------|--------------|
| `list_modules(prefix=None)` | Walks the import tree and returns all module names. |
| `list_symbols(module)` | Lists public symbols in a module with one-line summaries. |
| `get_source(symbol)` | Returns full source for a module / class / method. |
| `get_docstring(symbol)` | Returns the cleaned docstring. |
| `get_signature(symbol)` | Returns the call signature with type hints. |
| `read_file(relative_path, start, end)` | Reads a line range from any source file in the package. |
| `search_source(query, module_prefix, max_results)` | Case-insensitive substring search. |

Every tool is marked `readOnlyHint=True`, so MCP clients can auto-approve them safely.

## 4. Run over HTTP instead

For sharing the server across processes or machines:

```bash
python -m molmcp --import-root molpy --name molpy \
    --transport streamable-http --host 127.0.0.1 --port 8787
```

## 5. Multi-package server

Pass `--import-root` more than once to expose several MolCrafts packages from one server:

```bash
python -m molmcp \
    --import-root molpy \
    --import-root molpack \
    --import-root molexp \
    --name molcrafts
```

All seven introspection tools now operate over the union of those packages' source. Useful when an agent is doing comparative work across the ecosystem.

## What's next?

- **[Expose a package](../guides/expose-a-package.md)** — deeper guide on the introspection tools
- **[Write a Provider](../guides/write-a-provider.md)** — add *domain* tools (build, pack, simulate) from your MolCrafts package
- **[Architecture](../concepts/architecture.md)** — how molmcp composes the pieces
