---
title: Ordered named harness sources
status: done
grilled: true
created: 2026-09-08
---

# Ordered named harness sources

## Summary

A molmcp install can today be pointed at exactly one harness repository, named by three flat settings (`harness.owner` / `harness.repo` / `harness.ref`). This spec replaces that single locator with an ordered list of **named harness sources**, so one install can name the official MolCrafts repository, a private one, and a project one at the same time, with an order that is written down rather than discovered. Nothing is fetched differently yet: an install that names no source serves exactly as it does now, an install that names one behaves as it does now, and the order of a list of several is the contract the later resolution link inherits.

## Design

### The type

`HarnessSource` is a frozen, slotted dataclass in `src/molmcp/settings.py`, beside `Settings`, with four string fields: `name`, `owner`, `repo`, `ref`. It follows the `ComponentSpec` construction template (`components/models.py:87-138`) — frozen slots, validation in `__post_init__`, no silent rewriting. It is a *new* type rather than a reuse of `ComponentSpec` because `components/` is a shared stdlib leaf admitted only when an inner layer needs it, and nothing in `discovery/` has any reason to know a harness source exists. It lives in `settings.py` and **not** in a new `components/sources.py` for the same reason. `settings.py` imports no molmcp module today and still imports none after this spec.

`__post_init__` is permissive about absence and strict about shape:

- `name` is required: non-empty after stripping, and containing no whitespace. **It is held to no stricter grammar than that**, deliberately. `settings.py:58` records that `sources` members are unvalidated because they are user-chosen names; a harness name is user-chosen in exactly the same way, and holding it to `^[a-z][a-z0-9-]*$` would mean an operator who names an index source `MolCrafts` succeeds while the same operator naming a harness source `MolCrafts` fails — `MolCrafts` being the literal string today's docs use. No `HARNESS_SOURCE_NAME_PATTERN` is introduced.
- `owner`, `repo`, `ref` default to `""` and may stay empty. An empty coordinate is a half-authored entry, not an error.
- A non-empty coordinate must be an opaque token: no whitespace, no `/`, no `@`. This is the guard that keeps a second parser out of the tree.

Validation raises plain `ValueError`. **No new error type.** `molmcp.discovery.source.resolver.SourceError(RuntimeError)` already exists and is exported from `discovery/__init__.py:47`, and `discovery/source/github.py:24` already imports `molmcp.components.git` — a second `SourceError` with a different base would meet the first inside one module. Where a settings *file* is at fault, the per-file validator catches that `ValueError` and re-raises the existing `SettingsError` (itself a `ValueError`) naming the file and the entry index.

### Why `name` is required and the coordinates are not

The coordinates arrive by separate commands, so demanding all of them at load time would make the first command fail on its own output. Under a list the completion address is the entry's `name` — it replaces the dotted key `harness.owner` as the place the remaining fields get filled in later — which is why `name` is the one field that cannot be deferred. This changes no existing file's meaning: under the old model there was no named entry at all, and the empty table and the empty list both read as "no harness configured".

### Two ways to describe a GitHub repository, on purpose

molmcp describes a GitHub repository two ways: the `github:owner/repo@ref` **string** for an index source, and a four-field **object** for a harness source. `discovery/source/github.py:36 _parse_github_spec` stays the only `owner/repo[@ref]` parser in the tree; `settings.py` splits nothing on `/` or `@`.

### The setting, and deliberately no merge channel

`_SCHEMA["harness"]` becomes `list`. `harness` leaves `_NESTED_SCHEMA` and `_MERGED_DICTS`, and **is added to nothing** — not `_MERGED_LISTS`, and no new channel is created. A new private helper called from `_reject_unknown` validates the list per file: a list of objects, each object's keys a subset of `_HARNESS_ENTRY_KEYS`, each entry constructed as a `HarnessSource` so the type's own rules are the only rules, and no two entries in one file sharing a name. Strays are reported by position, `harness[1].onwer`.

