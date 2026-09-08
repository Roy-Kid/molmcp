---
slug: harness-evo-03-fold
criteria:
  - id: ac-001
    summary: pointer_path refuses every traversal-shaped source name
    type: code
    pass_when: |
      tests/test_harness.py::TestPointerPath parametrizes at least
      "..", ".", "../../evil", "a/b", "a\\b", an absolute path such as
      "/etc/passwd", "" and a reserved name; each raises ConfigurationError
      whose message contains the offending name, and no file is created
      outside the cache root.
    status: pending
  - id: ac-002
    summary: pointer_path maps distinct names to distinct files under the root
    type: code
    pass_when: |
      pointer_path(root, "official") == root / "harness.official.pointer" and
      pointer_path(root, "private") != pointer_path(root, "official"), both
      resolving under root.
    status: pending
  - id: ac-003
    summary: SourcedComponent pairs a source with an untouched ComponentSpec
    type: code
    pass_when: |
      SourcedComponent is a frozen slots dataclass with fields source: str and
      spec: ComponentSpec; a test asserts sc.spec.id == "provider.demo" for a
      component folded out of a source named "official", and assignment to
      either field raises.
    status: pending
  - id: ac-004
    summary: fold_components is first-wins on spec.id in source order
    type: code
    pass_when: |
      Given two checkouts whose catalogs both declare provider.demo,
      fold_components(...).kept holds exactly one SourcedComponent for that id
      and its source is the first checkout's; distinct ids from both sources
      are all kept, ordered by source then catalog order.
    status: pending
  - id: ac-005
    summary: A displaced component is reported, never stored
    type: code
    pass_when: |
      A caplog assertion shows exactly one warning naming the winning source,
      the losing source and the contested id. There is no `displaced` field:
      it would have had no production reader, and the repo's own first-wins
      precedents drop losers without storing them.
    status: pending
  - id: ac-006
    summary: activated_checkouts is covered by a test that fakes no seam
    type: code
    pass_when: |
      tests/test_harness.py::TestActivatedCheckouts calls the real
      activated_checkouts with a real ImmutableGitStore and real
      Activation.bind over tmp_path (no monkeypatch of Activation,
      ImmutableGitStore, load_harness_catalog or WorkerProvider), and asserts
      two sources yield two Checkouts in file order carrying their own source,
      exactly one commits/ directory exists under the cache root, and a source
      whose pointer file is absent is skipped while its neighbour still yields
      a checkout.
    status: pending
  - id: ac-007
    summary: activated_checkouts errors name the source they came from
    type: code
    pass_when: |
      A pointer naming an unpublished SHA raises ConfigurationError containing
      both the SHA and that source's name; two harness entries sharing a name
      raise ConfigurationError naming that name, and so do two entries whose
      names differ only in case ("official" / "Official") - on darwin and
      Windows those map to one pointer file, which is the hazard the check
      exists for.
    status: pending
  - id: ac-008
    summary: The _wire seam answers a per-source current
    type: code
    pass_when: |
      All five seam targets (Activation, ImmutableGitStore, GitHubTransport,
      load_harness_catalog, WorkerProvider) are patched on molmcp.harness, not
      molmcp.server, and wiring.transports still records exactly one
      construction; _ActivationSeam returns a different current per
      pointer path, a second SHA constant sits beside _SHA, and a test asserts
      one source activated and one not produces one bind per source with
      catalogs read only for the activated one.
    status: pending
  - id: ac-009
    summary: Two sources shipping provider.demo mount one demo namespace
    type: code
    pass_when: |
      A new tests/test_stack.py test with both sources declaring
      provider.demo asserts exactly one WorkerProvider named "demo" is
      constructed, "demo_worker" resolves once in the composed tool names, the
      first source's spec won, and an entry-point plane named "demo" is still
      excluded by the folded name set.
    status: pending
  - id: ac-010
    summary: create_stack consumes every activated source, reading settings once
    type: code
    pass_when: |
      src/molmcp/server.py no longer defines _activated_checkout,
      _checkout_components, _checkout_planes, _import_root or _Checkout;
      its three arms call molmcp.harness; SUPPORTED_CAPABILITIES is defined in
      molmcp.harness and NOT re-imported by molmcp.server, which after the
      move holds no code reference to it (ruff F401) and does not carry it in
      __all__; tests/test_stack.py:768-780 name molmcp.harness instead;
      the one folder is fold_components and no checkout_components exists;
      checkout_planes takes exactly one argument, the ComponentFold, which
      carries the Checkout objects it was folded from so no caller keeps a
      second list in sync;
      from_checkout is fold.names rather than a set comprehension over
      workers; N activated sources produce N
      binds and 1xN or 2xN catalog reads; and
      test_the_locator_is_read_once_with_the_project_root still passes
      unmodified.
    status: pending
  - id: ac-011
    summary: The pinned boundary and precedent tests stay green unmodified
    type: code
    pass_when: |
      Content pins, not a diff: activate.ACTIVATION_VERSION == 1 and
      activate._POINTER_KEYS == frozenset({"version","active","staging",
      "previous"}) - the two facts the per-source-pointer route was chosen to
      preserve, and the two that change the moment someone reaches for a
      version-2 record. Plus: tests/test_components/test_activate.py,
      tests/test_components/test_catalog.py, tests/test_settings.py and
      tests/test_no_builtin_harness_source.py all pass;
      test_server_module_imports_nothing_from_discovery passes; and an
      equivalent AST assertion covers src/molmcp/harness.py. A bare `git diff`
      clause is deliberately not used - it names no base, so it passes
      vacuously either way, which is the golden-not-self-proving failure this
      chain already recorded once.
    status: pending
  - id: ac-012
    summary: create_stack's Raises list and the harness doc match the new behaviour
    type: code
    pass_when: |
      create_stack's docstring Raises section names the two new
      ConfigurationError cases (unusable source name, duplicate source name),
      and docs/concepts/harness.md names per-source activation pointers, the
      shared store and the first-wins fold - not only in the resolution
      paragraph but also at :50-54 ("an activation pointer: a small JSON file
      beside the harness store") and :200-201 ("the only root molmcp itself
      ever passes is the tree of the commit the activation pointer names"),
      both of which are singular today. The same sweep covers
      docs/concepts/harness.md:235-236, docs/guides/harness-migration.md:67 and
      src/molmcp/settings.py:128-130 (HarnessSource.ref names "the activation
      pointer" in the singular), or the spec states why a per-ref sentence
      stays singular.
    status: pending
  - id: ac-014
    summary: A stale single-source pointer is named, never silently read
    type: code
    pass_when: |
      With <root>/harness.pointer present and no harness.<name>.pointer for
      any configured source, activated_checkouts logs exactly one warning
      naming the stale file and returns no checkout from it - the legacy file
      is never read. Nothing in src/ writes that file (no caller of
      Activation.stage/promote/rollback exists), which is why it is warned
      about rather than migrated.
    status: pending
  - id: ac-013
    summary: Full check and test suite pass from a cold ruff cache
    type: code
    pass_when: |
      rm -rf .ruff_cache && uv run ruff check src tests &&
      uv run ruff format --check src tests && uv run pytest -v all succeed.
    status: pending
---

# Acceptance criteria

**ac-001 / ac-002** close the traversal hole. `HarnessSource.name` is deliberately ungoverned (`settings.py:152-159` excludes `name` from the `/` and `@` rejection), so the guard belongs at the point of use and nowhere else.

**ac-003 / ac-004 / ac-005** are the fold: the pair type keeps `spec.id` untouched, the key is `spec.id` in source order, and every loser is reported — logged, never stored. A `displaced` field was considered and dropped; its only reader would have been a test.

**ac-006** is the `faked-seam-hides-broken-reader` rule (notes, 2026-09-08) applied ahead of time: `_wire` fakes the store, the activation and the catalog loader, so a suite built only on it would pass with an `activated_checkouts` that never worked — which is exactly how `molmcp serve` was left broken by link 01 with 1852 tests green.

**ac-009** is the collision that has no coverage today. `tests/test_stack.py::test_checkout_wins_the_name_and_entry_point_only_planes_pass_through` (line 835) covers the one-source XOR; the two-source variant is new.

**ac-011** is the negative half of the design. The per-source-pointer route was chosen precisely because it changes nothing in `activate.py`; a diff touching that module or its tests means the route was abandoned mid-flight.

**ac-014** is the stale-pointer notice. The probe is one `legacy.exists()` plus one
`pointer_path(...).exists()` per source, evaluated once before the per-source loop —
filesystem contact `activated_checkouts` otherwise never makes, and stated in the
Design so it is not mistaken for an accident. It fires only when *no* source has a
pointer; a half-migrated install is deliberately not warned twice.

**ac-013** runs from a cold `.ruff_cache` because ruff's first-party isort judgement flips once `src/molmcp/harness.py` exists, and a warm cache hid exactly that failure in commit `751e874`.
