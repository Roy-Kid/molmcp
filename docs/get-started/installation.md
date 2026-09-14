# Installation

molmcp is published on PyPI as **`molcrafts-molmcp`** and requires Python ≥ 3.12.
The import name is `molmcp`.

## With pip

```bash
pip install molcrafts-molmcp
```

## With uv

```bash
uv add --prerelease=allow molcrafts-molmcp
# or, into the active environment:
uv pip install --prerelease=allow --upgrade molcrafts-molmcp
```

Check the binary you actually got:

```bash
which molmcp
molmcp --version
```

!!! warning "Without `--prerelease=allow`, uv will not install 0.6+"

    molmcp requires **FastMCP 4** (MCP 2026-07-28). FastMCP 4 is still beta
    (`4.0.0b5`). `pip install -U molcrafts-molmcp` is fine; uv is not:

    ```
    Because only fastmcp<4.0.0b5 is available and molcrafts-molmcp
    depends on fastmcp>=4.0.0b5 ... cannot be used.
    ```

    Bare `uv pip install --upgrade molcrafts-molmcp` can also **downgrade**
    to 0.2.1 (the last release whose dependencies are all stable). Always
    pass `--prerelease=allow` until FastMCP 4.0.0 final ships.

    `--version` exists from **0.6.1**. An older CLI prints
    `the following arguments are required: command` instead.

## What gets installed

The base install is infrastructure only: the multi-plane MCP runtime, knowledge
index, and CLI. Domain packages (molpy, molvis, molq, …) stay optional — install
them when you need a provider plane or richer local discovery.

## Optional extras

| Extra | Purpose | Command |
|-------|---------|---------|
| `dev` | pytest + ruff for the test suite and linting | `pip install "molcrafts-molmcp[dev]"` |
| `docs` | local preview of this documentation site | `pip install "molcrafts-molmcp[docs]"` |

Docs pin: `zensical>=0.0.53` and `molcrafts-zensical-theme>=0.2.5`.

## Verify the install

```bash
python -c "import molmcp; print(molmcp.__version__)"
molmcp planes
molmcp --help
```

`molmcp planes` lists connectable product domains. Each plane is a **separate**
MCP process (`molmcp serve <plane>`).

## Editable install (contributors)

```bash
git clone https://github.com/MolCrafts/molmcp.git
cd molmcp
uv sync --extra dev
uv run pytest -v
```

## Settings

Configuration lives in `~/.molmcp/settings.json` and is edited through the
CLI. There are no environment variables — `molmcp config list` is the whole
truth, which a variable that exists in one shell could never be.

```bash
molmcp config list
molmcp config set sources.molpy pkg:molpy
```

A project may add `.molmcp/settings.json` (checked in) and
`.molmcp/settings.local.json` (untracked). They layer over the user file in
that order. Writes target the user file unless `--project` or `--local` is
given: a plane server inherits its working directory from whichever MCP client
launched it, so project scope has to be asked for.

### What gets indexed

Installed MolCrafts distributions are discovered automatically — a package
qualifies by declaring a `molmcp.*` entry point or the `molcrafts` keyword, so
your dependencies are never dragged in.

The working directory is **not** a source unless you say so. It used to be
unconditionally, which meant an unconfigured install indexed whatever it was
started next to; one real install had accumulated two unrelated repositories,
a monorepo root, and a pile of temp directories that way.

```bash
molmcp config set indexWorkspace true --project   # index this repo as well
molmcp config set sources.atomiverse pkg:atomiverse
```

### Keys

| Key | Meaning |
|-----|---------|
| `sources` | Extra sources, `name → spec` (`pkg:`, `local:`, `github:`, or a path) |
| `indexWorkspace` | Also index the working directory (default `false`) |
| `knowledgeScope` | Narrow which indexed sources `packages` / `outline` / `open` surface |
| `excludes` | Extra ignore globs for the file walk |
| `watch` | Re-index local sources on change (default `true`) |
| `cacheDir` | Where the index lives (default `~/.cache/molmcp/discovery`) |
| `maxCacheBytes` | Ceiling on the code index (default 512 MB) |
| `maxCacheAgeDays` | Retention window for extraction payloads (default 30) |
| `pythonEnv` | Environment to discover from: a venv root, a python, or a site-packages dir |
| `discoverInclude` / `discoverExclude` | Force a distribution in or out of auto-discovery |
| `harness` | Ordered list of named harness sources, each an object `{name, owner, repo, ref}` |
| `molexp.workspace` | Default molexp workspace path |
| `molq.database` | Override the molq job database |

Unknown keys are rejected. A mistyped `indexWorkspaces` that quietly does
nothing is worse than one that says so.

`harness` is the one key in that table whose elements are objects, so the
string-valued write verbs cannot author it and it has two subcommands of its own:
`molmcp config harness set MolCrafts/harness [--alias NAME] [--enable BUNDLE] [--disable BUNDLE]`
upserts one entry, `molmcp config harness remove --name NAME` drops one, and both
take the same `--project` / `--local` scope flags as the verbs above. What the
list is for, what an entry means, what a half-written one does at serve time,
and a worked snippet of the file live on
[Harness catalog](../concepts/harness.md); molmcp ships no default source, so an
install that names none simply has no harness.

Because rejection happens on every *read*, and every `config` verb reads the
file before it writes it, a typo anywhere in the file stops all of
`config list`, `get`, `set`, `add`, `remove` and `harness` — and `molmcp serve`
too — with exit status 2, the message naming the file and the offending key.
The fix is to edit that same file; no verb can do it for you.

### `molcrafts.json`

Still accepted with an explicit `--config PATH`, but no longer picked up from
the working directory — that was the same accident-of-cwd the workspace source
was.

## Next steps

- **[Quickstart](quickstart.md)** — `molmcp serve` and `molmcp init`
- **[Architecture](../concepts/architecture.md)** — FastMCP composition
- **[Harness catalog](../concepts/harness.md)** — the ordered `harness` source list, how to write one into your settings file, and why a harness is a Git SHA rather than a plane
- **[Deploy](deploy.md)** — local stdio for Claude Code
