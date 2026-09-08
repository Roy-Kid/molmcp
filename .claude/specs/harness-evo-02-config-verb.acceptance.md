---
slug: harness-evo-02-config-verb
criteria:
  - id: ac-001
    summary: set_harness_source upserts by name and appends unknown names last
    type: code
    pass_when: |
      tests/test_settings.py::TestHarnessSourceEdit shows a second call with an
      existing name updating that entry in place (list length unchanged) and a
      call with a new name appending at the end, leaving the prior first entry
      first.
    status: verified
    last_checked: 2026-09-08
  - id: ac-002
    summary: A field passed None leaves the stored value untouched
    type: code
    pass_when: |
      set_harness_source(path, name="mine", ref="dev") on an entry already
      carrying owner/repo writes ref="dev" and leaves owner and repo at their
      previous values; a name-only call on an unknown name stores "" for all
      three coordinates.
    status: verified
    last_checked: 2026-09-08
  - id: ac-003
    summary: A refused harness write leaves no settings file behind
    type: code
    pass_when: |
      set_harness_source with an illegal coordinate (e.g. owner="acme/harness")
      raises SettingsError carrying HarnessSource's own message text, and
      st.user_settings_path().exists() is False afterwards.
    status: verified
    last_checked: 2026-09-08
  - id: ac-004
    summary: remove_harness_source drops one entry, never the key
    type: code
    pass_when: |
      remove_harness_source removes only the named entry and leaves "harness":
      [] when the last one goes (key still present); an absent name and an
      absent harness key each raise SettingsError in remove_value's message
      shape, naming the name and the file respectively.
    status: verified
    last_checked: 2026-09-08
  - id: ac-005
    summary: Both functions are exported in sorted __all__
    type: code
    pass_when: |
      settings.__all__ contains "remove_harness_source" and
      "set_harness_source". __all__ is NOT sorted - it is grouped (constants,
      then types, then functions, alphabetical within each group), so
      LOCAL_SETTINGS_NAME precedes HarnessSource. Assert the target positions
      instead: remove_harness_source immediately before remove_value, and
      set_harness_source immediately before set_value.
    status: verified
    last_checked: 2026-09-08
  - id: ac-006
    summary: The CLI verb drives the real settings functions end to end
    type: code
    pass_when: |
      cli.main(["config", "harness", "set", "--name", "official", "--owner",
      "MolCrafts", "--repo", "harness", "--ref", "main"]) returns 0 and the
      file on disk holds that entry, with no monkeypatch of
      settings.set_harness_source anywhere in the test.
    status: verified
    last_checked: 2026-09-08
  - id: ac-007
    summary: Scope flags and name-only authoring work on both harness leaves
    type: code
    pass_when: |
      --project and --local route config harness set/remove to
      project_settings_path(cwd) and project_settings_path(cwd, local=True)
      while the user file stays empty, and `config harness set --name mine`
      alone exits 0 writing a name-only entry.
    status: verified
    last_checked: 2026-09-08
  - id: ac-008
    summary: An unrecognised config_action never reaches remove_value
    type: code
    pass_when: |
      Two assertions, because config_action is required=True with fixed choices
      (cli.py:184) so an unknown action cannot reach _config through cli.main -
      argparse exits 2 first. (i) a hand-built Namespace with an unhandled
      config_action passed directly to cli._config raises ConfigurationError;
      (ii) a structural test derives the registered action names from
      _build_parser() and, for each, calls _config(Namespace(config_action=name))
      asserting ConfigurationError is NOT raised - a dispatched branch fails
      instead on AttributeError for its own missing fields. Stating the
      mechanism matters: building a full Namespace per action would copy every
      subparser's argument shape into the test, and source-scraping _config
      would not survive the _config_harness delegation this same spec adds.
      This is the test that sees a new subparser landing without a branch. A spy
      on settings.remove_value asserting it is never reached is kept as a
      secondary, not as the criterion.
    status: verified
    last_checked: 2026-09-08
  - id: ac-009
    summary: config get harness.owner errors instead of answering null
    type: code
    pass_when: |
      cli.main(["config", "get", "harness.owner"]) returns 2 with a "molmcp:"
      message naming the key, while cli.main(["config", "get", "cacheDir"])
      still returns 0 printing null, `config get layers` still returns 0, and
      `config get nope` and `config get sources.nope` both still return 0
      printing null - those two are the cases the preserved
      "part not in node" arm actually serves, since cacheDir answers null by
      the different route of an unset value.
    status: verified
    last_checked: 2026-09-08
  - id: ac-010
    summary: Both refusal messages name the verb, bare and resolvable
    type: code
    pass_when: |
      _reject_object_list_write's message names the verb and no longer contains
      "by editing"; _resolve's dotted refusal carries that sentence for a
      harness.* key and the unchanged generic message for excludes.foo and
      cacheDir.x; and `config remove harness official` with an entry named
      official present no longer answers "'official' is not present in
      'harness'" - remove_value's value arm (value is not None) is guarded too,
      while remove_value(path, "harness") still drops the whole key. That
      refusal names "molmcp config harness remove", not "... set": the message
      derives its leaf from the calling verb, because answering a remove with a
      set is a precise misdirection.
    status: verified
    last_checked: 2026-09-08
  - id: ac-011
    summary: No test or doc still claims no verb can author a harness source
    type: code
    pass_when: |
      Neither tests/test_settings.py, tests/test_cli_config.py,
      tests/test_harness_catalog_fixture.py, docs/get-started/installation.md,
      docs/concepts/harness.md nor docs/reference/cli.md states that the config
      verbs cannot write harness or that a verb is coming; the installation
      page still only points at concepts/harness.md and no page names
      harness.owner; the harness.md) pointer at docs/reference/cli.md:29
      survives, since that file is a _POINTER_PAGES member whose live assertion
      requires it.
    status: verified
    last_checked: 2026-09-08
  - id: ac-013
    summary: A name-only entry is accepted, and its serve-time cost is pinned
    type: code
    pass_when: |
      After cli.main(["config", "harness", "set", "--name", "mine"]) exits 0,
      calling the REAL molmcp.server._harness_locator() against that settings
      file raises ConfigurationError whose message names "mine" and contains
      every member of server._HARNESS_KEYS - derived, not a hand-written triple,
      so the test keeps server._HARNESS_KEYS distinct from
      settings._HARNESS_ENTRY_KEYS the way server.py:85-93 documents. No _wire
      fake anywhere in the test. This
      is the only coverage that raise has; `grep -rn "is incomplete" tests/`
      returns nothing today. _config_harness must NOT enumerate missing
      coordinates itself: server._HARNESS_KEYS stays the only completeness
      rule.
    status: verified
    last_checked: 2026-09-08
  - id: ac-014
    summary: Every _OBJECT_LISTS member has a registered config subparser
    type: code
    pass_when: |
      For every member of settings._OBJECT_LISTS, _build_parser() registers a
      `config <member>` subparser AND that subparser registers a `set` leaf.
      Asserting only the member is not enough: a future member offering just
      `remove` would satisfy it while still yielding a hint nothing resolves. This keeps the derived
      "molmcp config {key} set" sentence in _reject_object_list_write truthful
      as the table grows, as machinery rather than as a docstring promise.
    status: verified
    last_checked: 2026-09-08
  - id: ac-012
    summary: Full check and test suite pass
    type: code
    pass_when: |
      `uv run ruff check src tests && uv run ruff format --check src tests` and
      `uv run pytest -v` both exit 0, with tests/test_no_builtin_harness_source.py
      and tests/test_harness_catalog_fixture.py unchanged in behaviour.
    status: verified
    last_checked: 2026-09-08
