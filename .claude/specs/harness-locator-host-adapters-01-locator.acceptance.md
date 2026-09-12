---
spec: harness-locator-host-adapters-01-locator
created: 2026-09-11
criteria:
  - id: ac-001
    summary: parse_harness_locator canonicalizes GitHub and local locators
    type: code
    pass_when: |
      uv run pytest tests/test_components/test_locator.py -v is green;
      MolCrafts/harness, https://github.com/MolCrafts/harness.git/ and
      github.com/MolCrafts/harness all yield origin_key literal
      "molcrafts/harness"; ./checkout raises LocatorError;
      locator.py AST-imports neither molmcp.discovery nor molmcp.settings
    status: verified
    last_checked: 2026-09-11
  - id: ac-002
    summary: HarnessSource persists only name, locator, enable
    type: code
    pass_when: |
      dataclasses.fields(HarnessSource) names are name, locator, enable;
      asdict and written JSON contain those keys only; origin_key/ref/owner/repo/path
      are readable attributes and absent from the file
    status: verified
    last_checked: 2026-09-11
  - id: ac-003
    summary: Old owner/repo/path keys are a hard cut
    type: code
    pass_when: |
      A settings file whose harness entry still has owner, repo or path
      raises SettingsError whose message tells the operator to re-run
      molmcp config harness set <locator>
    status: verified
    last_checked: 2026-09-11
  - id: ac-004
    summary: set_harness_source upserts by origin_key with alias origin
    type: code
    pass_when: |
      One origin_key stays one entry across URL and Owner/repo spellings;
      a first insert without --alias is named origin; a second different
      origin_key without --alias is refused; omit enable on insert stores
      None (all); --disable all stores () without dropping the entry
    status: verified
    last_checked: 2026-09-11
  - id: ac-005
    summary: CLI set takes locator; old coordinate flags are gone
    type: code
    pass_when: |
      molmcp config harness set requires positional locator and accepts
      repeatable --enable/--disable and optional --alias;
      --name --owner --repo --ref --path are not registered;
      cli.py AST does not import components.locator or harness_paths
    status: verified
    last_checked: 2026-09-11
  - id: ac-006
    summary: remove, sync and rollback accept alias or locator
    type: code
    pass_when: |
      match_harness_source is the single matcher used by
      remove_harness_source and harness_sync._named
    status: verified
    last_checked: 2026-09-11
  - id: ac-007
    summary: Renaming an alias moves the pointer via relocate_pointer
    type: code
    pass_when: |
      relocate_pointer lives in harness_sync; after set --alias newname,
      harness.<old>.pointer is gone and harness.<new>.pointer holds the
      same bytes; settings.py AST does not import harness_paths
    status: verified
    last_checked: 2026-09-11
  - id: ac-008
    summary: Field consumers use derived identity, not stored owner/repo/path
    type: code
    pass_when: |
      local_checkout_path still expands the derived path; github sync still
      calls GitHubTransport.resolve_commit; HARNESS_COORDINATES is absent;
      assert_servable does not read enable
    status: verified
    last_checked: 2026-09-11
  - id: ac-009
    summary: concepts snippet constructs under the new entry keys
    type: docs
    pass_when: |
      the harness JSON snippet in docs/concepts/harness.md has no owner,
      repo or path keys and HarnessSource(**entry) succeeds for each object
    status: verified
    last_checked: 2026-09-11
  - id: ac-010
    summary: Regression reproduces locator goldens as literals
    type: runtime
    pass_when: |
      regressions/harness-locator-host-adapters-01-locator.py exits 0;
      origin_key == "molcrafts/harness" for MolCrafts/harness and the
      https URL as independent literals; ./checkout raises; first
      set_harness_source without alias writes name "origin"
    status: verified
    last_checked: 2026-09-11
    verified_by: agent-auto
out_of_scope:
  - catalog filtering (02)
  - host adapters and deleting init --source (03)
  - full narrative docs (04)
  - migrating old owner/repo/path files
---

# Acceptance — harness-locator-host-adapters-01-locator

一条定位符、一个 origin、三个持久化字段。改名只经 `relocate_pointer`。`enable` 只存不滤。
