---
slug: harness-evo-01-sources
criteria:
  - id: ac-001
    summary: HarnessSource is a frozen four-field settings type
    type: code
    pass_when: |
      tests/test_settings.py::TestHarnessSource passes: a four-field entry
      round-trips with every field preserved, HarnessSource(name="mine")
      constructs with owner == repo == ref == "", and the class is a
      frozen slots dataclass declared in src/molmcp/settings.py.
    status: verified
    last_checked: 2026-09-08
  - id: ac-002
    summary: A composite coordinate is refused, not parsed
    type: code
    pass_when: |
      tests/test_settings.py::TestHarnessSource raises plain ValueError for
      owner="acme/harness", owner="acme@main", and owner="acme harness".
      Refusing the composite value is what keeps a second owner/repo[@ref]
      parser out of the tree; settings.py splits nothing on "/" or "@".
    status: verified
    last_checked: 2026-09-08
  - id: ac-003
    summary: A name is required but not grammar-checked, matching sources
    type: code
    pass_when: |
      tests/test_settings.py::TestHarnessSource raises ValueError for
      name="", name="  ", and name="my harness"; HarnessSource(name="MolCrafts")
      constructs successfully; and src/molmcp/settings.py defines no
      HARNESS_SOURCE_NAME_PATTERN (assert not hasattr(st,
      "HARNESS_SOURCE_NAME_PATTERN")).
    status: verified
    last_checked: 2026-09-08
  - id: ac-004
    summary: harness is a plain list setting in no merge channel
    type: code
    pass_when: |
      tests/test_settings.py::TestSettingsHarnessSources asserts
      _SCHEMA["harness"] is list; "harness" not in _MERGED_DICTS, not in
      _MERGED_LISTS, not in _NESTED_SCHEMA; and "harness" in _OBJECT_LISTS.
      The three tests asserting the retired model -
      tests/test_settings.py:204 (_SCHEMA is dict), :207 (_NESTED_SCHEMA
      members) and :210 (_MERGED_DICTS membership) - are deleted, not
      adapted.
    status: verified
    last_checked: 2026-09-08
  - id: ac-005
    summary: First entry wins within one file's list
    type: code
    pass_when: |
      tests/test_settings.py::TestSettingsHarnessSources loads a user file
      holding two entries and asserts
      tuple(s.name for s in load_settings(root).harness) equals the file
      order exactly, with no sorting applied anywhere on the path.
    status: verified
    last_checked: 2026-09-08
  - id: ac-006
    summary: The most specific layer's list replaces; excludes still unions
    type: code
    pass_when: |
      tests/test_settings.py::TestSettingsHarnessSources writes a one-entry
      harness list in the user file and a different one-entry list in the
      project-local file and asserts the loaded harness is exactly the local
      entry - the user entry does not survive. The opposite behaviour of
      _MERGED_LISTS members is pinned declaratively by ac-004, not by
      re-asserting excludes here; excludes keeps its owner at
      tests/test_settings.py:85. The contradicting test
      tests/test_settings.py:210 is deleted.
    status: verified
    last_checked: 2026-09-08
  - id: ac-007
    summary: A partially authored entry survives load
    type: code
    pass_when: |
      tests/test_settings.py::TestSettingsHarnessSources loads
      [{"name": "mine", "owner": "acme"}] without raising and yields
      HarnessSource(name="mine", owner="acme", repo="", ref="").
    status: verified
    last_checked: 2026-09-08
  - id: ac-008
    summary: Malformed entries are rejected by indexed name
    type: code
    pass_when: |
      tests/test_settings.py::TestSettingsHarnessSources raises
      SettingsError naming harness[0].<member> for each of dev, cacheDir,
      token, daily, telemetry; for an entry with no name; for two entries
      sharing a name in one file; and for a "harness" value that is a dict,
      both {"owner": "x"} and {}, whose message names the list shape.
      tests/test_settings.py:223 (a partial table is stored) is deleted and
      :231 (stray member reported as harness.<member>) is rewritten for the
      indexed form; :239 and :243 are kept untouched.
    status: verified
    last_checked: 2026-09-08
  - id: ac-009
    summary: The bare harness key cannot be written by set or add
    type: code
    pass_when: |
      tests/test_settings.py::TestHarnessWriteGuard asserts
      set_value(path, "harness", "x") and add_value(path, "harness", "x")
      each raise SettingsError and that `path.exists()` is False after each;
      that load_settings(root) afterwards still returns harness == () rather
      than raising; that set_value(path, "harness.owner", "x") raises
      SettingsError and creates no file; and that
      remove_value(path, "harness") on a file holding a valid list clears
      the key and leaves a file load_settings accepts. Both verbs reach the
      refusal through the declared _OBJECT_LISTS table rather than a
      "harness" literal in either function body.
      tests/test_settings.py:166 is deleted and :184-192 is rewritten into
      this class.
    status: verified
    last_checked: 2026-09-08
  - id: ac-010
    summary: An empty list serves exactly as an unset locator does today
    type: code
    pass_when: |
      tests/test_stack.py with harness=() records no bind, no catalog and
      extras == (), entry points are discovered and disable= honoured, and
      the dual-injection test still records wiring.settings == [].
      Both tests pass a dict literal today and are converted by hand, not by
      the _LOCATOR rename: :368 becomes harness=(_SOURCE,) - tuple() over its
      current dict would yield ("owner",) and fail silently - and :383
      becomes harness=(), its "Three keys unset" docstring rewritten.
    status: verified
    last_checked: 2026-09-08
  - id: ac-011
    summary: Serve-time refuses an incomplete entry, naming entry and fields
    type: code
    pass_when: |
      tests/test_stack.py raises ConfigurationError whose message contains
      the entry's name and every missing field, for a half-authored entry,
      for a name-only entry, and for an incomplete second entry whose
      predecessor is complete (no entry is ever skipped).
    status: verified
    last_checked: 2026-09-08
  - id: ac-012
    summary: Several complete sources return in file order, one store root
    type: code
    pass_when: |
      tests/test_stack.py asserts _harness_locator() returns both sources of
      a two-entry list in file order and that the stack still binds exactly
      one store rooted at <cache>/harness with its pointer at
      <cache>/harness.pointer (the pins at tests/test_stack.py:532 and :560
      are unchanged).
    status: verified
    last_checked: 2026-09-08
  - id: ac-013
    summary: No harness source is built in anywhere in src/molmcp
    type: code
    pass_when: |
      tests/test_no_builtin_harness_source.py asserts load_settings over an
      empty settings tree returns harness == (); ac-010 independently
      asserts that harness == () yields no bind, no catalog and extras == ().
      Those two behavioural assertions are the whole criterion. No AST lint
      gates this: a module-level dict literal fed through the same path file
      data takes never calls HarnessSource(...) with a literal at all, so a
      call-site scan cannot catch the case it would exist for.
    status: verified
    last_checked: 2026-09-08
  - id: ac-014
    summary: The components layer is untouched and the id carries no namespace
    type: code
    pass_when: |
      tests/test_no_builtin_harness_source.py finds neither "HarnessSource"
      nor "harness_source" in the text of src/molmcp/components/models.py or
      src/molmcp/components/catalog.py. ComponentSpec.id's grammar is not
      re-asserted here - it is owned by
      tests/test_components/test_models.py:224 test_rejects_id_mismatch,
      which this spec leaves untouched.
    status: verified
    last_checked: 2026-09-08
  - id: ac-016
    summary: config list prints harness as an array of objects
    type: code
    pass_when: |
      tests/test_cli_config.py asserts that after a settings file holding a
      one-entry harness list, `molmcp config list` emits a "harness" value
      that is a JSON array whose single element is an object with the four
      entry keys - not a JSON object. Settings.to_dict is the reader that
      makes this visible (settings.py:123 -> cli.py:508).
    status: verified
    last_checked: 2026-09-08
  - id: ac-015
    summary: Docs teach the settings-file JSON shape, not the retired keys
    type: code
    pass_when: |
      tests/test_harness_catalog_fixture.py asserts "harness.owner" appears
      in neither docs/concepts/harness.md nor
      docs/get-started/installation.md; that the ~/.molmcp/settings.json
      block fenced in docs/concepts/harness.md parses as JSON whose
      "harness" value is a list; and that every entry's keys are a subset of
      _HARNESS_ENTRY_KEYS and construct a HarnessSource.
    status: verified
    last_checked: 2026-09-08
