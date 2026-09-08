---
title: molmcp config harness set|remove, and the two gaps link 01 left owed
status: done
grilled: pending
created: 2026-09-08
---

# `molmcp config harness set|remove` and the two gaps link 01 left owed

## Summary

Link 01 turned `settings.harness` into an ordered list of named `HarnessSource` entries and left no way to author one except opening `~/.molmcp/settings.json` in an editor. This link gives that list a verb: `molmcp config harness set --name N [--owner O] [--repo R] [--ref F]` upserts one entry by name, `molmcp config harness remove --name N` drops one, and both compose with the existing `--project` / `--local` scope flags. Two behaviours link 01 recorded as owed come with it: `config get harness.owner` stops answering `null` for a path that cannot exist and says so instead, and the two refusal messages that today tell the operator to hand-edit a file now name the verb that does the job. A latent trap is closed in the same change — `_config`'s branch chain ends in a bare `else` that calls `remove_value`, so adding any new `config_action` without fixing it would make an unmatched action silently delete a setting.

## Design

**Entities touched.** `src/molmcp/settings.py` gains two module-level functions and adjusts three existing ones; `src/molmcp/cli.py` gains one subparser group, one handler helper, and loses a bare `else`. No new module, no new class, no new seam.

### L2 — `settings.py`

Two new functions live in the `# -- editing (the molmcp config verbs) --` section, immediately after `remove_value` and before `get_value`, and join `__all__` at their alphabetical-within-group positions: `__all__` is
**grouped** (constants, then types, then functions), not sorted — `LOCAL_SETTINGS_NAME`
precedes `HarnessSource` today — so the targets are `remove_harness_source`
immediately before `remove_value`, and `set_harness_source` immediately before
`set_value`.

- `set_harness_source(path, *, name, owner=None, repo=None, ref=None) -> dict[str, Any]` — upsert **by `name`**. A field passed `None` means "leave as it was" on an existing entry and `""` (the `HarnessSource` default) on a new one, so no coordinate is ever defaulted to a value nobody typed. An unknown name is appended **last**: appending never changes which already-configured source wins, which is the property `docs/concepts/harness.md:246-251` calls a contract.
- `remove_harness_source(path, name) -> dict[str, Any]` — drop the one entry whose `name` matches. An absent `harness` key raises `SettingsError(f"'harness' is not set in {path}")`, matching `remove_value:360`; a present list with no such name raises `SettingsError(f"{name!r} is not present in 'harness'")`, matching `remove_value:366`. Removing the **last** entry leaves `"harness": []`, not a missing key — dropping the key is `remove_value(path, "harness")`, a different operation that `tests/test_settings.py:212-223` pins.

Both follow the section's read-modify-write shape: validate -> `_resolve(path, "harness", create=True)` -> build a **new** list -> `write_settings_file(path, root)` -> `return root`. Ordering is the binding part: the arguments are validated by constructing a `HarnessSource` **before** `_resolve`, exactly as `set_value:319` puts `_reject_object_list_write` before `_resolve`, so a refused call creates no file at all (`settings.py:313-317` states that contract, `tests/test_settings.py:183-200` asserts it). The merged entry is constructed a second time, after the read and still before the write, so the dataclass — never this module — is what decides whether the result is legal.

Lifecycle and ownership are unchanged: `HarnessSource.__post_init__` (`settings.py:136-156`) remains the sole owner of every field rule, `read_settings_file` -> `_reject_bad_harness_entries` (`settings.py:406-463`) remains the sole owner of whole-file entry validation, and `server._HARNESS_KEYS` (`server.py:93`) remains the **only** completeness rule. A `--name`-only invocation is therefore accepted at the settings layer and stores
`{"name": "mine", "owner": "", "repo": "", "ref": ""}`; authoring by repeated edits
is the documented model (`settings.py:98-104`, `docs/concepts/harness.md:239-244`).