`_HARNESS_ENTRY_KEYS` is **derived** — `frozenset(f.name for f in dataclasses.fields(HarnessSource))` — not a hand-written literal. A hand-written one would silently reject a fifth field the day someone adds it to the dataclass, and the shape test would still pass.

Precedence needs no code at all. `settings_layers()` (`:164-170`) yields low->high and the existing default branch (`:188 merged[key] = value`) makes the last assignment win, so: **the effective list is the most specific layer's list, in file order; the first entry wins.**

What this gives up is **union across layers** — you cannot name the official source in your user file, add a team source in a project file, and get both. That is assigned to the spec that reads more than one source, because nothing in *this* spec reads more than one: `_harness_locator` raises on any incomplete entry and never skips, and `_activated_checkout` still binds a single store root.

**A warning, because two list settings twelve lines apart now behave oppositely.** `_MERGED_LISTS` members (`excludes`, `knowledgeScope`, `discoverInclude`, `discoverExclude`) `extend` low->high, so an entry in the *user* file survives a project file that also sets the key. `harness` does not: the *local* file's list replaces the user file's outright. The asymmetry is intended — `extend` on a first-wins list would land the user file's entries at the front and make the user file outrank the project file, the inverse of every other setting — but it is a real trap and is stated in the docs as well as here.

`Settings.harness` becomes `tuple[HarnessSource, ...]`, default `()`. `to_dict` emits a list of four-key objects.

**Anti-pattern, named:** this is deliberately *not* the shape of `settings.sources` — `dict[str, str]` (`settings.py:79`) merged by `dict.update` (`:183-184`), then re-sorted by `runtime.py:175` `sorted(config.sources.items())`, discarding insertion order outright. **Name collisions are not renamed** either: `config.py:233 _dedupe_source_name` resolves collisions by renaming (`name-2`), right for auto-discovered index sources nobody typed; a harness source is typed by hand, so a duplicated name inside one file is refused.

### The write guard the type change makes mandatory

Turning `_SCHEMA["harness"]` into `list` opens two CLI write paths that are safely refused today, and both write before anything validates:

- `molmcp config set harness x` -> `_parse` (`:334`) returns `["x"]`, a list of a bare string.
- `molmcp config add harness x` -> `add_value` (`:226`) now passes its `is list` guard and appends the bare string (`:232`).

Either one reaches `write_settings_file`. The per-entry validator then rejects `"x"` on the *next* read — and `read_settings_file` -> `_reject_unknown` (`:151`) sits under `load_settings`, hence under `config list`, `config get`, `config set`, `config remove` and `serve`, plus `source_scope.py:75`, `config.py:259`, `providers/molq/provider.py:86,323`, `providers/molexp/provider.py:40` and `scaffold.py:28`. `cli.py:679-688` turns every one of them into exit 2, and **no CLI verb can undo it**.

So the fact that stops both is **declared, not branched on**. `settings.py` already
states per-key behaviour in tables read by the generic verbs (`_NESTED_SCHEMA:61`,
`_MERGED_DICTS:71`, `_MERGED_LISTS:72`); this adds one more,
`_OBJECT_LISTS = ("harness",)` — the list settings whose elements are objects, which
the string-valued verbs cannot author. `set_value` and `add_value` each consult it
**at the top, before `_resolve` and before any write**, and raise `SettingsError`.
The general fact is "`harness` is the first list of objects", not "`harness` is
special", so the next such setting closes the same hole by joining the tuple rather
than by someone remembering to add a second branch. `_OBJECT_LISTS` is not a merge
channel and takes no part in `load_settings`; it has two consumers the day it lands. The message names the settings-file shape; it does not name a command, because a hint pointing at a name nothing resolves is the habit `tests/test_tool_hints.py` exists to prevent. The dotted forms need no guard: `_resolve` (`:296`) rejects `harness.owner` automatically once the schema type is no longer `dict`. `remove_value` needs no guard either — `remove_value(path, "harness")` clears the key and leaves a valid file, and `remove_value(path, "harness", "x")` already raises because `"x"` is not a member of a list of objects. This guard stays in this spec even though the editing verbs leave it: this spec is what opens the hole.

