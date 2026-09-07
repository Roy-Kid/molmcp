---
title: harness-evaluator — 双 agent 盲测的 harness 评估器
status: approved
created: 2026-09-07
---

## 2026-09-07 修订：`regressions/` 已删除

本仓从未发布过任何版本，没有可回归的对象；`regressions/` 也从来不在 CI 里跑
（`uv run pytest -v` 只跑 `tests/`），以致其中一个脚本烂掉很久无人察觉。整个目录
已删。**下文凡是要求新增 `regressions/<slug>.py` 的任务与判定一律作废**；相应的
正确性证明由 `tests/` 下的单元与结构性守卫承担。


# harness-evaluator — 双 agent 盲测的 harness 评估器

## Summary

给开发者一套「换了 harness 到底有没有变好」的可复现判据，而不给 molmcp 增加任何运行时。评估器由三样东西组成：一个 **actor** subagent 在干净上下文里扮演用户做一项任务，它拿到的 harness 是**提示词里的文本**而不是它去读的目录，并且**永远不知道判据**；一个 **observer** subagent 拿到两份不带标签的 transcript、握着该用例的判据、跑在固定的 harness 版本上，只输出它能从 transcript 上数出来的东西；一段薄薄的 Python 把观察者的结构化输出接到**已经落库的** `molmcp.evolution.evaluate(...)`，由 Python 独占短路顺序、四项读数各自独立比较、以及那七个冻结的 reason 字面量。观察者读 transcript 这件事本身就是遥测源，没有中间件，没有 API 用量字段。两处必须当面说清的代价：**`tokens` 与 `latency_s` 在 transcript 上读不出来**，所以两侧一律记 0，在 `evaluate` 的独立比较下它们既不能定罪也不能立功；**`DROP_*` 阈值全是 0，而 LLM 重跑本身有噪声**，所以一份报告是证据不是决定，晋升仍是 spec 12 里操作员的单独动作。调阈值属于 `evaluate` 自己的 spec，本 spec 一行都不改它。

## Design

### 形状（已定，不重开）

```
champion 侧                              challenger 侧
  actor subagent                           actor subagent
  干净上下文，扮演用户                      干净上下文，扮演用户
  harness 文本来自提示词                    harness 文本来自提示词
  不知道判据                                不知道判据
        |                                        |
    transcript                               transcript
        \________________  ___________________/
                         \/
                observer subagent
                握判据；盲：不知道哪份是挑战者
                跑在固定 harness 版本上
                         |
        ContractOutcome（做对了没有） + Metrics（从 transcript 上数出来的）
                         |
              scripts/harness_eval.py
                         |
              molmcp.evolution.evaluate(...)
                         |
                 EvaluationReport
```

演员知道判据就会去对判据优化，量到的是应试而不是「harness 会不会自然把人带到对的路上」；观察者知道哪份是挑战者、或者跟被测对象一起变，判官和被告就一起动，两次运行不可比。这两条是整份设计的地基。

### 实体与新符号

**`scripts/harness_cases.py` —— 用例集（数据）**

零依赖纯 Python，不引 YAML / JSON / TOML。沿用 `tests/discovery/golden_queries.py` 的**三段式形状**（task + 正向期望 + 负向对照），键名另起（那边是 `expect_top1_suffixes` / `forbid_top3_suffixes`，排序专用；`id` / `graduated` 在那边没有对应）：

```python
CASES: list[dict] = [
    {
        "id": "capability-gap-report",
        "graduated": False,
        "task": "<原样交给 actor 的用户请求文本>",
        "expect": ["<观察者要在 transcript 上核对的一条判据>", ...],
        "forbid": ["<transcript 里出现即算失败的一条>", ...],
    },
    ...
]

def case_by_id(case_id: str) -> dict: ...
def held_out_ids() -> tuple[str, ...]: ...
def graduated_ids() -> tuple[str, ...]: ...
```

三条用例，全部只考 CLAUDE.md 里已经写死的规矩，因而判据能从 transcript 上直接核对：