**State the cost plainly, because accepting it is a choice.** `server.py:554-566`
raises `ConfigurationError` for an entry that sets none of `owner`/`repo`/`ref`
(`.strip()` makes `""` count as missing), and `create_stack` reaches that on the
default serve path (`server.py:374`). So `molmcp config harness set --name mine`
exits 0 and leaves **every subsequent `molmcp serve` at exit 2** until the
coordinates are filled in. That is the two-step ritual the load-time/serve-time
split has always implied, but it was previously unreachable in one command.

The answer is **not** a second completeness rule. `_config_harness` must not
enumerate missing coordinates — `server._HARNESS_KEYS` stays the only place that
decides what "complete" means, and duplicating it is how the two drift. Instead the
consequence is pinned by an acceptance criterion (a name-only write, then
`_harness_locator()` raising and naming that entry), and the sentence at
`docs/concepts/harness.md:239-244` that documents the half-authored state survives
the rewrite in substance and is **linked** from `docs/reference/cli.md`, not repeated there:
that file is a `_POINTER_PAGES` member (`tests/test_harness_catalog_fixture.py:104-113`,
"may only point at the concept page, never restate its contract"), and a second copy
of a contract sentence is the one that goes stale. Today that raise has **zero** test coverage —
`grep -rn "is incomplete" tests/` returns nothing — which is
`faked-seam-hides-broken-reader` on the exact reader this verb puts one command
away from firing.

Three existing functions change, each narrowly:

- `get_value` (`settings.py:372-379`) splits its one condition into two. `part not in node` still returns `None`. Be precise about which cases that arm
actually serves: `Settings.to_dict()` always carries all fifteen keys, `cacheDir`
among them, so `config get cacheDir` answers `null` because the **value** is `None`
and the walk ends — not through the `part not in node` arm at all. That arm is
reachable only for keys absent from `to_dict()`: `config get nope` and
`config get sources.nope`, both of which answer `null` today and must keep doing so. Descending into something that is not a dict while parts remain raises `SettingsError` naming the key and the segment that is not an object. `Settings.to_dict()` always contains every key, so `harness.owner`, `cacheDir.x`, `excludes.x`, `indexWorkspace.x` and `layers.x` all reach the new arm. The head key is **not** checked against `_SCHEMA`: `to_dict()`'s key space includes `layers`, which `_SCHEMA` does not, and validating there would break a working command. The single production call site is `cli.py:511`, reached only by `config get`, and `main`'s funnel (`cli.py:677-690`) already maps `SettingsError` to exit 2, so no CLI change is needed to surface it.
- `_resolve`'s dotted refusal (`settings.py:517-518`) is improved **in place** — `_reject_object_list_write`'s docstring (`:478-482`) explicitly delegates the dotted case here, and moving the refusal earlier would falsify that docstring. The friendlier sentence is appended **only when `parts[0] in _OBJECT_LISTS`**; `excludes.foo` and `cacheDir.x` keep the generic message, because pointing them at a harness verb would be a worse error than the vague one.
- `_reject_object_list_write`'s message (`settings.py:500-505`) stops saying "author it by editing {path}" and names the verb. The command string is derived from `key` (`molmcp config {key} set`), not written as a `harness` literal, so the table-driven property the docstring promises survives: a second `_OBJECT_LISTS` member gets a correct message only if it also gets its
verb. That obligation is **machinery, not prose**: an acceptance criterion asserts
that for every `settings._OBJECT_LISTS` member, `_build_parser()` registers a
`config <member>` subparser. This repo enforces exactly this class of promise with a
check rather than a comment — `tests/test_tool_hints.py` for hints, `molmcp gate`
for the CI literal — and a message naming a command nothing resolves is the failure
mode `CLAUDE.md` singles out. Note `_resolve` is reached from `remove_value` too, so
the sentence is worded to cover both leaves rather than naming only `set`.

Both new messages name the command **bare, as registered** (`molmcp config harness set`), per `CLAUDE.md`'s hint rule — and they may name it only now that it resolves.

### L1 — `cli.py`