### Editing is deferred, the file format is documented

`set_harness_source` / `remove_harness_source`, a friendlier `_resolve` message for `harness.*`, and their tests **belong to `harness-evo-02-config-verb`**, where they acquire a CLI caller. Shipping a public editing API whose only callers are tests, in the same change that retires the working `molmcp config set harness.owner` path, would leave a CLI user instructed to import a Python function.

The migration cost of deferring is nil: `git show v0.6.1:src/molmcp/settings.py` contains no `harness` key, so **the harness setting has not shipped in any tagged release** and there is no installed base to strand. (Nine tags exist through `v0.6.1`; it is the setting that is unreleased, not the project.)

`docs/concepts/harness.md:213-228`, its cross-reference at `harness.md:310` ("where `harness.owner` / `repo` / `ref` live", which is rewritten rather than deleted so the `#settings` anchor stays alive) and `docs/get-started/installation.md:142` therefore stop showing the three `config set` lines — which exit 2 after this change — and show the **settings-file JSON shape** instead: a worked `~/.molmcp/settings.json` snippet whose `harness` value is a list of `{name, owner, repo, ref}` objects, with a note that the `molmcp config harness set|remove` verb is not available yet and arrives with the next link. A JSON example is the file-format contract, not a dangling command hint, and the snippet is pinned by parsing it and constructing a `HarnessSource` from each entry — the same discipline `tests/test_harness_catalog_fixture.py` already applies to `harness.example.toml`.

The snippet carries a recovery sentence, because the docs are now routing authoring through the one channel this spec argues is unrepairable by command: a mistyped entry (`"onwer"`) makes `config set`, `add`, `remove`, `list`, `get` and `serve` all exit 2 until the file is fixed **by editing that same file**. The property is pre-existing — `_NESTED_SCHEMA` behaves this way today — but making hand-editing the instructed path turns an accident into the main road, so it is stated where the reader is standing.

### Serve time

Completeness stays a serve-time `ConfigurationError`, raised by a generalized `server.py:516 _harness_locator` — the existing reader, not a second one, keeping both policies it owns, applied **per entry**: all-or-none completeness (`:545-554`) and no default (`:534-535`). Signature becomes `() -> tuple[HarnessSource, ...]`, where the empty tuple is the un-harnessed configuration.

- An entry with a `name` and no coordinates is an *error*, not an unset harness. The empty **list** means unset.
- The message names the entry as well as the missing fields.
- Skipping an incomplete entry and serving from the next is refused for the same reason a default is refused: it would serve code from a repository the operator did not select.

`create_stack`'s arm gate (`server.py:368`) changes from `is not None` to truthiness.

`_harness_locator` is the **sole behavioural reader** of `Settings.harness`: `load_settings(...).harness` occurs exactly once in `src/`, at `server.py:537`. The only other reader is `Settings.to_dict` (`settings.py:123`), which serializes it — and which ships in one of the two files this spec already moves, so the atomicity argument holds. `to_dict`'s output reaches the CLI at `cli.py:508` (`config list`) and `:512` (`config get`), so `molmcp config list` changes the `harness` value from a JSON object to a JSON array of objects; that is a user-visible output change and is pinned by its own criterion rather than left to be noticed. The type change and its one reader are therefore a single atomic edit, which is why `settings.py` and `server.py` move together rather than as two links.

### What this spec does not move

`_activated_checkout` (`server.py:558-605`) is untouched: one store root at `<cache>/harness` (`:590`, pinned by `tests/test_stack.py:532,560`) and one pointer at `<cache>/harness.pointer`. Per-source store roots are unnecessary because `components/store.py:109 ImmutableGitStore.publish(sha, *, owner, repo)` already writes provenance into `metadata.json` and raises `ShaConflictError` (`store.py:35`) when a SHA is claimed by a different repo. Cross-source collisions will be keyed by a `(source_name, component_id)` **pair at the resolution layer**; the namespace never enters `ComponentSpec.id`, which `components/models.py:104,125-127` pins to `f"{kind}.{name}"` behind `_MEMBER_PATTERN`. **`components/models.py` and `components/catalog.py` are not modified by this spec**, and a structural guard says so.

