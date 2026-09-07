---
slug: autonomous-harness-evolution-16-migration-docs
created: 2026-09-04
criteria:
  - id: ac-001
    summary: Fixture test parses published example and named keys
    type: runtime
    pass_when: |
      `uv run pytest tests/test_harness_catalog_fixture.py -v` exits 0.
      TestHarnessCatalogFixture reads docs/concepts/harness.example.toml via
      tomllib.loads, or via load_harness_catalog if that symbol is importable,
      and asserts each plugin table has id, sha, and label in
      {official, gate, canary}. The test module does not define a catalog
      dataclass.
    status: pending
  - id: ac-002
    summary: Example lives under docs/ and is never auto-loaded
    type: code
    pass_when: |
      docs/concepts/harness.example.toml exists; neither harness.toml nor
      harness.example.toml exists at the repo root; src/molmcp/cli.py and
      src/molmcp/server.py contain no load of harness.toml.
    status: pending
  - id: ac-003
    summary: Concept page states disjoint registries and SHA identity
    type: docs
    pass_when: |
      docs/concepts/harness.md states MCP planes = molmcp.providers, harness
      plugins = Git SHA catalog, identity = Git SHA, official/gate/canary are
      labels on a SHA, maps harness.example.toml to consumed harness.toml, and
      states WikiSkill is not an init channel and must not wrap
      packages/molvis_open/molq/molexp.
    status: pending
  - id: ac-004
    summary: Notes file is two-repo decision plus SHA rule only
    type: docs
    pass_when: |
      .claude/notes/harness-contract.md states MolCrafts/harness is a new empty
      repo (not a rename of molcrafts-harness), old molcrafts-harness is
      archived or deleted only after cutover, and identity is Git SHA; it does
      not define catalog schema keys or a license rewrite.
    status: pending
  - id: ac-005
    summary: Migration runbook stops at step 5 before GitHub mutations
    type: docs
    pass_when: |
      docs/guides/harness-migration.md is numbered steps 1–5 and ends at STOP.
      It does not instruct the implementer to gh repo create, archive, bundle,
      or delete, and it forbids piling provider repos into the new catalog repo.
    status: pending
  - id: ac-006
    summary: License table does not relicense molmcp BSD-3-Clause
    type: docs
    pass_when: |
      docs/concepts/harness.md has a license table that records molmcp as
      BSD-3-Clause and does not change it; the root LICENSE file still begins
      with "BSD 3-Clause License".
    status: pending
  - id: ac-007
    summary: Pointer pages add no plane, entry point, or SHA labels
    type: docs
    pass_when: |
      architecture.md, provider-design.md, providers.md, write-a-provider.md,
      and cli.md each point at docs/concepts/harness.md and do not introduce a
      harness plane id, molmcp serve harness, or a molmcp.providers entry
      point. official/gate/canary do not appear as settings or provider-design
      contract terms.
    status: pending
  - id: ac-008
    summary: Docs do not advertise the old marketplace URL as current
    type: runtime
    pass_when: |
      A search of docs/ and .claude/notes/ finds no current-install command
      `/plugin marketplace add https://github.com/MolCrafts/molcrafts-harness`.
    status: pending
  - id: ac-009
    summary: SKILL.md untouched; installation uv warning preserved
    type: code
    pass_when: |
      src/molmcp/skill/SKILL.md is unmodified by this spec.
      docs/get-started/installation.md still contains the admonition titled
      "Without `--prerelease=allow`, uv will not install 0.6+" and the FastMCP
      4 / 4.0.0b5 explanation.
    status: pending
  - id: ac-010
    summary: Molvis workbench harness word is disambiguated
    type: docs
    pass_when: |
      docs/guides/molvis-workbench.md states that its out-of-tree playbook
      (molvis-agent-e2e/) is not the Git SHA plugin catalog documented in
      docs/concepts/harness.md.
    status: pending
  - id: ac-011
    summary: No MOLMCP_* env and no src/ catalog type
    type: code
    pass_when: |
      This spec adds no src/ file and no MOLMCP_* environment variable.
      uv run pytest tests/test_no_env_switches.py -v still exits 0.
    status: pending
  - id: ac-012
    summary: Regression script reproduces hard-coded catalog and license goldens
    type: runtime
    pass_when: |
      python regressions/autonomous-harness-evolution-16-migration-docs.py
      exits 0 after asserting hard-coded literals: published example plugin
      keys id/sha/label; root LICENSE contains "BSD 3-Clause License";
      docs/guides/harness-migration.md contains steps 1–5 and STOP before any
      create/archive/bundle/delete action. No third-party import or subprocess.
    status: pending
out_of_scope:
  - src/ changes including load_harness_catalog and any catalog type
  - gh repo create / archive / bundle / delete (separate authorization)
  - editing SKILL.md or introducing WikiSkill as an init wrapper
  - relicensing molmcp away from BSD-3-Clause
  - rewriting the installation.md uv --prerelease warning
---

# Acceptance — autonomous-harness-evolution-16-migration-docs

本 spec 完成的标志是：两仓契约与许可证表写在公开概念页，内部 notes 只保留「新建空仓 + SHA 身份」，退出手册在第 5 步 STOP，CI 钉住已发布示例能 parse 且不再把旧 marketplace URL 当现行安装地址。远程 GitHub 操作与 schema 实现都不在「done」里。

## AC-001 — Fixture test parses published example and named keys

`tests/test_harness_catalog_fixture.py` 是本契约的 CI 钉，不是 e2e。Schema 仍属 spec 02。

## AC-002 — Example lives under docs/ and is never auto-loaded

loader 是人类 / 未来消费者 / spec 02 测试，不是 `molmcp serve|init`。

## AC-003 — Concept page states disjoint registries and SHA identity

概念页是公开真相源：两个注册表、SHA、标签、示例映射、WikiSkill 否决。

## AC-004 — Notes file is two-repo decision plus SHA rule only

notes 不扩写成 schema 或许可证正文。

## AC-005 — Migration runbook stops at step 5 before GitHub mutations

手册可描述后续需要另授的操作，但不得把它们写成本步命令。

## AC-006 — License table does not relicense molmcp BSD-3-Clause

表是说明；`LICENSE` 文件仍是权威。

## AC-007 — Pointer pages add no plane, entry point, or SHA labels

指针页保持 pointer-only。

## AC-008 — Docs do not advertise the old marketplace URL as current

旧 URL 若出现，只能作为正在退出的名字，不能作为现行 `marketplace add`。

## AC-009 — SKILL.md untouched; installation uv warning preserved

init 通道与 uv 警告都不在本 diff 的重写范围。

## AC-010 — Molvis workbench harness word is disambiguated

同一词两个指称必须在 workbench 页划界。

## AC-011 — No MOLMCP_* env and no src/ catalog type

本 spec 的边界：docs + notes + 一个 fixture 测试。

## AC-012 — Regression script reproduces hard-coded catalog and license goldens

`/mol:impl` 交付时跑该脚本；金值写死在脚本里。
