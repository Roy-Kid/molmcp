---
title: 目录成员只来自组发现
status: approved
created: 2026-09-04
---

# 目录成员只来自组发现

## Summary

`list_planes` / `known_plane_ids` 的成员只来自 `discover_providers`，不再把硬编码官方名并进目录。`PlaneInfo.purpose` / `when_to_connect` 仍由 `planes.py` 的目录文案表提供（不是成员表）；`tools_hint` 只从实例的 `tool_specs()` 读取。树内官方实现与三行 entry point 保留。`create_stack` 签名不变。

## Design

今日 `_PROVIDER_META` 同时做三件事：成员并集、产品文案、工具名单。成员并集是第二份权威，删掉。工具名单与 `tool_specs()` 重复，删掉。文案没有别的非泛化家园：删光会让已发现的 molvis/molq/molexp 只剩通用回退句（law: one-home）。因此 **删除 `_PROVIDER_META` 作为成员表**，在 `planes.py`（layer 2）留下一张 **目录所有的** `purpose` / `when_to_connect` 文案表。该表的键 **不是** 成员；只对已经发现的名字查文案。

**成员。** `list_plane_infos` 与 `known_plane_ids` 的 provider id 只来自 `discover_providers`。默认 `only_available=True`（`probe()` 为假则静默省略，行为不变）。`include_unavailable_providers=True` 调用 `discover_providers(only_available=False)`，不得与文案表的键求并。`known_plane_ids(only_available=False)` 只并 `BUILTIN_PLANE_IDS` 与当次发现结果。文案表里有、发现结果里没有的名字 **不出现**。测试 fake / `monkeypatch` `discover_providers`，不为夹具加 pyproject 行。

**文案表（copy，非 membership）。** 在 `planes.py` 用新名字（例如 `_PROVIDER_COPY: dict[str, tuple[str, str]]`）保存今日三份产品句，**不含** tools 元组：

- molvis：`"Live molvis viewer: persistent Python namespace + browser canvas."` / `"User wants to draw, load, select, or interact with a molecule in 3D."`
- molq：`"molq job lifecycle: list/get/logs destinations; opt-in submit/cancel."` / `"User wants cluster jobs, queue status, or submission."`
- molexp：今日 `_PROVIDER_META` 的 purpose / when 两句（workspace navigation / experiment workspaces）

发现名在表中 → 用表中两句。发现名不在表中 → 现有泛化回退：`Provider plane '{name}' (entry point molmcp.providers).` 与 `When work needs the '{name}' product surface.`。不把 `purpose` / `when_to_connect` 做成 `ProviderBase` ClassVar，不写进 `Provider` Protocol。

**tools_hint。** 只从实例 duck-type 读取，写法与 `provider_available` 对 `probe` 相同：`specs_fn = getattr(provider, "tool_specs", None)`；可调用则 `tuple(spec.name for spec in specs_fn())`，否则 `()`。`planes.py` **不得** `import` `molmcp.providers.base`。`tool_specs` **不得** 加入 `Provider` Protocol。不增加 `tools_hint` ClassVar，不另做工具名单。不在本 spec 按 MUTATION 过滤（该标注也用在 molvis 会话工具上）。

**第一方。** `src/molmcp/providers/<name>/` 与当前三条 entry-point 名 `molexp` / `molq` / `molvis`。树内包与 `pyproject.toml` 三行不删。

**路由。** `_ROUTE_HINTS` 仍是核心关键词表，不是成员表。未知组员只出现在 `list_planes`，不被关键词路由。

**组装与配置。** `create_stack` 关键字参数名与全 `KEYWORD_ONLY` 冻结。`settings` 的 `molexp` / `molq` 具名键保留。无环境变量、无自动安装。四条件不改；mutation 仍仅限第一方（树内）。

**skill。** 两条路径，禁止让模型去调 `require_upstream()`，禁止尚未存在的 `*-mcp` 安装行；静默省略规则不变：

1. **核心不在** → `pip install molcrafts-molmcp`。
2. **核心在、namespaced 工具缺失** → 先检查 `--disable` 并重开该平面；否则安装对应科学包：molvis → `molcrafts-molvis`，molq → `molcrafts-molq`，molexp → `molexp`。不得再装 molmcp。

**保留。** `tests/providers/test_provider_base.py` 不改、不删。

### Reuse decision

librarian 报告：blueprint refresh deferred。

- `reuse discover_providers` — 成员的唯一来源。
- `reuse provider_available` 的 `getattr(probe)` — `tool_specs` 同一 duck-type。
- `reuse ProviderBase.tool_specs` — 只通过 getattr 取 `tools_hint`；`planes.py` 不 import base。
- `reuse` 今日三份 purpose/when 字面量 — 迁入 `planes.py` 文案表，去掉 tools 元组与成员并集。
- `reuse _ROUTE_HINTS`、`create_stack`、settings 具名键、树内三 provider、`test_provider_base.py`、`molmcp.providers.base` import 路径。
- `new` — 无 `purpose` ClassVar，无 Protocol 上的 `tool_specs`，无平行 tools 名单。文案表是旧表去掉成员与 tools 后的剩余职责，不是新概念层。

## Files to create or modify

- `src/molmcp/planes.py`
- `src/molmcp/skill/SKILL.md`
- `docs/concepts/provider-design.md`
- `tests/test_planes.py` (new)
- `tests/test_stack.py`
- `tests/test_settings.py`
- `tests/test_client_config.py`
- `regressions/autonomous-harness-evolution-14-provider-cutover.py` (new)

## Tasks

