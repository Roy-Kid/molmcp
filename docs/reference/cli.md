# CLI reference

```
molmcp [-h] [-V] {serve,init,planes,route,config,cache,info,search,explore,index} ...
python -m molmcp …
```

The `molmcp` script is installed by `pip install molcrafts-molmcp`.
`python -m molmcp` is equivalent when the package is importable.

**Default with no arguments:** `molmcp planes` (list connectable planes).
`molmcp --version` / `-V` prints `molmcp <version>` from the installed
package metadata. `molmcp serve` with no plane id starts the composed stack.

## `molmcp serve [plane]`

With **no plane**, start the molcrafts core and FastMCP-mount every enabled
provider (`molvis_open`, `molq_list_jobs`, …). Pass a plane id for a
single-plane debug server (bare tool names).

```bash
molmcp serve
molmcp serve molvis
molmcp serve molq
```

| Argument / flag | Meaning |
|-----------------|---------|
| `plane` | Optional. Omit for the composed stack. `molcrafts` or a provider name for a focused process. `catalog` is not a plane. |
| `--disable PLANE` | Omit a provider mount (emitted by `molmcp init --disable`). |
| `--config PATH` | Explicit `molcrafts.json`. Not searched for in the working directory — scope comes from settings; see [`molmcp config`](#molmcp-config). |
| `--env LOCATOR` | Python env to discover packages from (venv root, interpreter, or site-packages). Overrides the `pythonEnv` setting. |
| `--transport {stdio,streamable-http}` | Override transport (default stdio / config). |
| `--host` / `--port` | HTTP bind (streamable-http only). Non-loopback needs `server.auth_token_env`. |
| `--no-discover` | Do not load `molmcp.providers` entry points (provider plane needs inject). |

On the composed server, core tools are `molcrafts__packages`; mounted
provider tools are `molcrafts__molvis_open`.

## `molmcp planes`

List the molcrafts core and optional provider planes.

```bash
molmcp planes
molmcp planes --json
```

## `molmcp route <task>`

Suggest which **provider** plane(s) to connect for a free-text task.
molcrafts is already the core connection.

```bash
molmcp route "draw dopamine"
molmcp route "list slurm jobs"
```

## `molmcp config`

Read and edit settings. Verb shape follows `claude config`.

```bash
molmcp config list                              # resolved settings + which files contributed
molmcp config get sources.molpy
molmcp config set sources.molpy pkg:molpy
molmcp config add excludes vendor               # list-valued keys
molmcp config remove sources.molpy
```

| Flag | Meaning |
|------|---------|
| *(none)* | Write `~/.molmcp/settings.json` — the default, because a plane server inherits its working directory from the client that launched it |
| `--project` | Write `./.molmcp/settings.json` (checked in) |
| `--local` | Write `./.molmcp/settings.local.json` (untracked) |

Layers merge user → project → local. Unknown keys are an error rather than a
silent no-op. See the [installation guide](../get-started/installation.md#settings)
for every key.

There are **no environment variables**. The two the code still reads are
secrets, not configuration: the bearer token an HTTP-transport server checks
against, and `GITHUB_TOKEN` for `github:` sources. Both name a variable in
config rather than storing its value, which is the point — a settings file
is the wrong place for a credential.

## `molmcp init <host>`

Install managed skills (user-level, overwritten) and the MCP JSON for one
host. Host is required: `grok`, `claude`, `cursor`, `codex`. Skills:

- `molcrafts` — API discovery constitution (always loaded)
- `molexp-plan` — interactive experiment planner (`/molexp-plan`)

```bash
molmcp init grok
molmcp init grok --disable molq
molmcp init claude -o ~/.claude.json
```

JSON is one `molcrafts` entry running `molmcp serve`, with `--disable` flags
for omitted mounts. `--disable molcrafts` errors. The command uses the
resolved absolute path to `molmcp`.

## `molmcp cache`

Report the shared code index, and reclaim it.

```bash
molmcp cache                # size, live bytes, entry count
molmcp cache --prune        # drop payloads past retention and over the ceiling
molmcp cache --gc           # drop snapshots for sources no longer configured
molmcp cache --vacuum       # hand freed pages back to the filesystem
```

`used_bytes` is live content; `size_bytes` is the file. They diverge after a
prune because SQLite reuses freed pages rather than shrinking, and only
`--vacuum` closes the gap — with no plane server running, since it needs
exclusive access. A blocked vacuum reports `skipped` and changes nothing.

## Offline knowledge helpers

These drive the collection index without an MCP client (they need at least one
configured source — see `molmcp config`):

| Command | Role |
|---------|------|
| `molmcp info` | Registry + index coverage |
| `molmcp search <query>` | Full collection search (`--kind`, `--namespace`, `--source`, `--limit`) |
| `molmcp explore <task>` | Bounded task context pack (`--budget-chars`, …) |
| `molmcp index` | Index configured sources (`--force`, optional source list) |

```bash
molmcp search "Conformer" --source molpy
molmcp index --force
```

## Client wiring

```bash
claude mcp add molcrafts -- molmcp serve
```

Or generate the composed map and managed skills with `molmcp init grok`.

See [Deploy](../get-started/deploy.md) for the full layout.

## Read next

- [Architecture](../concepts/architecture.md)
- [API reference](api.md)
- [Quickstart](../get-started/quickstart.md)
