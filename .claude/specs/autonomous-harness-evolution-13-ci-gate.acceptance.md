---
slug: autonomous-harness-evolution-13-ci-gate
created: 2026-09-04
criteria:
  - id: ac-001
    summary: Cheap run_gate never calls evaluate; --full does
    type: code
    pass_when: |
      tests/test_gate.py::TestRunGate shows run_gate(full=False) on champion-eq-challenger succeeds even when the injected evaluate callable raises, and run_gate(full=True, evaluate=recording_fn) calls that callable once with the same root.
    status: pending
  - id: ac-002
    summary: contract-fail fails; champion-eq-challenger passes cheap
    type: runtime
    pass_when: |
      run_gate(root=tests/fixtures/gate/contract-fail, full=False).ok is False, and run_gate(root=tests/fixtures/gate/champion-eq-challenger, full=False).ok is True.
    status: pending
  - id: ac-003
    summary: Required check name is official/gate, not job id gate
    type: code
    pass_when: |
      molmcp.gate.CHECK_NAME == "official/gate"; .github/workflows/official-gate.yml job id official-gate has name: official/gate; no job in that file has id gate; release.yml still has jobs.gate.
    status: pending
  - id: ac-004
    summary: Two jobs, literal run: strings, no env profile
    type: code
    pass_when: |
      official-gate.yml defines official-gate (if: github.event_name != 'schedule', run: uv run molmcp gate --full) and official-gate-schedule (name other than official/gate, if: github.event_name == 'schedule', run: uv run molmcp gate); every run: is a single-line scalar with no ${{; neither job has env: selecting a profile; uv sync --extra dev is a prior Install step.
    status: pending
  - id: ac-005
    summary: PR run: and pre-commit entry: equal FULL_RUN
    type: code
    pass_when: |
      TestOfficialGateParity reads only the PR job's molmcp-gate run: and the official-gate hook entry: and asserts both equal the literal uv run molmcp gate --full (molmcp.gate.FULL_RUN); neither token contains uv sync or bash -c.
    status: pending
  - id: ac-006
    summary: official-gate hook is pre-push only
    type: code
    pass_when: |
      .pre-commit-config.yaml hook id official-gate has stages: [pre-push] and entry: uv run molmcp gate --full; the pre-commit (commit) stage still has ci-lint and does not list official-gate.
    status: pending
  - id: ac-007
    summary: ci.yml product matrix and ci.config stay put
    type: code
    pass_when: |
      .github/workflows/ci.yml still has the OS/Python matrix and contains no molmcp gate; CLAUDE.md and AGENTS.md mol_project.ci.config remain .github/workflows/ci.yml.
    status: pending
  - id: ac-008
    summary: CLI dispatches gate/--full and has no --skip
    type: code
    pass_when: |
      tests/test_cli_vnext.py shows cli.main(["gate"]) calls run_gate with full=False, cli.main(["gate", "--full"]) calls it with full=True, _gate contains no verdict logic beyond run_gate, and cli.main(["gate", "--skip"]) exits non-zero via argparse.
    status: pending
  - id: ac-009
    summary: Parity sentence outside managed; CLI docs name gate
    type: docs
    pass_when: |
      After <!-- mol:bootstrap:managed end --> in both CLAUDE.md and AGENTS.md a sentence states pair 1 (ci-lint/ci-test ≡ ci.yml lint/test run:) and pair 2 (official-gate pre-commit entry: ≡ official-gate.yml PR job run: ≡ uv run molmcp gate --full), and names the schedule uv run molmcp gate as a third invocation; docs/reference/cli.md documents molmcp gate and --full.
    status: pending
  - id: ac-010
    summary: Regression pins official/gate literals and fixture verdicts
    type: runtime
    pass_when: |
      regressions/autonomous-harness-evolution-13-ci-gate.py exits 0 asserting CHECK_NAME == "official/gate", FULL_RUN == "uv run molmcp gate --full", CHEAP_RUN == "uv run molmcp gate", the repo PR job run: and pre-commit official-gate entry: equal FULL_RUN, cli.main(["gate"]) == 0 on the champion-eq-challenger fixture, and cli.main(["gate"]) == 1 on the contract-fail fixture.
    status: pending
out_of_scope:
  - Changing ci.yml OS/Python matrix or folding official/gate into ci.yml
  - Implementing molmcp.evaluate.evaluate (spec 11)
  - --skip, env-selected profiles, expressions in run:
  - Renaming release.yml job gate
  - GitHub branch-protection UI
---

## 2026-09-07 修订：`--full` 已删除

下列条目中凡提到 `--full` / `FULL_RUN` / `evaluate` 的部分作废，理由见 spec 正文
同日期修订节：评估要起两个 subagent，GitHub runner 里没有 agent，`--full` 在 CI
上不可能执行；且它要 import 的 `molmcp.evaluate` 从来不存在（spec 11 交付的是
`molmcp.evolution.evaluate`，签名完全不同）。

判定改为：唯一调用字面量是 `GATE_RUN = "uv run molmcp gate"`；`run_gate(*, root)`
无 `evaluate` 参数；`GateReport` 无 `full` 字段；parity pair 2 是
pre-commit `entry:` ≡ PR job `run:` ≡ `GATE_RUN`。评估另起 spec `harness-evaluator`，
不进 required check。


# Acceptance criteria

Done means: the unique required check is named `official/gate`; PR and pre-push run the literal `uv run molmcp gate --full`; schedule runs `uv run molmcp gate`; `ci.yml` is untouched as the package matrix; `gate.py` owns the verdict; `cli.py` only dispatches.

## AC-001 — Cheap skips evaluate

`run_gate` 的 `full` 布尔是唯一档位。廉价路径不得 import spec 11。

## AC-002 — Fixture verdicts

`contract-fail` 必须红，`champion-eq-challenger` 廉价必须绿。这是判决函数的契约，不是 e2e。

## AC-003 — Check name vs job id

GitHub required check 跟的是 job `name:`。id 用 `official-gate`，把 `gate` 留给 `release.yml`。

## AC-004 — Two literal jobs

禁止在 `run:` 里用表达式或用 `env:` 选档。两条 job、两句字面 `run:`。

## AC-005 — Pair 2 character-for-character

Parity 测试只读 PR job 的 gate `run:` 和 pre-commit `entry:`。Install 不是 token。

## AC-006 — Push-tier hook

official-gate 只在 pre-push（和 PR）。commit 档仍是 `ci-lint`。

## AC-007 — ci.yml stays the product matrix

`mol_project.ci.config` 不改。矩阵 job 不跑 `molmcp gate`。

## AC-008 — Dispatch-only CLI

无 `--skip`。判决不进 `cli.py`。

## AC-009 — Parity prose survives bootstrap

两对 parity 的句子写在 managed 标记外，CLAUDE.md 与 AGENTS.md 同一 commit；`docs/reference/cli.md` 写上 `gate` / `--full`。

## AC-010 — Regression

`regressions/autonomous-harness-evolution-13-ci-gate.py` 钉死字面量与两个 fixture 的出口码；不在运行时拉第三方、不默认 import spec 11。
