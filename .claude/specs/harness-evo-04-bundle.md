---
title: A component_root key so molmcp can load the real MolCrafts/harness layout
status: done
created: 2026-09-09
---

# A component_root key so molmcp can load the real MolCrafts/harness layout

## Summary

Links 01–03 built the whole road — settings name an ordered list of harness sources, a CLI verb authors them, and `molmcp serve` binds a pointer per source and folds their components first-wins. Nothing can drive on it, because the one repository the road was built for cannot be read. `MolCrafts/harness` carries no `harness.toml` at its checkout root, and its 55 components live under `plugins/mol/` because `.claude-plugin/marketplace.json` declares the live Claude Code plugin at `./plugins/mol` and cannot move. This link adds one optional top-level catalog key, `component_root`, and applies it in exactly one place — a new `ComponentFold.root_for(source)`.

**State the reachable outcome precisely, because the obvious claim is false.** After this link the real catalog **parses and folds**; it does not yet deliver anything. The two arms that consume a fold read `ComponentKind.OVERLAY` (`server.py:364`) and `ComponentKind.PROVIDER` (`server.py:390`) — verified, no other kind is folded anywhere in `src/` — while `MolCrafts/harness` is 28 skills, 19 agents, 8 rules and **zero** providers or overlays. So all 55 of its components are kinds no arm reads. The one live skill-delivery path is `host/install.py:163` `materialize_daily`, which reads `<source>/daily/skills/<name>/` off `molmcp init --source PATH`, holds no `HarnessCatalog`. `host/` is stdlib-only **today**, and no test enforces it — so `harness-evo-04b-materialize` must earn its injected seam on its own argument rather than inheriting a barrier that is a convention.