*Cosmetic debt, noted not fixed:* `_reject_unknown` outgrows its name once it also does type, shape, required-field and intra-file uniqueness validation. A rename is owed when this lands; nothing is restructured for it here.

### Migration

Changing `harness` from a table to a list is a breaking settings-format change, so the release carrying it bumps the minor version per the project's strict-SemVer rule. Any table value — populated or empty — is **rejected at load** with a message naming the list shape, rather than migrated by inventing a `name`. One rule, one message, and no file in any installed base to strand.

### Reuse decision

- `server.py:516 _harness_locator` — **generalize.** One reader, "for each entry", both policies verbatim. No second reader.
- `server.py:85-88 _HARNESS_KEYS` — **reuse**, unchanged, as the per-entry completeness tuple. Stays distinct from the derived `_HARNESS_ENTRY_KEYS` (which includes `name`) for the same reason `SUPPORTED_CAPABILITIES` is not `ALLOWED_REQUIRES` (`server.py:78-82`): one is what may be written, the other what must be filled.
- `settings.py _reject_unknown` / `load_settings` / `Settings` / `to_dict` / `set_value` / `add_value` — **reuse**, extended in place; the per-entry validator is a helper *called from* `_reject_unknown`, not a parallel pass.
- `settings.py` merge machinery (`_MERGED_DICTS`, `_MERGED_LISTS`, `:188` default branch) — **reuse by not extending.** No new channel; the existing default branch already yields the precedence wanted.
- `settings.py:79 sources` name policy — **reuse as precedent**: user-chosen names are not grammar-checked.
- `discovery/source/github.py:36 _parse_github_spec` — **reuse by not competing.**
- `discovery SourceError` — **reuse by not competing.** No new error type.
- `components/store.py ImmutableGitStore.publish` / `ShaConflictError` — **reuse**, uncalled and unchanged here.
- `components/models.py ComponentSpec` — **pattern only**, copied in construction shape (frozen slots, `__post_init__`). `COMPONENT_NAME_PATTERN` is deliberately *not* mirrored. `components/` must not learn about settings.
- `tests/test_no_env_switches.py` — **pattern only**, copied as the housing for a repo-wide structural guard.
- `config.py:233 _dedupe_source_name` — **not reused**: renames, which is wrong for a name an operator chose.

## Files to create or modify

- `src/molmcp/settings.py`
- `src/molmcp/server.py`
- `tests/test_settings.py`
- `tests/test_stack.py`
- `tests/test_no_builtin_harness_source.py` (new)
- `tests/test_cli_config.py`
- `tests/test_harness_catalog_fixture.py`
- `docs/concepts/harness.md`
- `docs/get-started/installation.md`

## Tasks

- [x] Write failing unit tests for `HarnessSource` and the list-valued `harness` setting (tests/test_settings.py -> `TestHarnessSource`, `TestSettingsHarnessSources`)
- [x] Write a failing unit test for the `config list` harness array shape (tests/test_cli_config.py), red until `to_dict` emits a list
- [x] Implement `HarnessSource`, the derived `_HARNESS_ENTRY_KEYS`, the `list` schema entry and the per-file entry validator in `src/molmcp/settings.py`, with Google-style docstrings
- [x] Write failing unit tests for the bare-`harness` write guard on `set_value` and `add_value` (tests/test_settings.py -> `TestHarnessWriteGuard`)
- [x] Implement the bare-key guard at the top of `set_value` and `add_value` in `src/molmcp/settings.py`
- [x] Write failing unit tests for the multi-source serve-time locator in tests/test_stack.py (retire `_LOCATOR`, update the `_wire` seam at `:301`)
- [x] Generalize `_harness_locator` to every named source in `src/molmcp/server.py` and switch the `create_stack` arm gate at `:368` to truthiness
- [x] Write structural guard tests for no built-in source and the untouched components layer in tests/test_no_builtin_harness_source.py
- [x] Update `docs/concepts/harness.md` and `docs/get-started/installation.md` to the settings-file JSON shape and pin the snippet in tests/test_harness_catalog_fixture.py
- [x] Run full check + test suite

