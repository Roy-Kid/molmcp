# Deploy locally (stdio)

This guide covers the most common molmcp deployment: a local **stdio**
MCP server that an MCP client (Claude Code, Claude Desktop, Continue,
…) spawns as a subprocess each session. No HTTP, no auth, no
infrastructure — the client talks to molmcp over stdin/stdout the way a
shell pipes two processes together.

The first half of the page is **client-agnostic**: pick which servers
to run, install the dependencies, decide which packages to expose. The
second half is the **per-client wiring** — currently Claude Code; more
clients land here as we get to them.

---

## Pick your servers

There are two server flavours. You'll typically want one of each.

| Flavour | What it gives the agent | When to use |
|---------|-------------------------|-------------|
| **Foundation** (`molmcp` CLI) | Seven generic source-introspection tools (`list_modules`, `get_source`, `get_signature`, …) pointed at any MolCrafts import root. | "Show me the source of `molpy.compute.RDF`." |
| **Plugin** (`molmcp-molpy`, `molmcp-molrs`, `molmcp-molpack` CLIs) | Curated *domain* tools: per-package catalogs, file-format readers, structure inspectors. | "What restraint types does molpack support? Inspect this `.inp` file." |

You can run both in parallel — clients mount each under its own
namespace, so tool names never collide.

## Prerequisites

- **Python ≥ 3.10** with the molmcp foundation:
  ```bash
  pip install molcrafts-molmcp
  ```
- One or more MolCrafts packages you want to expose:
  ```bash
  pip install molcrafts-molpy molcrafts-molrs molcrafts-molpack
  ```
- (Optional) The matching plugin packages for curated domain tools:
  ```bash
  pip install molmcp-molpy molmcp-molrs molmcp-molpack
  ```

!!! tip "Use a venv"

    Most clients spawn the server with whatever `python` is on `PATH`
    at the time of registration. A dedicated venv keeps the server's
    dependency tree predictable. With `uv`:
    ```bash
    uv venv && source .venv/bin/activate
    uv pip install molcrafts-molmcp molcrafts-molpy molmcp-molpy
    ```

## What each plugin server exposes

=== "molpy plugin (`molmcp-molpy`)"

    All read-only:

    - `list_readers` — file-reader catalog (XYZ, PDB, GRO, LAMMPS, Mol2, XSF)
    - `list_compute_ops` — compute-operator catalog (`NeighborList`, `RDF`, `MCDCompute`, `PMSDCompute`)
    - `inspect_structure` — open a structure file via `molpy.io` and summarise it

=== "molrs plugin (`molmcp-molrs`)"

    All read-only:

    - `list_compute_ops` — molrs analysis catalog (`RDF`, `MSD`, `Cluster`, `GyrationTensor`, `InertiaTensor`, `RadiusOfGyration`, `CenterOfMass`, `Pca2`, `KMeans`)
    - `list_neighbor_algos` — `NeighborQuery`, `LinkedCell`, `NeighborList`
    - `list_readers` — molrs I/O readers (XYZ, PDB, LAMMPS, CHGCAR, Cube, SMILES, …)
    - `list_writers` — molrs I/O writers
    - `inspect_structure` — open a structure file via `molrs.io` and summarise it

=== "molpack plugin (`molmcp-molpack`)"

    All read-only:

    - `list_restraints` — `InsideBoxRestraint`, `InsideSphereRestraint`, `OutsideSphereRestraint`, `AbovePlaneRestraint`, `BelowPlaneRestraint`
    - `list_formats` — structure-file format support
    - `inspect_script` — parse a Packmol-compatible `.inp` script and summarise it (targets, atom counts, output path)