---

# Acceptance criteria

`ac-001`-`ac-005` bind the two new settings functions: upsert-by-name with append-last ordering, `None`-means-unchanged, refusal-before-write, single-entry removal that never drops the key, and the exports.

`ac-006`-`ac-008` bind the CLI: the verb driving the real functions with no seam (the `faked-seam-hides-broken-reader` rule captured 2026-09-08), the scope flags composing onto both leaves, and the bare-`else` trap being closed such that an unhandled action provably cannot reach `remove_value`. ac-008 is locality of change, not urgency: adding the `harness` action would **not**
itself fire the trap — that Namespace carries no `key`/`value`, so it would raise an
uncaught `AttributeError` rather than delete anything. The trap is latent for a
future action that does carry them, and the moment to remove it is while this spec is
already editing `_config`'s chain.

`ac-009`-`ac-010` bind the two behaviours link 01 left owed — a dotted read that answered `null` for a path that cannot exist, and two refusal messages that could not name a verb because none existed.

`ac-011` binds the prose, test docstrings and docs alike, that this change falsifies.

`ac-013` is the one that keeps this change honest. `config harness set --name mine`
exits 0 and leaves every subsequent `molmcp serve` at exit 2 until the coordinates
are filled in — the two-step ritual the load-time/serve-time split always implied,
now reachable in one command. The answer is not a second completeness rule in the
CLI; it is that the consequence is pinned by a test driving the real
`_harness_locator`, which today has no coverage at all for its incomplete-entry
raise. `ac-014` turns the forward obligation on `_OBJECT_LISTS` into machinery
rather than a docstring promise.

`ac-012` is the gate.

ac-008(ii) and ac-014 are this suite's first argparse-internals introspection —
`_build_parser` appears in no test today. Both reach the registered `config` action
names by one traversal (`parser._actions` → the `_SubParsersAction` →
`.choices["config"]` → its `_SubParsersAction` → `.choices`), and both must share a
single helper so that private-API surface lives in one place rather than two.

Every criterion is `type: code`: `regressions/` was deleted by operator decision and is not recreated, so the spec reaches `done` without an external evaluator.
