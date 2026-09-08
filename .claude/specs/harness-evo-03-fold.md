---
title: Fold several harness sources into one served checkout set
status: in-progress
created: 2026-09-08
---

# Fold several harness sources into one served checkout set

## Summary

`molmcp serve` reads every harness source the operator named, not just the fact that one exists. Today `server.py:374` consumes `_harness_locator()`'s ordered tuple as a boolean and then calls `_activated_checkout(plane_config)`, which binds a single pointer at `<cache>/harness.pointer` — so a second, third or tenth entry in the `harness` list changes nothing about what is served. After this link each named source gets its own activation pointer beside the one shared store, every activated source contributes its catalog's components, and components that collide across sources are resolved first-wins in file order, with every displaced entry reported rather than dropped silently. The four private harness arms move out of the 832-line `server.py` into a new `src/molmcp/harness.py`, leaving three thin call sites behind.

## Design

### Placement

A new module `src/molmcp/harness.py` at **L2**, beside `server.py` / `runtime.py` / `settings.py`. It sits on the **heavy** side of the child-safe import boundary by choice: it carries `from .provider_worker.worker import WorkerProvider` (`server.py:49`), so the FastMCP-bearing worker stack is a cost of importing it. `server.py` pays that today; a later link wanting `activated_checkouts` from a CLI `activate` verb inherits it, and should know that before reaching for it (`notes.md:worker-child-isolation`). It is not re-exported from `molmcp/__init__.py` — like `components/`, `helpers/` and `evolution/` it is reached by its owning layer only, so no `__all__` gains a name and the facade-collision rule does not bite.

Two placements are refused, both for reasons already written into the repo:

- **Not `components/`.** `tests/test_no_builtin_harness_source.py:69-75` forbids the names `HarnessSource` / `harness_source` in `components/models.py` and `components/catalog.py`, and its own failure message states the package-wide reason: "A harness source is a settings concept; components/ is a shared leaf that must not depend on it. Cross-source namespacing belongs to the resolution layer, keyed by a (source_name, component_id) pair, and never enters `ComponentSpec.id`." `discovery/source/github.py` imports `components.git`, so anything settings-flavoured inside `components/` drags settings toward L4. This spec builds exactly the `(source_name, component_id)` pair that message names, in exactly the layer it names.
- **Not `server.py`.** That file is **832 lines**, past this repo's 800-line ceiling, and its harness arms are all `_`-private, which is right for a composition root and wrong for a type later links must name. The forward-looking case is deliberately **not** made through `evolution/`: `evolution/evaluate.py` imports only stdlib, and the layer table classifies `evolution/` as a shared stdlib leaf, not a layer. A source-qualified `Challenger.component` must be a plain `str` pair encoded by its L2 caller, **never** an import from `harness.py`. The 800-line ground stands alone.

`tests/test_stack.py::test_server_module_imports_nothing_from_discovery` (line 722) walks `server.py`'s AST and asserts no imported module name contains `discovery` and that neither `DiscoveryConfig` nor `default_cache_dir` is imported. `harness.py` imports `resolved_cache_dir` from `molmcp.runtime` — the same shield `server.py` already uses — so `from .harness import ...` adds no discovery name to `server.py` and the test stays green untouched. The same AST assertion is extended to cover `harness.py` itself.

### Symbols in `src/molmcp/harness.py`

- `Checkout` — the moved `server._Checkout` (`server.py:96-106`), frozen slots, **gaining a third field `source: str`** beside `sha` and `tree`. All three consumers already take a `Checkout`, so one field reaches every one. Public in its new module; `server.py` imports it by name.
- `SourcedComponent` — `@dataclass(frozen=True, slots=True)` with `source: str` and `spec: ComponentSpec`. **`spec.id` is untouched.** `components/models.py:120-127` pins `id == f"{kind}.{name}"` and `_MEMBER_PATTERN` (`models.py:65`) admits only `^(skill|agent|rule|provider|overlay)\.[a-z][a-z0-9-]*$`, so a namespaced id is not constructible. The model is `collection/models.py:49-69` `SearchHit`, which keeps `source: str | None` as a field *beside* `ref` and never folds one into the other — this repo's existing answer to "the same id from two origins".
- `ComponentFold` — frozen slots result of one fold: `checkouts: tuple[Checkout, ...]` (the ones it was folded from, in source order), `kept: tuple[SourcedComponent, ...]` (source order, catalog order within a source), a `names` property returning `frozenset(sc.spec.name for sc in kept)`, and `specs_from(source: str) -> tuple[ComponentSpec, ...]`.
  It carries the `Checkout` **objects**, not a parallel `source -> tree` map: `Checkout` is already gaining `source` beside `tree` for exactly this, and a second mapping of the same fact would be a second owner. Both arms read the checkouts through the fold, so there is one owner used everywhere.