1. `capability-gap-report`（held-out）—— 用户要调一个不存在的上游 API。`expect`：transcript 报出 capability gap 并指名 step / package / ref。`forbid`：凭空造出的符号名被当作真的用。
2. `discover-before-code`（held-out）—— 用户要照着某个包写代码。`expect`：第一段代码之前至少有一次 `packages` / `outline` / `open`。`forbid`：任何发现调用之前就出现代码块。
3. `no-env-switch`（graduated）—— 用户要加一个由环境变量开关的功能。`expect`：转向 settings 并引用 no-env 规则。`forbid`：给 `src/` 提出 `os.environ` 读取。

`graduated` 决定这条用例进 `evaluate` 的 `regression_cases` 还是 `held_out_cases`，与 `evaluate` 的两个参数一一对应，没有第三种。

**`scripts/harness_eval.py` —— 那一个薄入口**

```python
@dataclass(frozen=True, slots=True)
class ObservedChallenger:   # 实现 evaluate.Challenger
    sha: str
    component: str
    affected_paths: tuple[str, ...]

class ObservedRunner:       # 实现 evaluate.ContractRunner
class ObservedReplay:       # 实现 evaluate.ReplayFn

def report(observation: Mapping[str, object],
           manifest: Mapping[str, object],
           *, store) -> EvaluationReport: ...

def main(argv: Sequence[str] | None = None) -> int: ...
```

`Challenger` / `ContractRunner` / `ReplayFn` 是 Protocol，仓库至今只有回归脚本里的假对象实现过它们。这三个类是它们的**第一份具体实现**——实现一个 Protocol 不是造平行类型，造平行类型是再写一个 `Metrics`。

**两份输入，故意分开的两个文件。** 观察者只产出 `observation`；`manifest` 由编排方在**运行之前**写好，并且**从不给观察者看**：

```jsonc
// manifest（编排方写；观察者看不到）
{
  "champion_sha": "<40 位小写十六进制>",
  "challenger_sha": "<40 位小写十六进制>",
  "component": "<component id>",
  "affected_paths": ["skills/daily/pack.md"],
  "seeds": [1, 2, 3],
  "sides": {"A": "champion", "B": "challenger"}
}

// observation（观察者写；只有盲标签 A / B）
{
  "schema": "harness-eval/1",
  "readings": [
    {"case_id": "discover-before-code", "seed": 1, "side": "A",
     "contract_met": true, "tool_errors": 0, "call_count": 7},
    ...
  ]
}
```

**`report()` 的拒收规则（盲性与可观测性靠检查，不靠自觉）**

1. `observation` 里出现 `sides` / `champion` / `challenger` / `champion_sha` / `challenger_sha` 中任意一个 → `EvaluationError`。观察者说得出边就说明它被告知过了。
2. `manifest["sides"]` 不是 `{"A": …, "B": …}` 到 `{"champion", "challenger"}` 的双射 → `EvaluationError`。
3. 任何一条 reading 带 `tokens` 或 `latency_s` → `EvaluationError`。**transcript 上读不出来的读数，本评估器不报**；允许它写进来，下一版观察者就会去猜一个数，那是伪造遥测。产出的 `Metrics` 两侧一律 `tokens=0, latency_s=0.0`；在 `evaluate` 的 `challenger > champion + drop` / `challenger < champion - drop` 下，0 对 0 既不构成回退也不构成收益，是唯一「什么都不决定」的取值。
4. `case_id` 不在 `CASES` 里 → `EvaluationError`。拼错的用例名被静默平均进均值，比报错糟得多。
5. **某条 held-out 用例在任一侧 `contract_met` 为 false → `EvaluationError`，并指名 case / side / seed。** 没做完的一轮通常读数更便宜——调用更少、错误更少——把它收进均值等于让「放弃」看起来像「改进」。补救是把这条用例升为 graduated，或者去修 harness，不是让它悄悄拉低均值。
6. `(side, seed, case)` 三元格必须**不重不漏**地铺满两侧全部用例；缺一格就悄悄改变了均值的分母 → `EvaluationError`。
7. `store.tree_path(manifest["challenger_sha"])` 与 `store.tree_path(manifest["champion_sha"])` 都必须解得开，`UnknownShaError` 原样上抛。store 没发布过的 harness 上的报告不可复现。这也是本 spec 唯一的 checkout 机制，不另写第二套。

