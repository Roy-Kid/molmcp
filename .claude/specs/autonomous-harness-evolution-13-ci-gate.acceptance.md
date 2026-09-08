---
slug: autonomous-harness-evolution-13-ci-gate
created: 2026-09-04
criteria:
  - id: ac-001
    summary: run_gate takes only root and never reaches for an evaluator
    type: code
    pass_when: |
      inspect.signature(molmcp.gate.run_gate) has exactly one parameter, keyword-only `root`, with no default — no full, no evaluate, no skip, no profile. molmcp.gate has no FULL_RUN, no CHEAP_RUN and no GATE_PROFILE attribute. src/molmcp/gate.py imports nothing whose name contains evaluate, and spawns no process.
    status: pending
  - id: ac-002
    summary: contract-fail fails; wired passes
    type: runtime
    pass_when: |
      run_gate(root=tests/fixtures/gate/contract-fail).ok is False with a non-empty failed naming the inconsistency, and run_gate(root=tests/fixtures/gate/wired).ok is True with failed == ().
    status: pending
  - id: ac-003
    summary: Required check name is official/gate, not job id gate
    type: code
    pass_when: |
      molmcp.gate.CHECK_NAME == "official/gate"; .github/workflows/official-gate.yml job id official-gate has name: official/gate; no job in that file has id gate; release.yml still has jobs.gate.
    status: pending
  - id: ac-004
    summary: Two jobs, one literal run:, no env profile
    type: code
    pass_when: |
      official-gate.yml defines official-gate (if: github.event_name != 'schedule') and official-gate-schedule (name other than official/gate, if: github.event_name == 'schedule'), and BOTH gate steps run the same literal `uv run molmcp gate`; every run: is a single-line scalar with no ${{; neither job has env: selecting a profile; uv sync --extra dev is a prior Install step.
    status: pending
  - id: ac-005
    summary: PR run: and pre-commit entry: equal GATE_RUN
    type: code
    pass_when: |
      TestOfficialGateParity reads only the PR job's molmcp-gate run: and the official-gate hook entry: and asserts both equal the literal `uv run molmcp gate` (molmcp.gate.GATE_RUN); neither token contains uv sync or bash -c.
    status: pending
  - id: ac-006
    summary: official-gate hook is pre-push only
    type: code
    pass_when: |
      .pre-commit-config.yaml hook id official-gate has stages: [pre-push] and entry: uv run molmcp gate; the pre-commit (commit) stage still has ci-lint and does not list official-gate.
    status: pending
  - id: ac-007
    summary: ci.yml product matrix and ci.config stay put
    type: code
    pass_when: |
      .github/workflows/ci.yml still has the OS/Python matrix and contains no molmcp gate; CLAUDE.md and AGENTS.md mol_project.ci.config remain .github/workflows/ci.yml.
    status: pending
  - id: ac-008
    summary: CLI dispatches gate with no flags
    type: code
    pass_when: |
      tests/test_cli_vnext.py shows cli.main(["gate"]) calls run_gate with root=Path.cwd() and returns 0 when ok, 1 otherwise; _gate contains no verdict logic beyond run_gate; the gate subparser accepts no flags, so cli.main(["gate", "--full"]) and cli.main(["gate", "--skip"]) both exit non-zero via argparse.
    status: pending
  - id: ac-009
    summary: Parity sentence outside managed; CLI docs name gate
    type: docs
    pass_when: |
      After <!-- mol:bootstrap:managed end --> in both CLAUDE.md and AGENTS.md a sentence states pair 1 (ci-lint/ci-test = ci.yml lint/test run:) and pair 2 (official-gate pre-commit entry: = official-gate.yml PR job run: = uv run molmcp gate); docs/reference/cli.md documents molmcp gate.
    status: pending
out_of_scope:
  - Changing ci.yml OS/Python matrix or folding official/gate into ci.yml
  - Implementing molmcp.evaluate.evaluate (spec 11)
  - --skip, env-selected profiles, expressions in run:
  - Renaming release.yml job gate
  - GitHub branch-protection UI
---

## 2026-09-07 修订：`regressions/` 已删除

本仓从未发布过任何版本，没有可回归的对象；`regressions/` 也从来不在 CI 里跑
（`uv run pytest -v` 只跑 `tests/`），以致其中一个脚本烂掉很久无人察觉。整个目录
已删。**下文凡是要求新增 `regressions/<slug>.py` 的任务与判定一律作废**；相应的
正确性证明由 `tests/` 下的单元与结构性守卫承担。




# Acceptance criteria

Done means: the unique required check is named `official/gate`; PR, schedule and pre-push all run the one literal `uv run molmcp gate`; `ci.yml` is untouched as the package matrix; `gate.py` owns the verdict; `cli.py` only dispatches. Evaluation is NOT here — it needs two subagents and a GitHub runner has none; `harness-evaluator` owns it, developer-side.

## AC-001 — One profile, no evaluator seam

`run_gate` 的 `full` 布尔是唯一档位。廉价路径不得 import spec 11。

## AC-002 — Fixture verdicts

`contract-fail` 必须红，`wired` 廉价必须绿。这是判决函数的契约，不是 e2e。

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

两对 parity 的句子写在 managed 标记外，CLAUDE.md 与 AGENTS.md 同一 commit；`docs/reference/cli.md` 写上 `gate`。

## AC-010 — Regression

`regressions/autonomous-harness-evolution-13-ci-gate.py` 钉死字面量与两个 fixture 的出口码；不在运行时拉第三方、不默认 import spec 11。
