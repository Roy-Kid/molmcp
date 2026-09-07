---
title: official/gate — molmcp gate / molmcp gate --full
status: approved
created: 2026-09-04
---

## 2026-09-07 修订：`regressions/` 已删除

本仓从未发布过任何版本，没有可回归的对象；`regressions/` 也从来不在 CI 里跑
（`uv run pytest -v` 只跑 `tests/`），以致其中一个脚本烂掉很久无人察觉。整个目录
已删。**下文凡是要求新增 `regressions/<slug>.py` 的任务与判定一律作废**；相应的
正确性证明由 `tests/` 下的单元与结构性守卫承担。


# official/gate — molmcp gate / molmcp gate --full

## Summary

仓库的 GitHub required check 只此一个，名字固定为 `official/gate`。本地与 PR 跑 `molmcp gate --full`，定时任务跑廉价的 `molmcp gate`（`--full` 去掉 spec 11 的 `evaluate`）。包的 lint/test 仍留在 `ci.yml` 的 OS/Python 矩阵里，本 spec 不改那份产品矩阵，也不把 official/gate 折进 `ci.yml`。

## 2026-09-07 修订：删除 `--full`（CI 里没有 agent）

本文件下文仍按「廉价 vs 完整」两档写。**那一档已作废**，理由如下；下文与本节冲突处以本节为准。

**为什么作废。** 原设计里 `--full` 在廉价检查之后调用 spec 11 的 `evaluate`。但
评估的新设计是：起**两个 subagent** —— 一个扮演用户在干净上下文里做任务（不知道
判据），另一个盲测观察两份 transcript 并打分。GitHub runner 里**没有 agent**，
起不了 subagent，所以 `--full` 在 CI 上不可能执行。

原文那条 `from molmcp.evaluate import evaluate`（签名 `Path -> bool`）也从来不成立：
spec 11 交付的是 `molmcp.evolution.evaluate`，签名是 8 参数返回 `EvaluationReport`。
spec 11 说「生产 runner 由 13 注入」，本 spec 说「不实现 evaluate」——两边互相推诿，
没人建过那个模块。**正确答案是两边都不该有它。**

**改成什么。** `molmcp gate` 只做它本来就该做、且 CI 真做得到的事：**检查接线契约**
（workflow 与 pre-commit 的字面量一致、无 `${{ }}` 表达式、无 env 选档）。

- 删除 `--full` 旗标、`FULL_RUN` 常量、以及 `run_gate` 的 `evaluate` 参数与惰性 import。
- `run_gate(*, root: Path) -> GateReport`；`GateReport` 去掉 `full` 字段。
- `GATE_RUN = "uv run molmcp gate"` 是唯一的调用字面量。
- workflow 仍是两个 job（PR 与 schedule），但两个 job 跑的是同一条 `GATE_RUN`；
  schedule job 保留只是为了定期复查接线没被改坏。
- parity pair 2 变成：pre-commit `entry:` ≡ PR job 的 `run:` ≡ `GATE_RUN`。
- pre-commit hook 仍 `stages: [pre-push]`。

**评估去哪了。** 开发者侧手动触发，不进 required check。制品是 `.claude/agents/`
下的两个 agent 定义加一个用例集，另起 spec（`harness-evaluator`）。

## Design

`src/molmcp/gate.py` 是判决的唯一所有者。`cli.py` 只把 `gate` / `--full` 转给 `run_gate`，不在 CLI 层拼 profile、不读环境、不解析 workflow。廉价与完整不是两种「配置文件」，而是一个布尔：`full=False` 跑接线契约，`full=True` 在廉价之后调用 spec 11 的 `evaluate`。没有 `--skip`，没有 `GATE_PROFILE` / `env:` 选档，也没有在 `run:` 里写 `${{ }}` 表达式——否则 parity 对到的就不是字面量。

**常量（一处权威，其余是副本）**