`config_actions` gains a third parser, `harness`, whose own `add_subparsers(dest="harness_action", required=True)` carries `set` and `remove`. This is the package's first three-level nesting; `cli.py:53` and `cli.py:184` are the only two `add_subparsers` calls today and this is the third `dest`. The cost is paid deliberately: verbs stay verbs and read like the existing `config set|get|add|remove`, whereas the flag-bearing alternative would need a set/remove **exclusive mode flag**, a shape this CLI uses nowhere (`cache`'s `--prune/--vacuum/--gc` are additive actions on one noun). `_scope_arguments` (`cli.py:232-249`) composes onto both leaves unchanged.
`--owner/--repo/--ref` default to `None`, never to a value.

The nearer alternative — a top-level `molmcp harness set|remove`, which would keep
the existing two-level depth — is rejected because `config list` and
`config get harness` already read this key, and splitting the reader from the writer
across two top-level commands costs more than one nesting level: `_scope_arguments`
would have to be re-composed onto a command outside the `config` tree, and an
operator would learn the key in one place and edit it in another.

`_config` (`cli.py:498-526`) keeps its read-only head (`list`, `get`) and its shared write tail (`print(f"wrote {target}")` + `_emit`), and its branch chain becomes fully explicit: `set` / `add` / `harness` / `elif args.config_action == "remove"` / `else: raise ConfigurationError(...)`. The bare `else` at `:522-523` is closed here, on an honest reading of the hazard.
It is **not** true that adding the `harness` action would itself trigger a silent
delete: the drafted Namespace carries `harness_action / name / owner / repo / ref /
project / local` and no `key` or `value`, so `remove_value(target, args.key,
args.value)` would raise `AttributeError` — which `main`'s funnel (`cli.py:679-688`)
does not catch, giving a traceback rather than a quiet deletion. The real hazard is
latent and forward-looking: a *future* `config_action` that happens to carry `key`
and `value` would fall into `remove_value` and delete silently. Closing the `else`
while this file is already open is worth doing on that ground alone, without
inflating it. The harness leg delegates to a new module-private `_config_harness(args, target)` holding its own explicit `set` / `remove` branches and its own terminal raise, so `_config` does not grow a nested chain. `ConfigurationError` is already funnelled to exit 2 by `main` and already imported.

### What this verb deliberately does not do

It authors entries into a **valid** file. It reads through `read_settings_file` like every other verb, so a file already carrying a bad entry still fails on read — `docs/get-started/installation.md:140-144` ("the fix is to edit that same file; no verb can do it for you") stays true and stays on the page. No dotted `harness.<name>.owner` path is opened: `_resolve` refuses `len(parts) > 2` outright and `_parse` dispatches on the head key's declared type, which is exactly the write-before-validate hole `schema-type-flip-unlocks-writes` records.

### Reuse decision

- `_resolve` (`settings.py:508`) — **reuse**. For a one-part key it returns `(root, "harness", root)` after `read_settings_file`, and it is not guarded by `_reject_object_list_write` (called only from `set_value:319` / `add_value:342`), so the new functions may call it.
- `HarnessSource` + `dataclasses.asdict` (`settings.py:90-156`, `asdict` imported at `:24`, used at `:209`) — **reuse**. Construct the entry, `asdict` it, re-raise `ValueError` as `SettingsError` the way `_reject_bad_harness_entries:454-457` does. No field rule is restated.
- `read_settings_file` / `_reject_bad_harness_entries` (`:227`, `:406`) — **reuse**, reached through `_resolve`. An already-broken list fails before the new verb writes; no second validation of existing entries is added.
- `_HARNESS_ENTRY_KEYS` (`:162`) — **reuse** wherever the upsert enumerates fields. Never a literal tuple.
- `write_settings_file` (`:241`) — **reuse**, called last in both functions.
- `_reject_object_list_write` (`:466`) — **reuse**, and **extended to one more caller**. `remove_value` is not guarded by `_OBJECT_LISTS` today (only `:319` and `:342` are), so `molmcp config remove harness official` answers `"'official' is not present in 'harness'"` while an entry named `official` *is* present — vague today, actively false once entries are named things. The guard extends to `remove_value`'s **value arm only** (`value is not None`),
because `remove_value(path, "harness")` dropping the whole key must keep working;
`tests/test_settings.py:212-223` pins that boundary. The message derives its **leaf
from the calling verb**, not only its key: a refused `config remove harness official`
must name `molmcp config harness remove`, not `... set`. Answering a remove with a
set is a precise misdirection, which is worse than the vague message it replaces.
- `_scope_arguments` (`cli.py:232`) and `_config`'s write tail (`cli.py:524-525`) — **reuse** unchanged on both new leaves.
- `add_value` / `remove_value` list arms (`:349-350`, `:367`) — **new, pattern only**. `add_value` de-dups by string equality and `remove_value` matches `item != value`; neither can address a dict element by its `name`, and widening either would change a string verb's contract. The new functions borrow their message shapes and their guard-before-`_resolve` ordering, and extend neither.
- `get_value` (`:372`) — **modified in place**, not generalized. It is a two-case condition doing the work of one; splitting it is a fix with one production call site.