The foundation server's seven tools (`list_modules`, `list_symbols`,
`get_source`, `get_docstring`, `get_signature`, `read_file`,
`search_source`) come from any `--import-root` you point it at — see
[Quickstart](quickstart.md#3-the-seven-tools).

---

## Wire it up

### Claude Code

Reference: [Claude Code MCP docs](https://docs.claude.com/en/docs/claude-code/overview).

#### Foundation server (introspection)

```bash
claude mcp add molpy -- python -m molmcp --import-root molpy --name molpy
```

What this command does:

- `claude mcp add molpy` — register an MCP server under the local
  Claude Code config with the friendly name `molpy`.
- `--` — boundary between Claude Code's args and the spawn command.
  Everything after `--` is what Claude Code runs each session.
- `python -m molmcp --import-root molpy --name molpy` — the molmcp
  foundation, told to introspect the `molpy` import root and to
  advertise itself as `molpy` to MCP clients.

Verify:

```bash
claude mcp list
```

You should see:

```
molpy: python -m molmcp --import-root molpy --name molpy - ✓ Connected
```

#### Plugin servers (curated domain tools)

```bash
claude mcp add molpy-tools   -- molmcp-molpy   --transport stdio
claude mcp add molrs-tools   -- molmcp-molrs   --transport stdio
claude mcp add molpack-tools -- molmcp-molpack --transport stdio
```

After registration, `claude mcp list` should show:

```
molpy:          python -m molmcp --import-root molpy --name molpy - ✓ Connected
molpy-tools:    molmcp-molpy --transport stdio                    - ✓ Connected
molrs-tools:    molmcp-molrs --transport stdio                    - ✓ Connected
molpack-tools:  molmcp-molpack --transport stdio                  - ✓ Connected
```

The foundation `molpy` server (introspection) and the plugin
`molpy-tools` server (curated domain tools) coexist — they just publish
non-overlapping tool names under different namespaces.

#### Use it

Open a Claude Code session. Ask:

> What modules does molpy expose? Then show me the signature of
> `molpy.compute.RDF`.

Behind the scenes Claude calls:

- `mcp__molpy__list_modules` → returns every module under `molpy.*`
- `mcp__molpy__get_signature` with `symbol="molpy.compute.RDF"`

The `mcp__<name>__<tool>` prefix comes from the `--name molpy` you
passed: change `--name foo` and you'd see `mcp__foo__list_modules`.

#### Multi-package introspection in one server

If you want a single foundation server that introspects *several*
MolCrafts packages, repeat `--import-root`:

```bash
claude mcp add molcrafts -- python -m molmcp \
    --import-root molpy \
    --import-root molrs \
    --import-root molpack \
    --name molcrafts
```

Tools become `mcp__molcrafts__list_modules`, etc.; the prefix tree
returned spans all three packages. Useful for cross-package
comparative work.

#### Removing a server

```bash
claude mcp remove molpy
```

To rewire (e.g. point at a different venv), remove and re-add.

#### Troubleshooting (Claude Code)

**"✗ Failed to connect"** — run the spawn command in a terminal to see
the traceback:

```bash
python -m molmcp --import-root molpy --name molpy
```

The server should print nothing and wait for stdin (because that's
where Claude Code would normally talk to it). `Ctrl+C` to exit. Common
causes: wrong Python on PATH, `molpy` not installed in that venv,
`molcrafts-molmcp` missing.

**Tools not showing up after `claude mcp add`** — restart the Claude
Code session. Tool registration is read at session start.

**"Tool name collision"** — happens if two servers expose tools under
the same `--name`. Use distinct names (`molpy` for foundation,
`molpy-tools` for the plugin) and the `mcp__<name>__<tool>` prefix
keeps them apart.

### Other clients

Add `--transport stdio` (the default) and point your client at the
spawn command. The exact config-file format varies by client; the
[CLI reference](../reference/cli.md#wiring-into-claude-desktop) has a
worked Claude Desktop JSON example. Other clients (Continue, Cursor,
…) land here as we write them up.

---

## A worked example: pick the right RDF binning

Open your client and ask:

> I have an XYZ trajectory at `/tmp/water.xyz` in a 30 Å cubic box.
> Give me a Python snippet that computes the O–O RDF using molpy out
> to `r_max = 8 Å`. Confirm the relevant API exists first.

The agent will typically:

1. Call `mcp__molrs-tools__inspect_structure` with `path=/tmp/water.xyz`
   to see how many atoms / which simbox.
2. Call `mcp__molpy-tools__list_compute_ops` to confirm `RDF` and
   `NeighborList` exist and learn their signatures.
3. Write the snippet using the verified signatures.

That's the loop molmcp is built for: the agent verifies the API
against the live source before writing code, instead of guessing from
training data.

---

## What's next?

- **[CLI reference](../reference/cli.md)** — every flag the `molmcp`
  CLI accepts.
- **[Architecture](../concepts/architecture.md)** — how the foundation
  and plugin layers compose.
- **[Write a Provider](../guides/write-a-provider.md)** — author a
  curated plugin for your own MolCrafts package.
- Want stdout logs from the server? molmcp keeps stdout silent because
  that's the MCP wire. Use `--transport streamable-http` and run the
  server in another terminal if you need to watch what it does.