`gate.py` 模块级常量，测试按字符钉死：

- `CHECK_NAME = "official/gate"` — GitHub required check 名 = PR job 的 `name:`。不是 job id。
- `PR_JOB_ID = "official-gate"` — **禁止**用 `gate`：`.github/workflows/release.yml` 已经占用 job id `gate`。
- `SCHEDULE_JOB_ID = "official-gate-schedule"`
- `FULL_RUN = "uv run molmcp gate --full"`
- `CHEAP_RUN = "uv run molmcp gate"`

`.github/workflows/official-gate.yml` 与 `.pre-commit-config.yaml` 是这些常量的序列化副本。权威在 Python 常量；副本由 `tests/test_gate.py` 的 parity 断言拉齐。GitHub 认的是 YAML 的 `name:`，所以 PR job 必须写 `name: official/gate`，与 `CHECK_NAME` 相等。

**`run_gate(*, full: bool = False, root: Path, evaluate: Callable[[Path], bool] | None = None) -> GateReport`**

`GateReport` 是 `frozen=True, slots=True` 的 dataclass（`ok: bool`, `full: bool`, `failed: tuple[str, ...]`），与 `PlaneInfo` / `SubprocessResult` 同形。`root` 必填，CLI 传入 `Path.cwd()`，测试传入 fixture 根；不读隐藏 cwd 约定之外的环境。

廉价步骤（`full=False`）只检查 `root` 下的接线契约，**不** import、不调用 `evaluate`，也**不**跑 ruff/pytest（那是 `ci.yml` 的活）：

1. 存在 `.github/workflows/official-gate.yml` 与 `.pre-commit-config.yaml`。
2. 两个 job，id 分别为 `official-gate` 与 `official-gate-schedule`。
3. PR job：`name:` == `CHECK_NAME`，`if: github.event_name != 'schedule'`，其 **molmcp gate** 那条 `run:`（单行标量，不是 `|` 块）== `FULL_RUN`。`uv sync --extra dev` 是**前一步** Install，不折进被比较的 token。
4. Schedule job：`name:` **不是** `official/gate`（用 `official/gate (schedule)`），`if: github.event_name == 'schedule'`，其 molmcp gate 那条 `run:` == `CHEAP_RUN`。这条是第三次调用，**不**进入 pair 2。
5. 任一 job 的任意 `run:` 都不含 `${{`；两个 job 都没有用 `env:` 选 cheap/full。
6. pre-commit hook `id: official-gate` 的 `entry:` == `FULL_RUN`（不得写成 `entry: uv` + `args: [...]`，不得包 `bash -c 'uv sync && …'`），`stages: [pre-push]`，不进 pre-commit 档。commit 档仍只有现有的 `ci-lint`。

`full=True`：先跑廉价；通过后再调用 `evaluate(root)`。`evaluate` 参数默认 `None` 时，函数体内 `from molmcp.evaluate import evaluate`（前驱 spec `autonomous-harness-evolution-11-evaluate` 的符号）。本 spec **不**实现 evaluate、不造平行的 champion/challenger 比较器。注入的 callable 供单测使用，签名 `Path -> bool`。缺模块时 `run_gate` 抛已有的 `ConfigurationError`（CLI 出口 2），与契约失败（`GateReport.ok=False`，CLI 出口 1）分开。`gate.py` 不读 `os.environ` / `getenv`；`tests/test_no_env_switches.py` 已覆盖，不加豁免。

**工作流形状（两条 job，literal `run:`）**

新文件 `.github/workflows/official-gate.yml`。`on:` 为 `pull_request`（`branches: [master, dev]`，与 `ci.yml` 对齐）、`schedule`（`cron: "0 6 * * 1"`，周一 06:00 UTC，不是旋钮）、`workflow_dispatch`。**不加** `push`，避免每条推送与 `ci.yml` 叠床。`runs-on: ubuntu-latest`，Python 3.12，单轴；OS/Python 矩阵留在 `ci.yml`。每个 job 的步骤顺序：`actions/checkout@v4` → `astral-sh/setup-uv@v5` → `run: uv sync --extra dev` → 字面 `run: uv run molmcp gate --full` 或 `run: uv run molmcp gate`。`if:` 可以是表达式；`run:` 不可以。

