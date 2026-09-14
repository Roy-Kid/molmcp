---
title: Harness locator 与可选 sci/dev 束的公开文档
status: done
created: 2026-09-11
grilled: true
---

# Harness locator 与可选 sci/dev 束的公开文档

## Summary

读者按三步把 harness 源写进设置、钉到某个 commit、再装进 AI 客户端：`molmcp config harness set <locator>`（可选 `--alias`，束开关只有 `--enable` / `--disable`）、`molmcp harness sync <alias>`、`molmcp init <host>`。Locator 是 `MolCrafts/harness`、`owner/repo[@ref]` 或 `~/` / 绝对路径；检出本身就是 locator。`molmcp init <host> --enable/--disable` 仍然只开关 plane。公开示例用可选的 `sci` / `dev` 束演示语法，不再声称每个 catalog 必须有 `daily` 和 `dev`。`README.md` 不动。

## Domain basis

Not applicable (`science.required` is false).

## Design

本 spec 只改公开文档和钉住这些文档的契约测试。Locator 与束开关由 01 交付；host 放置由 03 交付。

束的 `--enable` / `--disable` 只出现在 `molmcp config harness set <locator> [--alias] [--enable|--disable …]`。`molmcp init --enable/--disable` 走 `resolve_plane_toggles`。不发明 `--enable-bundle`。不把 `init --enable sci` 教成选束。

主循环：set → sync → init。`init` 不带 `--source`。iterate 指南可保留「One route this is not」仅当 03 已删该旗——03 要求指南不再把 `--source` 写成现行路由，本 spec 与之一致：页顶循环无 `--source`。

`docs/concepts/harness.example.toml` 演示五个 component kind；束名改为可选 `sci` 与 `dev`。删除「每个 catalog 必须定义 daily 和 dev」。继续经 `load_harness_catalog` 加载。

`TestHarnessCatalogFixture` 删除 `_REQUIRED_BUNDLES`。页面钉 locator `set`；四处都不出现 `config harness set --name`、`--enable-bundle`、`init --enable sci`。`_MARKETPLACE_ADD` 保持。fixture 不得 import `molmcp.cli`。

### Reuse decision

- reuse `load_harness_catalog`、`HarnessSource`、`TestHarnessCatalogFixture`、`_MARKETPLACE_ADD`、`resolve_plane_toggles`
- new — 无生产符号

## Files to create or modify

- `docs/concepts/harness.md`
- `docs/concepts/harness.example.toml`
- `docs/guides/iterate-on-a-harness.md`
- `docs/reference/cli.md`
- `docs/get-started/installation.md`
- `tests/test_harness_catalog_fixture.py`
- `regressions/harness-locator-host-adapters-04-docs.py` (new)

## Tasks

- [x] Write failing unit tests for TestHarnessCatalogFixture pinning locator set, optional sci/dev, no --enable-bundle, plane-only init flags, kept marketplace-add grep
- [x] Rewrite docs/concepts/harness.example.toml so bundles are optional sci and dev
- [x] Rewrite docs/concepts/harness.md authoring, loop, and bundle grammar
- [x] Rewrite docs/guides/iterate-on-a-harness.md to set → sync → init with no --source and no bundle flags on init
- [x] Rewrite docs/reference/cli.md and docs/get-started/installation.md
- [x] Add regression example regressions/harness-locator-host-adapters-04-docs.py (public API only; hard-coded goldens, no third-party runtime)
- [x] Verify against load_harness_catalog on the published example with literal bundle names sci and dev
- [x] Run full check + test suite

## Testing strategy

扩展 `tests/test_harness_catalog_fixture.py`。期望 `{b.name for b in catalog.bundles} == {"sci", "dev"}` 写在测试里。回归脚本只 `load_harness_catalog`。

## Out of scope

- README.md
- CLI / settings / catalog.py / host 实现（01–03）
- 发明 `--enable-bundle` 或让 init --enable 接受束名
- argparse 测试
