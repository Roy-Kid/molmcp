---
title: 两仓契约、许可证表与旧仓退出手册
status: approved
created: 2026-09-04
---

## 2026-09-07 修订：`regressions/` 已删除

本仓从未发布过任何版本，没有可回归的对象；`regressions/` 也从来不在 CI 里跑
（`uv run pytest -v` 只跑 `tests/`），以致其中一个脚本烂掉很久无人察觉。整个目录
已删。**下文凡是要求新增 `regressions/<slug>.py` 的任务与判定一律作废**；相应的
正确性证明由 `tests/` 下的单元与结构性守卫承担。


# 两仓契约、许可证表与旧仓退出手册

## Summary

本仓公开文档与内部契约写清两件事：MolCrafts 的 MCP 产品仍是 `MolCrafts/molmcp`（BSD-3-Clause，不改许可）；agent harness 插件目录的目标仓是新建空仓 `MolCrafts/harness`（Git SHA 身份），不是把旧 marketplace `MolCrafts/molcrafts-harness` 改名。概念页给出许可证表与 `harness.example.toml`（文档示例；被消费的文件名是 `harness.toml`），旧仓退出手册只写到第 5 步 STOP。远程 GitHub 的 create / archive / bundle / delete 需单独授权，本 spec 不执行、不调用 `gh`。

## Design

**两仓，不是一次 rename。** Discuss 已定：`MolCrafts/harness` 是**新建空仓**；旧 `MolCrafts/molcrafts-harness` 只在 cutover **之后** archive 或 delete。本 spec 不创建、不归档、不打包历史、不删除任何远程仓。agent 面向文字不得再把 `https://github.com/MolCrafts/molcrafts-harness` 写成现行 marketplace 安装地址。新仓是空目录仓，不把 molq / molexp / molvis / molpy 等 provider 仓或 MCP 平面堆进去。

**两个不相交的注册表。** MCP planes 的权威仍是 `molmcp.providers` 入口点（`molvis` / `molq` / `molexp` …）。Harness 插件的权威是 Git SHA 目录。没有 harness plane id，没有 `molmcp serve harness`，没有 `molmcp.providers` 下的 harness 入口点。`official` / `gate` / `canary` 只是某个 SHA 上的标签，写在概念页与示例里，不写进 `provider-design.md`、不写进 settings、不发明 `MOLMCP_*` 环境变量。

**示例文件 vs 被消费的 `harness.toml`。** 本仓只发布 `docs/concepts/harness.example.toml`。被人类 / 未来 harness 消费者 / spec 02 `load_harness_catalog`（若已可 import）读取的文件名是 `harness.toml`。示例永不放仓库根，永不从 cwd 自动加载；`molmcp serve` 与 `molmcp init` 不是 loader。Schema 所有权在 spec 02：本 spec 不新增 catalog 类型、不复刻 `Capability` / `load_catalog`。

**所有权。** `.claude/notes/harness-contract.md` 只记两条长期规则：两仓决定（新建空仓，旧仓 cutover 后退出）+ 身份 = Git SHA。键名的短表与示例同住 `docs/concepts/harness.md`。`docs/guides/harness-migration.md` 只是退出 runbook（步骤 1–5 后 STOP）。`LICENSE` 仍是 molmcp 的 BSD-3-Clause 权威；许可证表是副本说明，不重新授权。`src/molmcp/skill/SKILL.md` 是 `molmcp init` 通道，本 spec 不改。WikiSkill 不是 init 通道，不得包装 `packages` / `molvis_open` / `molq_*` / `molexp_*`，禁止 CoT 包装。

**指针页（只加一句，不扩写契约）。** `architecture.md`、`provider-design.md`、`providers.md`、`write-a-provider.md`、`cli.md` 各加「harness 不是 plane / 不是入口点」的指针，链到概念页。`molvis-workbench.md` 把该页已有的 out-of-tree「harness」（`molvis-agent-e2e/` 剧本）与 Git SHA 目录拆开。`installation.md` **合并**一条 Related 指针，保留现有 uv `--prerelease` 警告原文。`zensical.toml` 只加导航条目。

**退出手册（文档内容，不是本 spec 要执行的 `gh`）。**

1. 盘点本仓仍把 `MolCrafts/molcrafts-harness` 写成现行 marketplace 的句子；用测试钉死「不得再当现行 `marketplace add`」。
2. 落盘两仓契约：目标仓 `MolCrafts/harness` 为新建空仓；身份 = Git SHA。
3. 发布 `docs/concepts/harness.example.toml`，并写清示例文件名 vs 被消费的 `harness.toml`。
4. 概念页放许可证表：molmcp BSD-3-Clause 不改；旧仓 MIT；新仓许可证在 create 时另授，禁止把 BSD-3-Clause 抄过去。
5. **STOP。** 不 `gh repo create`、不 archive、不 bundle、不 delete。远程 GitHub 操作需单独授权。禁止 provider 仓堆。

**CI pin。** `tests/test_harness_catalog_fixture.py` 只断言：已发布示例能 parse，且携带概念页点名的键。优先 `tomllib.loads`；若 spec 02 的 `load_harness_catalog` 可 import 则改走它。不在本 spec 实现 catalog 类型。

### Reuse decision

