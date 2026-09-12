---
spec: harness-locator-host-adapters-03-adapters
created: 2026-09-11
criteria:
  - id: ac-001
    summary: HostLayout is five path tuples with no frontmatter field
    type: code
    pass_when: |
      HostLayout fields are mcp_json, skill_dir, adapter, agents, rules;
      no frontmatter, commands, or molmcp_dev; every field value is tuple[str, ...]
    status: pending
  - id: ac-002
    summary: remap_frontmatter renames top-level keys without parsing values
    type: runtime
    pass_when: |
      folded description continuations stay when the key is kept;
      metadata/tools/model blocks are absent; host/ imports no yaml
    status: pending
  - id: ac-003
    summary: Per-host allowlist keeps recognized keys and drops the rest
    type: runtime
    pass_when: |
      grok keeps when-to-use; claude drops when-to-use; every host drops
      tools and model; expected strings are independent literals
    status: pending
  - id: ac-004
    summary: install_skill remaps packaged SKILL.md and does not copy2
    type: runtime
    pass_when: |
      install_skill("claude") has no when-to-use and no metadata;
      packaged src/molmcp/skill/SKILL.md still contains those keys
    status: pending
  - id: ac-005
    summary: place_components remaps text before write
    type: runtime
    pass_when: |
      a fenced skill with when-to-use placed on claude has no when-to-use;
      the same file on grok still has it; SKIP_MANAGED_USAGE_SKILL still fires
    status: pending
  - id: ac-006
    summary: Checkout primitives are gone; only init loses --source
    type: code
    pass_when: |
      resolve_bundle_source, materialize_daily, materialize_dev_index,
      activate_dev are not importable from molmcp.host;
      molmcp init --help has no --source; search and explore --help still do
    status: pending
  - id: ac-007
    summary: ADAPTER_TEXT points at usage skill, MCP, and catalog dirs
    type: code
    pass_when: |
      ADAPTER_TEXT mentions usage skill, MCP, skills/agents/rules and does
      not mention molmcp-dev or commands/ as destinations
    status: pending
  - id: ac-008
    summary: host/ isolation is the union of seven forbidden roots
    type: code
    pass_when: |
      FORBIDDEN_ROOTS includes client_config, cli, server, providers,
      discovery, components, harness; remap_frontmatter is not in
      molmcp.host.__all__
    status: pending
  - id: ac-009
    summary: Docs stop presenting init --source as a live route
    type: docs
    pass_when: |
      docs/concepts/harness.md and docs/guides/iterate-on-a-harness.md
      do not present molmcp init --source as a current command
    status: pending
  - id: ac-010
    summary: Regression reproduces hard-coded host fence goldens
    type: runtime
    pass_when: |
      regressions/harness-locator-host-adapters-03-adapters.py exits 0
      using install_skill, write_adapter, place_components only
    status: pending
out_of_scope:
  - Editing packaged SKILL.md source
  - PyYAML or nested YAML rewrite
  - Codex openai.yaml
  - Restoring init --source
---

# Acceptance — harness-locator-host-adapters-03-adapters

路径表仍是元组；frontmatter 改写是 layout.py 私有允许表加按行改顶层键名；init 不再接受 `--source`。