**读数怎么算。** 每个 `(side, seed)`：把该侧该轮**全部 held-out 用例**的 `tool_errors` 与 `call_count` 分别求和，得到一个 `Metrics`。graduated 用例的计数**不进读数**——毕业用例是正确性合同，不是读数；让它的开销参与比较，等于让一条合同题的长短去决定晋升。

**graduated 行两侧都收，只用挑战者侧。** actor 不知道哪条用例是毕业用例，观察者不知道哪一侧是挑战者，所以两侧都会跑出 graduated 行。`report()` 用 `sides` 解盲后，只把挑战者侧的 graduated 行喂给 `ObservedRunner`（某条用例在任一 seed 上 `contract_met` 为 false 即整条失败，失败 id 收进 `ContractOutcome.failed_case_ids`），冠军侧的 graduated 行丢弃——`evaluate` 只在挑战者树上跑毕业用例。

**`ObservedReplay` 怎么分侧。** 沿用 `ReplayFn` 文档里已经写死的约定：冠军以 `str` sha 传入，挑战者以 `Path` 树传入，`isinstance(target, Path)` 就是分派条件。不新加 side 参数。

**`seeds` 在这里是什么。** 不是随机数种子——LLM 不吃种子。它是**重复轮次编号**：同一侧、同一份提示词、独立重跑第 1/2/3 轮。`DEFAULT_SEEDS` 的三轮是这里能给出的全部可重复性，而 `DROP_*` 全为 0 的前提（「replay 是有种子的，任何朝坏方向的移动都是真的」）在 LLM 上**不成立**。这条债当面记在这里：一份报告是证据，不是晋升；晋升是 spec 12 里操作员的动作。加噪声带要改 `evaluate` 的模块常量，那是它自己的 spec。

**`main()`。** `--observation PATH --manifest PATH --store-root PATH`，三个都必填，**不读环境**、无默认值。构造 `ImmutableGitStore(store_root, GitHubTransport())`——transport 只为满足构造签名，`tree_path` 是只读查表，不发请求。打印报告；**产出了报告就退 0**（拒绝也是一次成功的评估），只有把观察结果变不成报告（`EvaluationError` / `UnknownShaError`）才退 1。运行手册写在模块 docstring 里，与 `scripts/eval_relevance.py` 同形。

**`scripts/harness_eval.py` 里不得出现的东西**：任何阈值常量、任何 reason 字面量、任何 `score` / 加权 / 排名、任何 `import anthropic`、任何 `os.environ` / `getenv`。判决整个来自 `molmcp.evolution.evaluate`。

**两份 agent 定义（`.claude/agents/`）。** 仓库此前没有 `.claude/agents/`；两份文件都是 YAML frontmatter（`name` / `description` / `tools` / `model`）加 markdown 过程体，形制按 Claude Code 自己的 `.claude/agents/` frontmatter 约定（本仓没有可引用的样例文件；四个键由 ac-012 独立钉死）。

- `harness-actor.md`：干净上下文；扮演用户；**被测 harness 以文本随提示词到达**（`<harness-under-test>` 段），因此活的 `.claude/` 从不被改写、一轮运行完全可复现；明令**不得**去读 `.claude/` 取被测 harness；**不含任何判据**，也不引用 `harness_cases` 的 `expect` / `forbid`；`model` 写死，两侧同一个；`tools` 两侧逐字相同且**不含 `Write` / `Edit`**——一轮评估不允许改动仓库，两侧工具表不同就等于被测的不止 harness。
- `harness-observer.md`：`tools` 只需 `Read`（transcript 可能很大）；`model` 写死，因为判官不能跟被告一起变；输入是两份**不带标签**的 transcript（`A` / `B`）加该用例的 `expect` / `forbid`；输出严格是上面的 `observation` schema；明令**不得**输出侧名、sha、`tokens`、`latency_s`。它自己的定义住在本仓库、不住在被测树里，这就是「跑在固定 harness 版本上」的落实方式。

### Reuse decision

本轮 caller 未附 `librarian_report`（blueprint 刷新推迟）。以下逐条按源码扫描处置：

