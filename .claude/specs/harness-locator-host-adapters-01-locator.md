---
title: Harness 源定位符与别名
status: done
created: 2026-09-11
grilled: true
---

# Harness 源定位符与别名

## Summary

操作者用一条定位符登记 harness 源：`molmcp config harness set molcrafts/harness`（也可写 GitHub URL、`owner/repo[@ref]`、或 `~/` / 绝对路径），可选 `--alias`，可选 `--enable` / `--disable`。设置文件每个条目只持久化 `name`、`locator`、`enable` 三个操作员字段；GitHub 身份与本地路径由构造时解析得到，不写成并列的家。同一 origin 无论怎么拼都 upsert 成一条。旧的 `owner` / `repo` / `ref` / `path` 键硬切。本 spec 只把 `enable` 存下来，不按它过滤 init/serve——那是链上的 02。

## Domain basis

Not applicable (`science.required` is false).

## Design

### 叶：`parse_harness_locator`

新建 `src/molmcp/components/locator.py`（stdlib only）。公开：`LocatorError(ValueError)`、`ParsedHarnessLocator`（frozen）、`parse_harness_locator(text: str) -> ParsedHarnessLocator`。

`ParsedHarnessLocator` 字段：`locator`（原文）、`kind`（`"github"` | `"local"`）、`origin_key`、`ref`（无则为 `""`）、`owner` / `repo`（仅 github，已小写、已剥 `.git`）。

接受（空白一律拒绝）：

| 输入 | kind | origin_key |
|---|---|---|
| `https://github.com/Owner/repo`，可选 `.git`、可选尾斜杠 | github | 小写 `owner/repo` |
| `github.com/Owner/repo[.git][/]` | github | 同上 |
| `Owner/repo`、`Owner/repo@ref`、`molcrafts/harness` | github | 小写 `owner/repo`；`@ref` 只进 `ref` |
| 绝对路径 | local | `str(Path(raw).expanduser().resolve())`；此时路径不必存在 |
| `~/…` | local | 先 expanduser 再 resolve |

拒绝：相对路径、`http://`、`github:` 前缀、SSH、URL 多余 path 段、反斜杠。`www.github.com` 与 `github.com` 同一身份。ref **不是**身份：`MolCrafts/harness@dev` 与 `https://github.com/molcrafts/harness.git` 的 `origin_key` 都是 `molcrafts/harness`。

本模块不得出现 `HarnessSource`、别名、`enable`。不得 import `discovery`、`settings`、`urllib`、`git`。调用方 `from molmcp.components.locator import parse_harness_locator`，不经 package `__all__`。

### `HarnessSource` 只持久化操作员字段

dataclass 字段恰好：

- `name: str` — 别名。非空、无空白；`/` 仍由 `pointer_path` 在变成路径时拒绝。
- `locator: str` — 操作者写下的原文。
- `enable: tuple[str, ...] | None = None` — `None` = 全部（哨兵，02 解释为 `"all"`）；`()` = 显式全关，源留下；非空元组 = bundle 名。词法走 `COMPONENT_NAME_PATTERN`，不在 set 时查 catalog。

构造时调用一次 `parse_harness_locator(locator)`。派生属性（不是字段，不进 JSON）：`origin_key`、`ref`、`owner`、`repo`、`path`（local 为写下的规范路径，github 为 `""`）。`asdict` / 落盘只有三个操作员字段。`_harness_entry` 剥离任何派生键。

加载时条目带 `owner` / `repo` / `path`（即便同时有 `locator`）→ `SettingsError`，提示 `re-run molmcp config harness set <locator>`。缺 `locator` 同样拒绝。缺 `enable` 键 → `None`。写出：`None` 不落 `enable` 键；`()` 落 `[]`；具名落字符串数组。同一文件 `name` 重复或 `origin_key` 重复都拒绝。

`is_local`：`kind == "local"`。`assert_servable` 读派生属性；`enable` **不读**。删除 `HARNESS_COORDINATES`。

### upsert 与 CLI

`set_harness_source(path, locator, *, alias=None, enable=(), disable=())`：按 `origin_key` upsert，不是按别名。同一 GitHub 仓换拼法更新那一条的 `locator` 原文。插入且 `alias is None` → `"origin"`（`DEFAULT_HARNESS_ALIAS`）；若 `origin` 已被另一 origin 占用 → 要求 `--alias`。更新且 `alias is None` → 保留已有别名。`--alias` 改名；新名冲突则拒绝。新源追加在列表末尾。

`enable` / `disable` 空序列 = 不改该字段：

- 插入且两次都空 → `None`（全部）。
- `--enable all` → `None`（写成省略键）。不得与具名 `--enable` 同一次出现。
- `--disable all` → `()`，源留下。不得与 `--enable all` 同一次出现。
- 具名 `--enable` / `--disable`：对显式名单做并/差。当前为 `None`（全部）时，仅具名 `--enable` 把哨兵换成这次的具名列表；具名 `--disable` 在哨兵上拒绝（没有 catalog 不能做补集；02 消费 catalog）。