**两对 parity，同一 commit；句子写在 managed 块外**

`mol_project.ci.config` **保持** `.github/workflows/ci.yml`，不改 frontmatter。

1. 既有：`ci-lint` / `ci-test` ≡ `ci.yml` 的 Lint/Test `run:`。`ci.yml` 继续是产品矩阵。本 spec 不把 official/gate 折进去，也不为了 pair 1 去改 `ci-lint`/`ci-test` 的 `bash -c 'uv sync && …'` 包装。
2. 新增：official-gate 的 pre-commit `entry:` ≡ `official-gate.yml` **PR job** 的 gate `run:` ≡ `uv run molmcp gate --full`。Parity 测试**只**读这两个 token，按字符相等。Install 不是被比较的 token。Schedule 的 `uv run molmcp gate` 是第三次调用，不进 pair 2。

当前 CLAUDE.md / AGENTS.md 里「CI parity: pre-commit mirrors ci.yml」写在 `<!-- mol:bootstrap:managed -->` 内，bootstrap 会盖掉。本 spec 在**两个文件**的 managed `end` 标记**之后**各写一段两对 parity 的句子（同一 commit）。Managed 块内 bootstrap 那句 pair 1 默认文案不动——改它等于下次 bootstrap 打回。

**CLI**

`_build_parser` 增加 `gate` 子解析器，唯一 flag 是 `--full`（`store_true`）。`main` 的 `handlers` 登记 `"gate": _gate`。`_gate` 调用 `run_gate(full=args.full, root=Path.cwd())`，打印 `GateReport`，`ok` → 0，否则 1。没有 `--skip`、没有 `--profile`、没有 `--json`。

**夹具**

`tests/fixtures/gate/` 下两棵与生产同相对路径的树，供 `run_gate(root=…)` 单测：

- `contract-fail/`：PR job 的 gate `run:` 与 pre-commit `entry:` 不一致（或 `run:` 含 `${{`）→ 廉价必须失败。
- `champion-eq-challenger/`：接线合法；廉价通过；`full=True` 且注入的 `evaluate` 返回 `True`（champion == challenger）时通过。

**对 architect 🔴 的逐条闭合**

- 两个 job、`run:` 无表达式、无 env 选档：见工作流形状。
- job id `official-gate` 而非 `gate`：见 `PR_JOB_ID`。
- parity 只比较 PR `run:` 与 pre-commit `entry:`，且等于 `uv run molmcp gate --full`：见 pair 2。
- Install 是前一步，不折进 token；pre-commit 不包 `bash -c 'uv sync && …'`：见廉价步骤 3/6。
- CI parity 句子在 managed 块外：见两对 parity。

### Reuse decision

Caller 未附 `librarian_report`（本轮为 architect 🔴 后重拟）。对照 blueprint 与源码扫描的处置：

- `reuse cli._build_parser` / `cli.main` handlers — 只加 `gate` 子命令与 `"gate": _gate`，不另开入口。
- `reuse tests.test_no_env_switches` — `gate.py` 不读环境；不加 `_ALLOWED` 豁免。
- `reuse molmcp.evaluate.evaluate`（spec 11）— `--full` 调用；本 spec 不实现比较器。
- `pattern .pre-commit-config.yaml` 的 `ci-lint` / `ci-test`（`repo: local`, `language: system`, `pass_filenames: false`, `always_run: true`）— official-gate hook 同形，但 `entry:` 必须是 `uv run molmcp gate --full`，不套 `bash -c 'uv sync && …'`。
- `pattern tests/test_cli_vnext.py` — CLI 测试 monkeypatch `run_gate`，跟 `create_stack` 假对象同一手法。
- `pattern cli._cache` / `_config` — cli 只分发。
- `new — run_gate` / `GateReport` / `CHECK_NAME` / `FULL_RUN` / `CHEAP_RUN` — 仓库没有 official/gate 判决函数；`release.yml` 的 job id `gate` 是发布门，禁止复用。
- 不 reuse `scripts/eval_relevance.py` — 读 `ANTHROPIC_API_KEY`，文件头写明不是 CI gate。
- 不 generalize `ci.yml` job `test` — 产品矩阵留在原地。

