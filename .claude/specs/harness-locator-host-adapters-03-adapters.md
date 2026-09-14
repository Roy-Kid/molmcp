---
title: Host 适配器：frontmatter 重映射并拆除 checkout 路由
status: done
created: 2026-09-11
grilled: true
---

# Host 适配器：frontmatter 重映射并拆除 checkout 路由

## Summary

`molmcp init <host>` 写入 skill / agent / rule 时，按该宿主的顶层 YAML 键允许表改写 frontmatter：能认出的键留下，认不出的丢掉。托管用法技能仍由 `install_skill` 从包装内 `SKILL.md` 读出、改写、写出，不走 `place_components`。同时拆除 `molmcp init --source` 以及 `HostLayout.commands` / `molmcp_dev`。`search` / `explore` 的 `--source` 不动。

## Domain basis

Not applicable (`science.required` is false).

## Design

`HostLayout` 仍是路径元组（`mcp_json`、`skill_dir`、`adapter`、`agents`、`rules`）。没有 `frontmatter` 字段。删除 `commands`、`molmcp_dev`。`test_every_field_value_is_a_tuple_of_str` 保留。

frontmatter 允许表是 `layout.py` 里 `HOSTS` 旁边的模块私有 `MappingProxyType`，只有 `remap_frontmatter(text, host)` 读。不进 `HostLayout`，不进 `molmcp.host.__all__`。禁止叫 `adapt_frontmatter`（adapter 已指指针文件）。

一张表，skill / agent / rule 同一条管道。未列出的顶层键（`tools`、`model`、`metadata`）整块丢弃含续行。

| 宿主 | 留下的源键（恒等改名） |
|---|---|
| grok | name, description, when-to-use, user-invocable, disable-model-invocation, argument-hint |
| claude | name, description, user-invocable, disable-model-invocation, argument-hint |
| cursor | name, description, disable-model-invocation |
| codex | name, description |

文法：只改顶层键名，不解析 value，无 PyYAML。有开头与闭合 `---` 才当 fence，否则原文返回。folded `>` 续行原样跟随被留下的键。

`place_components` 仍是拷文件；remap 是写出前一步。`install_skill` 不走 `place_components`：读包装 SKILL.md → remap → `_write`。

删除 `resolve_bundle_source`、`materialize_daily`、`materialize_dev_index`、`activate_dev`。`init` 子解析器删除 `--source`。`_init`：`render_init` → `install_skill` → `write_adapter` → `install_harness_components`。

`ADAPTER_TEXT` 只指向用法技能、MCP、catalog 的 skills/agents/rules，不含 `molmcp-dev` / `commands/`。

隔离并集：`client_config`、`cli`、`server`、`providers`、`discovery`、`components`、`harness`。

### Reuse decision

- reuse `place_components`、`layout_for` / `HOSTS` / `SKILL_NAME`、`write_adapter`、`SKIP_MANAGED_USAGE_SKILL`、`_write`
- generalize `install_skill`（copy2 → read/remap/write）
- new `remap_frontmatter` in layout.py — gate YAML walker 会拆 value，不拟合
- 不 generalize 四个 checkout 原语：删除

## Files to create or modify

- `src/molmcp/host/layout.py`
- `src/molmcp/host/install.py`
- `src/molmcp/host/place.py`
- `src/molmcp/host/__init__.py`
- `src/molmcp/cli.py`
- `tests/test_host/test_layout.py`
- `tests/test_host/test_install.py`
- `tests/test_host/test_place.py`
- `tests/test_client_config.py`
- `tests/test_harness_install.py`
- `docs/concepts/harness.md`
- `docs/guides/iterate-on-a-harness.md`
- `regressions/harness-locator-host-adapters-03-adapters.py` (new)

## Tasks

- [x] Write failing unit tests for remap_frontmatter and the shrunk HostLayout (tests/test_host/test_layout.py → TestRemapFrontmatter, TestHostLayout)
- [x] Implement remap_frontmatter, private maps, and the five-field HostLayout in src/molmcp/host/layout.py
- [x] Write failing unit tests for remapped install_skill and deleted checkout primitives (tests/test_host/test_install.py → TestInstallSkill)
- [x] Generalize install_skill; delete checkout primitives; rewrite ADAPTER_TEXT
- [x] Write failing unit tests for remapped place_components (tests/test_host/test_place.py → TestPlaceComponents)
- [x] Remap frontmatter in place_components before write
- [x] Remove init --source from cli.py; update tests/test_client_config.py and tests/test_harness_install.py
- [x] Strike live --source / molmcp-dev / commands destinations from docs/concepts/harness.md and docs/guides/iterate-on-a-harness.md
- [x] Add regression example regressions/harness-locator-host-adapters-03-adapters.py (public API only; hard-coded goldens, no third-party runtime)
- [x] Run full check + test suite

## Testing strategy

`TestRemapFrontmatter`：folded description 续行在键留下时原样；metadata/tools/model 丢掉；无 fence 原文返回。`TestInstallSkill`：claude 无 when-to-use/metadata；grok 有 when-to-use 无 metadata；包装源文件仍含那些键。`init --help` 无 `--source`；`search --help` 仍有。隔离七个 forbidden roots。

## Out of scope

- 改包装 SKILL.md 源文
- PyYAML、解析 value、嵌套改写
- 分 kind 的三张表；HostLayout.frontmatter 字段
- 从 molmcp.host 导出 remap_frontmatter
- 删除 search/explore --source
- Codex openai.yaml
- 恢复 init --source
