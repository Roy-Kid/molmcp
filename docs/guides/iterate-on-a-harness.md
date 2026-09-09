# Iterate on a harness from a checkout

A **harness** is the pile of agent tooling you actually work with: instruction
files an AI coding agent reads before it starts (**skills**), definitions of
specialised workers it can delegate to (**agents**), and constraints that hold
across every task (**rules**). If you keep those in a git repository, molmcp can
install them into your AI client for you — and, more to the point, remember
exactly which commit it installed.

This guide starts where most people actually are: a checkout on your own disk,
pushed nowhere. It gets those files into your client, then takes you round the
loop again after you change one. Three commands, and the rest of this page is
what each of them is for:

```bash
molmcp config harness set --name local --path /abs/path/to/checkout
molmcp harness sync local
molmcp init claude
```

The first names the checkout. The second pins it to one commit. The third
installs what that commit declares. [Harness catalog](../concepts/harness.md) is
the full contract behind all three; everything needed to run the loop is here.

Two words before the first step. A **host** is the AI client being wired —
`claude`, `cursor`, `codex` or `grok` — each of which keeps its files in its own
directory under your home. A **catalog** is a file in your checkout listing what
that repository offers. Nothing is installed without one, because molmcp never
walks your tree looking for likely files: an editor backup sitting next to a
skill is not a skill, and the way molmcp knows that is that you did not list it.

## 1. Write the catalog

The catalog is `harness.toml`, and it sits at the **root of the checkout** —
beside `.git`, not inside a subdirectory. It has to be in every commit you
intend to install from, for the reason step 3 explains: what molmcp reads is a
commit, not your working directory.

Here is one that loads:

```toml
component_root = "plugins/mol"

[[component]]
kind = "skill"
name = "spec"
path = "skills/spec/SKILL.md"

[[component]]
kind = "agent"
name = "architect"
path = "agents/architect.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.spec"]

[[component]]
kind = "bundle"
name = "dev"
members = ["agent.architect"]
```

Every row is spelled `[[component]]`, which is TOML's syntax for "another
element of a list of tables". The first two rows above are **components** — one
installable piece each — and the last two are **bundles**, which are something
else entirely; they are covered at the end of this step.

A component row carries a `kind`, a `name`, and a `path`. There are five kinds,
and each one reserves a directory that the `path` must start with:

| `kind` | What it is | `path` starts with |
|--------|------------|--------------------|
| `skill` | Instruction file an agent reads | `skills/` |
| `agent` | Definition of one specialised worker | `agents/` |
| `rule` | A constraint that holds across tasks | `rules/` |
| `provider` | An MCP server this commit contributes | `providers/` |
| `overlay` | Domain knowledge layered onto the code graph | `overlays/` |