## Files to create or modify

- `src/molmcp/gate.py` (new)
- `src/molmcp/cli.py`
- `tests/test_gate.py` (new)
- `tests/test_cli_vnext.py`
- `tests/fixtures/gate/contract-fail/.github/workflows/official-gate.yml` (new)
- `tests/fixtures/gate/contract-fail/.pre-commit-config.yaml` (new)
- `tests/fixtures/gate/champion-eq-challenger/.github/workflows/official-gate.yml` (new)
- `tests/fixtures/gate/champion-eq-challenger/.pre-commit-config.yaml` (new)
- `.github/workflows/official-gate.yml` (new)
- `.pre-commit-config.yaml`
- `CLAUDE.md`
- `AGENTS.md`
- `docs/reference/cli.md`
- `regressions/autonomous-harness-evolution-13-ci-gate.py` (new)

## Tasks

- [ ] Write failing unit tests for run_gate (tests/test_gate.py → TestRunGate) and fixture trees tests/fixtures/gate/contract-fail/ plus tests/fixtures/gate/champion-eq-challenger/
- [ ] Implement CHECK_NAME, FULL_RUN, CHEAP_RUN, GateReport, run_gate in src/molmcp/gate.py (Google-style docstring; no os.environ)
- [ ] Write failing tests for CLI dispatch (tests/test_cli_vnext.py) and repo-file parity (tests/test_gate.py → TestOfficialGateParity)
- [ ] Implement gate subcommand and --full dispatch in src/molmcp/cli.py (handlers only; no --skip)
- [ ] Add .github/workflows/official-gate.yml with jobs official-gate and official-gate-schedule, each with a literal single-line run: and Install as a prior step
- [ ] Add official-gate hook to .pre-commit-config.yaml (stages: [pre-push], entry: uv run molmcp gate --full, no bash -c uv-sync wrapper)
- [ ] Write the two-pair CI parity sentence outside mol:bootstrap:managed in CLAUDE.md and AGENTS.md; document gate/--full in docs/reference/cli.md
- [x] ~~Add regression example regressions/autonomous-harness-evolution-13-ci-gate.py (public API only; hard-coded goldens, no third-party runtime)~~ — 作废：`regressions/` 已删除（2026-09-07）
- [ ] Run full check + test suite

## Testing strategy

Unit-only under `tests/`，路径镜像：`src/molmcp/gate.py` → `tests/test_gate.py`（`TestRunGate`, `TestOfficialGateParity`）；`src/molmcp/cli.py` → 既有 `tests/test_cli_vnext.py`（函数级，与周围 CLI 测试一致）。单测只打一个模块；出站依赖用假对象。单元变绿 = `uv run pytest {path} -v`。

**TestRunGate（`run_gate`）**

- Happy：`root=champion-eq-challenger`，`full=False` → `ok is True`，注入的 `evaluate` 不被调用（传入会 raise 的 callable 仍通过）。
- Happy：同一 fixture，`full=True`，`evaluate=lambda root: True` → `ok is True`，callable 被调用一次，参数为该 root。
- Edge：`root=contract-fail`，`full=False` → `ok is False`，`failed` 非空。
- Edge：`full=True` 且 `evaluate is None` 时走 `molmcp.evaluate.evaluate` 的惰性 import；模块缺失 → `ConfigurationError`，不是 `GateReport.ok=False`。
- Edge：`gate.py` 源码不含 `os.environ` / `getenv`（`test_no_env_switches.py` 已是网；本模块不加豁免）。
- Edge：argparse 契约由 CLI 测试覆盖，但 `run_gate` 签名没有 skip/profile 参数。