- `fold_components(checkouts, kind) -> ComponentFold` — reads each checkout's catalog and folds one kind. Keys on `spec.id` in source order via `setdefault`, the first-wins idiom already at `discovery/overlay/catalog.py:83` and `discovery/overlay/conventions.py:95`; `collection/index.py:450-462` is the same first-wins-across-ordered-channels discipline written with an explicit `seen` set. Every loser is logged once through `harness.py`'s own module logger, at warning level, in a message naming **the winning source, the losing source and the contested id** — the register of `_harness_locator`'s own message (`server.py:560-566`), which names the entry *and* every field it is missing.
- `activated_checkouts(config: AppConfig, sources) -> tuple[Checkout, ...]` — takes an **already resolved** `AppConfig`. `create_stack:377` resolves and passes `plane_config` for the reason its own comment at `:375-376` records, so `_resolve_config` stays `create_stack`'s job and `harness.py` gets no second copy. — the moved `_activated_checkout`, now plural. It takes the already-read sources as an argument and **never calls `_harness_locator` itself**; `tests/test_stack.py::test_the_locator_is_read_once_with_the_project_root` (line 739) asserts settings are read exactly once per `create_stack`.
- `checkout_planes(fold)` — the moved `_checkout_planes`, plural. **There is no separate `checkout_components`**: `fold_components` is the one folder and both arms call it. An earlier shape had both names for one concept with contradictory return types — that is the collision `facade-symbol-collision` says must be settled in the spec, not left to the implementer. `checkout_planes` takes **only** the fold: a fold built from a different checkout list would silently yield `()` from `specs_from(source)` — no plane, no error — so `ComponentFold` carries the `Checkout` objects it was folded from rather than the caller keeping two arguments in sync. It carries the checkouts themselves, not a parallel `source -> tree` map: `Checkout` already holds `source` beside `tree`, and a second mapping of that fact would be a second owner. `_import_root` moves too and stays module-private.
- `pointer_path(root, name) -> Path` — `<root>/harness.<name>.pointer`, guarded (below).

### Collision resolution, and what it is not

First-wins on `spec.id`, keyed in source order, and every loser is **reported** — logged, not stored. A `displaced` tuple was considered and dropped: nothing in production would read it, and this repo's own first-wins precedents (`discovery/overlay/catalog.py:83`, `conventions.py:95`, `collection/index.py:453-462`) drop losers without recording them. The warning earns its keep; a field whose only reader is a test does not. A module logger is well precedented — `server.py:57`, `provider.py:19`, `discovery/engine.py:31`, `middleware/path_safety.py:13`. For `ComponentKind.PROVIDER` an id collision *is* a plane-name collision, since `id == f"provider.{name}"`, so keying on the id closes the mount hazard: `server.py:405` builds `from_checkout = {worker.name for worker in workers}` and `server.py:435` calls `parent.mount(child, namespace=provider.name)`. Two sources shipping `provider.demo` would otherwise construct two `WorkerProvider(name="demo")` and mount twice under one namespace. **That name set becomes `fold.names`, an output of the fold, not a post-hoc set comprehension over the constructed workers.**

Three alternatives are rejected here so nobody re-opens them:

- **`config.py:233 _dedupe_source_name` is not reused.** It *renames* on collision (`name` -> `name-2`) and renames the **origin**, not an id within an origin. Applied here it would turn `provider.demo` into `provider.demo-2` — changing the plane id clients see and the namespace tools mount under, and producing a string `ComponentSpec.__post_init__` rejects anyway.
- **`HarnessCatalog.__post_init__`'s duplicate-id check is not relaxed.** `components/catalog.py:89-91` raises `CatalogError("duplicate component id")` *per catalog*, pinned by `tests/test_components/test_catalog.py::test_rejects_duplicate_component_ids` (line 238). The cross-source key must not be implemented by loosening it.
- **A hard error on cross-source collision is not the rule.** `collection/index.py:75` raises on a duplicate *origin* name and is the precedent for one hard error only: `activated_checkouts` refuses two `harness` entries sharing a `name`, because that would make two checkouts share one pointer file and make `specs_from(source)` ambiguous. **The comparison is `name.casefold()`, not exact equality.** `HarnessSource` deliberately permits `MolCrafts` casing, so `official` and `Official` pass an exact check — and on darwin (this repo's dev platform) and Windows they map to one pointer file, which is exactly the hazard this error exists to prevent.

### One store root, several pointers

`ImmutableGitStore(root=root / "harness")` and `GitHubTransport()` **stay shared, one of each.** `components/store.py:170-181` keys `_sha_dir` on the SHA alone, `git.py:39,55` take `(owner, repo)` per call, and `tests/test_stack.py::test_two_sources_still_bind_exactly_one_store_root` (line 468) already records the reason in its docstring: the store records provenance per SHA and refuses a SHA claimed by a second repository, so a second root would strand every already-published tree.

`Activation.bind(root / "harness.pointer")` is the chokepoint. `activate.py:23` checks `_POINTER_KEYS` with an exact set match and `_ActivationRecord` (`activate.py:58-62`) holds three `str | None` fields, so one record structurally cannot hold N sources. **The route taken is one pointer file per source.** `Activation.bind` (`activate.py:144-151`) accepts an arbitrary path and holds no opinion about it, and `_write_record` (`activate.py:103-113`) writes `<name>.partial` then `os.replace`, so each file is independently atomic. N binds against one shared store is therefore legal today with **zero changes to `activate.py`**: `ACTIVATION_VERSION` stays `1`, every test in `tests/test_components/test_activate.py` stays green unmodified, and rollback stays per-source. A version-2 record holding N sources is rejected: it would move that whole module, bump the on-disk version, and rewrite the activation suite to buy nothing this link needs.

### The path-traversal hole this spec closes

`HarnessSource.name` is validated only as a non-empty, whitespace-free string: `settings.py:152-159` puts the `/` and `@` rejection in an `elif` that explicitly excludes `name`, and the class docstring (`settings.py:109-115`) says so on purpose — an operator who may name an index source `MolCrafts` may name a harness source `MolCrafts`. So `HarnessSource(name="../../evil")` constructs today and a naive `<cache>/harness.{name}.pointer` is a traversal that writes outside the cache root.

`HarnessSource` is **not** tightened — that would reject settings files which load today. The guard is at the point of use, in `pointer_path`, reusing the shape already at `components/store.py:170-181`: reject empty, reject a reserved name (`_RESERVED_SHA_KEYS` there is `{".", "..", "refs", "pointers", "hints"}` — `harness.py` gets its own small reserved set covering `.` and `..`, kept for symmetry rather than because they traverse — embedded as `harness.{name}.pointer` neither is a path segment, and the separator and absolute checks do the real work; the docstring says so), reject `Path(name).is_absolute()`, `os.sep`, `/`, `\`, and `os.altsep` when it is not `None`. A rejected name raises `ConfigurationError` naming the source, so it surfaces the same way an incomplete source does.

### Reuse decision

| Verdict | Symbol | Why |
|---|---|---|
| `reuse` | `ImmutableGitStore` (`components/store.py:43`) | One root at `<cache>/harness`; SHA-keyed, provenance-checked. |
| `reuse` | `GitHubTransport` (`components/git.py:72`) | One instance; `(owner, repo)` are per-call. `__init__` stores a token and does no I/O, so a real one is safe in a unit test. |
| `reuse` | `Activation.bind` (`components/activate.py:144`) | Per-source path; the classmethod already accepts any path. |
| `reuse` | `load_harness_catalog` (`components/catalog.py:168`) | One call per checkout, same `SUPPORTED_CAPABILITIES` **object**. |
| `reuse` | `resolved_cache_dir` (`molmcp.runtime`) | The one owner of the unset-`cacheDir` fallback; keeps `harness.py` out of `discovery`. |
| `reuse` | `_session_capability_overlays` (`runtime.py:41`) | See the correction below. |
| `pattern` | `SearchHit` (`collection/models.py:49-69`) | `source` as a field beside the id — shape for `SourcedComponent`. |
| `pattern` | `discovery/overlay/catalog.py:83`, `conventions.py:95` | `setdefault` first-wins over an ordered stream. |
| `pattern` | `ImmutableGitStore._sha_dir` (`store.py:170-181`) | Segment guard for `pointer_path`. |
| `pattern` | `_harness_locator` message (`server.py:560-566`) | Error register: name the entry and the specifics. |
| `new` | `Checkout.source`, `SourcedComponent`, `ComponentFold`, `fold_components`, `pointer_path` | No existing symbol pairs an origin with a `ComponentSpec`, and no existing symbol folds several catalogs. |
| rejected | `_dedupe_source_name` (`config.py:233`) | Renames the origin, and would rewrite a plane id. |
| rejected | `HarnessCatalog.resolve_bundle` (`catalog.py:142-165`) | Zero production callers — verified by grep; the only hits are `host/install.py`'s unrelated `resolve_bundle_source` and `tests/test_components/test_catalog.py`. |
| rejected | version-2 `_ActivationRecord` | Moves `activate.py`, bumps `ACTIVATION_VERSION`, rewrites its suite. |

### Correction to the brief: the overlay arm needs per-checkout grouping

`runtime._session_capability_overlays(seeds, tree_path)` (`runtime.py:41-43`) takes **one** `tree_path`, and resolves each seed's import root as the parent of `tree_path / spec.path`. With N checkouts there are N trees, so `server.py:381-385` cannot pass a flat spec list. The overlay arm becomes one call per checkout, concatenated in source order, with each call handed `fold.specs_from(checkout.source)` — which is why `specs_from` exists on `ComponentFold` rather than the fold returning a bare tuple. **Both arms iterate `fold.checkouts`, not a separate `checkouts` local**, so `checkout_planes(fold)` is one argument and the overlay arm has no second source of truth either. The provider arm needs the same grouping for `_import_root(checkout.tree, spec.path)`, which the fold now supplies.

### The three call sites in `server.py`

- `:374` — `if (build_overlays or enumerate_planes) and (sources := _harness_locator()):` then `checkouts = activated_checkouts(plane_config, sources)`. This one line is where multi-source was lost.
- `:381-385` — extras concatenate one `_session_capability_overlays` call per checkout over the OVERLAY fold.
- `:404-405` — `fold = fold_components(checkouts, ComponentKind.PROVIDER)`, `workers = checkout_planes(fold)`, `from_checkout = fold.names`.

**`SUPPORTED_CAPABILITIES` moves to `harness.py`** and `server.py` imports it from there. It cannot stay at `server.py:83`: `activated_checkouts` (through `Activation.bind`) and `fold_components` (through `load_harness_catalog`) both consume it, neither signature takes it as a parameter, and `server.py` carries a module-level `from .harness import ...` — so importing `molmcp.server` would enter `harness.py` before line 83 binds the name. A function-local import would dodge the cycle and break the notes rule that function-local imports are for optional dependencies only. Moving the constant is the only arrangement that loads.

  `server.py` then holds **no** reference to it: its only two code uses (`:606`, `:641`) sit inside functions that move, and `:308` is a `:data:` docstring reference ruff does not count — so a re-import would be F401 under `select = ["E","F","I"]`. `server.py` therefore does not re-import it, and it is **not** added to `server.__all__` (that would give one constant two public homes). The consequence is named rather than wished away: `tests/test_stack.py:768,769,772,779,780` say `server.SUPPORTED_CAPABILITIES` today and **repoint to `molmcp.harness`**; those two tests join the modified list. It stays the **same object** on every call — `tests/test_stack.py::test_one_capability_object_reaches_bind_and_both_catalog_calls` (line 755) asserts identity (`is`), not equality. `_harness_locator` stays in `server.py`; `tests/test_stack.py` calls it directly at lines 465, 527, 535 and 568.

`create_stack`'s `Raises:` list (`server.py:346-362`) enumerates every exception the composition root raises and gains the two new `ConfigurationError` cases: a source name that cannot be a pointer file segment, and two `harness` entries sharing a name. The existing "unknown sha" `ConfigurationError` (`server.py:611-616`) gains the source name alongside the SHA and the store root.

## Files to create or modify

- `src/molmcp/harness.py` (new)
- `src/molmcp/server.py`
- `tests/test_harness.py` (new)
- `tests/test_stack.py`
- `src/molmcp/runtime.py` — two docstring cross-references. `:56-59` names `molmcp.server` / `_import_root` as "where the reason the two coexist is written down"; that symbol moves. `:119-121` (`resolved_cache_dir`) says "`molmcp.server` reads the root from this function precisely so that it need not import `molmcp.discovery`" and "Discovery has exactly two importers — this module and the CLI — and the harness wiring is not a third"; after the move the reader is `molmcp.harness`, and that sentence is the very shield ac-011's AST assertion protects. `server.py:686-687` carries the matching half.
- `.claude/notes/notes.md` — record that the cross-layer union is dropped and why; a spec is deleted on completion, so the reasoning must outlive it.
- `docs/concepts/harness.md`

## Tasks

The move comes **first**, as its own commit whose diff is a pure relocation with the
suite green — otherwise a reviewer cannot tell moved lines from changed ones, and the
traversal guard, a security fix, would be buried in the noise. Everything after it is
behaviour.

- [ ] Move `_Checkout`, `_activated_checkout`, `_checkout_components`, `_checkout_planes`, `_import_root` and `SUPPORTED_CAPABILITIES` from src/molmcp/server.py into a new src/molmcp/harness.py unchanged, repoint the five `_wire` seam targets and `tests/test_stack.py:768-780` to `molmcp.harness`, and rewrite the tests/test_stack.py:78-81 comment — no behaviour change, suite green, one commit
- [ ] Write failing unit tests for `pointer_path`, `SourcedComponent` and `fold_components` (tests/test_harness.py -> `TestPointerPath`, `TestSourcedComponent`, `TestFoldComponents`)
- [ ] Implement `Checkout`, `SourcedComponent`, `ComponentFold`, `fold_components` and `pointer_path` in src/molmcp/harness.py with Google-style docstrings stating the first-wins rule and the segment guard
- [ ] Write failing unit tests driving the real `activated_checkouts` against an on-disk store and hand-written per-source pointer files, with no `_wire` seam (tests/test_harness.py -> `TestActivatedCheckouts`)
- [ ] Repoint the `_wire` seam's **five** `monkeypatch.setattr` targets (`Activation`, `ImmutableGitStore`, `GitHubTransport`, `load_harness_catalog`, `WorkerProvider`) from `molmcp.server` to `molmcp.harness`, rewrite the tests/test_stack.py:78-81 comment whose single-composition-root reason stops being true, and extend the seam to a per-source `current` mapping with a second SHA constant beside `_SHA`
- [ ] Write failing multi-source composition tests in tests/test_stack.py (split `test_two_sources_still_bind_exactly_one_store_root`, per-source bind paths, N catalog reads, mixed activated/unactivated, two-source plane-name collision)
- [ ] Rewrite the moved functions as plural (`activated_checkouts`, `fold_components`, `checkout_planes`), add the per-source pointer and the legacy-pointer notice, and rewire the three `create_stack` arms, extending its `Raises:` list
- [ ] Update the resolution paragraph of docs/concepts/harness.md to name per-source pointers, the first-wins fold, and the shared store
- [ ] Run full check + test suite

## Testing strategy

Unit tests only, per `tests-owned-behavior`. `src/molmcp/harness.py` mirrors to `tests/test_harness.py`; `server.py`'s arms stay in `tests/test_stack.py` because the `_wire` seam lives there, as link 01 recorded. Green for one path is `uv run pytest <path> -v`. There is **no regression example**: `regressions/` was deleted by operator decision and is not recreated, so every acceptance criterion is `type: code`.

The rule `faked-seam-hides-broken-reader` (`.claude/notes/notes.md`, 2026-09-08) governs the split. `_wire` fakes `Activation`, `ImmutableGitStore`, `load_harness_catalog`, `WorkerProvider` and `load_settings`, so a `tests/test_stack.py` suite proves the composition order and nothing about the functions it fakes out. `TestActivatedCheckouts` therefore drives the **real** `activated_checkouts` against a real `ImmutableGitStore` and a real `Activation.bind` over `tmp_path`: SHA directories are planted by hand as `<root>/harness/commits/<sha>/` with a `metadata.json` file and a `tree/` directory (the layout `components/store.py:43-52` documents and `has` checks at `store.py:90-91`), and pointer files are written as literal version-1 JSON. Nothing fetches; `GitHubTransport.__init__` (`git.py:82-89`) stores a token and performs no I/O, and `publish` is never called.

### `tests/test_harness.py` (new)

- **`TestPointerPath`** — happy path `<cache>/harness.official.pointer`; two distinct names never map to one file; rejection cases `..`, `.`, `../../evil`, `a/b`, `a\b`, an absolute `/etc/passwd`, `""`, and a reserved name, each raising `ConfigurationError` whose message contains the offending source name.
- **`TestSourcedComponent`** — frozen and slotted; `spec.id` is carried unchanged (`"provider.demo"`, never `"official.provider.demo"`); assignment raises.
- **`TestFoldComponents`** — first-wins on `spec.id` in source order; the second source's `provider.demo` is reported, not kept; distinct ids from two sources are both kept in source order; `names` is the kept component-name set; `specs_from` returns only one source's kept specs in catalog order and `()` for an unknown source; the `caplog` warning names the winning source, the losing source and the contested id.
- **`TestActivatedCheckouts`** (real function) — two sources with two distinct pointer files and two distinct SHAs yield two `Checkout`s in file order, each carrying its own `source`; exactly one `commits/` directory exists under the cache root; a source whose pointer file is absent is skipped while its neighbour still yields a checkout; a pointer naming an unpublished SHA raises `ConfigurationError` containing both the SHA and the source name; two entries sharing a `name` raise `ConfigurationError` naming that name.
- **Boundary** — the AST scan pattern of `test_server_module_imports_nothing_from_discovery` applied to `harness.py`: no imported module name contains `discovery`.

### `tests/test_stack.py` (modified)

- **The four patch targets move first.** `tests/test_stack.py:348-353` patches **five** names on `molmcp.server` — `Activation`, `ImmutableGitStore`, `GitHubTransport` (`:348`, constructed at `server.py:602` inside `_activated_checkout`), `load_harness_catalog` and `WorkerProvider` — with the reason at `:78-81`. Missing `GitHubTransport` would leave the real one constructed (harmless, it does no I/O) and `test_named_store_and_pointer_hang_off_the_resolved_cache_root` failing on an empty `wiring.transports`. After the move `server.py` references none of them — ruff would strip the imports — so every one of those `monkeypatch.setattr` calls raises `AttributeError` and ~20 harness tests die at setup. They repoint to `molmcp.harness`; `load_settings`, `build_collection` and `discover_providers` stay on `molmcp.server`.
- `_wire` gains `currents: Mapping[str, str | None] | None`; `_ActivationSeam.bind` recovers the source name from the pointer path and answers per source, with the existing scalar `current=` preserved as "this SHA for every source" so the ~20 single-source call sites stay untouched. A second SHA constant joins `_SHA` at line 82.
- `test_two_sources_still_bind_exactly_one_store_root` (line 468) **splits**: the store half keeps its docstring and its `len(wiring.stores) == 1`; the bind half becomes one bind per source at `<cache>/harness.official.pointer` and `<cache>/harness.private.pointer`.
- `test_named_store_and_pointer_hang_off_the_resolved_cache_root` (line 673): `wiring.transports == [((), {})]` stays; the path and `store is` assertions become per-source.
- `test_unset_cache_dir_still_binds_under_the_resolved_default_root` (line 694): `len(wiring.stores) == 1` survives; the bind count and pointer path become per-source under the resolved default root.
- `test_one_capability_object_reaches_bind_and_both_catalog_calls` (line 755): `len(wiring.catalogs) == 2` becomes 2 x N; the `is`-identity loop generalizes untouched.
- Lines 623 and 644 (`len(wiring.catalogs) == 1`) become 1 x N.
- `test_absent_current_falls_back_without_resolving_or_promoting` (line 575) gains the interesting mixed case: one source with a `current` and one without — one bind per source, catalogs read only for the activated one.
- **New**: two sources both declaring `provider.demo` construct exactly one `WorkerProvider(name="demo")`, mount one `demo` namespace, and take the first source's spec, with the second reported.
- **New**: an entry-point plane named `demo` is still XORed out when the winning `demo` came from the second source — the folded name set, not the first catalog, decides.

### Existing coverage cited, not rewritten

- `tests/test_components/test_store.py:178-194` — the three `ShaConflictError` cases *are* the two-source SHA-provenance conflict, written before there were two sources.
- `tests/test_components/test_activate.py` — must stay green **unmodified**. That is the practical argument for the per-source-pointer route.
- `tests/test_components/test_catalog.py::test_rejects_duplicate_component_ids` (line 238) — per-catalog rejection stays per-catalog.
- `tests/test_settings.py::test_harness_is_a_list_setting_with_no_merge_channel` (line 606) and `test_the_most_specific_layer_replaces_the_list_rather_than_merging` (line 638) — unchanged; see Out of scope.
- `tests/test_no_builtin_harness_source.py` — unchanged; the new module is not under `components/`.
- `ImmutableGitStore.publish` has zero production callers (only `tests/test_components/test_store.py`); this link must avoid making it unreachable, and adds no caller.

## Out of scope

- **Cross-layer union of the `harness` list — dropped, not deferred.** Link 01 listed it as owed here, but link 01 also shipped the opposite as pinned behaviour: `tests/test_settings.py:606-611` (`harness` in no merge channel), `:638` (most specific layer replaces the list whole), `settings.py:84-89`, and `docs/concepts/harness.md:253-261` — the last build-enforced, since `tests/test_harness_catalog_fixture.py` parses that page's JSON through the real `HarnessSource`. "The most specific layer's list wins whole" is coherent and nothing in this link needs to break it.
- **Bundle merging across sources.** `HarnessCatalog.resolve_bundle` (`catalog.py:142-165`) is not wired in: it has zero production callers, the serve path (`server.py:641-642`) filters `catalog.components` by kind instead, and `_REQUIRED_BUNDLES.issubset` is enforced *per catalog* (`catalog.py:87-88`), so N sources means N `daily` bundles by construction. Note the ambiguity trap: `host/install.py:163-198 materialize_daily` reads `<source>/daily/skills/<name>/` off a filesystem directory with no `HarnessCatalog` in the path, and `host/` is stdlib-only and barred from importing `components` — so "merge the daily bundle across sources" names two unrelated things in this repo until it says which.
- **Migrating an existing `<cache>/harness.pointer` — not migrated, and on honest grounds.** An earlier rationale said such an install "serves unharnessed until it activates again". That names an action that does not exist: nothing in `src/` calls `Activation.stage`, `.promote` or `.rollback` (zero hits outside `components/activate.py`) and `cli.py` has no activate verb — link 02 added only `config harness set|remove`. The only production reader is `server.py:603`. There is no supported route back.
  The same fact is the real argument: **because nothing in the product ever writes that file, the affected population is very nearly empty.** That is a better ground than `stage: experimental`, and it is the one recorded.
  One cheap guard replaces a fallback, and its shape is stated because `Activation.bind` turns a missing file into an empty record and exposes no "the file existed" signal: once, before the per-source loop, `legacy.exists() and not any(pointer_path(root, s.name).exists() for s in sources)` — N+1 `Path.exists()` calls the function otherwise never makes. It is unambiguous because no source name can map to `harness.pointer` (the store root is a *directory* named `harness`). A half-migrated install — stale file plus one activated source — is deliberately **not** warned again; nothing in the product writes that file, so one notice at the point it can still matter is enough. Authority stays unambiguous because the legacy file is never read — the shape `CLAUDE.md`'s stranded-orphan rule asks for, and `discovery/engine.py:354` already demonstrates. Note too that keeping `ACTIVATION_VERSION = 1` buys version stability by moving the *filename* rather than the *content*: the on-disk contract does change, in the one place the version field cannot see it.
- **Tightening `HarnessSource.name`.** The ground is *not* "it would reject files that load today" — that is false, and worth saying so: both new `ConfigurationError`s reject settings files that load **and serve** today (`name="a/b"` is legal at `settings.py:152-159` and harmless right now because `name` never reaches a path; `official`/`Official` likewise serve fine under one pointer). The real distinction is that `molmcp config get|set|add|remove` must keep working on a file that `serve` refuses, so the operator can repair it with the verb link 02 shipped. The affected population is non-empty in principle.
- **Renaming colliding component ids** — `_dedupe_source_name`'s strategy is rejected in the Design.
- **A version-2 activation record** holding N sources.
- **Fetching or publishing at serve time.** Serving stays a read of the pointer; `publish`, `stage`, `promote` and `rollback` belong to the commands asked to change what is activated.
- **Per-source enable/disable or priority overrides** beyond file order.
- **Giving `evolution/evaluate.py`'s `Challenger.component` a source** — a later link in this chain.
