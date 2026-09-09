---
slug: harness-evo-04-bundle
criteria:
  - id: ac-001
    summary: component_root parses into the catalog, absent means empty
    type: code
    pass_when: |
      tests/test_components/test_catalog.py loads CANONICAL_TOML plus
      component_root = "plugins/mol" through the real load_harness_catalog and asserts
      catalog.component_root == "plugins/mol"; loading unmodified CANONICAL_TOML
      asserts catalog.component_root == ""; HarnessCatalog built by keyword without
      component_root still constructs; and a file carrying the key does not raise "unknown field(s)" - asserted
      behaviourally rather than by reaching into catalog._TOP_LEVEL_KEYS,
      which would be true the instant the implementer edits that line.
    status: verified
    last_checked: 2026-09-09
  - id: ac-002
    summary: Component paths are carried, never rewritten by component_root
    type: code
    pass_when: |
      With component_root = "plugins/mol" set, catalog.get("skill.daily").path equals
      the literal "skills/daily/SKILL.md" - the expected value written out
      independently of the TOML input, not derived from it - and the five
      exact-path assertions at tests/test_components/test_models.py:154,165,
      176,188,200 pass unchanged.
    status: verified
    last_checked: 2026-09-09
  - id: ac-003
    summary: A traversal-shaped component_root is refused; a multi-segment one is not
    type: code
    pass_when: |
      Parametrized over "..", "../evil", "a/../b", "/plugins",
      "plugins\\mol", "D:evil" and ".", each raises CatalogError whose str()
      contains the offending value. The drive-relative case is not padding:
      it carries no "..", holds no backslash, and Path("D:evil").is_absolute()
      is False on POSIX, so it passes the other three - yet
      PureWindowsPath("C:/store/tree") / "D:evil" is WindowsPath("D:evil"),
      the base discarded, and CI runs windows-latest - asserted both through load_harness_catalog and
      through direct HarnessCatalog(component_root=...) construction, because the value
      gate lives in __post_init__. "plugins/mol" and "plugins/mol/nested"
      both load, pinning that the separator half of _sha_dir's guard is
      deliberately absent: plugins/mol is two segments and must stay legal.
    status: verified
    last_checked: 2026-09-09
  - id: ac-004
    summary: an empty component_root is refused at the loader
    type: code
    pass_when: |
      A harness.toml containing `component_root = ""` raises CatalogError naming it as
      empty-when-present. The "." spelling is ac-003's, not this one: it is
      refused by the segment predicate in __post_init__ with a segment
      message, not by the loader's presence check, while the same file with no component_root key loads and
      yields catalog.component_root == "". Both assertions are required; only their
      difference proves the presence check exists.
    status: verified
    last_checked: 2026-09-09
  - id: ac-005
    summary: A real MolCrafts/harness-shaped catalog loads through the real loader
    type: code
    pass_when: |
      tests/test_components/test_catalog.py defines HARNESS_REPO_TOML with
      component_root = "plugins/mol", at least one row per ComponentKind spelled as the
      harness repo authors them (skills/spec/SKILL.md, agents/scientist.md,
      rules/large-spec-split.md, a provider row, an overlay row) and the daily and dev bundles the grammar requires. Its kind census is the
      real repository's - skills, agents and rules - PLUS one provider and one
      overlay row, because those two are the only kinds any arm reads today.
      The extra two rows are there to cover the loader's kind table, not to
      exercise any arm - ac-005 builds no fold and asserts nothing about
      providers or overlays; ac-010 and ac-011 cover the arms elsewhere. It is
      deliberately not described as "the same shape" as the real file.
      load_harness_catalog returns catalog.component_root == "plugins/mol"
      with every authored path unchanged. No _wire seam, no monkeypatch.
    status: verified
    last_checked: 2026-09-09
  - id: ac-007
    summary: No version marker becomes spellable in harness.toml
    type: code
    pass_when: |
      test_rejects_identity_top_level_field passes unchanged for every one of
      sha, version, tag, release, id, and test_rejects_unknown_top_level_key
      still refuses an unexpected key.
    status: verified
    last_checked: 2026-09-09
  - id: ac-008
    summary: ComponentFold.root_for joins tree and catalog component_root per source
    type: code
    pass_when: |
      In tests/test_harness.py, a fold over a checkout whose harness.toml
      declares component_root = "plugins/mol" answers root_for(source) ==
      checkout.tree / "plugins" / "mol"; a fold over a rootless catalog
      answers exactly checkout.tree (path equality against the tree the test
      built, so a "." component or trailing separator fails); and a
      two-source fold with one rooted and one rootless catalog answers each
      source with its own base - the case a global application of component_root gets
      wrong.
    status: verified
    last_checked: 2026-09-09
  - id: ac-009
    summary: root_for refuses a source the fold was not built from
    type: code
    pass_when: |
      fold.root_for("nobody") raises CatalogError whose message contains
      "unknown-source" and the repr of the name; ComponentFold has no default
      for component_roots; and its __post_init__ refuses a pair whose source
      names are not unique on both sides, or not exactly equal across them.
      Five constructions are tested: omitting a source, misnaming one,
      supplying an extra, duplicating one (which set equality alone admits,
      and root_for's linear scan would then answer silently), and supplying
      two checkouts sharing a source name with different trees (which
      roots-side uniqueness alone admits).
      There is no "unrelated path" case because there is no stored path:
      component_roots holds the raw strings and root_for joins against that
      source's own Checkout.tree, so a base belonging to the wrong tree is
      unconstructible rather than asserted against. "No default" alone would
      cover only the totally-absent case, which is the one nobody writes.
    status: verified
    last_checked: 2026-09-09
  - id: ac-010
    summary: The provider arm imports from the folded base
    type: code
    pass_when: |
      checkout_planes over a real tree holding
      plugins/mol/providers/demo/plane.py yields a worker whose path is
      tree/plugins/mol/providers/demo, and a rooted sibling of
      test_worker_provider_path_is_the_import_root_directory asserts the same
      at composition level. inspect.signature(harness._import_root)
      reads (base, path): _import_root is this arm's, and it stops receiving a
      checkout tree once root_for is threaded through it, while harness.py's
      Checkout.tree still means the other thing.
    status: verified
    last_checked: 2026-09-09
  - id: ac-011
    summary: The overlay arm resolves seeds under the folded base
    type: code
    pass_when: |
      A tests/test_stack.py test records the second argument create_stack
      hands server._session_capability_overlays with a catalog carrying
      component_root = "plugins/mol" and asserts it equals tree/plugins/mol.
      The signature pin for _session_capability_overlays lives in
      tests/test_runtime.py beside TestSessionCapabilityOverlays, which owns
      that module's contract; this criterion keeps only the composition
      assertion, which is create_stack's own subject.
      A text scan is deliberately NOT asserted:
      runtime.py:97 already binds a local named import_root and the other
      subject is named _import_root, so a scan for "root" fails on day one
      and a scan for an exact phrase is a golden that can only pass.
    status: verified
    last_checked: 2026-09-09
  - id: ac-012
    summary: The published example and the concept page name component_root
    type: code
    pass_when: |
      docs/concepts/harness.example.toml carries component_root = "plugins/mol",
      placed beside `requires` and above the first [[component]] table (a bare
      key after a table header is a TOMLDecodeError). "plugins/mol" and not
      some neutral literal because that file's own header records its
      publication as repo MolCrafts/harness, which is exactly the repository
      whose layout that value describes - a neutral value would leave the
      header and the key contradicting each other, and "components" would
      additionally collide with the name of the parser package - with
      its comment block corrected to say paths resolve under component_root;
      tests/test_harness_catalog_fixture.py:72's re-spelled _TOP_LEVEL_KEYS
      is {"requires", "component", "component_root"}; and the whole of that module
      passes, including test_example_carries_every_key_the_page_names; the
      top-level key row in docs/concepts/harness.md reads
      `requires`, `component_root` (test_example_carries_every_key_the_page_names
      compares the example against the module-local constant, not against the
      page, so nothing else would hold that row);
      test_consumed_filename_is_resolved_in_exactly_one_module (still exactly
      {"components/catalog.py"}) and
      test_contract_note_holds_the_two_rules_and_no_schema with
      .claude/notes/harness-contract.md unmodified.
    status: verified
    last_checked: 2026-09-09
  - id: ac-013
    summary: The release carrying the fail-closed key is 0.7.0 and says so
    type: code
    pass_when: |
      pyproject.toml declares version = "0.7.0",
      tests/test_version_single_source.py passes against the re-synced
      environment (uv sync --extra dev must be re-run, or importlib.metadata
      still reports 0.6.1), and docs/concepts/harness.md states that a
      catalog carrying component_root fails to load on molmcp older than 0.7.0 with
      "unknown field(s) in harness.toml: component_root".
    status: verified
    last_checked: 2026-09-09
  - id: ac-015
    summary: The public loader signature is renamed and nothing still spells it root
    type: code
    pass_when: |
      inspect.signature(components.load_harness_catalog) reads
      (tree, sha, supported_capabilities) - it is exported from
      components/__init__.py so the parameter name is a public keyword
      contract, and its two sibling renames are each pinned by ac-010 and
      ac-011 while this one otherwise would not be. Additionally
      tests/test_stack.py's _wire double no longer declares a `root`
      parameter or records a "root" key, and docs/concepts/harness.md
      contains no `load_harness_catalog(tree_root` call.
    status: verified
    last_checked: 2026-09-09
  - id: ac-014
    summary: Full check and suite green from a cold ruff cache
    type: code
    pass_when: |
      rm -rf .ruff_cache && uv run ruff check src tests &&
      uv run ruff format --check src tests && uv run pytest -v all succeed,
      and uv run molmcp gate reports the wiring contract holds.
    status: verified
    last_checked: 2026-09-09
---

# Acceptance criteria

**ac-001 – ac-005, ac-007 — the grammar.** One optional key, guarded like a path fragment, that does not weaken the prefix rule beside it. ac-002 is what catches a regression to the rejected "rewrite `path` at parse time" design: a component's `path` must come out exactly as authored. ac-003's accepted cases matter as much as its refused ones — `plugins/mol` is two segments, so the separator check `_sha_dir` and `pointer_path` both carry is deliberately absent here, and a later "simplification" that restores it would break the only layout this link exists to support. ac-005 is the load-bearing one: the only evidence in this repository that the *real* harness layout parses, driven through the real loader. It is honest about what it is not — the real repository ships zero providers and zero overlays, so the fixture's extra two rows cover the loader's kind table rather than standing in for the repository.

**ac-008 – ac-011 — the single-applier property.** One join site, reached from both arms. ac-008's two-source mixed fold is the case that fails if `component_root` is applied globally rather than per source. ac-011 holds the recorded second argument and the frozen signature; it deliberately asserts no text scan, because `runtime.py:97` already binds a local named `import_root` and the other subject is named `_import_root`, so a scan for the word fails on day one and a scan for an exact phrase is a golden that can only pass. Together they make "half the components resolve" unreachable rather than merely untested — and half a harness, where providers resolve and overlays do not, is far harder to diagnose than one that resolves nothing, because the install looks like it works.

**ac-012 – ac-013 — publication.** The example is where a reader learns the grammar, so the key is optional in the parser and mandatory in the published example. ac-013 pins the SemVer consequence of a fail-closed grammar change, so nobody discovers it from a broken install.

**The `MolCrafts/harness` catalog task carries no criterion of its own, by construction.** It lands in another repository and this suite cannot see it. ac-005 is its only verifiable shadow: the in-repo fixture carries the real repository's kind census and the same authored path spellings, so the file drafted for the other repository is known to parse before anyone pushes it. What ac-005 cannot show is delivery: after this link the real catalog parses and folds, and its 55 skill/agent/rule components still have no consumer. That is `harness-evo-04b-materialize`.

`ac-006` is absent by design: it covered the `evo` bundle's eligibility, which was withdrawn with the bundle itself when this link stopped declaring one. The remaining ids are left unrenumbered so earlier review rounds still resolve.

Every criterion is `type: code`: `regressions/` was deleted by operator decision and is not recreated.
