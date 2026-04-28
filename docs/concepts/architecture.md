# Architecture

molmcp is the central piece of MCP infrastructure for the MolCrafts ecosystem. Each MolCrafts package contributes its domain tools through molmcp, and clients see one coordinated server.

```
                ┌────────────────────────────────────┐
                │  MCP clients                       │
                │  (Claude Code, Claude Desktop, …)  │
                └──────────────┬─────────────────────┘
                               │  stdio / streamable-http / sse
                               ▼
                ┌────────────────────────────────────┐
                │  molmcp                            │
                │  • Provider contract               │
                │  • IntrospectionProvider           │
                │  • PathSafety / ResponseLimit      │
                │  • Annotations validator           │
                │  • run_safe / fence_untrusted      │
                └──────────────┬─────────────────────┘
                               │
       ┌───────────┬───────────┼───────────┬───────────┐
       ▼           ▼           ▼           ▼           ▼
   molpy_mcp   molpack_mcp  molexp_mcp   molq_mcp  mollog_mcp
   (domain)    (domain)     (domain)    (domain)   (domain)
```

## Three responsibilities

### 1. Transport plumbing

molmcp delegates the wire-level work — JSON-RPC framing, transport adapters (stdio, streamable-http, sse), tool/resource/prompt decorators, the middleware pipeline — to its underlying server library. molmcp doesn't reinvent any of that, and you generally don't have to think about it: when you call `create_server(...)` you get a working server back.

### 2. The MolCrafts Provider contract

This is what molmcp adds:

1. **Source introspection** — the `IntrospectionProvider` exposes seven read-only tools that work against any MolCrafts package's source.
2. **A plugin contract** — `Provider` Protocol + `molmcp.providers` entry point group, so each MolCrafts package can ship domain tools that the host server discovers automatically.
3. **Default safety middleware** — path traversal guards, response-size limits, startup-time annotation validation. Mounted automatically when `create_server(...)` is called.

### 3. MolCrafts packages

Where the *actual chemistry / physics / workflow* lives. molmcp doesn't know what `molpy` does or what `molpack` does — it just hands each downstream package an empty server instance and lets it register tools. This is what keeps molmcp generic across the ecosystem.

## How a request flows through

```
Client   →   stdio        molmcp        mid-      mid-      Provider
                          decoder       ware 1    ware 2    tool

Claude   →   tools/call →  call_tool → Path-   → Response → @mcp.tool
calls                                   Safety   Limit       def get_source(...)
mcp__molpy
__get_source                                                ← returns text
                                      ← passes ← truncates ←
                                        OK        if too big

         ←  encoded JSON-RPC response
         ←  stdio
```

Every Provider tool flows through every middleware. Adding a Provider doesn't require it to understand the middleware contract — it just declares its tools, and molmcp wires them up.

## Why this split?

Without molmcp, every MolCrafts package would have to:

- Author its own MCP server (~200 lines of boilerplate per package).
- Maintain its own transport configuration.
- Decide independently what counts as a "safe" path argument.
- Decide independently when to truncate large responses.
- Decide independently whether tool annotations are required.

The result would be: fragmented quality, inconsistent UX across packages, security defaults set wherever someone happened to remember. With molmcp:

- A user runs **one** invocation pattern (`python -m molmcp ...`).
- Security defaults are uniform across every MolCrafts package.
- Multiple MolCrafts packages can be exposed via a single server with `mcp.mount(prefix=...)`. Agents see `molpy__list_modules` and `molpack__pack_box` side by side.
- Updating the underlying transport library is a one-line dep bump in molmcp, not a coordinated change across N packages.

## What molmcp deliberately does *not* do

- **No domain tools.** No structure I/O facade, no `compute_rdf`, no `parse_smiles`. Those belong in MolCrafts package Providers (`molpy_mcp`, `molpack_mcp`, etc.).
- **No batteries-included science deps.** molmcp's wheel pulls in only its server-framework dependency.
- **No opinions about Provider internals.** A Provider can be 5 lines or 5,000 — molmcp only requires that it has a `name` and a `register(mcp)` method.
- **No MolCrafts package import.** molmcp imports nothing from `molpy`, `molcfg`, `molexp`, etc. That keeps it adoptable on any cadence.

## Read next

- **[Providers](providers.md)** — the contract in detail
- **[Middleware](middleware.md)** — what each default middleware does and how to disable it
- **[Write a Provider](../guides/write-a-provider.md)** — practical guide