---

# Acceptance criteria

- **ac-001 - ac-003 - the type.** `HarnessSource` is permissive about an absent coordinate and strict about a malformed one. ac-002 keeps a second `owner/repo[@ref]` parser out of the tree; `_parse_github_spec` stays the only one. ac-003 pins the deliberate *absence* of a name grammar: `settings.py:58` records that `sources` members are user-chosen and unvalidated, and a harness name is user-chosen the same way - an operator who can call an index source `MolCrafts` must be able to call a harness source `MolCrafts`.
- **ac-004 - ac-006 - the setting and its precedence.** ac-004 pins that `harness` joins **no** merge channel and that no new one was invented, which is what makes the precedence free: `settings_layers()` runs low-to-high and the default branch's last assignment wins. ac-005 is order *within* a file; ac-006 is *between* layers, and it deliberately asserts `excludes` in the same test so the opposite behaviour of two list settings twelve lines apart is written into a test rather than discovered in an install.
- **ac-007, ac-008 - load-time permissive, load-time strict.** A half-authored entry parses because the coordinates arrive one command at a time; an entry with no name, a duplicate name in one file, a stray member, or a dict-valued `harness` does not, because none of them can be addressed or completed later.
- **ac-009 - the hole this spec would otherwise open.** Making `harness` a `list` unlocks `config set harness x` and `config add harness x`, both of which write a bare string before anything validates it. The next `load_settings` then fails, and `load_settings` sits under every config verb and under `serve`, so no CLI verb can undo it. The `path.exists()` assertions are the binding half; the follow-up `load_settings` assertion is the one that says the install is still usable.
- **ac-010 - ac-012 - serve-time.** The empty list is the un-harnessed install and is not a failure; a named-but-unfinished entry is. Nothing gains a second store root.
- **ac-016 - the visible output.** `Settings.to_dict` is the second reader of `harness`, and `config list` prints what it returns, so the shape change reaches a user's terminal. It ships in `settings.py`, one of the two files this spec already moves, which is why it needs a criterion rather than a link of its own.
- **ac-013 - ac-014 - the boundaries.** No official coordinate is built in anywhere, and `components/` never learns that a harness source exists. ac-013 leads with two behavioural assertions on purpose: an AST walk for `HarnessSource(...)` string literals is defeated by `HarnessSource(**_DEFAULT)`, by a module constant, and most realistically by a module-level list of plain dicts fed through the same path file data takes - which never calls `HarnessSource(...)` with a literal at all. What no defeat survives is an empty settings tree loading to `()` and `()` producing no bind, so those two assertions are the criterion and no lint gates it.
- **ac-015 - the docs.** The three `config set harness.<key>` lines exit 2 after this change, so they leave. What replaces them is the file-format contract - a worked `settings.json` snippet - parsed and constructed by the test, in the same spirit as the `harness.example.toml` fixture that already lives in that module.

Six live tests in `tests/test_settings.py` assert the model this spec replaces; ac-004, ac-006, ac-008 and ac-009 each name the ones they retire, so the retirement is part of the contract rather than something an implementer improvises. Every criterion is `type: code`. No `type: runtime` criterion exists: `regressions/` was deleted by operator decision, and this spec does not recreate it, so the spec can reach `done` without an external evaluator.
