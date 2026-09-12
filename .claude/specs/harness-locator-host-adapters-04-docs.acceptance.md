---
spec: harness-locator-host-adapters-04-docs
created: 2026-09-11
criteria:
  - id: ac-001
    summary: Example catalog loads via load_harness_catalog with optional sci/dev
    type: code
    pass_when: |
      load_harness_catalog on a copy of harness.example.toml succeeds with
      SHA literal 9f1c3b2a7d4e0165c8a9b3d27e5f10486c73ab92 and bundle names
      equal to the literal set {"sci", "dev"}; _REQUIRED_BUNDLES is gone
    status: pending
  - id: ac-002
    summary: Locator set CLI is what the four pages teach
    type: docs
    pass_when: |
      harness.md, iterate-on-a-harness.md, cli.md, and installation.md
      teach molmcp config harness set with a locator; none contain
      config harness set --name, --owner/--repo, or set --name mine --path
    status: pending
  - id: ac-003
    summary: Bundle enable lives only on config harness set; init flags stay planes
    type: docs
    pass_when: |
      bundle --enable/--disable is shown only on config harness set;
      no page contains --enable-bundle or init --enable sci;
      cli.md documents init --disable molq as a plane toggle
    status: pending
  - id: ac-004
    summary: Taught loop is set, sync, init without --source
    type: docs
    pass_when: |
      iterate-on-a-harness.md opens with config harness set, harness sync,
      init <host>; that opening loop block does not contain --source
    status: pending
  - id: ac-005
    summary: Marketplace-add grep remains; README.md unchanged
    type: code
    pass_when: |
      _MARKETPLACE_ADD still scans docs/ and .claude/notes/;
      README.md does not mention config harness set
    status: pending
  - id: ac-006
    summary: Fixture does not assert CLI argparse; regression loads the example
    type: runtime
    pass_when: |
      test_harness_catalog_fixture.py has no import of molmcp.cli;
      regressions/harness-locator-host-adapters-04-docs.py calls
      load_harness_catalog only and asserts the SHA and {sci, dev} literals
    status: pending
out_of_scope:
  - README.md edits
  - CLI / catalog / host implementation
  - Inventing --enable-bundle
---

# Acceptance — harness-locator-host-adapters-04-docs

四页只教 locator 三步循环；束开关只在 `config harness set`；`init --enable/--disable` 仍是 plane。