- `reuse molmcp.evolution.evaluate` —— 判决、短路顺序、四项独立比较、未取整均值、七个 reason 字面量全部由它给。本 spec 不写比较器、不写阈值、不加 `score`。
- `reuse molmcp.evolution.{EvalCase, Metrics, ContractOutcome, EvaluationReport, EvaluationError}` —— 一律从包 façade 导入。malformed 的观察结果就是「交到这一层的坏值」，正是 `EvaluationError` 文档里那类，不另开错误类型。
- `reuse molmcp.evolution.{Challenger, ContractRunner, ReplayFn}` —— `ObservedChallenger` / `ObservedRunner` / `ObservedReplay` 是这三个 Protocol 的具体实现。`propose.Candidate` **不能**复用为 `Challenger`：它没有 `sha`，而且 `__init__.py` 明写这两个 `C` 是不同概念。
- `reuse molmcp.components.ImmutableGitStore.tree_path` —— 唯一 checkout 机制，同时兼作「这个 sha 真的发布过」的前置检查。
- `generalize molmcp/components/__init__.py 的 __all__` —— **2026-09-07 architect 🔴 的裁定**：
  `molmcp.components` 目前只导出 `CatalogError` 与 `GitError`，而 `store` 的
  `UnknownShaError` / `StoreError` / `ShaConflictError` 一个都没导出（该包 docstring
  第 44 行甚至点名了 `IneligibleShaError`，同样没导出）。ac-009 与回归 golden 6 都要
  catch 未发布 sha 的那个异常，于是原稿自相矛盾：走公开面拿不到它，reach-through
  `molmcp.components.store` 又违反本 spec 自己的「只走公开面」，补进 `__all__` 又被
  ac-011 的「`src/` 一行不动」挡住。裁定：**补 `__all__`**。调用方必须 catch 的异常本来
  就属于公开面，`CatalogError` / `GitError` 已经在那里，store 那几个是漏的。ac-011 精确
  放宽到这一处：只加 import 与 `__all__` 条目，不改任何行为。
- `reuse molmcp.components.SHA_PATTERN` —— manifest 的 sha 校验，不另写正则。
- `reuse molmcp.components.GitHubTransport` —— 只为满足 `ImmutableGitStore` 的构造签名（`token: str | None = None`，不读环境）。
- `pattern tests/discovery/golden_queries.py` —— 沿用它的用例格式与键词汇（`task` + 正向期望 + 负向期望、零依赖纯 Python、一个数据源同时喂确定性检查和模型判官），只把排序专用的 `_top1` / `_top3` 后缀去掉。**不做代码级 generalize**：两套 oracle 的期望值域不相交（qualname 后缀 vs. transcript 判据），共享的只是约定而没有一行共享代码，硬合并只会得到一个装着两份无关列表的容器；而搬动 `golden_queries.py` 会牵动 `test_golden_ranking.py` 与 `eval_relevance.py`，属另一次改动。出现第三个 oracle 时再把这套约定提为模块。
- `pattern scripts/eval_relevance.py` —— 「开发者侧、不进 CI、不进 wheel 的 Python 放 `scripts/`」这条放置规矩照抄。**不复用它本身**：它自己驱动模型（`import anthropic` + 读 `ANTHROPIC_API_KEY`），而本 spec 的模型是开发者手上那个 agent，入口只吃观察者已经产出的结构化结果，既不发请求也不读环境。
- `pattern regressions/autonomous-harness-evolution-11-evaluate.py` —— 回归脚本形制：standalone、`_require`、goldens 与输入分开各写各的字面量、dual-callable。
- `pattern tests/test_no_env_switches.py` —— 结构性守卫（按路径读文件、文本/AST 断言）的写法，用于用例集与两份 agent 定义。
- `new — scripts/harness_cases.py 的 CASES 与三个访问器` —— 仓库没有 harness 用例集。
- `new — scripts/harness_eval.py 的 report / main / 三个 Observed* 实现` —— 仓库没有把观察者输出接到 `evaluate` 的适配器。
- **不在 `src/` 下新增任何模块** —— `molmcp.evolution` 的 leaf 声明是「标准库加那个 helper」，而入口必须拿 `ImmutableGitStore`；放进去就把那句话变成假的。这也正好保住「不是运行时组件」：`[tool.setuptools.packages.find] where = ["src"]`，`scripts/` 不进 wheel。
- 不 reuse `src/molmcp/gate.py` —— 该文件尚不存在；spec 13（**未实现**，仍在 `.claude/specs/` 上）已把 `--full` 删掉，理由就是 GitHub runner 里起不了 subagent。本 spec 同理不进 required check。

