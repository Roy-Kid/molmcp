---
slug: harness-evaluator
created: 2026-09-07
criteria:
  - id: ac-001
    summary: Case set is plain-Python, five keys, both kinds present
    type: code
    pass_when: |
      tests/test_harness_cases.py::TestHarnessCases shows every entry of
      scripts/harness_cases.CASES has exactly the keys id, graduated, task,
      expect, forbid; ids are unique; expect and forbid are each non-empty;
      at least one entry has graduated True and at least one has graduated
      False; held_out_ids() and graduated_ids() are disjoint and their union
      is every id; case_by_id on an unknown id raises KeyError; the module
      imports nothing outside the standard library and parses no
      YAML/JSON/TOML.
    status: pending
  - id: ac-002
    summary: The actor is never told the criteria
    type: code
    pass_when: |
      For every case in CASES, no string in its expect or forbid list is a
      substring of its task (tests/test_harness_cases.py), and
      .claude/agents/harness-actor.md contains none of those strings and none
      of the words expect, forbid, harness_cases
      (tests/test_harness_agents.py).
    status: pending
  - id: ac-003
    summary: A payload that names a side is refused, not trusted
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestBlindnessGuard shows report() raises
      EvaluationError when the observation carries any of sides, champion,
      challenger, champion_sha, challenger_sha, and when manifest["sides"] is
      not a bijection from {"A","B"} onto {"champion","challenger"}; in the
      first case the injected store records zero tree_path calls.
    status: pending
  - id: ac-004
    summary: Unblinding decides; swapping sides flips the verdict
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestBlindnessGuard feeds one observation
      twice with manifest["sides"] swapped and gets report.reason ==
      molmcp.evolution.ACCEPTED once and molmcp.evolution.WORSE_CALL_COUNT
      the other time.
    status: pending
  - id: ac-005
    summary: Unobservable readings are refused and pinned to zero
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestObservedSeams shows report() raises
      EvaluationError for any reading carrying a tokens or a latency_s key,
      and that on a well-formed observation both report.champion_metrics and
      report.challenger_metrics have tokens == 0 and latency_s == 0.0.
    status: pending
  - id: ac-006
    summary: An abandoned held-out run raises instead of reading cheap
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestBlindnessGuard shows a held-out reading
      with contract_met False and the lowest call_count in the observation
      makes report() raise EvaluationError whose message names the case_id,
      the side and the seed; flipping that one field to True makes the same
      input produce an EvaluationReport.
    status: pending
  - id: ac-007
    summary: Graduated failure short-circuits before any replay
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestReport shows a challenger-side
      graduated case with contract_met False under any seed yields
      report.reason == molmcp.evolution.REGRESSION_FAILED,
      report.regression_passed is False, both metrics all zero, and the
      injected ObservedReplay recorded zero calls.
    status: pending
  - id: ac-008
    summary: Readings sum held-out cases only; cells must be complete
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestReport shows each side/seed Metrics
      equals the sum of that side's held-out tool_errors and call_count with
      graduated-case counts excluded, and that a missing or duplicated
      (side, seed, case_id) cell each raise EvaluationError.
    status: pending
  - id: ac-009
    summary: ImmutableGitStore.tree_path is the only checkout mechanism
    type: code
    pass_when: |
      tests/test_harness_eval.py::TestObservedSeams shows report() calls
      store.tree_path for both champion_sha and challenger_sha and lets
      UnknownShaError propagate for an unpublished sha;
      scripts/harness_eval.py contains no subprocess, no git, no shutil, no
      tarfile and no second checkout path, and ObservedReplay dispatches str
      targets to the champion table and Path targets to the challenger table.
    status: pending
  - id: ac-010
    summary: No second comparator, no score, no threshold
    type: code
    pass_when: |
      A character scan of scripts/harness_eval.py finds none of the QUOTED
      literals "accepted", "worse_tool_errors", "worse_call_count",
      "worse_tokens", "worse_latency", "no_practical_gain",
      "regression_failed", and none of DROP_ or score. The scan is on the
      quoted form because EvaluationReport.accepted is a field name: a main()
      that prints report.accepted must not be forced into concatenation or
      getattr to pass its own acceptance, which is exactly the obfuscation
      this check exists to prevent. It finds an import of evaluate from
      molmcp.evolution;
      report.reason is always compared against the constants imported from
      molmcp.evolution rather than a local copy.
    status: pending
  - id: ac-011
    summary: No runtime dependency, no env var, nothing under src/
    type: code
    pass_when: |
      anthropic appears in no group of pyproject.toml; scripts/harness_eval.py
      and scripts/harness_cases.py contain no os.environ, no getenv and no
      import anthropic; the only pyproject.toml edit is adding "scripts" to
      [tool.pytest.ini_options] pythonpath;
      tests/test_no_env_switches.py::_ALLOWED still has exactly three entries.
      EXACTLY ONE file under src/ changes: src/molmcp/components/__init__.py
      gains UnknownShaError, StoreError and ShaConflictError in its import and
      its __all__ and nothing else (2026-09-07 architect ruling — the facade
      exported neither, so ac-009 and regression golden 6 could not both be
      met; an exception a caller must catch belongs on the public surface,
      where CatalogError and GitError already are). No other file under src/
      is added or modified, and no behaviour changes.
    status: pending
  - id: ac-012
    summary: Both agent definitions pin a model and a tool list
    type: code
    pass_when: |
      tests/test_harness_agents.py::TestHarnessAgents shows
      .claude/agents/harness-actor.md and .claude/agents/harness-observer.md
      each open with YAML frontmatter carrying name, description, tools and
      model; name is harness-actor and harness-observer respectively; both
      model values are non-empty literals with no {{ placeholder; the actor's
      tools list contains neither Write nor Edit; the observer body names the
      A/B blind labels and the keys case_id, seed, side, contract_met,
      tool_errors, call_count and contains none of champion, challenger,
      tokens, latency_s.
    status: pending
  - id: ac-013
    summary: Regression reproduces every verdict with a negative control
    type: runtime
    pass_when: |
      `uv run python regressions/harness-evaluator.py` exits 0 and pins these
      hard-coded in-repo goldens (2026-09-07, no third-party oracle), each
      written as its own literal never reused as an input, each paired with a
      one-field-different negative control that yields a different result:
      (1) reason "accepted" with both sides tokens 0 and latency_s 0.0 and the
      two golden call_count means, where no single seed reading equals its own
      field mean — control: sides swapped gives "worse_call_count";
      (2) an observation carrying "sides" raises EvaluationError — control:
      key removed gives a report; (3) a reading carrying "tokens": 900 raises
      — control: key removed gives a report; (4) a held-out reading with
      contract_met false and the lowest call_count raises — control: set true
      gives a report; (5) a failed graduated case gives "regression_failed"
      with both metrics zero and zero replay calls — control: set true gives a
      different reason; (6) a fake store missing the challenger sha raises
      UnknownShaError — control: registered sha gives a report;
      (7) report.reason equals the constant imported from molmcp.evolution and
      hasattr(report, "score") is False. The script imports no pytest, no
      anthropic, opens no network, git or subprocess, reads no environment
      variable, and exposes both main() and test_harness_evaluator().
    status: pending
---

# Acceptance criteria

- **ac-001 / ac-002 — 用例集是数据，且演员看不见判据。** 用例集是这套评估器唯一的正确性口径。`expect` 与 `forbid` 都必须非空：只有正向期望的用例没有负向对照，永远不会失败。`task` 里不许含判据字符串，是把「演员不知道判据」从叮嘱变成一条会挂的断言。
- **ac-003 / ac-004 — 盲性靠检查。** 观察者说得出「哪一侧是挑战者」，就说明它被告知过了；解盲只能来自 manifest。ac-004 是这条的正面证明：同一份观察结果、`sides` 反过来，结论必须翻。
- **ac-005 — transcript 上读不出来的读数不报。** `tokens` 与 `latency_s` 两侧一律 0；在 `evaluate` 的独立比较里，0 对 0 是唯一「什么都不决定」的取值。允许观察者写进来，下一版就会去猜一个数。
- **ac-006 — 放弃不许显得便宜。** 没做完的一轮读数更少，收进均值等于让放弃看起来像改进。补救是把用例升为 graduated 或去修 harness，不是悄悄拉低均值。
- **ac-007 / ac-008 — 短路与分母。** 毕业用例失败必须在任何 replay 之前就定案；毕业用例的开销不进读数；缺一格就改变了均值的分母。
- **ac-009 / ac-010 — 一套 checkout，一套比较器。** 树只从 `ImmutableGitStore.tree_path` 来；判决只从 `molmcp.evolution.evaluate` 来。字面量扫描是防止有人「顺手」在入口里复制一个阈值或一个 reason。
- **ac-011 — 评估器不是运行时。** `scripts/` 不进 wheel，`src/` 一行不动，`anthropic` 一组都不进，环境变量豁免名单仍是三条。
- **ac-012 — 判官和被告都不许漂。** 两侧 `model` 写死；演员没有 `Write` / `Edit`，一轮评估不改仓库。
- **ac-013 — 每个 golden 配一个负向对照。** 本链上已有两个回归带着「同一个常量既喂夹具又喂断言」的空洞 golden 落库。这里要求每个 golden 是独立写出的字面量，并且有一个只差一处的输入能让它产出不同的值——断言必须证明得了自己会失败。
