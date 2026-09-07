---
slug: autonomous-harness-evolution-14-provider-cutover
spec: autonomous-harness-evolution-14-provider-cutover
created: 2026-09-04
criteria:
  - id: ac-001
    summary: Catalog ids come only from discover_providers
    type: code
    pass_when: |
      molmcp.planes has no _PROVIDER_META; list_plane_infos and
      known_plane_ids never union copy-table keys into membership;
      an empty discover_providers patch yields only molcrafts even
      though the copy table still names molvis/molq/molexp
      (tests/test_planes.py::TestListPlaneInfos, TestKnownPlaneIds).
    status: pending
  - id: ac-002
    summary: purpose/when live in planes.py copy table, not ProviderBase
    type: code
    pass_when: |
      A discovered name present in the planes.py purpose/when table
      gets those two literals on PlaneInfo; a discovered name absent
      from the table gets the generic fallback strings; ProviderBase
      has no purpose or when_to_connect ClassVar.
    status: pending
  - id: ac-003
    summary: tools_hint via getattr(tool_specs); planes does not import base
    type: code
    pass_when: |
      list_plane_infos sets tools_hint from getattr(provider,
      "tool_specs", None) when callable, else (); src/molmcp/planes.py
      does not import molmcp.providers.base; Provider Protocol has no
      tool_specs member; ProviderBase has no tools_hint ClassVar.
    status: pending
  - id: ac-004
    summary: include_unavailable_providers lists discovered names only
    type: code
    pass_when: |
      list_plane_infos(include_unavailable_providers=True) uses
      discover_providers(only_available=False) and does not add a
      copy-table name that discover_providers did not return; a
      probe-false discovered provider still appears.
    status: pending
  - id: ac-005
    summary: Freeze create_stack keyword-only signature
    type: code
    pass_when: |
      inspect.signature(molmcp.create_stack).parameters names equal
      (collection, config, providers, disable, discover_entry_points,
      enable_path_safety, enable_response_limit, response_limit_bytes,
      validate_annotations, instructions) and each kind is KEYWORD_ONLY.
    status: pending
  - id: ac-006
    summary: Keep in-tree official providers and pyproject rows
    type: code
    pass_when: |
      find_spec("molmcp.providers.molexp"), find_spec("molmcp.providers.molq"),
      and find_spec("molmcp.providers.molvis") are not None;
      pyproject.toml still has the three official entry-point rows.
    status: pending
  - id: ac-007
    summary: Keep test_provider_base.py unmodified
    type: code
    pass_when: |
      tests/providers/test_provider_base.py exists and pytest still
      collects its @tool/probe/annotation/duplicate-name tests.
    status: pending
  - id: ac-008
    summary: Keep settings molexp/molq nested keys; no providers bag
    type: code
    pass_when: |
      molmcp.settings._SCHEMA contains molexp and molq as dict and
      does not contain providers; _NESTED_SCHEMA["molq"] is
      frozenset({"database", "allowSubmit"}) and
      _NESTED_SCHEMA["molexp"] is frozenset({"workspace"}).
    status: pending
  - id: ac-009
    summary: Skill names frozen science packages; no require_upstream call
    type: docs
    pass_when: |
      src/molmcp/skill/SKILL.md keeps pip install molcrafts-molmcp for
      missing core tools; namespaced-missing recovery says re-enable
      --disable or install molcrafts-molvis / molcrafts-molq / molexp
      respectively; the skill text does not contain require_upstream
      or molcrafts-*-mcp pip lines.
    status: pending
  - id: ac-010
    summary: Docs keep in-tree first-party and four-conditions
    type: docs
    pass_when: |
      docs/concepts/provider-design.md still places first-party at
      src/molmcp/providers/<name>/; four conditions and first-party-only
      mutations remain; catalog membership is the molmcp.providers
      group, not a hardcoded id set.
    status: pending
  - id: ac-011
    summary: Regression pins catalog-cutover goldens
    type: runtime
    pass_when: |
      python regressions/autonomous-harness-evolution-14-provider-cutover.py
      exits 0 and asserts Testing strategy goldens 1–8.
    status: pending
  - id: ac-012
    summary: route keeps core keyword table; unknown members listed only
    type: code
    pass_when: |
      route_task("draw dopamine") still returns plane molvis when
      discover_providers is patched to []; a discovered id not in
      _ROUTE_HINTS appears in list_plane_infos and is not
      keyword-routed.
    status: pending
  - id: ac-013
    summary: Tests fake discover_providers; no pyproject fixture row
    type: code
    pass_when: |
      Catalog tests monkeypatch discover_providers or pass
      create_stack(providers=...); pyproject.toml molmcp.providers
      table is not given a test/fixture entry.
    status: pending
out_of_scope:
  - physical extraction of molexp/molq/molvis packages
  - molcrafts-*-mcp distributions and pip lines
  - ProviderBase purpose/when_to_connect/tools_hint ClassVars
  - adding tool_specs to Provider Protocol
  - planes.py importing providers.base
  - create_stack signature change
  - generic settings providers bag
  - opening mutations to any group member
  - skill teaching require_upstream()
  - changing silent-omit
  - deleting tests/providers/test_provider_base.py
  - provider_sdk package (spec 01)
---

# Acceptance criteria

「完成」是：目录 **id** 只来自组发现；**文案** 仍由 `planes.py` 表提供；**tools_hint** 只 duck-type `tool_specs`。树内实现不搬走。

## AC-001 — 成员只来自发现

无 `_PROVIDER_META` 成员并集。文案表的键不能把未发现的官方名写进目录。

## AC-002 — purpose/when 在目录层

表是 copy 不是 membership。`ProviderBase` 不加这两项。未入表的发现名用泛化回退。

## AC-003 — tools_hint duck-type

`getattr(tool_specs)`；`planes.py` 不 import `providers.base`；Protocol 不加 `tool_specs`。

## AC-004 — 不可用列表仍是发现结果

`probe()` 假的已加载实例可出现；文案表不能复活未发现的名字。

## AC-005 — `create_stack` 签名冻结

参数名与全关键字-only 按字面量钉死。

## AC-006 — 树内实现仍在

三个 `find_spec` 非空；pyproject 三行仍在。

## AC-007 — `test_provider_base.py` 原样保留

不改、不删；契约测试仍被收集。

## AC-008 — settings 具名键

`molexp` / `molq` 不是 generic bag。

## AC-009 — skill 科学包名写死

核心不在 → molmcp。namespaced 缺失 → `--disable` 或 `molcrafts-molvis` / `molcrafts-molq` / `molexp`。不出现 `require_upstream`，不出现 `*-mcp`。

## AC-010 — 文档第一方仍是树内

四条件与 mutation 政策不放宽。

## AC-011 — 回归脚本

黄金 1–8。

## AC-012 — 路由是核心词汇

画图仍路由到 molvis；未知组员只列出。

## AC-013 — 夹具注入

fake `discover_providers` 或 `providers=`。