## Files to create or modify

- `.claude/agents/harness-actor.md` (new)
- `.claude/agents/harness-observer.md` (new)
- `scripts/harness_cases.py` (new)
- `scripts/harness_eval.py` (new)
- `tests/test_harness_cases.py` (new)
- `tests/test_harness_eval.py` (new)
- `tests/test_harness_agents.py` (new)
- `pyproject.toml`
- `src/molmcp/components/__init__.py` — **仅**把 `UnknownShaError` / `StoreError` / `ShaConflictError` 加进 import 与 `__all__`（architect 🔴 裁定；无行为改动）
- `regressions/harness-evaluator.py` (new)

## Tasks

- [ ] Export UnknownShaError, StoreError and ShaConflictError from src/molmcp/components/__init__.py (import + __all__ only; no behaviour change) and pin them with a test
- [ ] Write failing structural tests for the case set (tests/test_harness_cases.py → TestHarnessCases) and add "scripts" to pytest pythonpath in pyproject.toml
- [ ] Implement CASES, case_by_id, held_out_ids, graduated_ids in scripts/harness_cases.py (three cases, >=1 graduated and >=1 held-out; Google-style docstrings)
- [ ] Write failing unit tests for the observation adapter (tests/test_harness_eval.py → TestReport, TestBlindnessGuard, TestObservedSeams)
- [ ] Implement ObservedChallenger, ObservedRunner, ObservedReplay, report and main in scripts/harness_eval.py (Google-style docstrings; no threshold, no reason literal, no score, no anthropic, no os.environ)
- [ ] Write failing structural tests for the two agent definitions (tests/test_harness_agents.py → TestHarnessAgents)
- [ ] Write .claude/agents/harness-actor.md (frontmatter name/description/tools/model; harness arrives as prompt text; no criteria; no Write/Edit tool)
- [ ] Write .claude/agents/harness-observer.md (frontmatter name/description/tools/model; blind A/B transcripts; emits the observation schema only)
- [x] ~~Add regression example regressions/harness-evaluator.py (public API only; hard-coded goldens with a negative control per golden, no third-party runtime)~~ — 作废：`regressions/` 已删除（2026-09-07）
- [ ] Run full check + test suite

## Testing strategy

`tests/` 下只放单元与结构性守卫，路径按模块镜像，每个测试模块只打一个源模块；单元变绿 = `uv run pytest {path} -v`。真正的两 agent 对局**不在 `tests/` 里跑**——GitHub runner 里没有 agent，那正是 spec 13 删掉 `--full` 的理由。

**`tests/test_harness_cases.py` → `TestHarnessCases`（打 `scripts/harness_cases.py`）**

- Happy：`CASES` 每条恰有 `id` / `graduated` / `task` / `expect` / `forbid` 五个键，类型正确。
- Happy：`case_by_id` 对每个 id 取回同一个 dict；`held_out_ids()` 与 `graduated_ids()` 互不相交、并集等于全部 id。
- Edge：id 唯一；`expect` 与 `forbid` 都非空——没有负向对照的用例不算用例。
- Edge：至少一条 `graduated is True`、至少一条 `graduated is False`（`evaluate` 对空 `held_out_cases` 抛错）。
- Edge（**判据不泄漏**）：任何一条 `expect` / `forbid` 的字符串都**不是**该用例 `task` 的子串。演员拿到的文本里不能含判据。
- Edge：`case_by_id("nope")` 抛 `KeyError`。

**`tests/test_harness_eval.py`（打 `scripts/harness_eval.py`）**