`match_harness_source(sources, token)`：先精确比 `name`，再 parse locator 比 `origin_key`。`remove_harness_source`、`harness sync|rollback` 都走它。

CLI：`molmcp config harness set <locator> [--alias NAME] [--enable TOKEN] [--disable TOKEN]`。丢掉 `--name --owner --repo --ref --path`。`--enable` / `--disable` 可重复。CLI **不** import `locator.py` 或 `harness_paths`。

### 改名走 `harness_sync.relocate_pointer`

`pointer_path` 仍只按别名命名。`settings` 不得 import `harness_paths`（环）。CLI 不得在 `set_harness_source` 之后自己改指针。

`relocate_pointer(config, settings_path, *, locator, name, …)` 住在 `harness_sync`：命中 origin → 用 `pointer_path` 命名旧/新文件 → `set_harness_source` 改别名 → 旧指针存在则 `os.replace`。目标已存在则拒绝且设置不动。无指针文件则只改设置。

CLI 的 set：若命中且 `--alias` 与当前不同 → `relocate_pointer`；否则 `set_harness_source`。

### 字段消费者

`local_checkout_path` 仍是唯一 `~` 展开：读派生 `path`。github 臂 reuse `GitHubTransport.resolve_commit(owner, repo, ref or None)`。本地臂 reuse `LocalGitTransport`。`activated_checkouts` 仍只读 `source.name` 调 `pointer_path`。`server.py` 删除 `HARNESS_COORDINATES` / `_HARNESS_KEYS`。

### Reuse decision

- reuse `GitHubTransport.resolve_commit`、`LocalGitTransport`、`pointer_path`、`local_checkout_path`、`ImmutableGitStore.publish`
- generalize `HarnessSource`、`set_harness_source`、`remove_harness_source`、`assert_servable`
- new `parse_harness_locator` — discovery `_parse_github_spec` 在 L4 且语法是 `github:` 前缀；settings 不得 import discovery
- new `match_harness_source`、`relocate_pointer`、`DEFAULT_HARNESS_ALIAS`

## Files to create or modify

- `src/molmcp/components/locator.py` (new)
- `src/molmcp/settings.py`
- `src/molmcp/harness_sync.py`
- `src/molmcp/cli.py`
- `src/molmcp/harness.py`
- `src/molmcp/harness_paths.py` (no new public writer; `pointer_path` stays namer)
- `src/molmcp/server.py`
- `tests/test_components/test_locator.py` (new)
- `tests/test_settings.py`
- `tests/test_cli_config.py`
- `tests/test_cli_harness.py`
- `tests/test_harness.py`
- `tests/test_stack.py`
- `tests/test_harness_catalog_fixture.py`
- `docs/concepts/harness.md`
- `regressions/harness-locator-host-adapters-01-locator.py` (new)

## Tasks

- [x] Write failing unit tests for parse_harness_locator (tests/test_components/test_locator.py → TestParseHarnessLocator)
- [x] Implement parse_harness_locator in src/molmcp/components/locator.py
- [x] Write failing unit tests for HarnessSource / set_harness_source / match_harness_source / load hard-cut (tests/test_settings.py → TestHarnessSource, TestHarnessSourceEdit)
- [x] Generalize HarnessSource and set_harness_source in src/molmcp/settings.py; implement match_harness_source
- [x] Write failing unit tests for relocate_pointer and positional CLI (tests/test_cli_config.py → TestConfigHarness; tests/test_cli_harness.py; tests/test_harness.py; tests/test_stack.py)
- [x] Implement relocate_pointer in src/molmcp/harness_sync.py; wire cli.py; rewrite assert_servable; drop HARNESS_COORDINATES; update docs/concepts/harness.md JSON snippet
- [x] Add regression example regressions/harness-locator-host-adapters-01-locator.py (public API only; hard-coded goldens, no third-party runtime)
- [x] Run full check + test suite

## Testing strategy

单测镜像 `src/`，一类一函数。`TestParseHarnessLocator`：`MolCrafts/harness`、`https://github.com/MolCrafts/harness.git/`、`github.com/MolCrafts/harness` 的 `origin_key` 字面量都是 `"molcrafts/harness"`；`./checkout` 抛错；`locator.py` AST 不含 discovery/settings。`TestHarnessSourceEdit`：同一 origin 换拼法仍一条；第一条无 alias 名为 `origin`；`--disable all` 落 `[]` 且条目还在；旧 `owner/repo/path` 文件加载失败。`TestConfigHarness`：位置参数 locator；旧坐标旗从 parser 消失；改 alias 走 `relocate_pointer`。`cli.py` AST 不 import locator 或 harness_paths。回归脚本用独立字面量钉 `origin_key == "molcrafts/harness"` 与默认别名 `"origin"`。

## Out of scope

- catalog 过滤、必选 bundle（02）
- host adapter、删除 `init --source`（03）
- 叙述性文档全页改写（04）；本切片只改概念页被 fixture 构造的 JSON
- 旧 schema 自动迁移
- `github:` discovery spec、SSH、`http://`
- 在 `servable_sources` 里消化 `enable`