- [ ] Write failing unit tests for list_plane_infos and known_plane_ids (tests/test_planes.py → TestListPlaneInfos, TestKnownPlaneIds, TestRouteTask)
- [ ] Write failing unit tests for create_stack signature freeze (tests/test_stack.py → TestCreateStackSignature) and settings nested-key pin (tests/test_settings.py → TestNestedSchemaFirstParty)
- [ ] Implement catalog membership and copy table in src/molmcp/planes.py: delete `_PROVIDER_META`; ids only from discover_providers; purpose/when from catalog-owned copy table or generic fallback; tools_hint via getattr(tool_specs)
- [ ] Update src/molmcp/skill/SKILL.md recoveries (core-down vs namespaced-missing with frozen science-package names) and pin the text in tests/test_client_config.py; note in docs/concepts/provider-design.md that catalog membership is the entry-point group, keeping in-tree first-party and four-conditions
- [ ] Add regression example regressions/autonomous-harness-evolution-14-provider-cutover.py (public API only; hard-coded goldens, no third-party runtime)
- [ ] Run full check + test suite

## Testing strategy

单元测试只打本模块；`discover_providers` 用 fake / `monkeypatch`。绿色路径：`uv run pytest {path} -v`。`planes.py` 的测试不得 import `molmcp.providers.base` 来构造目录（可用带 `tool_specs` 方法的普通对象）。

- `tests/test_planes.py` → `TestListPlaneInfos` / `TestKnownPlaneIds` / `TestRouteTask`
  - Happy：patch 返回 `name="molvis"` 且带 `tool_specs()` 产出 `open` 的对象 → id 在列表中；`purpose` / `when_to_connect` 等于文案表字面量；`tools_hint == ("open",)`。
  - Happy：patch 返回 `name="demo"` 带 `tool_specs` 产出 `peek` → 泛化回退句 + `tools_hint == ("peek",)`。
  - Edge：无 `tool_specs` 的 Protocol 替身 → `tools_hint == ()`；若其 `name` 为 `molq` 仍用文案表两句。
  - Edge：发现为空 → ids 只有 `molcrafts`，即使文案表含 molvis/molq/molexp。
  - Edge：`include_unavailable_providers=True` 列出 `probe() is False` 的已发现实例；未发现的官方名不得因文案表出现。
  - Guard：`molmcp.planes` 无 `_PROVIDER_META`；`src/molmcp/planes.py` 源码不含 `providers.base`。
  - `TestRouteTask`：`route_task("draw dopamine")` 仍返回 `molvis`；未知组员只列出、不关键词路由。
- `tests/test_stack.py` → `TestCreateStackSignature`：`tuple(inspect.signature(create_stack).parameters) == ("collection", "config", "providers", "disable", "discover_entry_points", "enable_path_safety", "enable_response_limit", "response_limit_bytes", "validate_annotations", "instructions")` 且均为 `KEYWORD_ONLY`。
- `tests/test_settings.py` → `TestNestedSchemaFirstParty`：`_SCHEMA` 含 `molexp`/`molq` 为 `dict`，不含 `providers`；`_NESTED_SCHEMA["molq"] == frozenset({"database", "allowSubmit"})`，`_NESTED_SCHEMA["molexp"] == frozenset({"workspace"})`。
- `tests/test_client_config.py`：核心不在 → `pip install molcrafts-molmcp`；核心在而 namespaced 缺失 → `--disable` 重开，否则 `molcrafts-molvis` / `molcrafts-molq` / `molexp`；正文不含 `require_upstream`，不含 `molcrafts-*-mcp`。
- 树内包与三行 entry point仍在（回归钉扎）。`tests/providers/test_provider_base.py` 文件存在且契约测试仍被收集。

回归 `regressions/autonomous-harness-evolution-14-provider-cutover.py` 硬编码期望：

1. 无 `_PROVIDER_META`。
2. patch 发现为空 → `[p.id for p in list_plane_infos()] == ["molcrafts"]`。
3. patch `name="demo"` + `tool_specs`→`peek` → 泛化 purpose 含 `demo`，`tools_hint == ["peek"]` 或 `("peek",)`。
4. patch `name="molvis"` + `tool_specs`→`open` → purpose 等于 molvis 文案表字面量，`tools_hint` 含 `open`。
5. `include_unavailable_providers=True` 不发明未发现的官方名。
6. `create_stack` 参数名元组等于上列冻结字面量。
7. `PROVIDER_ENTRY_POINT_GROUP == "molmcp.providers"`；`pyproject.toml` 三行仍在；`find_spec("molmcp.providers.molexp")` 等非空。
8. `tests/providers/test_provider_base.py` 存在。

## Out of scope

- 不删除树内 `src/molmcp/providers/{molexp,molq,molvis}/`，不删三行 entry point。
- 不实现 `molcrafts-molvis-mcp` / `molcrafts-molq-mcp` / `molcrafts-molexp-mcp`；那些名字不是第一方定义，也不是 skill 的 pip 行。
- 不在 `ProviderBase` 上增加 `purpose` / `when_to_connect` / `tools_hint` ClassVar。
- 不把 `tool_specs` 加入 `Provider` Protocol；`planes.py` 不 import `providers.base`。
- 不改 `create_stack` 签名。
- 不把 settings 收成 generic `providers` 袋。
- 不把四条件改成「组内任一成员」。
- 不扩展 `_ROUTE_HINTS` 为插件注册表。
- 不自动安装、不引入环境变量。
- 不删除或修改 `tests/providers/test_provider_base.py`。
- 不让 skill 教模型调用 `require_upstream()`。
- 不改变 `probe()` 静默省略。
- 不新建 `provider_sdk` 包（01）。
- 不改 discovery schema。