`TestReport`
- Happy：挑战者严格更少 `call_count`、其余打平 → `report.accepted is True`、`reason == ACCEPTED`（从 `molmcp.evolution` 导入的那个常量）；两侧 `Metrics.tokens == 0` 且 `latency_s == 0.0`。
- Happy：`seeds` 原样记进 `report.seeds`；每 `(side, seed)` 的读数是该轮 held-out 用例的和，graduated 用例的计数不在其中。
- Edge：挑战者某条 graduated 用例在某一 seed 上 `contract_met` 为 false → `reason == REGRESSION_FAILED`、`regression_passed is False`、两侧 `Metrics` 全零，且注入的 replay **一次都没被调用**。
- Edge：`case_id` 不在 `CASES` 里 → `EvaluationError`。
- Edge：缺一格 `(side, seed, case)` → `EvaluationError`；重复一格 → `EvaluationError`。

`TestBlindnessGuard`
- Edge：`observation` 带 `sides` / `champion` / `challenger` / `champion_sha` / `challenger_sha` 任一 → `EvaluationError`，且未触碰 store。
- Edge：`manifest["sides"]` 不是双射（两个 `"champion"`、少一边、出现第三个标签）→ `EvaluationError`。
- Edge（**解盲真的在决定**）：同一份 `observation`、`sides` 反过来 → 结论从 `accepted` 翻成 `worse_call_count`。
- Edge（**放弃不许显得便宜**）：某条 held-out 读数 `contract_met` 为 false 且 `call_count` 全场最低 → `EvaluationError`，错误信息指名 case / side / seed；把它改成 true 后同一份输入产出报告。

`TestObservedSeams`
- Edge：任何一条 reading 带 `tokens` 或 `latency_s` → `EvaluationError`。
- Edge：`ObservedReplay` 对 `str` 目标取冠军表、对 `Path` 目标取挑战者表；反过来喂会取错表。
- Edge：store 没发布过挑战者 sha → `UnknownShaError` 上抛（不吞成 `ok=False`）；冠军 sha 同样。
- Edge（**没有第二套比较器**）：`scripts/harness_eval.py` 源码不含 `"accepted"` / `"worse_"` / `"no_practical_gain"` / `"regression_failed"` 任一字面量、不含 `DROP_`、不含 `score`、不含 `os.environ` / `getenv` / `anthropic`（按字符扫源码，与 `test_no_env_switches.py` 同手法）。

**`tests/test_harness_agents.py` → `TestHarnessAgents`（打 `.claude/agents/` 两份 md）**

- Happy：两份文件都以 `---` 开头，frontmatter 含 `name` / `description` / `tools` / `model` 四个键，`name` 分别是 `harness-actor` / `harness-observer`。
- Edge：两份的 `model` 都是写死的字面量（非空、不含 `{{`）——判官与被告都不许随环境漂。
- Edge：actor 的 `tools` 不含 `Write`、不含 `Edit`。
- Edge（**判据不泄漏**）：`harness-actor.md` 全文不含任何一条 `CASES[*]["expect"]` / `["forbid"]` 字符串，也不含 `expect` / `forbid` / `harness_cases` 这些词。
- Edge：`harness-observer.md` 正文出现 `A` / `B` 盲标签与 observation schema 的键名（`case_id` / `seed` / `side` / `contract_met` / `tool_errors` / `call_count`），且**不含** `champion` / `challenger` / `tokens` / `latency_s`。

**回归示例（`regressions/harness-evaluator.py`）**

Standalone，不 import pytest，只走公开面：`scripts/harness_eval.report` / `main` 与 `molmcp.evolution` façade 的类型和常量（不 import `molmcp.evolution.harness…` 之类的私有路径）。`scripts/` 不在 standalone 运行的 `sys.path` 上，脚本顶部一行 `sys.path.insert` 指向仓库 `scripts/`，与 `scripts/eval_relevance.py` 现有手法同形并注明理由。store 用一个只实现 `tree_path` 的假对象，不碰网络、不碰 git、不碰环境变量。

硬编码 golden（in-repo，2026-09-07，无第三方 oracle）。**每个 golden 都是独立写出的字面量，绝不由喂给 `report()` 的输入常量派生；每个 golden 都配一个负向对照——一个只差一处的输入，必须产出不同的值，以证明该断言真的会失败。** 前面这条链上有两个回归带着「同一个常量既喂夹具又喂断言」的空洞 golden 落库，这里不再重演。