## Files to create or modify

- `src/molmcp/settings.py` — `set_harness_source`, `remove_harness_source`, `__all__`, the `get_value` walk, the `_resolve` dotted message, the `_reject_object_list_write` message and its docstring.
- `src/molmcp/cli.py` — the `config harness set|remove` subparsers, `_config_harness`, the explicit `remove` branch and the unrecognised-action raise.
- `tests/test_settings.py` — new `TestHarnessSourceEdit`; rewritten prose in `TestHarnessWriteGuard`; rewritten `test_set_refuses_every_dotted_harness_key_not_only_a_stray_one`.
- `tests/test_cli_config.py` — new `TestConfigHarness`; rewritten `test_list_prints_harness_as_an_array_of_entry_objects`.
- `tests/test_harness_catalog_fixture.py` — docstring-only: the module docstring (`:16`) and `test_concept_page_fences_one_settings_file` (`:362-363`) both say no verb exists yet. No assertion changes.
- `docs/get-started/installation.md` — the "`harness` is the one key ... a verb is coming" paragraph (`:133-138`).
- `docs/concepts/harness.md` — the authoring paragraphs under "Where a harness comes from" (`:211-244`).
- `docs/reference/cli.md` — the `config` usage block (`:63-69`).
- `tests/test_stack.py` — one test for ac-013, reusing the existing `_home_settings` helper (`:497`) and the real-locator pattern (`:493-535`). It adds a `cli` import this module does not have today.

## Tasks

- [x] Write failing unit tests for `set_harness_source` and `remove_harness_source` (tests/test_settings.py -> `TestHarnessSourceEdit`)
- [x] Implement `set_harness_source` and `remove_harness_source` in src/molmcp/settings.py and add both to `__all__` at their alphabetical-within-group positions
- [x] Write failing unit tests for the `get_value` walk split and the two rewritten refusal messages (tests/test_settings.py)
- [x] Split `get_value`'s walk condition and rewrite the `_resolve` dotted message and `_reject_object_list_write` message + docstring in src/molmcp/settings.py
- [x] Write failing CLI tests for `config harness set|remove` and the unrecognised-action guard (tests/test_cli_config.py -> `TestConfigHarness`)
- [x] Add the `config harness set|remove` subparsers and `_config_harness` dispatch in src/molmcp/cli.py, replacing `_config`'s bare `else` with an explicit `remove` branch and a loud raise
- [x] Write a failing test that a name-only entry authored through the verb makes the real `_harness_locator()` raise, naming that entry (tests/test_stack.py)
- [x] Rewrite the falsified prose in tests/test_settings.py, tests/test_cli_config.py and tests/test_harness_catalog_fixture.py
- [x] Update docs/get-started/installation.md, docs/concepts/harness.md and docs/reference/cli.md to name the verb
- [x] Run full check + test suite

## Testing strategy

