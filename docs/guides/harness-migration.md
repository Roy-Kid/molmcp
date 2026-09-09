# Retiring the old harness marketplace

`MolCrafts/molcrafts-harness` is the plugin **marketplace** MolCrafts used
before harness identity became a Git SHA — a marketplace being a repository an
agent host is told about once, after which it installs plugins from it by name.
It is being retired. Its replacement is `MolCrafts/harness`, a **new and empty
repository**, and the difference between "new and empty" and "the old one
renamed" is the reason this runbook exists at all.
[Harness catalog](../concepts/harness.md) is the concept page; read it first if
the words *plane*, *catalog* or *SHA* are not already familiar.

This page is a **runbook for a human**, not a script. It has five steps, and it
ends. The five steps are the ones that can be done inside this repository, with
a diff a reviewer can read. Everything that mutates a remote repository on
GitHub is listed after the stop, as work that needs its own authorisation.

Nothing here calls `gh`.

## 1. Inventory every sentence that still points a reader at the old repository

Search the whole working tree — public documentation, internal notes, the usage
skill, agent-facing hint and error strings — for `molcrafts-harness`, and read
each hit in context. Classify each one:

- **An install instruction**, telling someone to add that repository to their
  host right now. These are wrong and must go. A host wired to a repository
  that is about to disappear fails at the moment its user is least able to
  diagnose it, and an agent that was told the address will repeat it.
- **A historical mention**, naming the repository as the thing being replaced.
  These are fine and this page is one of them.

Pin the outcome rather than trusting the search: `tests/test_harness_catalog_fixture.py`
scans `docs/` and `.claude/notes/` and fails if the old address ever reappears
as a current `marketplace add`. A one-off grep proves the tree is clean today;
the test is what keeps it clean after the next writer forgets.

## 2. Write down the two-repository decision where it will be found again

Two facts have to survive longer than anyone's memory of this migration:

- `MolCrafts/harness` is a **new empty repository**. It is not
  `molcrafts-harness` under a different name, and the old repository's history
  is not carried into it.
- **Identity is a Git SHA.** Not a version, not a tag, not a branch.

Both go in `.claude/notes/harness-contract.md`, which holds those two rules and
nothing else, indexed from `.claude/notes/README.md`. Keeping it to two rules is
deliberate: a note that also restates the catalog keys becomes a second copy of
the grammar, and the copy is the one that goes stale. The keys live on the
concept page beside the example that demonstrates them.

## 3. Publish the example catalog, and say which filename is actually read

`docs/concepts/harness.example.toml` is the published example. The file a
consumer reads is `harness.toml`, at the root of one published commit tree.
Both facts have to be written down together, because a reader who sees only the
first will reasonably assume the example is the live file and start editing it.

Two placement rules follow, and both are load-bearing:

- The example stays under `docs/`. At the repository root it would sit exactly
  where a future loader might look for a real catalog, and molmcp's own
  repository would be the first thing to load it.
- Nothing auto-loads it, or any catalog, from the working directory. The
  filename is joined onto a root the caller passes in — one place in `src/`,
  `molmcp/components/catalog.py` — and the only roots molmcp passes are the
  trees of the commits its activation pointers name, one per activated source,
  in the order the settings file lists them.

## 4. Put the licence table on the concept page

Three repositories, three separate grants, one table on
[Harness catalog](../concepts/harness.md#licences). The table describes the
grants; it does not issue them.

- `MolCrafts/molmcp` is **BSD-3-Clause**, and the authority for that is the
  `LICENSE` file at this repository's root. This migration does not relicense
  molmcp, and no row in that table can.
- `MolCrafts/molcrafts-harness` is MIT and stays MIT for as long as it exists.
- `MolCrafts/harness` has no licence yet, because it has no commits yet. Its
  grant is settled when the repository first exists, by somebody choosing it.
  Reproducing molmcp's BSD-3-Clause text there because it was nearby would be a
  licensing decision taken as a formatting step.

## 5. STOP

The runbook ends here. What remains is a set of operations against remote
repositories on GitHub, and **each of them needs its own authorisation before
anybody runs it.** They are described below so the shape of the remaining work
is clear — the descriptions are not instructions to act now, and no command on
this page is meant to be pasted into a shell:

- **Creating `MolCrafts/harness`** (`gh repo create`) — a new empty repository,
  with its licence chosen at that moment rather than inherited.
- **Archiving or deleting `MolCrafts/molcrafts-harness`** — only *after*
  cutover, and only once no host configuration still points at it. Archiving
  leaves the history readable; deleting does not, and deleting also frees the
  name for anyone to take.
- **Bundling the old history** (`git bundle`) — if any of it is worth keeping,
  it is captured before either of the above, not after.

One thing is out of scope even with authorisation: **do not pile provider
repositories into the new one.** molq, molexp, molvis and molpy keep their own
repositories. A harness commit means "the agent tooling changed"; if a science
package can also change under the same SHA, the SHA stops answering the one
question it exists to answer.

## Read next

- [Harness catalog](../concepts/harness.md) — SHA identity, the two registries, the licence table
- [Providers](../concepts/providers.md) — the other registry, the `molmcp.providers` entry-point one