1. 接受判决：`reason == "accepted"`、两侧 `tokens == 0`、`latency_s == 0.0`，并钉住两侧 `call_count` 均值。逐 seed 的读数刻意让**没有任何单轮读数等于它自己那一项的均值**（照 spec 11「均值真的是均值」的做法）。负向对照：同一份 observation 把 `sides` 反过来 → `reason == "worse_call_count"`。
2. 盲性：observation 带 `"sides"` → `EvaluationError`。负向对照：删掉该键，同一份输入产出报告。
3. 不可观测读数：某条 reading 带 `"tokens": 900` → `EvaluationError`。负向对照：删掉该键 → 产出报告。
4. 放弃不许显得便宜：某条 held-out 读数 `contract_met` 为 false 且 `call_count` 最低 → `EvaluationError`。负向对照：改成 true → 这一轮变成看起来最漂亮的「收益」，正说明那次报错拦住的是什么。
5. 毕业用例失败：`reason == "regression_failed"`、两侧 `Metrics` 全零、replay 调用次数为 0。负向对照：把该格 `contract_met` 改成 true → `reason` 不再是 `regression_failed`。
6. store 是唯一 checkout：假 store 里没有挑战者 sha → `UnknownShaError`。负向对照：在假 store 里登记该 sha → 产出报告。
7. 判决来自上游：`report.reason` 与从 `molmcp.evolution` 导入的常量按字符相等；`hasattr(report, "score") is False`。

`main()` 与 `test_harness_evaluator()` 双入口；`uv run python regressions/harness-evaluator.py` 直接可跑，成功退 0。

## Out of scope

- **任何用户侧回路。** 用户只读公开的 harness 知识、写不了它；唯一的回路是他们在能力缺口或报错处**主动开的一个 PR**。不从用户身上采集任何东西。
- **记忆系统。** molmcp 不建；用户习惯住在宿主自己的 memory 里。
- **在 CI 里跑这套东西。** GitHub runner 里没有 agent、起不了 subagent，这正是 spec 13 刚把 `molmcp gate --full` 删掉的理由。本 spec 不加 required check、不碰 `.github/workflows/`、不碰 `.pre-commit-config.yaml`。
- **开放式任务的 LLM 判官、复合 score、项目级（相对于用户级）知识**，以及改动 `propose.py` / `promote.py` / `wiki.py`。
- **改 `evaluate` 的任何东西**：`DROP_*` 阈值、噪声带、`Metrics` 字段、七个 reason 字面量、短路顺序。上面已当面记下「`DROP_*` 全 0 遇上 LLM 噪声」这条债；调它属于 `evaluate` 自己的 spec。
- **在 `src/` 下新增或修改任何模块**；`anthropic` 不进 `pyproject.toml` 的任何一组；不加环境变量，也不给 `tests/test_no_env_switches.py` 的三条豁免名单加第四条。
- **搬动 `tests/discovery/golden_queries.py`**（会牵动 `test_golden_ranking.py` 与 `scripts/eval_relevance.py`）。本 spec 只沿用它的格式约定。
- **把 `scripts/` 纳入 ruff。** 目前 lint 范围是 `src tests`，`scripts/eval_relevance.py` 与 `regressions/*.py` 一律不在其中；扩范围要同一 commit 改 `pyproject.toml` 的 tox、`.pre-commit-config.yaml`、`.github/workflows/ci.yml` 与 `mol_project.ci.local`，属 CI parity 变更，单独一次改动。此处按现有边界办，但**代价要说清**：`scripts/` 不被 lint 是既有状况；而把 `"scripts"` 加进 pythonpath 之后，CI 的 Test 步骤会在每次推送时 import 并执行 `scripts/harness_cases.py` 与 `scripts/harness_eval.py` —— 这是**本 spec 第一次**让 required check 执行未过 lint 的代码（`scripts/eval_relevance.py` 至今没被测试套 import 过）。扩 ruff 范围是单独一次 CI parity 改动。
- **改 `.claude/settings.local.json`、CLAUDE.md、`docs/`。** 运行手册写在 `scripts/harness_eval.py` 的模块 docstring 里，与 `scripts/eval_relevance.py` 同形。
- **刷新 `.claude/notes/architecture.md`**（blueprint 仍由 `/mol:map` 写）。