Unit tests only, mirroring the modules they cover: `settings.py` -> `tests/test_settings.py`, and the `cli.py` config surface -> `tests/test_cli_config.py`, which is this repo's established per-verb split of the CLI tests rather than a
single `tests/test_cli.py`. Each test drives one function. **One departure, named:**
ac-013 lands in `tests/test_stack.py`, not in either of those, because that module
already owns real-`_harness_locator` coverage (`:493-535`) and its `_home_settings`
helper (`:497`) is the setup it needs; putting a `server.py` assertion in a
`settings.py` mirror would be the worse split. There is **no** `regressions/` example: the directory was deleted by operator decision and is not recreated, so every acceptance criterion is `type: code`.

`faked-seam-hides-broken-reader` applies. This change introduces no new test seam, and the CLI tests call `cli.main([...])` all the way through to the real `settings.set_harness_source` / `remove_harness_source` and assert against the file on disk. The one spy in the suite is in the unrecognised-action test, whose whole point is asserting a function is **not** reached.

**New — `tests/test_settings.py::TestHarnessSourceEdit`** (beside `TestSettingsEdit`, `:104-157`):

- happy path: `set_harness_source(path, name="mine", owner="acme", repo="harness", ref="main")` writes one four-key entry; the file round-trips through `load_settings` to one `HarnessSource`.
- upsert by name: a second call with `name="mine", ref="dev"` updates in place, leaves `owner`/`repo` as they were, and keeps the list length at 1.
- append order: a call with a new name appends **last**, leaving the existing first entry first.
- partial authoring: `set_harness_source(path, name="mine")` alone writes `{"name": "mine", "owner": "", "repo": "", "ref": ""}` and the file loads.
- edge — refusal writes nothing: `owner="acme/harness"` raises `SettingsError` and `user_settings_path()` does not exist afterwards.
- edge — the type owns the rules: a whitespace-carrying coordinate raises `SettingsError` whose text is `HarnessSource`'s own message.
- `remove_harness_source` drops the named entry and leaves the others in order; removing the last leaves `"harness": []` and a loadable file.
- edge — `remove_harness_source` on an absent name raises `SettingsError` naming the name; on a file with no `harness` key raises one naming the file.
- exports: both names are in `settings.__all__`, `remove_harness_source` immediately before `remove_value` and `set_harness_source` immediately before `set_value`. The list is grouped, not sorted; asserting sortedness would fail on the existing file.

**New — `get_value` and the messages** (`tests/test_settings.py`):

- `get_value(Settings().to_dict(), "harness.owner")` raises `SettingsError` naming `harness.owner`; same for `cacheDir.x` and `layers.x`.
- `get_value(data, "nope")` and `get_value(data, "sources.nope")` still return
  `None` — these are the cases the `part not in node` arm actually serves.
  `get_value(data, "cacheDir")` also still returns `None`, by the different route of
  an unset value.
- `_reject_object_list_write`'s message contains `molmcp config harness set` and no longer contains "by editing".

**Retired or rewritten:**

- `tests/test_settings.py:160-223` `TestHarnessWriteGuard` — **survives, docstring rewritten.** Its prose (`:169-172`) says the file "has to be hand-edited to make the install usable again" and "No CLI verb can undo that"; the second clause is what this spec falsifies, and the rewrite keeps the true half (a file that fails validation on read still needs an editor, because the new verb reads before it writes). The bare-key refusals (`:183-200`) and the `_OBJECT_LISTS` table assertion (`:180-181`) stay exactly as they are.
- `tests/test_settings.py:202-210` `test_set_refuses_every_dotted_harness_key_not_only_a_stray_one` — **rewritten** for the friendlier message, still asserting no file is created and still satisfying both parametrized cases (`"owner"`, a real field, and `"dev"`, a stray).
- `tests/test_settings.py:212-223` `test_remove_still_clears_the_key_and_leaves_a_loadable_file` — **kept as the boundary** between dropping the key and dropping one entry.
- `tests/test_cli_config.py:64-86` `test_list_prints_harness_as_an_array_of_entry_objects` — **rewritten** to author its fixture through `cli.main(["config", "harness", "set", ...])` instead of `write_settings_file`, and to drop the docstring sentence (`:72-73`) claiming no `config` verb can author a list of objects.
- `tests/test_cli_config.py:97-104` `test_get_an_unset_key_is_null_not_an_error` — **kept unchanged**, as the guard that keeps the `get_value` fix narrow.
- `tests/test_no_builtin_harness_source.py` — **untouched but constraining**: an install naming nothing must still resolve to `harness == ()`, so no coordinate may default to anything.
- `tests/test_harness_catalog_fixture.py` — **docstring-only**; `test_neither_page_names_the_retired_dotted_harness_key` (`:346-355`) keeps passing, so no doc edit may reintroduce `harness.owner`.