**TestOfficialGateParity（真实仓库文件 + `FULL_RUN`）**

- PR job `official-gate` 的 gate `run:` 与 pre-commit `id: official-gate` 的 `entry:` 都等于 `FULL_RUN`（`uv run molmcp gate --full`）按字符。只读这两个 token。
- 该 `run:` / `entry:` 不含 `uv sync`，不含 `bash -c`。
- PR job `name:` == `CHECK_NAME` == `"official/gate"`；job id 是 `official-gate` 不是 `gate`。
- Schedule job id `official-gate-schedule`，`name:` != `"official/gate"`，gate `run:` == `CHEAP_RUN`。
- 两个 job 的全部 `run:` 都不含 `${{`，job 下无选档 `env:`。
- official-gate hook `stages == [pre-push]`；`ci-lint` 仍在 commit 档；commit 档没有 official-gate。
- `.github/workflows/ci.yml` 仍含 OS/Python 矩阵，且没有任何 `molmcp gate`；`CLAUDE.md` / `AGENTS.md` frontmatter `ci.config` 仍是 `.github/workflows/ci.yml`。
- `release.yml` 仍有 job id `gate`（发布门未改名）。

**CLI（`tests/test_cli_vnext.py`）**

- `cli.main(["gate"])` 以 `full=False` 调用 `run_gate`（monkeypatch）。
- `cli.main(["gate", "--full"])` 以 `full=True` 调用。
- parser 无 `--skip`：`cli.main(["gate", "--skip"])` 非 0（argparse 退出）。

**回归（`regressions/autonomous-harness-evolution-13-ci-gate.py`）**

公共 API：`molmcp.cli.main` 与 `molmcp.gate` 的常量。硬编码字面量（无第三方运行时）：

- `CHECK_NAME == "official/gate"`
- `FULL_RUN == "uv run molmcp gate --full"`
- `CHEAP_RUN == "uv run molmcp gate"`
- 读仓库 `official-gate.yml` PR job 与 `.pre-commit-config.yaml` official-gate `entry:`，二者等于 `FULL_RUN`
- `chdir` 到 `champion-eq-challenger` fixture 后 `cli.main(["gate"]) == 0`
- `chdir` 到 `contract-fail` fixture 后 `cli.main(["gate"]) == 1`

不在回归里跑 `--full` 的默认 import（那是 spec 11）；`--full` 由 `TestRunGate` 注入 callable 覆盖。

## Out of scope

- 改 `.github/workflows/ci.yml` 的 OS/Python 矩阵，或把 official/gate 折进 `ci.yml`。
- 改 `mol_project.ci.config` / `ci.local`（仍指向 `ci.yml`）。
- 实现 `molmcp.evaluate.evaluate`（spec 11）或复用 `scripts/eval_relevance.py`。
- `--skip`、`--profile`、用 `env:` / 环境变量选 cheap/full。
- 在任何 `run:` 里写 GitHub 表达式；把 Install `uv sync --extra dev` 折进 parity token；把 official-gate 的 pre-commit `entry:` 包成 `bash -c 'uv sync && …'`。
- 把 official-gate hook 放进 pre-commit（commit）档；commit 档仍是 `ci-lint`。
- 重命名 `release.yml` 的 job `gate`。
- 给 `gate.py` 开 `test_no_env_switches` 豁免。
- 加 PyYAML；parity 用 stdlib 抽标量。
- 在 GitHub 仓库设置里点 required check（操作员动作，不是代码）。
- 刷新 `.claude/notes/architecture.md`（blueprint 仍由 `/mol:map` 写）。