- `reuse tomllib.loads` — 解析已发布示例；本 spec 不造 catalog 类型。
- `reuse load_harness_catalog`（仅当 spec 02 已可 import）— schema 的唯一加载入口；fixture 调用它，不平行实现。
- `new — molmcp.discovery.overlay.catalog.load_catalog` 吃的是 `[[capability]]` overlay 目录，不是 Git SHA 插件 pin；拿来当 harness catalog 会变成平行概念。
- `pattern tests/test_version_single_source.py` — `tomllib` 钉文件契约。
- `pattern tests/test_tool_hints.py` — 钉死 agent 面向字符串不得广告失效地址。
- `pattern docs/guides/molvis-workbench.md` — 保留该页 out-of-tree 剧本用词，但必须与 Git SHA 目录划界。

## Files to create or modify

- `docs/concepts/harness.md` (new)
- `docs/concepts/harness.example.toml` (new)
- `docs/guides/harness-migration.md` (new)
- `.claude/notes/harness-contract.md` (new)
- `tests/test_harness_catalog_fixture.py` (new)
- `regressions/autonomous-harness-evolution-16-migration-docs.py` (new)
- `docs/concepts/architecture.md`
- `docs/concepts/provider-design.md`
- `docs/concepts/providers.md`
- `docs/guides/write-a-provider.md`
- `docs/reference/cli.md`
- `docs/guides/molvis-workbench.md`
- `docs/get-started/installation.md`
- `zensical.toml`
- `.claude/notes/README.md`

## Tasks

- [ ] Write failing unit tests for TestHarnessCatalogFixture (tests/test_harness_catalog_fixture.py → TestHarnessCatalogFixture)
- [ ] Add docs/concepts/harness.md with disjoint registries, SHA identity, official/gate/canary as SHA labels, license table, example-vs-consumed mapping, and WikiSkill-not-init
- [ ] Add docs/concepts/harness.example.toml carrying the keys harness.md names (never at repo root)
- [ ] Add .claude/notes/harness-contract.md (two-repo decision + SHA rule only) and index it in .claude/notes/README.md
- [ ] Add docs/guides/harness-migration.md as runbook steps 1–5 ending STOP (no create/archive/bundle/delete actions)
- [ ] Add pointer-only sentences in docs/concepts/architecture.md, docs/concepts/provider-design.md, docs/concepts/providers.md, docs/guides/write-a-provider.md, docs/reference/cli.md, docs/guides/molvis-workbench.md; MERGE a Related pointer into docs/get-started/installation.md without rewriting the uv --prerelease warning; add nav entries in zensical.toml
- [x] ~~Add regression example regressions/autonomous-harness-evolution-16-migration-docs.py (public API only; hard-coded goldens, no third-party runtime)~~ — 作废：`regressions/` 已删除（2026-09-07）
- [ ] Verify against the published example parse, named keys, LICENSE still BSD-3-Clause, migration STOP, and no current molcrafts-harness marketplace add
- [ ] Run full check + test suite

## Testing strategy

单元测试只覆盖本 spec 拥有的文档契约，路径 `tests/test_harness_catalog_fixture.py`，类 `TestHarnessCatalogFixture`（与 `tests/test_version_single_source.py` / `tests/test_tool_hints.py` 同级的契约钉，不镜像 `src/`，因为本 spec 不改 `src/`）。单测绿 = `uv run pytest tests/test_harness_catalog_fixture.py -v`。解析走 `tomllib.loads`，若 `load_harness_catalog` 可 import 则改走它；禁止在测试里定义 catalog dataclass。

- Happy path：`docs/concepts/harness.example.toml` parse 成功；每个 `[[plugin]]` 表含概念页点名的 `id` / `sha` / `label`；`label` 为 `official` 或 `gate` 或 `canary`。
- Edge：仓库根不存在 `harness.toml` 或 `harness.example.toml`；`src/molmcp/cli.py` 与 `src/molmcp/server.py` 不出现对 `harness.toml` 的加载；`docs/` 与 `.claude/notes/` 不含现行安装命令 `/plugin marketplace add https://github.com/MolCrafts/molcrafts-harness`；`src/molmcp/skill/SKILL.md` 本 spec 不改。
- 不测 `molmcp serve` / `init` 的进程编排，不测 GitHub API。

回归示例 `regressions/autonomous-harness-evolution-16-migration-docs.py`：读已发布示例与 `LICENSE`、迁移手册，断言硬编码字面量（无第三方运行时）——`plugin` 表键 `id`/`sha`/`label`；`LICENSE` 含 `BSD 3-Clause License`；`docs/guides/harness-migration.md` 含编号步骤 1–5 与 STOP，且 STOP 出现在任何 create/archive/bundle/delete 动作说明之前（本手册把后者标成需另授的后续，而不是本步命令）。

## Out of scope

- 任何 `src/` 改动，包括 `load_harness_catalog`、catalog 类型、plane、入口点、settings 键、`MOLMCP_*` 环境变量。
- 远程 GitHub：`gh repo create MolCrafts/harness`、archive/delete `molcrafts-harness`、bundle 历史、把 provider 仓推进新仓。需单独授权。
- 编辑 `src/molmcp/skill/SKILL.md`。WikiSkill 不是 init 通道，不得包装 `packages` / `molvis_open` / `molq_*` / `molexp_*`，禁止 CoT 包装。
- 重写 `docs/get-started/installation.md` 的 uv `--prerelease` 警告。
- 把 molmcp 从 BSD-3-Clause 改成其他许可。
- 在 `provider-design.md` 或 settings 里定义 `official`/`gate`/`canary`。
- 给尚未创建的 `MolCrafts/harness` 写现行 `/plugin marketplace add` 安装行。