Giving skill / agent / rule components a consumer is **`harness-evo-04b-materialize`**, the next link: an injected seam letting `molmcp init` materialise them from an *activated* checkout — SHA-pinned and rollbackable — instead of only from a directory the operator points at by hand. That link needs `component_root` (the harness repo's skills live under `plugins/mol/skills/`), which is why this one comes first and why `root_for` is built now rather than invented there.

## Design

### The one new key

`harness.toml` gains an optional top-level `component_root`, a tree-relative POSIX directory every component `path` in that catalog resolves under. `_TOP_LEVEL_KEYS` (`catalog.py:24`) becomes `frozenset({"requires", "component", "component_root"})` — purely additive; nothing that loads today stops loading.

```toml
component_root = "plugins/mol"

[[component]]
kind = "skill"
name = "spec"
path = "skills/spec/SKILL.md"
```

`HarnessCatalog` gains `component_root: str = ""`, declared **last** because `HarnessCatalog`'s four existing fields (`sha`, `requires`, `components`, `bundles`, `catalog.py:74-77`) carry no defaults, and a defaulted field cannot precede them. Keyword construction itself is order-independent. `""` means "the tree itself", which is what every catalog in the repo has today.

### Why the path is not rewritten

`ComponentSpec.__post_init__` re-runs `_validate_component_path` on whatever lands in `path`, and both `dataclasses.replace()` and direct construction re-enter it:

```
dataclasses.replace(spec, path="plugins/mol/" + spec.path)
  → CatalogError: path must start with 'skills/' and continue
```

There is no "validate before the rewrite" seam, and creating one would weaken the prefix rule this spec exists to preserve. It would also break the five exact-path assertions at `tests/test_components/test_models.py:154,165,176,188,200` (verified: those lines are `assert spec.path == "skills/daily/SKILL.md"` and its four siblings, not helper calls). The type's stated rule is at `models.py:92-93`: construction rejects bad values, it does not rewrite them. `KIND_PATH_PREFIX` therefore validates paths **unchanged**, and `component_root` is carried beside them, never folded into them.

### The collision is renamed away, not documented away

`load_harness_catalog(root, sha, capabilities)` already has a parameter called `root` meaning *the directory the catalog file sits in*, and returns a catalog whose new field would mean *the directory the components sit in* — same signature, same return value, opposite senses. `harness.pointer_path(root, …)` is a third (the cache root) and `ImmutableGitStore(root=…)` a fourth. Documenting the difference is the remedy `notes.md:facade-symbol-collision` explicitly rejects: 撞名不是实现细节,是两个概念抢一个词,必须在 spec 阶段解决.

**Two renames, both at spec stage:**

- The catalog field and TOML key are **`component_root`**, never bare `root`.
- `load_harness_catalog`'s first parameter is renamed **`tree`**, matching `Checkout.tree`, which is exactly what every caller passes.
- **The two receiving parameters are renamed `base`**, because both stop receiving a tree the moment `root_for` is threaded through them. `_import_root(tree, path)` (defined `harness.py:466`; its Args line at `:484` reads "Root of the activated checkout") and `_session_capability_overlays(seeds, tree_path)` (`runtime.py:42`, documented "the seed's `path` inside `tree_path`") both stop receiving a tree the moment `root_for` is threaded through them: they receive `tree / component_root`. Both parameters become `base`, and both docstrings say "the directory this source's component paths resolve under" instead of naming a checkout. `src/molmcp/runtime.py` is therefore **in** the Files list and in the task that swaps the overlay arm — link 03 listed it for exactly this class of stale cross-reference.

`ComponentFold.root_for` returns the join of the two and is the only place that join is spelled.

### The guard, and which half of it is load-bearing

A new module-private `_validate_component_root(value)` in `components/catalog.py` refuses `..` **and `.`** segments, absolute paths, backslashes, **and any value containing `:`** — raising `CatalogError` with the offending value in `repr`.

**Spell the absolute check as two clauses, not one.** `Path(value).is_absolute() or value.startswith("/")` — `_validate_component_path` (`models.py:184`) already carries exactly that pair. Deriving it as "`_sha_dir`'s shape minus the separator checks" is what leaves only `is_absolute()`, and `PureWindowsPath("/plugins").is_absolute()` is **False** while `PureWindowsPath("C:/store/tree") / "/plugins"` is `WindowsPath("C:/plugins")` — the same escape the colon check was added for, through a different door.

**The colon check is not decoration.** `component_root = "D:evil"` carries no `..`, holds no backslash, and `Path("D:evil").is_absolute()` is `False` on POSIX — so it passes the other three. But `PureWindowsPath("C:/store/tree") / "D:evil"` is `WindowsPath('D:evil')`: a drive letter on the *first* joined component resets the anchor and discards the base entirely, and the escaped base reaches `sys.path.insert` at `runtime.py:97,99`. `.github/workflows/ci.yml:20` runs `windows-latest`, so this is a live platform. `spec.path` is immune only because it is never the first component after the tree; `component_root` always is.

**`ImmutableGitStore._sha_dir` (`store.py:170-181`) and `harness.pointer_path` both refuse path separators**, because a SHA and a source name are interpolated as single segments. `component_root` is the opposite case — `plugins/mol` is two segments and must stay legal — so **the separator check is deliberately not carried over**. The `..`-segment and absolute-path checks are the ones that close the escape, and they are the same two `_validate_component_path` already relies on. State this, or a later "simplification" will restore the separator check and break the only layout this link exists to support.

The guard splits across two gates on purpose:

- `HarnessCatalog.__post_init__` validates the **value**, so `HarnessCatalog(component_root="../evil")` is unconstructible, exactly as an invalid `sha` is. It treats `""` as "no component_root", because at construction time a defaulted `""` and a written `""` are the same string.
- `load_harness_catalog` additionally refuses the **key present with an empty value** (`component_root = ""`), the only gate that can still see presence.
- Segment-level refusal covers `"."` alongside `".."`, in one predicate over `value.split("/")` — **not** `PurePath.parts`, which silently drops `.` and collapses `//` and would therefore miss the case. Without it `"."` passes every other check and `Path("/store/tree") / "."` is `/store/tree`, a second spelling of `""`. Empty segments are **not** refused: `"plugins/mol/"` and `"plugins//mol"` both collapse to `tree/plugins/mol` in pathlib and escape nothing, so refusing them would be a knob with no pressure behind it.

### One application point: `ComponentFold.root_for`

There are exactly three sites where a tree is joined to a catalog-declared path, and no fourth:

| site | arm |
|---|---|
| `harness.py` `load_harness_catalog(checkout.tree, …)` | the catalog file itself — **unchanged**, `component_root` does not move it |
| `harness.py` `checkout_planes` -> `_import_root(checkout.tree, spec.path)` | provider |
| `server.py:369` -> `_session_capability_overlays(specs, checkout.tree)`, joined at `runtime.py:97` | overlay |

Sites 2 and 3 are both downstream of `fold_components`, which already reads every catalog **and** holds the checkouts. So `ComponentFold` gains:

- a field `component_roots: tuple[tuple[str, str], ...]` — the **raw strings**, not joined paths, **no default**, plus a `__post_init__` asserting two things: the source names of `checkouts` are **unique** and **exactly** those of `component_roots`, which are themselves unique (a duplicate passes set equality, and `root_for`'s linear scan would then answer with the first silently).

  Storing the string rather than `tree / component_root` is what keeps this from being the parallel `source -> Path` map `ComponentFold`'s own docstring argues against: `tree` already lives on the `Checkout` objects the fold carries, so a stored join would be a second copy of a fact the object already holds, and the whole invariant would exist only to police the agreement between two copies. With the string stored and the join performed inside `root_for` against that source's own `Checkout.tree`, "the base belongs to the right tree" is a theorem rather than an assertion — there is no other tree `root_for` could reach. The checkout-side half of the uniqueness clause is what makes that a theorem rather than an assumption: two `Checkout`s sharing one `source` with different trees would satisfy set equality and component_roots-side uniqueness while `root_for`'s scan answered with the first tree silently. `activated_checkouts` already refuses duplicate names, but `ComponentFold` is directly constructible and ac-009 mandates exactly that.

  This is deliberately *not* symmetric with the paragraph below that declines to re-validate the `component_root` string, and the distinguishing fact is worth stating: the **correspondence** between the two collections has no other owner anywhere, whereas the **value rule** has one — `HarnessCatalog.__post_init__`. A guard owns what nothing else owns.

  One collection of `(checkout, component_root)` pairs was considered and rejected: it would make four of the five disagreeing constructions unrepresentable and leave only source-name uniqueness to assert, which is the smaller shape. It is refused because `fold.checkouts` has two consumers that want the checkouts alone (`server.py:367`, `harness.py:461`), and pairing them would push a `.checkout` accessor into both. The cost of the rejected shape is one attribute hop at two call sites; the cost of the chosen one is the invariant and its five tests — recorded here so the next reader sees it was weighed rather than defaulted into.
- `root_for(source) -> Path`, a linear scan symmetric with `specs_from(source)`, raising `CatalogError(f"unknown-source: {source!r}")` — the `unknown-id` / `unknown-bundle` register `HarnessCatalog.get` and `get_bundle` already use. This makes `root_for` that type's **first raiser outside the components package and outside a catalog object**, so two docstrings must be restated with it rather than left to disagree: `CatalogError`'s own (`models.py:21-27`, which enumerates its raisers as the language gate and the eligibility check) and `create_stack`'s public `Raises:` block (`server.py:327-332`, which today tells callers it means a `harness.toml` failed the grammar or asked for an unimplemented capability). The alternative — a `molmcp.harness` `ValueError` subclass — is refused because the register genuinely matches and a second error family for one message would be the cost.
  `__post_init__` raises the same `CatalogError`, so the five construction tests each name one type. It deliberately does **not** copy `specs_from`'s "unknown source is not an error" tolerance: there is no empty `Path` a caller could use, and a wrong base is the exact half-applied failure this design prevents.

`root_for` returns `checkout.tree / component_root if component_root else checkout.tree`, so a rootless catalog yields the tree object itself and today's installs resolve byte-identical paths — no `.` component, no trailing separator.

Both arms swap `checkout.tree` for the fold's answer and keep `base / spec.path` **verbatim**: `_import_root(fold.root_for(checkout.source), spec.path)` in `checkout_planes`, and `overlay_fold.root_for(checkout.source)` as the second argument at `server.py:369`. Neither `_import_root` nor `_session_capability_overlays` learns that `component_root` exists; both keep taking a base directory and knowing nothing about where it came from.

**Why this shape:** the failure it exists to kill is `component_root` applied in one arm and forgotten in the other — half a harness, far harder to diagnose than one that resolves nothing, because the install looks like it works. `root_for` removes the second application site; the set-equality-plus-uniqueness invariant makes a fold whose sources disagree with its checkouts unconstructible; and storing the string rather than the join removes the third failure mode by construction rather than by assertion.

`ComponentFold`'s docstring currently argues against carrying a parallel `source -> tree` map, and that argument still holds for `tree`, which lives on the `Checkout` objects the fold already carries. the `component_root` **string** lives on no object the fold carries — `activated_checkouts` documents that it reads no catalog, and giving `Checkout` a `component_root` field would force it to — so recording the string is the fold's own new datum, not a duplicate. Recording the *joined path* would have been the duplicate, which is why it is not stored. The fold also does **not** re-validate the string: `HarnessCatalog.__post_init__` is that value's one home, `fold_components` is the only production populator and always reads a loaded catalog, and a second guard here would be a second owner of the same rule. The docstring must say both halves, or the next reader will read the new field as the thing the old paragraph forbids.

`Checkout.tree` keeps its contract, "where `harness.toml` sits". Folding `component_root` into it would move the catalog file too.

### The `evo` bundle is deferred

`evo` is **not** declared here. `HarnessCatalog.bundles`, `resolve_bundle`, `get_bundle` and `ResolvedBundle` have **zero** production consumers — verified; the only `resolve_bundle*` hits in `src/` are `host.resolve_bundle_source`, an unrelated function — and both serving arms filter `catalog.components` by kind. Declaring a third bundle here would spend criteria proving properties of a subsystem nothing reads, in a link whose subject is a path key. It belongs to the first link that actually reads a bundle.

One constraint recorded now so that link does not rediscover it: `_assert_eligible` (`catalog.py:294-304`) unions catalog-level `requires` with **every** bundle's `requires` before comparing against the process's capabilities. A `requires` on one bundle therefore makes the **whole catalog** ineligible for an install that cannot honour it — eligibility is not scoped per bundle, and there is no per-bundle eligibility anywhere.

### Two consequences written down, not discovered

**No version marker, and one must not be added.** `tests/test_components/test_catalog.py:425-426` `test_rejects_identity_top_level_field` is parametrized over `["sha","version","tag","release","id"]` and asserts each stops the file loading. Identity is the commit SHA the caller supplies, and a file stating its own version could disagree with the tree it sits in.

**A `component_root`-bearing catalog fails to load on every already-released molmcp**, with `unknown field(s) in harness.toml: component_root`. `_reject_unknown` runs at `catalog.py:224` and raises before any later line executes, so `requires` — parsed at `:225` — cannot gate the new key. That is fail-closed and correct. **State it as a requirement, not only a consequence: 0.7.0 must be released before the harness repository publishes the key.** Nothing enforces the ordering from this repo, and `fold_components` fails the whole serve rather than skipping an unreadable catalog. The blast radius is near-zero today for a second reason worth recording — there is no caller of `Activation.stage`, `promote`, `rollback` or `store.publish` anywhere in `src/`, so no product command can activate a harness source at all; only a hand-written pointer file can.

### The other repository

`MolCrafts/harness` gains a `harness.toml` with `component_root = "plugins/mol"` and 55 component rows (28 skills at `skills/<name>/SKILL.md`, 19 agents at `agents/<name>.md`, 8 rules at `rules/<name>.md` — all 55 names already match `COMPONENT_NAME_PATTERN`), plus the `daily` and `dev` bundles the grammar requires. It is the last drafting task below, and it lands in a different repository, so **this suite cannot verify it**; what stands in for it is a fixture loaded through the real `load_harness_catalog`.

### Reuse decision

- **reuse** `_require_string` / `_reject_unknown` (`catalog.py:307-329`) — `component_root` is parsed with the same helpers as every other scalar key.
- **reuse** the `unknown-<thing>: {value!r}` register of `HarnessCatalog.get` / `get_bundle` — `root_for`'s refusal reads like its neighbours.
- **pattern** `ComponentFold.specs_from` — `root_for` copies its signature shape and per-source scan, not its tolerance of an unknown source: there is no empty `Path` a caller could use, and a wrong base is the half-applied failure this design prevents.
- **pattern** `ImmutableGitStore._sha_dir` (`store.py:170-181`), already borrowed by `pointer_path` — `_validate_component_root` takes its inline-refusal shape **minus the separator checks** (`plugins/mol` is two segments and must stay legal), **plus a colon check** the borrowed guard never needed, **and keeping `_validate_component_path`'s two-clause absolute test** `Path(value).is_absolute() or value.startswith("/")` — dropping the separator clauses is exactly what would otherwise reduce the absolute test to one clause that answers `False` for `"/plugins"` off-Windows.
- **new** `_validate_component_root` — neither existing guard fits: `_validate_component_path` mandates a kind prefix this must not have, and `pointer_path` refuses the separator this requires.
- **new** `ComponentFold.component_roots` / `root_for` with the `__post_init__` invariant.
- **untouched** `host/install.py` — its consumer arrives in `harness-evo-04b-materialize`.
- **not touched** `.claude/notes/architecture.md` — `/mol:map` writes the blueprint; a hand-edit here would give it a second writer.

## Files to create or modify

- `src/molmcp/components/catalog.py`
- `src/molmcp/runtime.py`
- `src/molmcp/harness.py`
- `src/molmcp/server.py`
- `docs/concepts/harness.example.toml`
- `docs/concepts/harness.md`
- `pyproject.toml`
- `.claude/notes/notes.md`
- `tests/test_components/test_catalog.py`
- `tests/test_harness.py`
- `tests/test_stack.py`
- `tests/test_harness_catalog_fixture.py`

The `MolCrafts/harness` catalog is **not** in this list on purpose. `tests/test_harness_catalog_fixture.py:300-303` `test_example_lives_under_docs_and_not_at_the_repo_root` asserts `not (_ROOT / "harness.toml").exists()`, so a bare `harness.toml` entry here would have an implementer break a green test. It is the last drafting task below, in another repository.

## Tasks

- [x] Write failing unit tests for the `component_root` key in tests/test_components/test_catalog.py: it parses into the catalog; an absent key means `""`; component paths come out exactly as authored; the guard refuses all seven of `".."`, `"../evil"`, `"a/../b"`, `"."`, `"/plugins"`, `"plugins\mol"` and `"D:evil"` with the value in the message, through the loader **and** through direct construction; `"plugins/mol"` and `"plugins/mol/nested"` load; `component_root = ""` is refused as empty-when-present while an absent key is not; and `HARNESS_REPO_TOML` loads
- [x] Implement `component_root` in src/molmcp/components/catalog.py: the key in `_TOP_LEVEL_KEYS`, `HarnessCatalog.component_root: str = ""` declared last (the four existing fields carry no defaults, so a defaulted field cannot precede them), `_validate_component_root` called from `__post_init__`, the loader's empty-when-present refusal, and `load_harness_catalog`'s first parameter renamed `tree` — **with its docstring restated**: `catalog.py:200`'s Args entry still reads "root: Directory that contains `harness.toml`", and `HarnessCatalog`'s `Attributes:` block (`catalog.py:62-66`) lists all four current fields and must gain the fifth
- [x] Update the first of the two existing assertions these field additions turn red, neither of which is otherwise owned (the second is the `tests/test_harness.py:584` task below): `tests/test_components/test_catalog.py:312` `test_resolved_bundle_union_is_not_a_catalog_field` asserts `catalog_fields == ("sha", "requires", "components", "bundles")` and gains `"component_root"` **last** — its own subject, that the resolved-requires union is not a field, is untouched by the addition
- [x] Write failing unit tests for `ComponentFold.component_roots` and `root_for` in tests/test_harness.py: `_catalog_toml` gains the optional key; a rooted source answers `tree / "plugins" / "mol"` and a rootless one answers exactly `checkout.tree`; `root_for("nobody")` raises `unknown-source`; a two-source fold with one rooted and one not answers each with its own base; and the `__post_init__` refuses all **five** disagreeing constructions — omitting a source, misnaming one, supplying an extra, **duplicating one** (which set equality alone admits), and **two checkouts sharing a source name with different trees** (which roots-side uniqueness alone admits); plus `checkout_planes` over a real tree holding `plugins/mol/providers/demo/plane.py` yielding `tree/plugins/mol/providers/demo` — ac-010's load-bearing clause, the provider half of the half-a-harness property this link exists to make unreachable
- [x] Implement `ComponentFold.component_roots` (raw strings, no default) **declared second, between `checkouts` and `kept`** — all three fields are non-defaulted so any order is legal Python, and the tuple is asserted exactly; `root_for`, and the `__post_init__` asserting uniqueness on **both** sides plus set equality across them; populate them in `fold_components`; swap `checkout_planes` to `_import_root(fold.root_for(checkout.source), spec.path)`; rename `_import_root`'s first parameter `tree` -> `base` with its docstring restated; and **restate both no-parallel-map paragraphs** — `ComponentFold`'s own (`harness.py:141-145`) and the sharper one on `Checkout.source` (`:91-95`, "so that `source -> tree` has exactly one owner: `ComponentFold` carries these objects rather than a second mapping of the same fact"), whose distinguishing sentence is that `component_roots` is `source -> str`, not `source -> tree`, so the tree is still owned once so it says both halves — why the stored string is the fold's own datum, and why the join would have been the parallel map that paragraph already forbids
- [x] Update `tests/test_harness.py:584`, inside `test_a_contested_id_is_reported_once`, whose `tuple(f.name for f in dataclasses.fields(fold)) == ("checkouts", "kept")` becomes exactly `("checkouts", "component_roots", "kept")` — this is the assertion the new field breaks, **not** `test_the_fold_is_frozen_and_slotted` at `:604`, which asserts only frozen/slots/property and needs no change
- [x] Write a failing composition test in tests/test_stack.py recording the second argument `create_stack` hands `server._session_capability_overlays`, plus a rooted sibling of `test_worker_provider_path_is_the_import_root_directory`
- [x] Swap the overlay arm's base to `overlay_fold.root_for(checkout.source)` at src/molmcp/server.py:369, **and rename `_session_capability_overlays`' second parameter `tree_path` -> `base`** at src/molmcp/runtime.py:42, restating its docstring as the directory component paths resolve under rather than a checkout root, and updating the four-line comment at `server.py:360-363` directly above the changed line, which still explains the arm in terms of trees. Safe: `tests/test_runtime.py:309,323` call it positionally and no keyword `tree_path=` exists anywhere in `src/` or `tests/`
- [x] Publish the key: `component_root = "plugins/mol"` in docs/concepts/harness.example.toml, placed **beside `requires`, above the first `[[component]]`** (a bare key after a table header is a `TOMLDecodeError`), with `:30`'s comment — "`path` is relative to this file and must start with the directory the kind reserves" — **corrected, because the value makes its first clause false**; `plugins/mol` and not a neutral literal because that file's header records its publication as `repo MolCrafts/harness`, which is the repository whose layout the value describes. Plus the `requires`, `component_root` row in docs/concepts/harness.md's top-level key table, the older-install paragraph on the same page, the `_TOP_LEVEL_KEYS` re-spelling at tests/test_harness_catalog_fixture.py:72, three records in .claude/notes/notes.md (the `_assert_eligible` catalog-wide union, the deliberate absence of the separator check and why, and the release-ordering requirement — a spec is deleted on completion, so reasoning a later link needs must outlive it), and pyproject.toml to `0.7.0` followed by `uv sync --extra dev`, because tests/test_version_single_source.py compares the declared version against `importlib.metadata`
- [x] Retire the stale spellings the `load_harness_catalog` rename leaves behind: `_wire`'s **`load_harness_catalog` double** at tests/test_stack.py:419-425 — its `root: str | Path` parameter and its recorded `{"root": …}` key, with the assertion at `:1115`; the `immutable_git_store` double's `root` at `:414` **stays**, because it mirrors `ImmutableGitStore(root, transport)`, a public keyword this link does not touch. Plus the fenced `load_harness_catalog(tree_root, sha, supported_capabilities)` call at docs/concepts/harness.md:42, rewritten to the new spelling rather than deleted. After merge, run `/mol:map`: `.claude/notes/architecture.md:84` records `ComponentFold(checkouts, kept)` and `:149` records the public loader signature, and this spec deliberately does not hand-edit the blueprint
- [x] Draft harness.toml for the MolCrafts/harness repository (`component_root = "plugins/mol"`, 55 component rows, `daily` and `dev` bundles, no version marker) — lands in another repository, unverified by this suite, and must not be published before 0.7.0 is out
- [x] Write the three `inspect.signature` pins the renames otherwise have no home for — each rename's only pin, and each in the module that owns the symbol: `components.load_harness_catalog` reading `(tree, sha, supported_capabilities)` in tests/test_components/test_catalog.py beside the existing `test_supported_capabilities_has_no_default` (`:335`), `harness._import_root` reading `(base, path)` in tests/test_harness.py, and `runtime._session_capability_overlays` reading `(seeds, base)` in **tests/test_runtime.py** beside `TestSessionCapabilityOverlays` (`:302`), which owns that module's contract — a `test_stack.py` test going red because a runtime parameter was renamed would be choreography, not the owner's contract
- [x] Run the full gate: `rm -rf .ruff_cache && uv run ruff check src tests && uv run ruff format --check src tests && uv run pytest -v && uv run molmcp gate` — the cache removal and the gate are not in `mol_project.ci.local` and ac-014 requires both

## Testing strategy

Unit tests only, mirroring `src/`. No `regressions/` example: that directory was deleted by operator decision, so every criterion is `type: code`.

`faked-seam-hides-broken-reader` governs the split. `tests/test_stack.py`'s `_wire` fakes `load_harness_catalog`, so nothing it asserts is evidence that a `component_root`-bearing file parses at all. At least one test writes a real `harness.toml` carrying the key, loads it through the **real** loader, and resolves a real file on disk underneath.

**`tests/test_components/test_catalog.py`** — happy path, with the expected path literal written independently of the input per `golden-not-self-proving`; default `""` and keyword construction without the key; the guard parametrized over the same **seven** values the first task lists (`".."`, `"../evil"`, `"a/../b"`, `"."`, `"/plugins"`, `"plugins\mol"`, `"D:evil"`), each naming the value, through the loader *and* through direct construction because the value gate lives in `__post_init__`; `"plugins/mol"` and `"plugins/mol/nested"` accepted, pinning that the separator half is deliberately absent; empty-when-present refused while an absent key is not (two assertions — only their difference proves the presence check exists); `HARNESS_REPO_TOML`. That fixture's kind census is the real one — skills, agents and rules — plus one provider and one overlay row **to cover the loader's kind table**, not to exercise any arm; ac-005 builds no fold, and ac-010/ac-011 cover the arms elsewhere. It is deliberately not described as "the same shape" as the real file. `test_rejects_identity_top_level_field` unchanged. `test_resolved_bundle_union_is_not_a_catalog_field` (`:312`) **changes** — see the task that updates it.

**Deliberately untouched:** `tests/test_components/test_activate.py`'s independent copy of the canonical TOML needs no variant — `Activation.stage` (`activate.py:238-243`) loads a catalog for *eligibility* only and never resolves a component path. `tests/test_components/test_store.py:24` is content-free; `tests/test_components/test_models.py` is unaffected because paths stay as authored.

**`tests/test_harness.py`** — driven against real `harness.toml` files under `tmp_path`, as the whole file already is. The existing helpers are `_catalog_toml(specs)` (`:104`) and `_checkout(...)` (`:132`) — there is no `_write_catalog`. Then: rooted and rootless `root_for` (the latter asserted as path equality against the tree the test built, so a `.` component or trailing separator fails); `root_for("nobody")` raising `unknown-source`; **two sources, one rooted and one not, each answered with its own base**; the **five** disagreeing constructions the `ComponentFold` test task enumerates; `checkout_planes` over a real tree holding `plugins/mol/providers/demo/plane.py` yielding `tree/plugins/mol/providers/demo`. `test_a_contested_id_is_reported_once` (`:584`) **changes** — see the task that updates it; `test_the_fold_is_frozen_and_slotted` (`:604`) does not.

**`tests/test_stack.py`** — the overlay arm: record the second argument `create_stack` hands `server._session_capability_overlays` and assert it is `tree / "plugins" / "mol"`. Recording the caller's argument is the right unit assertion because the subject is `create_stack`'s composition; the real `_session_capability_overlays` is separately driven against a real tree in `tests/test_runtime.py`. Plus a rooted sibling of `test_worker_provider_path_is_the_import_root_directory`. `_catalog(...)` gains the keyword defaulted to `""` so every existing call site is unchanged. The `_wire` `load_harness_catalog` double changes — see the stale-spellings task.

**`tests/test_harness_catalog_fixture.py`** — `test_example_carries_every_key_the_page_names` asserts `set(example_table) == _TOP_LEVEL_KEYS` **exactly**, so the example file and the re-spelled constant must gain the key in the same commit. `test_consumed_filename_is_resolved_in_exactly_one_module` must stay `{"components/catalog.py"}`. `test_example_lives_under_docs_and_not_at_the_repo_root` (`:300-303`) and `test_contract_note_holds_the_two_rules_and_no_schema` both stay green untouched.

## Out of scope

- **Giving skill / agent / rule components a consumer.** That is `harness-evo-04b-materialize`, and it is the link that makes this one visible to a user. Until it lands, `component_root` makes the real catalog parse and fold and nothing more.
- **The `evo` bundle**, and making bundles do anything at all. Deferred to the first link that reads one.
- **The other "daily bundle":** `host/install.py:163-198` `materialize_daily` reads `<source>/daily/skills/<name>/` off a filesystem directory handed to `molmcp init --source PATH`, with no `HarnessCatalog`. Two unrelated notions that have never met; 04b connects them, this link does not.
- **Rewriting `ComponentSpec.path`, relaxing `KIND_PATH_PREFIX`, or a per-component path override.**
- **Moving `MolCrafts/harness`'s directories or touching `.claude-plugin/marketplace.json`.**
- **A version or schema marker in `harness.toml`**, and touching `.claude/notes/harness-contract.md`.
- **Any migration path for installs older than `0.7.0`.** Fail-closed is chosen; release ordering is the mitigation.
- **Per-component roots.** One per catalog.
