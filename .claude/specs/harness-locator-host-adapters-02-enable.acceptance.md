---
spec: harness-locator-host-adapters-02-enable
created: 2026-09-11
criteria:
  - id: ac-001
    summary: Catalogs may omit daily and dev bundles
    type: code
    pass_when: |
      catalog.py has no _REQUIRED_BUNDLES; TestHarnessCatalog constructs a
      catalog whose only bundle is sci and one whose bundles tuple is empty
    status: verified
    last_checked: 2026-09-11
  - id: ac-002
    summary: enabled_components first-seen-unions via resolve_bundle
    type: code
    pass_when: |
      enabled_components folds resolve_bundle and unions on spec.id;
      overlapping sci/lab members appear once in enable-list order
    status: verified
    last_checked: 2026-09-11
  - id: ac-003
    summary: Empty enable is a successful empty view
    type: code
    pass_when: |
      enabled_components(()) returns () without CatalogError
    status: verified
    last_checked: 2026-09-11
  - id: ac-004
    summary: Unknown enable names list real bundle names
    type: code
    pass_when: |
      enabled_components(("nope",)) raises CatalogError containing
      unknown-bundle and the catalog's real names
    status: verified
    last_checked: 2026-09-11
  - id: ac-005
    summary: Zero bundles is the implicit package of all components
    type: code
    pass_when: |
      A catalog with non-empty components and bundles=() constructs;
      enabled_components(None) equals catalog.components
    status: verified
    last_checked: 2026-09-11
  - id: ac-006
    summary: Checkout.enable is required and unsynced skip is current-is-None only
    type: code
    pass_when: |
      Checkout without enable raises TypeError; activated_checkouts omits
      a source only when current is None; enable=() still appears in the
      returned tuple
    status: verified
    last_checked: 2026-09-11
  - id: ac-007
    summary: fold_components iterates enabled_components not catalog.components
    type: code
    pass_when: |
      fold_components first-wins only across checkouts; a checkout with
      enable=("sci",) folds only sci members
    status: verified
    last_checked: 2026-09-11
  - id: ac-008
    summary: init empty-enable places nothing; unknown names fail at sync
    type: runtime
    pass_when: |
      synced enable=() yields installed==() while the source remains;
      harness_install still forbids importing molmcp.harness;
      sync of enable=("nope",) exits non-zero and does not promote
    status: verified
    last_checked: 2026-09-11
  - id: ac-009
    summary: sync with enable=() still publishes and promotes
    type: runtime
    pass_when: |
      molmcp harness sync on a source whose enable is () exits 0 and
      writes a current SHA into that source's pointer
    status: verified
    last_checked: 2026-09-11
  - id: ac-010
    summary: Docs stop requiring daily and dev; regression goldens
    type: docs
    pass_when: |
      docs/concepts/harness.md and harness.example.toml no longer say
      every catalog must define daily and dev;
      regressions/harness-locator-host-adapters-02-enable.py asserts
      the hard-coded union/empty/unknown/zero-bundle goldens
    status: verified
    last_checked: 2026-09-11
out_of_scope:
  - locator CLI (01)
  - host adapters and init --source (03)
  - validating unknown names at set time
  - using config harness remove as the off switch
---

# Acceptance — harness-locator-host-adapters-02-enable

bundle 是启用单位；空 enable 是成功的空视图且源还在；未 sync 仍是没有树；未知名只在拿到 catalog 之后失败。
