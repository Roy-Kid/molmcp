---
title: host 拥有 dest 表与唯一 install_skill
status: approved
created: 2026-09-04
---

## 2026-09-07 修订：`regressions/` 已删除

本仓从未发布过任何版本，没有可回归的对象；`regressions/` 也从来不在 CI 里跑
（`uv run pytest -v` 只跑 `tests/`），以致其中一个脚本烂掉很久无人察觉。整个目录
已删。**下文凡是要求新增 `regressions/<slug>.py` 的任务与判定一律作废**；相应的
正确性证明由 `tests/` 下的单元与结构性守卫承担。


# host 拥有 dest 表与唯一 install_skill

## Summary

`molmcp init <host>` 的宿主名单、MCP JSON 落点、skill 目录、以及 **唯一** 的 `install_skill` 都归 `molmcp.host`。`cli._init` 从 `molmcp.host` 导入 `install_skill` / `default_write_path` / `HOSTS`；`client_config` 只负责渲染一条 `mcpServers.molcrafts`，不再提供第二份 `install_skill` 或 dest 表。usage constitution 的权威是 `src/molmcp/skill/SKILL.md`；wheel 经现有 package-data **携带同一文件**（序列化副本）。`install_skill` 用 `shutil.copy2` 复制 skill 包 `__init__.py` 旁的 `SKILL.md`——checkout 与 PyPI wheel 走同一路径。删除公开 `skill_template`。无 `CheckoutRequired`、无环境变量、不把 init 绑在 git checkout 上。

## Design

### 产品切（earn-complexity）

**不** 切断 wheel package-data，**不** 引入 `CheckoutRequired`，**不** 把 MCP JSON 写盘门闩在 git checkout 上。PyPI / tox wheel 上的 `molmcp init` 必须能装 skill。package-data 是 `SKILL.md` 的序列化副本（one-home 允许副本，不允许第二份权威）。没有调用方需要「只能从 checkout init」。

因此：

- **保留** `pyproject.toml` `[tool.setuptools.package-data]` 的 `"molmcp.skill" = ["SKILL.md"]`（以及 `"molmcp.discovery.store" = ["*.sql"]`）。本 spec **不改** 该表。
- **一条复制路径**：`Path(molmcp.skill.__init__.py 所在目录) / "SKILL.md"`，`shutil.copy2` 到 dest。checkout 里这是树文件；wheel 里 package-data 把同一文件放在同一相对位置。禁止「树文件失败再 importlib.resources」两段 fallback；禁止公开 `skill_template`。
- **不** 定义 `CheckoutRequired`。缺文件是损坏安装，走已有 `cli.main` 的 `OSError` / `FileNotFoundError`。`cli._init`：先 `install_skill` 再写 JSON，二者不是 checkout 门闩；shipped wheel 上 copy 不会因「没有 git 树」失败。不为 skill 失败吞异常后继续写 JSON（无此调用方）。

### 所有权（primitive-surface / locality-of-change）

Predecessor **07** 引入 `src/molmcp/host/`。本 spec 把仍留在 `client_config` 的 `Host` 与两张 dest 表迁入该包，并让 `install_skill` 成为唯一复制者。

**唯一公开 `install_skill`。** 定义在 `src/molmcp/host/install.py`，经 `host/__init__.py` 再导出。`cli._init`：`from .host import install_skill`（**不是** `client_config`）。**删除** `client_config.install_skill`，禁止再导出、禁止 raise-only 替身。`client_config.__all__` **不得** 含 `install_skill`、`skill_template`、`default_skill_dir`、`Host`。dest 表测试只写在 `tests/test_host/test_install.py`。

**Host 与两张 dest 只在 host。** 放在 `src/molmcp/host/install.py`（不另开 `paths.py`：现有调用者就是 `cli._init` 与 `render_init`）：

- `Host = Literal["grok", "claude", "cursor", "codex"]`
- `HOSTS: tuple[Host, ...]` — 由 dest 映射的 key 得到。`cli` 的 `choices=` 与 `render_init` 的未知-host 校验 **共用** 这一集合。禁止在 `cli.py` / `client_config.py` 再写一份四宿主字面量。
- `SKILL_NAME = "molcrafts"`
- 一张 `_HOSTS` 映射：每个 host → MCP JSON 相对 `Path.home()` 的 parts **以及** skill 目录 parts（今日 `_HOST_PATHS` + `_HOST_SKILL_DIRS`）。
- `default_write_path(host) -> Path`
- `default_skill_dir(host) -> Path`

`client_config.render_init` 从 host 导入 `Host` / `HOSTS`。`client_config.default_write_path` **不是第二份实现**：`from .host import default_write_path`（同一函数对象，可留在 `client_config.__all__`）。`default_skill_dir` **只** 在 host 公开。