**New — `tests/test_cli_config.py::TestConfigHarness`:**

- `cli.main(["config", "harness", "set", "--name", "official", "--owner", "MolCrafts", "--repo", "harness", "--ref", "main"])` exits 0 and writes the user file.
- `--project` / `--local` land the same call in the project and local files respectively, and leave the user file empty.
- `--name` alone exits 0 and writes a name-only entry (partial authoring survives the CLI).
- `cli.main(["config", "harness", "remove", "--name", "official"])` exits 0 and empties the list; removing an unknown name exits 2 with a `molmcp:` message.
- `cli.main(["config", "get", "harness.owner"])` exits 2 (was: exit 0 printing `null`).
- **the trap, in two tests rather than one spy.** `config_action` is `required=True`
  with fixed choices (`cli.py:184`), so an unknown action cannot reach `_config`
  through `cli.main` at all — argparse exits 2 first. So: (i) a hand-built
  `Namespace` with an unhandled `config_action` passed straight to `cli._config`
  raises `ConfigurationError`; and (ii) a **structural** test that derives the
  registered action names from `_build_parser()` and asserts `_config` dispatches
  every one of them. Test (ii) is the one that catches the drift that matters — a
  new subparser landing without a branch — which a spy on `remove_value` cannot
  see. The spy is kept only as a secondary assertion.
- **the forward obligation:** for every member of `settings._OBJECT_LISTS`,
  `_build_parser()` registers a `config <member>` subparser. This is what keeps the
  derived `molmcp config {key} set` sentence truthful as the table grows.

Full-suite gate is `uv run pytest -v` plus `uv run ruff check src tests && uv run ruff format --check src tests`.

### Why this is one spec and not two

Ten tasks across nine files is large for this repo. It stays one link because every
thread is anchored to the same two functions being edited. The `get_value` split and
the two refusal messages are link 01's explicitly owed debt on this same key. The
bare-`else` closure is locality of change: this spec adds a branch to `_config`'s
chain, and the moment to remove a landmine from a function is while you are editing
that function — not on the honesty of "adding `harness` would trigger it", which the
Design retracts above. ac-014 exists because *this* spec introduces the derived
`molmcp config {key} {leaf}` sentence, so this spec owes the machinery that keeps it
truthful. ac-013 is the coverage this verb makes urgent by putting a previously
unreachable serve-time raise one command away.

## Out of scope

- **A `config harness list` / `get` verb.** `config list` already prints the array and `config get harness` already reads it; a third spelling would be a second contract.
- **Reordering entries (`--before` / `--after` / `move`).** Order is file order and appending is defined never to disturb it; reordering is an editor's job until something asks for it.
- **A dotted `harness.<name>.owner` write path.** `_resolve` refuses `len(parts) > 2` and `_parse` dispatches on the head key's type; re-opening that route is the write-before-validate hole `schema-type-flip-unlocks-writes` records.
- **Widening `add_value` / `remove_value` to address list elements by a member key.** That would change a string verb's contract for every list-valued setting to serve one key.
- **Any change to `server._HARNESS_KEYS` or serve-time completeness.** The verb writes half-filled entries on purpose; whether one can be fetched from stays a serve-time question.
- **Repairing an invalid settings file from the CLI.** The verb reads through `read_settings_file` like everything else, so a file that already fails validation still needs an editor.
- **A `regressions/` example.** The directory was deleted by operator decision and is not recreated.
