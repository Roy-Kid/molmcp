---
slug: autonomous-harness-evolution-15-bundle-cutover
created: 2026-09-04
criteria:
  - id: ac-001
    summary: Single public install_skill owned by host; gone from client_config
    type: code
    pass_when: |
      src/molmcp/client_config.py does not define install_skill or skill_template;
      neither name nor default_skill_dir nor Host appears in client_config.__all__;
      cli.py imports install_skill from molmcp.host (not client_config);
      tests/test_client_config.py has no test_skill_template_is_shipped
    status: pending
  - id: ac-002
    summary: Host type and both dest tables live only in host/
    type: code
    pass_when: |
      Host, HOSTS, MCP JSON dest parts, and skill dest parts are defined in
      src/molmcp/host/install.py only; client_config.py has no _HOST_PATHS or
      _HOST_SKILL_DIRS; cli init choices= uses host.HOSTS; host/install.py does
      not import client_config
    status: pending
  - id: ac-003
    summary: Wheel still ships SKILL.md via package-data; no CheckoutRequired
    type: code
    pass_when: |
      pyproject.toml [tool.setuptools.package-data] still lists
      "molmcp.skill" = ["SKILL.md"] and discovery.store *.sql;
      no CheckoutRequired symbol exists under src/molmcp/;
      skill/__init__.py docstring states the tree file is the constitution and
      the wheel carries that file
    status: pending
  - id: ac-004
    summary: Host copy2 of skill-package SKILL.md into fake dest
    type: runtime
    pass_when: |
      uv run pytest tests/test_host/test_install.py -v is green;
      TestInstallSkill copies Path(molmcp.skill.__file__).parent/SKILL.md to
      dest_dir/SKILL.md with literals SYMBOL_NOT_FOUND and
      disable-model-invocation: false
    status: pending
  - id: ac-005
    summary: cli._init copies skill then writes one MCP JSON entry
    type: runtime
    pass_when: |
      uv run pytest tests/test_client_config.py -v is green;
      test_cli_init_writes_json_and_skill still sees one mcpServers.molcrafts
      entry; cli._init calls host.install_skill then writes JSON without a
      checkout gate
    status: pending
  - id: ac-006
    summary: One mcpServers.molcrafts entry and no env on the init path
    type: runtime
    pass_when: |
      render_mcp_json for a core-only PlaneToggle has mcpServers keys exactly
      {"molcrafts"}; host/install.py reads no environment variables
    status: pending
  - id: ac-007
    summary: Regression pins constitution literals and deleted client_config APIs
    type: runtime
    pass_when: |
      python regressions/autonomous-harness-evolution-15-bundle-cutover.py exits 0;
      dest SKILL.md contains hard-coded SYMBOL_NOT_FOUND and
      disable-model-invocation: false; mcpServers has exactly one molcrafts
      entry; client_config.install_skill and skill_template are absent; no
      third-party import or subprocess
    status: pending
out_of_scope:
  - Dropping molmcp.skill SKILL.md from package-data
  - Adding CheckoutRequired or gating JSON write on a git checkout
  - Editing SKILL.md body or docs/get-started/installation.md
  - Re-exporting default_skill_dir or install_skill from client_config
  - Env switches; git clone in tests; multiple MCP entries
---

## 2026-09-07 修订：`regressions/` 已删除

本仓从未发布过任何版本，没有可回归的对象；`regressions/` 也从来不在 CI 里跑
（`uv run pytest -v` 只跑 `tests/`），以致其中一个脚本烂掉很久无人察觉。整个目录
已删。**下文凡是要求新增 `regressions/<slug>.py` 的任务与判定一律作废**；相应的
正确性证明由 `tests/` 下的单元与结构性守卫承担。


# Acceptance criteria

完成 = `molmcp.host` 拥有 `Host`、两张 dest 表、唯一 `install_skill`；wheel 仍携带 `SKILL.md`；无 `CheckoutRequired`；`client_config` 只渲染一条 MCP JSON；无 env。

## AC-001 — One install_skill

`cli._init` 从 `molmcp.host` 导入。`client_config` 删除 `install_skill` / `skill_template`；`__all__` 不含 `default_skill_dir` / `Host`。

## AC-002 — One host list

`Host`、`HOSTS`、JSON 落点、skill 目录只在 `host/install.py`。`cli` 的 `choices=` 与 `render_init` 共用 `HOSTS`。host 不 import `client_config`。

## AC-003 — Package-data kept; no CheckoutRequired

`pyproject.toml` 仍列出 `"molmcp.skill" = ["SKILL.md"]`。源码树无 `CheckoutRequired`。`skill/__init__.py` 声明树文件是 constitution、wheel 携带该文件。

## AC-004 — copy2 when present

`TestInstallSkill`、假 dest、skill 包旁 `SKILL.md` 原文 + 宪章字面量。不 boot 全量 init。

## AC-005 — Init sequence

先 `host.install_skill` 再写 JSON；无 checkout 门闩；JSON 仍一条 `molcrafts`。

## AC-006 — One MCP entry, no env

`mcpServers` 只有 `molcrafts`。init/host 路径不读环境变量。

## AC-007 — Regression

公开 `host.install_skill`、硬编码宪章字面量、一条 MCP entry、`client_config` 上无 `install_skill` / `skill_template`。