The prefix is not decoration and it is not inferred: a `skill` row whose path
does not begin `skills/` stops the file loading. The first three kinds are the
ones that become files in a host, and they are what this guide follows; the
[concept page](../concepts/harness.md#what-a-catalog-file-says) covers the other
two, which additionally need an `entrypoint`. (MCP is the Model Context
Protocol, the wire protocol an AI client speaks to call tools on a server; a
`provider` row is a server of that kind, contributed by the commit itself.)

`component_root` is optional, and it names the directory the component tree
begins at. The example above is for a repository that keeps its tooling under
`plugins/mol/`, so `skills/spec/SKILL.md` is read from
`plugins/mol/skills/spec/SKILL.md`. A repository laid out for this purpose — one
whose `skills/` and `agents/` sit at the top — simply leaves the key out. Note
what it does *not* move: `harness.toml` itself is always at the checkout root,
whatever `component_root` says.

You never write an id. A row's id is derived as `<kind>.<name>`, which is why
the bundle above refers to `skill.spec` and not to `spec`.

**The `daily` and `dev` bundles are required by the grammar.** A catalog missing
either one does not load, so the two rows above are the minimum. Be clear about
what you are getting for them: nothing in molmcp reads a bundle today. They are
declared, they are validated — every member must be the id of a component in the
same file — and no command consults them. Write them, and do not go looking for
their effect.

Now commit the file, along with whatever it points at.

## 2. Name the checkout as a source

A **harness source** is one repository this install is allowed to take a harness
from. You give it a name of your choosing and one origin:

```bash
molmcp config harness set --name local --path /abs/path/to/checkout
```

```
wrote ~/.molmcp/settings.json
```

The command also prints the settings file back as JSON, so you can see the entry
it wrote. `local` there is a label, not a keyword — it is how you will refer to
this source in every later command, and you could as easily have called it
`mine`.

**The path must be absolute, or start with `~/`.** A path like `./checkout` is
refused, with a message that says why: your settings file is shared by every
project on the machine, and a molmcp server started by an AI client inherits
whatever working directory that client happened to be in, so one stored
`./checkout` would name a different repository in every session. `~/harness` is fine, because home is the same
directory in every session. The refusal fires when something tries to *use* the
entry — the sync in the next step, or `molmcp serve` starting a server — rather
than when you write it, so an entry can be half-authored across several edits
without anything breaking in between.

An entry names **one** origin. `--path` is a checkout on disk; `--owner`,
`--repo` and `--ref` are a GitHub repository. The two shapes are mutually
exclusive, and putting them on the same entry is refused at the moment you write
it, with the file left as it was. That is the whole of the local-versus-remote
decision: there is no flag anywhere later that switches between them, because
the entry's shape already answers the question.

## 3. Sync: pin the source to a commit

Configuring a source fetches nothing. `sync` is the verb in between:

```bash
molmcp harness sync local
```

```
local: 11653d848fbdec2b54c744e4c922a474819e1403 activated
  tree    ~/.cache/molmcp/discovery/harness/commits/11653d84.../tree
  pointer ~/.cache/molmcp/discovery/harness.local.pointer
```

Three things happened, and each corresponds to a line.

The 40-character string is a **Git SHA**, the fingerprint git computes for every
commit from its own content. It names exactly one tree of files and can never be
made to name a different one, which is why it — and not a version number, a tag
or a branch name — is what a harness is identified by here. For a local source
the SHA is whatever `HEAD` resolves to in your checkout: the tip of the branch
you have checked out right now.

The `tree` line is where that commit was unpacked, in a store shared by every
source. (The directory is named by the full SHA; it is shortened above to fit.)

The `pointer` line is this source's **activation pointer**: a small JSON file
recording which SHA is in effect. Each source owns one, named after it, so
`local` and a second source called `official` are activated independently and
neither can move the other's.

Two consequences are worth stating plainly, because they are the reason to do
any of this instead of copying files by hand.

**What is published is a commit, never your working tree.** The commit's tree is
read out with `git archive` at the resolved SHA, so a file you edited and did
not commit is simply absent from it, and a file you created and did not `git add`
does not exist as far as molmcp is concerned. This is not friction to work
around — it is the feature. "Which harness was I running when that session went
well?" has an answer only if the thing being installed was a commit.

**Syncing the same commit twice is one sync.** The second run reports `already
activated` and leaves the pointer alone, deliberately: a pointer also records
the SHA it displaced, and re-activating the commit that is already current would
overwrite that record with the SHA that is already current.

## 4. Install into the host

Sync moved a pointer; it wrote nothing into your client. `init` is what reads
the pointer and installs what that commit's catalog declares:

```bash
molmcp init claude
```

```
wrote ~/.claude/skills/molcrafts/SKILL.md
placed 5 harness catalog component file(s), 0 refused
```

(Those are the two lines this loop is about. The rest of the output reports the
client's MCP configuration and the older `--source` route named at the end of
this page, neither of which is involved here.)

The checkout behind that run declared five component rows — three skills, an
agent and a rule — and here is where they landed:

```
~/.claude/skills/spec/SKILL.md
~/.claude/skills/impl/SKILL.md
~/.claude/skills/review/SKILL.md
~/.claude/agents/architect.md
~/.claude/rules/design-principles.md
```

The mapping is one substitution. Each kind has a directory in the host —
`skills/`, `agents/` and `rules/` under `~/.claude` for this host, and the same
three under `~/.cursor`, `~/.codex` or `~/.grok` for the others — and the kind's
prefix in the catalog path is replaced by it. So `skills/spec/SKILL.md` in the
catalog becomes `~/.claude/skills/spec/SKILL.md` in the host: the layout you keep
in the repository is the layout you get.

`0 refused` counts declared rows that were deliberately not installed, and there
are exactly two ways to earn one. A `provider` or `overlay` row is refused with
*kind has no host destination*: a provider is a server molmcp mounts and an
overlay is knowledge molmcp's own code index reads, so neither is a file any
client keeps. A row aiming at `skills/molcrafts/` is refused with *managed usage
skill is owned by molmcp init* — that directory holds the instruction file molmcp
writes for itself, the first line of the output above, and no catalog may take it
however the path is spelled.

One failure will find you early. Declaring a component whose file you forgot to
commit passes `sync` cleanly — a catalog is checked as a *file*, and nothing
confirms that the paths in it exist — and then fails `init`, naming the
component id and the path it could not find. Nothing is written when that
happens: every source is checked before the first byte is copied, so you do not
get half a harness. Commit the missing file and run `init` again.

## 5. Go round again

Changing a skill is the same loop, and it is short:

```bash
git commit -am "sharpen the spec skill"
molmcp harness sync local
molmcp init claude
```

`sync` resolves `HEAD` again, finds a new SHA, publishes it, and moves the
pointer. The SHA that was current becomes the pointer's `previous`, and both
trees stay in the store — the new commit's and the one it displaced — so both
remain readable. `init` then copies the new files over the old ones.

The step people leave out is the commit, and it is the one step that cannot be
skipped: an edit you have not committed is not in any commit, so `sync` will
resolve the same SHA as last time and report `already activated`. When the loop
seems to do nothing, that is almost always what happened.

## Several sources, and the ones you have not synced

An install may name more than one source — the one you are writing, one your
team keeps, one that belongs to a project — and they are read in the order the
settings file lists them.

You do not have to sync all of them. **A configured source that has never been
synced contributes nothing and is not an error**: it has no activation pointer,
so `molmcp init` passes over it and installs the others. Configuring a source is
naming an address; syncing it is the separate act of deciding to run it. A new
install that has configured sources and synced none of them is in an ordinary
state, not a broken one.

## One route this is not

You may meet `molmcp init <host> --source DIRECTORY`. It is an older, separate
route that reads a checkout laid out as `daily/` and `dev/` directories, and it
has nothing to do with the catalog: it does not read `harness.toml`, and the
`daily` and `dev` *bundles* from step 1 are not what it is looking for despite
the shared words. It is mentioned here only so that meeting it does not confuse
you. Nothing in this guide uses it.

## Read next

- [Harness catalog](../concepts/harness.md) — SHA identity, the full catalog grammar, and what serving does with a list of sources
- [CLI reference](../reference/cli.md#molmcp-harness) — every flag on `molmcp config harness` and `molmcp harness sync`
- [Installation](../get-started/installation.md#settings) — the settings files a harness source is written into, and the other keys beside it