`cli`：`from .host import HOSTS, default_write_path, install_skill`；`choices=HOSTS`。host **不** import `client_config`。方向：`cli` → `host`、`cli` → `client_config`、`client_config` → `host`。

### `install_skill`

```text
source = Path(molmcp.skill.__file__).resolve().parent / "SKILL.md"
dest = dest_dir or default_skill_dir(host)
dest.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, dest / "SKILL.md")
return dest / "SKILL.md"
```

- `_usage_skill_file() -> Path` 只返回上述路径（单测若需替换可 monkeypatch；公开 API **没有** `source=`）。
- `dest_dir: Path | None = None` 是测试缝（假 dest，不写真实 `$HOME`）。
- 删除 `skill_template`（定义与一切 `__all__`）。
- `src/molmcp/skill/__init__.py` 一行 docstring：树文件是 constitution；wheel 携带该文件。
- 不改 `SKILL.md` 正文。`skill/` 下不实现 adapter。
- Google 风格 docstring 写在 `install_skill` / `default_write_path` / `default_skill_dir`。无物理量。

### `cli._init`

1. `render_init`（纯函数，不写盘）。
2. `install_skill(args.host)`。
3. `default_write_path` / `-o` 的 mkdir + `write_text`。
4. stderr 两个 `wrote` 行。

`cli.main` 的 except 元组 **不** 增加新类型。不改 `docs/get-started/installation.md`。一条 `mcpServers.molcrafts`。无环境变量。

### Reuse decision

- reuse `src/molmcp/skill/SKILL.md` — constitution 权威；复制源；不改正文。
- reuse `[tool.setuptools.package-data] "molmcp.skill" = ["SKILL.md"]` — wheel 序列化副本；本 spec 不删。
- reuse `client_config.render_mcp_json` / `render_init` — 一条 `mcpServers`；`render_init` 从 host 取 `HOSTS`。
- reuse `shutil.copy2`（stdlib；`client_config` 已 import `shutil` 做 which）— host 用 copy2 复制 skill 文件。
- generalize `client_config.Host` / `_HOST_PATHS` / `_HOST_SKILL_DIRS` — 迁入 `host/install.py` 的 `_HOSTS` + `HOSTS`；`cli.choices` 与 `render_init` 共用。
- generalize `install_skill` onto `src/molmcp/host/install.py` — 唯一复制者；`cli._init` 从 host 导入。
- reuse `client_config.default_write_path` — `from .host import default_write_path` 同一对象。
- new — `client_config.install_skill`：删除，不得再导出。
- new — `client_config.default_skill_dir`：不进 `client_config.__all__`；测试在 `tests/test_host/test_install.py`。
- new — `skill_template`：删除（不是改成 raise-only getter）。
- new — `CheckoutRequired`：**不** 引入。
- new — 不把 `graphstore.py` 的 `importlib.resources` 做成 skill 的第二复制源（package-data 已让旁路路径在 wheel 上存在）。
- pattern `host/__init__.py` 再导出 — `middleware/__init__.py` / `helpers/__init__.py`。

## Files to create or modify

- `src/molmcp/host/__init__.py` (new) — 再导出 `HOSTS`、`Host`、`SKILL_NAME`、`default_skill_dir`、`default_write_path`、`install_skill`。07 已有则只对齐导出。
- `src/molmcp/host/install.py` (new) — `Host` / `HOSTS` / `_HOSTS` dest 映射、`default_write_path`、`default_skill_dir`、`install_skill`（`shutil.copy2`）。07 已有则迁入 dest 表并把复制源定为 skill 包旁 `SKILL.md`。
- `src/molmcp/client_config.py` — 删除 `install_skill`、`skill_template`、`_HOST_PATHS`、`_HOST_SKILL_DIRS`、本地 `Host` / `SKILL_NAME` / `default_skill_dir` 实现；`render_init` 与 `default_write_path` 从 host 导入。
- `src/molmcp/cli.py` — 从 `.host` 导入 `install_skill` / `default_write_path` / `HOSTS`；`choices=HOSTS`；先 `install_skill` 再写 JSON。
- `src/molmcp/skill/__init__.py` — 一行 docstring（树文件是 constitution；wheel 携带该文件）。
- `tests/test_host/test_install.py` (new) — `TestInstallSkill`：copy、dest 表、`HOSTS`。
- `tests/test_client_config.py` — 删除 `test_skill_template_is_shipped` 与 `test_each_host_has_a_skill_directory`；断言 client_config 不再公开 `install_skill` / `default_skill_dir` / `skill_template`；home patch 改到 host。
- `regressions/autonomous-harness-evolution-15-bundle-cutover.py` (new)

不修改：`pyproject.toml` 的 package-data、`src/molmcp/skill/SKILL.md` 正文、`docs/get-started/installation.md`。

## Tasks