## Testing strategy

Unit tests only, one function or method per test, no e2e under `tests/`. Paths mirror `src/` (`src/molmcp/settings.py` -> `tests/test_settings.py`); types mirror (`HarnessSource` -> `TestHarnessSource`). Two deviations, both named deliberately:

1. `src/molmcp/server.py`'s harness arms are tested in `tests/test_stack.py`, not a new `tests/test_server.py` — the `_wire` fake-seam scaffolding, `_LOCATOR`, and the partial-locator parametrize all live there already and all change together.
2. The tree-wide structural guard gets its **own module**, `tests/test_no_builtin_harness_source.py`, rather than riding in `tests/test_settings.py`. It parses every module under `src/molmcp/` and reads the text of `components/models.py` and `components/catalog.py`, which is not `settings.py` behaviour and would break the mirroring rule. `tests/test_no_env_switches.py` is the repo's existing pattern for exactly this — a repo-wide structural assertion in a module of its own, cited by `CLAUDE.md` § Configuration — and the new module copies its shape (`SRC` root, `rglob("*.py")`, parametrized `ast.parse`).

**Retirements in `tests/test_settings.py`.** Six live tests assert the model this
spec replaces, and they are named here for the same reason `test_stack.py`'s are —
so an implementer retires exactly these and no more. In `TestSettingsHarness`
(`:195-250`): `:204 test_harness_is_a_first_party_dict_setting` (asserts
`_SCHEMA["harness"] is dict`), `:207 test_harness_members_are_exactly_owner_repo_and_ref`
(pins `_NESTED_SCHEMA["harness"]`), `:210 test_harness_layers_merge_rather_than_replacing_one_another`
(asserts `"harness" in _MERGED_DICTS` — the exact inverse of the new behaviour, name
included) and `:223 test_a_partial_harness_table_is_stored_not_rejected` are
**deleted**; `:231 test_a_stray_harness_member_is_rejected_by_name` is **rewritten**
for the indexed message (`harness[0].<member>`); `:239` and `:243` are **kept
untouched**. The class docstring at `:196-203` is **rewritten** with the
class: it states the retired model verbatim ("``owner`` / ``repo`` / ``ref``, no
more") and justifies load-time permissiveness by naming `molmcp config set
harness.owner` — a command this spec retires. A docstring asserting the old contract
is a stale claim, not decoration. In `TestSettingsEdit`: `:166 test_set_harness_owner_repo_and_ref_round_trip`
is **deleted** (it drives the three retired `config set` calls) and
`:184-192 test_set_rejects_a_harness_member_outside_the_locator` is **rewritten** into
`TestHarnessWriteGuard`, where `_resolve` now rejects every `harness.*` key rather
than only a stray member. `TestNestedSchemaFirstParty` (`:252-275`) touches only
`molq` / `molexp` and is **not** affected.

**`tests/test_settings.py` — `TestHarnessSource`:**

- A four-field entry round-trips through construction with every field preserved.
- `name` alone constructs; `owner` / `repo` / `ref` default to `""`.
- An empty, whitespace-only, or whitespace-bearing `name` raises `ValueError`.
- `HarnessSource(name="MolCrafts")` constructs — a mixed-case name is as legal as a mixed-case `sources` key.
- A coordinate containing `/`, `@`, or whitespace raises `ValueError` (parametrized over `"acme/harness"`, `"acme@main"`, `"acme harness"`).

**`tests/test_settings.py` — `TestSettingsHarnessSources`:**

- `_SCHEMA["harness"] is list`; `"harness"` in neither `_MERGED_DICTS` nor `_MERGED_LISTS` nor `_NESTED_SCHEMA`; the module defines no `_PREPENDED_LISTS`.
- `_HARNESS_ENTRY_KEYS == {f.name for f in dataclasses.fields(HarnessSource)}`.
- Two entries in one user file load in file order.
- A one-entry user list and a different one-entry local list load as **the local list only**.
- Alongside it, `excludes` set in both files loads as both, pinning the opposite layer behaviour on purpose.
- Two entries sharing a `name` in one file raise `SettingsError` naming it.
- A half-authored entry (`name` + `owner` only) loads and is stored unchanged.
- A stray member is rejected by indexed name (parametrized over `dev`, `cacheDir`, `token`, `daily`, `telemetry` -> `harness[0].<member>`).
- An entry with no `name` raises `SettingsError`.
- A legacy `{"harness": {"owner": ...}}` table, and a bare `{"harness": {}}`, each raise `SettingsError` naming the list shape.
- `to_dict()["harness"]` is a list of four-key objects; `Settings().harness == ()`.

**`tests/test_settings.py` — `TestHarnessWriteGuard`:**

- `set_value(path, "harness", "x")` raises `SettingsError` and `path` does not exist afterwards.
- `add_value(path, "harness", "x")` raises `SettingsError` and `path` does not exist afterwards.
- After both, `load_settings(root)` still returns `harness == ()` — the install is not bricked.
- `set_value(path, "harness.owner", "x")` raises `SettingsError` (from `_resolve`) and creates no file.
- `remove_value(path, "harness")` on a file holding a valid list clears the key and leaves a file `load_settings` accepts.

**`tests/test_no_builtin_harness_source.py`** (new module; structural guards, source read as data):

- `load_settings` over an empty settings tree returns `harness == ()`. **This is the primary assertion** that no official coordinate is built in.
- The text of `src/molmcp/components/models.py` and `src/molmcp/components/catalog.py` contains neither `HarnessSource` nor `harness_source`.
- `ComponentSpec(kind=SKILL, name="daily", id="mine:skill.daily", path="skills/daily.md")` still raises `CatalogError`.
- *Secondary lint only:* parsing every module under `src/molmcp/`, no `ast.Call` to `HarnessSource` carries a string-constant argument. This is a smoke alarm, not a proof — a module-level dict literal fed through the same path file data takes would slip past it, which is why the behavioural assertions above lead. A bare literal blocklist is deliberately not used: `"molcrafts"` is the core plane id and appears throughout `server.py` for unrelated reasons.

**`tests/test_stack.py`** (`_LOCATOR` at `:82` becomes `_SOURCE = HarnessSource(...)`; the `_wire` seam at `:301` becomes `Settings(harness=tuple(harness or ()))`; the fourteen `harness=_LOCATOR` call sites become `harness=(_SOURCE,)`).

**Two call sites pass a dict literal rather than `_LOCATOR`, so the phrase above does
not cover them and each must be converted by hand.** `:368`
(`test_dual_injection_never_consults_the_harness_locator`) passes
`harness={"owner": "molcrafts"}`; under the new seam `tuple({"owner": "molcrafts"})`
evaluates to `("owner",)` — a tuple of *strings* — so this one fails **silently**,
producing a nonsense locator instead of an error, and becomes `harness=(_SOURCE,)`.
`:383` (`test_unset_locator_serves_exactly_like_today`) passes `harness={}`, which
`tuple({} or ())` happens to render correctly as `()`; it still becomes `harness=()`
explicitly, and its docstring "Three keys unset" is rewritten to name the empty list,
because there are no longer three keys to leave unset.

Assertions:

- An empty tuple serves exactly like today: no bind, no catalog, no extras, entry points then `disable=`.
- Dual injection still never consults the locator.
- Two complete sources in one list: `_harness_locator()` returns both in file order and the stack still binds the single store root `<cache>/harness` once (the pins at `:532` / `:560` unchanged).
- A partial entry raises `ConfigurationError` whose message contains the entry's `name` **and** each missing field (replacing the parametrize at `:397-410`).
- A second entry that is incomplete raises even though the first is complete.
- An entry with a `name` and no coordinates raises rather than reading as unset.

**`tests/test_cli_config.py`:** one test in the existing `config list` class — a
settings file holding a one-entry harness list makes `molmcp config list` emit a
`"harness"` value that is a JSON **array** whose single element is an object with the
four entry keys. `:61 test_list_reports_the_resolved_settings_and_their_layers`
already parses that output with `json.loads(capsys...)`, so this joins an existing
idiom rather than introducing one.

**`tests/test_harness_catalog_fixture.py`:** `_CONCEPT` and `_INSTALLATION` no longer contain `harness.owner`; the `~/.molmcp/settings.json` block fenced in `_CONCEPT` parses as JSON, its `harness` value is a list, every entry's keys are a subset of `_HARNESS_ENTRY_KEYS`, and every entry constructs a `HarnessSource`.

## Out of scope

- **The `molmcp config harness set|remove` CLI verb, and the editing functions under it.** `set_harness_source`, `remove_harness_source`, the friendlier `harness.*` message in `_resolve`, and their tests all move to `harness-evo-02-config-verb`, where a CLI caller exists for them. Shipping them here would mean a public API whose only callers are tests. Until the verb lands, entries are authored by editing the settings file, whose shape the docs now spell out. Nothing is stranded: the setting has not shipped in any tagged release (verified against `v0.6.1`).
- **`config get harness.owner` answering `null`.** `get_value` (`:254-261`) walks dicts, so a dotted read against a list returns `None` at the first hop — a *wrong* answer rather than an absent one, while the two write paths get explicit messages. **Accepted debt, not dismissed:** `get_value` (`settings.py:254-261`) is an existing public function in a file this spec already opens, so the deferral is not "it belongs to the other spec" — it is that its dotted walk is generic, and changing it is a contract change for **every** setting, which this spec is not the place to make. It lands with `harness-evo-02-config-verb`, which owns the read half of the verb. The exposure is bounded because no document names the key after this spec.
- **Union of harness sources across settings layers.** The most specific layer's list wins whole. Union belongs to the spec that reads more than one source; nothing here does. The replace semantics ac-006 and the docs note pin are therefore a **revisable contract**, expected to be revisited by that spec — not a permanent guarantee.
- **Multi-source fetch and resolution.** Which source a commit is published from, the `(source_name, component_id)` collision key, and reading more than one catalog per serve. `_activated_checkout`, `_checkout_components`, `_checkout_planes`, `components/store.py` and `components/activate.py` unchanged.
- **Per-source store roots.** Explicitly refused; `ImmutableGitStore` provenance plus `ShaConflictError` already cover the case.
- **Reordering an existing list.** Order is file order; changing it means editing the file.
- **Renaming `_reject_unknown`.** Owed once it also validates type, shape, required fields and intra-file uniqueness. Cosmetic; nothing is restructured for it here.
- **Auto-migrating an existing `harness` table.** Rejected with a message naming the list shape instead.
- **Regression examples.** `regressions/` was deleted by operator decision. This spec adds none and does not recreate the directory; every criterion is `type: code`.
- **Explicit assumption, and a dependency owed by a later link.** Verified 2026-09-08: the real `MolCrafts/harness` repository is a Claude Code plugin marketplace repo — `.claude-plugin/marketplace.json` declaring one plugin `mol` at `./plugins/mol`, laid out as `plugins/mol/{agents,rules,skills}/...`. It has no `harness.toml` anywhere, while molmcp's components layer requires `harness.toml` at the checkout root with top-level `skills/` `agents/` `rules/` `providers/` `overlays/` prefixes (`KIND_PATH_PREFIX`). Neither holds today, so **no source configured today would actually resolve a component.** That does not block this spec — naming a source is not loading from one, and every test here is hermetic — but publishing a conforming `harness.toml` is a prerequisite for the resolution link.
