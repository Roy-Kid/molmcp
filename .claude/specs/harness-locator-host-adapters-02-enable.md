---
title: 按 enable 过滤 harness catalog 的 bundle 成员
status: done
created: 2026-09-11
grilled: true
---

# 按 enable 过滤 harness catalog 的 bundle 成员

## Summary

Catalog 里的 bundle 是作者自选的子包名（`sci` / `dev` / `daily` 都不保留），零个 bundle 合法，此时整份 catalog 就是一个隐式包。`molmcp init` 与 `molmcp serve` 只贡献当前源 `enable` 选中的成员。`enable=()`（`--disable all`）贡献零成员但不删除该源，sync 仍发布并晋升。未知名字在 sync / init / serve 失败并列出该 commit catalog 里真实存在的 bundle 名，不在 `config harness set` 时查 catalog。

## Domain basis

Not applicable (`science.required` is false).

## Design

前驱 01 已落地：`HarnessSource` 带 `locator`、`name`、`enable: tuple[str, ...] | None`（`None` = 全部，`()` = 全关，非空 = 名字）。本 spec **不改** locator CLI 与 `set_harness_source` 签名。

### 三个状态，三条路径

未 sync（`current is None`）与「启用了零个 bundle」不得共用同一条 `continue`：

| 状态 | 判别 | 读 catalog | 结果 |
|---|---|---|---|
| 未 sync | `current is None` | 否 | 不产生 Checkout，init 不声明文件 |
| 显式全关 | current 有值且 `enable=()` | 是 | `enabled_components` 返回 `()`；init 零文件；fold 该源 kept 为空；sync 仍 publish/promote。不是 CatalogError，不删源 |
| 未知名字 | current 有值且 enable 含未知 bundle | 是 | `CatalogError` 含 `unknown-bundle` 与实际名字 |

### `HarnessCatalog.enabled_components`

新方法 `enabled_components(self, names: tuple[str, ...] | None) -> tuple[ComponentSpec, ...]`，实现为对 `resolve_bundle` 的 fold：

1. `names == ()` → 立刻 `()`。
2. `bundles=()` 且 `names is None` → `self.components`（隐式整包）。
3. `bundles=()` 且 `names` 非空 → `CatalogError`（`unknown-bundle`，known 空）。
4. 否则 `selected = names or 全部 bundle 名`；未知名 → `CatalogError`；按 selected 调 `resolve_bundle`，对 `spec.id` **first-seen union**。
5. `"all"` 不是 catalog 保留名。settings 里的 `None` 传到本方法为 `None`（全部），不是去 `get_bundle("all")`。

删除 `_REQUIRED_BUNDLES`。orphan 行在 `bundles` 非空时不可达，只写 docstring。`sci`/`dev`/`daily` 都不是保留字。

跨 checkout 的 first-wins **只** 属于现有 `fold_components`：它遍历 `catalog.enabled_components(checkout.enable)`。禁止两条读者各写 expander。

### Checkout.enable

必填字段 `enable: tuple[str, ...] | None`，**没有默认值**。`activated_checkouts`：`current is None` 才 `continue`；否则即使 `enable=()` 也构造 Checkout。`fold_components` 对该源得到空 kept，但 `root_for` 仍成功。

### 三个读方

- `fold_components`：每源 load 后 `enabled_components(checkout.enable)`。
- `harness_install._declared_files`：无 SHA 仍提前 return 且不读 catalog；有 SHA 则必须 load 再 filter。不得 import `molmcp.harness`。
- `sync_source`：publish 之后无论是否已 current 都 load + `enabled_components`。空元组放行；未知名不 promote。

### Reuse decision

- reuse `resolve_bundle`、`get_bundle`、`BundleSpec`、`load_harness_catalog`、`CatalogError` / `unknown-bundle`
- reuse `HarnessSource.enable`（01）
- generalize `_declared_files`、`fold_components`、`activated_checkouts` 的 skip（仅 unsynced）
- new `enabled_components` — 方法不是新类型
- new `Checkout.enable` — 与 `Checkout.source` 同形的只读拷贝

## Files to create or modify

- `src/molmcp/components/catalog.py`
- `src/molmcp/components/models.py`
- `src/molmcp/harness.py`
- `src/molmcp/harness_install.py`
- `src/molmcp/harness_sync.py`
- `tests/test_components/test_catalog.py`
- `tests/test_harness.py`
- `tests/test_harness_install.py`
- `tests/test_cli_harness.py`
- `tests/test_harness_catalog_fixture.py`
- `docs/concepts/harness.md`
- `docs/concepts/harness.example.toml`
- `regressions/harness-locator-host-adapters-02-enable.py` (new)

## Tasks

- [x] Write failing unit tests for HarnessCatalog.enabled_components (tests/test_components/test_catalog.py → TestHarnessCatalog)
- [x] Implement enabled_components and drop _REQUIRED_BUNDLES in src/molmcp/components/catalog.py; tweak CatalogError docstring in src/molmcp/components/models.py
- [x] Write failing unit tests for Checkout.enable and fold_components filtering (tests/test_harness.py → TestFoldComponents, TestActivatedCheckouts)
- [x] Implement Checkout.enable, unsynced-only skip, and enabled_components iteration in src/molmcp/harness.py
- [x] Write failing unit tests for empty-enable placement vs unsynced skip (tests/test_harness_install.py) and unknown names at sync (tests/test_cli_harness.py → TestHarnessSyncErrors)
- [x] Implement enabled_components calls in src/molmcp/harness_install.py and src/molmcp/harness_sync.py; stop requiring daily+dev in docs/concepts/harness.md, harness.example.toml, and tests/test_harness_catalog_fixture.py
- [x] Add regression example regressions/harness-locator-host-adapters-02-enable.py (public API only; hard-coded goldens, no third-party runtime)
- [x] Run full check + test suite

## Testing strategy

`TestHarnessCatalog`：并集字面量 `("skill.notes", "rule.style", "agent.reviewer")`；`()` 成功空；`("nope",)` 含 `unknown-bundle`；零 bundle + `None` = 全部 components。`TestActivatedCheckouts`：`current is None` 省略；`enable=()` 仍出现在返回值。`TestHarnessInstall`：空 enable 零文件但坏 catalog 仍失败（证明读了 catalog）。回归脚本只调 `load_harness_catalog` + `enabled_components`。

## Out of scope

- locator CLI 与 `set_harness_source`（01）
- host adapter、删除 `init --source`（03）
- 把 `config harness remove` 当成关开关
- 在 set 时对照 catalog 验名字
- 改 `init --enable/--disable`（plane）
- `harness_install` import `molmcp.harness`