- [ ] Write failing unit tests for `install_skill` (tests/test_host/test_install.py → TestInstallSkill): shutil.copy2 of skill-package SKILL.md into fake dest_dir; dest tables and HOSTS live here; no CheckoutRequired
- [ ] Generalize Host, both dest tables, and `install_skill` into `src/molmcp/host/install.py` (copy Path beside molmcp.skill / SKILL.md via shutil.copy2; dest_dir seam; host does not import client_config); add `src/molmcp/host/__init__.py` re-exports; Google-style docstrings
- [ ] Write failing unit tests in tests/test_client_config.py: client_config has no install_skill / skill_template / default_skill_dir in __all__; delete test_skill_template_is_shipped and test_each_host_has_a_skill_directory
- [ ] Delete `install_skill`, `skill_template`, `_HOST_PATHS`, `_HOST_SKILL_DIRS`, and the local Host / SKILL_NAME / default_skill_dir implementations from `src/molmcp/client_config.py`; import HOSTS and default_write_path from host
- [ ] Wire `cli._init` in `src/molmcp/cli.py` to import install_skill, default_write_path, and HOSTS from molmcp.host; set choices=HOSTS; call install_skill then write JSON
- [ ] Set a one-line docstring on `src/molmcp/skill/__init__.py` that the tree file is the constitution and the wheel carries that file
- [x] ~~Add regression example regressions/autonomous-harness-evolution-15-bundle-cutover.py (public API only; hard-coded goldens, no third-party runtime)~~ — 作废：`regressions/` 已删除（2026-09-07）
- [ ] Run full check + test suite

## Testing strategy

单测默认；`tests/` 镜像 `src/`；绿 = `uv run pytest {path} -v`。禁止 tests 内 e2e、`git clone`、用全量 `cli.main` 证明 copy。package-data 保留，故 editable 与 tox wheel 下 `molmcp.skill` 旁都有 `SKILL.md`；happy path **不必** 为 wheel 再注入路径。

**`tests/test_host/test_install.py` → `TestInstallSkill`**（dest 表 + copy 的唯一家）

- Happy：`install_skill("grok", dest_dir=tmp/dest)`；dest 文件字节（或 UTF-8 文本）等于 `Path(molmcp.skill.__file__).parent / "SKILL.md"`；钉字面量 `SYMBOL_NOT_FOUND`、`disable-model-invocation: false`、`user-invocable: false`、`when-to-use:`、`packages`。
- Dest 表：`default_skill_dir` 对 `HOSTS` 中每个 host 末段为 `molcrafts`；`default_write_path("grok")` 以 `.mcp.json` 结尾；未知 host → `ValueError`；`HOSTS` 与 dest 映射 key 集合相等。
- 仓库内 **没有** 名为 `CheckoutRequired` 的符号。`host/install.py` 不 import `client_config`。
- 不测「缺树文件则拒绝 init」——该行为已否决。

**`tests/test_client_config.py`**

- 无 `test_skill_template_is_shipped`、无 `test_each_host_has_a_skill_directory`。
- `install_skill` / `skill_template` / `default_skill_dir` / `Host` 不在 `client_config.__all__`；模块上无 `install_skill` 与 `skill_template`。
- `TestOneJsonForEveryHost` 遍历 `molmcp.host.HOSTS`。
- `test_cli_init_writes_json_and_skill`：钉一条 `mcpServers.molcrafts`；home patch `molmcp.host.install.Path.home`；skill 文件存在可作为 CLI 接线断言，copy 语义以 `TestInstallSkill` 为准。

**回归** `regressions/autonomous-harness-evolution-15-bundle-cutover.py`

- 公开 `molmcp.host.install_skill(..., dest_dir=temp)`。
- dest 含硬编码 `SYMBOL_NOT_FOUND`、`disable-model-invocation: false`。
- `client_config.render_mcp_json`：`mcpServers` keys `{"molcrafts"}`。
- `getattr(client_config, "install_skill", None)` 与 `skill_template` 均为 `None`。
- `python regressions/autonomous-harness-evolution-15-bundle-cutover.py` 退出 0；无第三方 import/subprocess。

## Out of scope

- 从 `pyproject.toml` 删除 `"molmcp.skill" = ["SKILL.md"]`（明确否决）。
- 引入 `CheckoutRequired`，或把 JSON 写盘门闩在 git checkout 上。
- 公开 `skill_template`，或改成 raise-only getter。
- 「树文件 → importlib.resources」两段 fallback。
- 改 `src/molmcp/skill/SKILL.md` 正文；在 `skill/` 下实现 installer/adapter。
- 改 `docs/get-started/installation.md`。
- 多条 MCP entry、改 `render_mcp_json` 形状、改 provider mounts。
- 环境变量、settings 键。
- 测试或回归里 `git clone`。
- 在 `client_config` 再导出 `default_skill_dir` 或保留第二份 `install_skill`。
