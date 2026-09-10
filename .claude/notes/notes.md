# Notes

Evolving architectural decisions. Appended by `/mol:note`; newest first.

<!-- mol:note:topic:spec-premise-verify -->
## 2026-09-07 — spec 引用别的模块时,必须当场核实再写进 Design

autonomous-harness-evolution 那条 16 员链上,**四条 spec 的 Design 引用了并不存在
的东西**,全部在实现阶段才炸:

- spec 05:「FastMCP 4 没有 `mcp.lifespan` 属性」——它有;「`_lifespan` 可能为
  `None`」——永不为 None;「dict 返回值需要 return 注解才有 structured content」
  ——不需要。
- spec 07:`resolve_bundle_source` 被定为「唯一解释入口」,却没有任何调用方,
  `--source /不存在` 会静默降级。
- spec 08:把 `AppConfig.cache_dir` 当成总有值——它默认 `None`,导致没配
  `cacheDir` 的用户 harness 开箱即坏。
- spec 12:`ActivationUnboundError` 仓里根本没有;而且 04 把未绑定状态做成了
  不可构造(`Activation()` 直接 `TypeError("use Activation.bind")`)。
- spec 13:`from molmcp.evaluate import evaluate`,签名 `Path -> bool`——真实符号
  在 `molmcp.evolution.evaluate`,签名是 8 参数返回 `EvaluationReport`。

共同点:这些 spec 是**一次性批量起草**的,谁都没去跑一下。

**Rule**: spec 起草时凡引用另一个模块的类型名、字段、异常或签名,先
`uv run python -c "import ...; print(inspect.signature(...))"` 核一遍,再写进
Design。跨 spec 链尤其如此——后一条引用前一条**交付的**符号,不是前一条 spec
里**写的**符号。

<!-- mol:note:topic:golden-not-self-proving -->
## 2026-09-07 — golden 必须是独立字面量,且必须真跑反例

本链两个回归带着**恒真断言**落库,同一个模式:一个常量既喂给被测函数当输入、
又当断言的期望值,改它两边一起动,断言永远通不掉。两次都是**执行反例控制**
时才暴露,code review 看不出来。

**Rule**: 测试与示例里的 golden 与构造输入的字面量**分开各写各的**;每个 golden
至少跑一次「改坏 → 必须失败 → 还原」。控制项自己也要验:如果一个控制"通过"了,
那说明该 golden 是空的,先修 golden 再说。

<!-- mol:note:topic:facade-symbol-collision -->
## 2026-09-07 — 往包门面加符号之前,先 grep 现有 `__all__`

同一批 spec 里撞了三次:

- spec 10 与 spec 11 都要从 `molmcp.evolution` 导出名为 `Candidate` 的东西——
  一个是「被提议的补丁」,一个是「待评估的检出」。改名 `Challenger` 才解开。
- spec 07 声明 `HOSTS: dict[Host, HostLayout]` 于 `host/layout.py`,spec 15 声明
  `HOSTS: tuple[Host, ...]` 于 `host/install.py`。
- spec 09 与 spec 10 对**同一个包**指定了不同的测试目录(10 还显式排除了 09 选的)。

**Rule**: 起 spec 时若要往某个包的 `__all__` 加符号,先读那个 `__all__`,再读
同链其它 spec 的 Files 段。撞名不是实现细节,是两个概念抢一个词,必须在 spec
阶段解决。

<!-- mol:note:topic:test-dir-prefix -->
## 2026-09-07 — 测试目录用 `test_` 前缀镜像 `src/`

仓里两种约定都有先例(`tests/discovery/` `tests/collection/` 无前缀;
`tests/test_components/` `tests/test_provider/` 有前缀),于是 spec 09 与 10 各
选一种、互相矛盾。已统一。

**Rule**: `src/foo/bar.py` 的单测放 `tests/test_foo/test_bar.py`。一个源码包的
测试只放一个目录,不得分散。

<!-- mol:note:topic:isolation-check-imports -->
## 2026-09-07 — 「不得依赖 X」用 AST 查 import,不要全文 grep 子串

`test_wiki.py` 曾禁止 `wiki.py` 全文出现小写 `github`,结果实现被迫写成
`_FORGE_SCHEME = "git" + "hub:"` ——而那段代码的作用恰恰是**拒绝** forge URL,
是隔离的证据而不是违反。测试自己也得靠 `"create_" + "stack"` 躲开自己的扫描。
钝 grep 同时过宽(命中 docstring 与拒绝逻辑)又过窄(躲不过字符串拼接)。

**Rule**: 依赖隔离断言走 AST——遍历 `Import` / `ImportFrom`,把相对 import 解析
成绝对点分路径再比。只有 `importlib.import_module("...")` 这种 AST 看不见的
动态导入才补一条针对**点分模块路径**的文本检查。

<!-- mol:note:topic:fastmcp4-lifespan -->
## 2026-09-07 — FastMCP 4.0.0b5 lifespan 事实（推翻 spec 05 的三条前提）

Spec 05 的 Design 写了三条关于 FastMCP 4 的断言，实测**全错**。它们已在
`provider_worker/` 的 docstring 里改正，但 06–16 的 spec 正文可能仍带着旧
说法——照抄前先核对源码。

1. **`mcp.lifespan` 存在**，是继承来的 `AggregateProvider.lifespan`
   (`fastmcp/server/providers/aggregate.py:345`)：无参 `@asynccontextmanager`，
   聚合的是**被 mount 的 provider** 的 lifespan。它与构造函数 `lifespan=`
   存进 `_lifespan` 的那个 callable 是**两个不同对象、不同签名**。
   设计上刻意不用它——但不能说它「不存在」。
2. **`FastMCP._lifespan` 永不为 `None`**：`__init__` 在未传 `lifespan=` 时
   回落到 `default_lifespan`（`server/server.py:404-408`）。任何
   `if previous is None` 分支都是防御性死代码，写注释说明，别当正常路径。
3. **dict 返回值无需 return 注解**即可产出 structured content
   （实测 `ToolResult.structured_content == {"text": "ping"}` 两种情形一致）。
   所以 worker 线协议**不带** return 字段——别为它加。

**Rule**: 引用 FastMCP 私有属性前，先读
`.venv/.../fastmcp/server/` 的对应源码核实；`_lifespan_manager`
(`server/mixins/lifespan.py:169`) 做的是
`enter_async_context(self._lifespan(self))` 并把 yield 值缓存成
`_lifespan_result`——**任何包装 `_lifespan` 的代码必须把前一个 lifespan 的
yield 值透传出去**，吞掉它就等于悄悄拿走了服务器的应用状态。

<!-- mol:note:topic:worker-child-isolation -->
## 2026-09-07 — worker 子进程的 FastMCP 隔离边界在 provider.py

`provider_sdk.py` 早就把 `FastMCP` 放在 `TYPE_CHECKING` 下，但它
`from .provider import Provider`，而 `provider.py` 当时是**模块级**
`from fastmcp import FastMCP`——于是 `import molmcp.provider_sdk` 照样把整个
FastMCP 栈拖进任何进程。spec 05 的 child 必须 import `ProviderBase`，
AC-002（子进程无 fastmcp）因此不可能成立。已把那一行移到 `TYPE_CHECKING`
下（行为不变：该文件有 `from __future__ import annotations`，`FastMCP` 只出现在
docstring 与被字符串化的 `register` 注解里）。

**Rule**: `molmcp.provider_sdk` 及其依赖链（`provider.py`）是**子进程可安全
import 的边界**——不得在这条链上新增模块级 FastMCP / `molmcp.server` import。
`molmcp/__init__.py` 与 `provider_worker/__init__.py` 是 PEP 562 惰性门面，
`__getattr__` **必须**对未知名抛 `AttributeError`：CPython 的
`_handle_fromlist` 靠它回落到子模块导入，`from molmcp import cli/settings/
runtime/client_config` 等十余处调用点依赖这一点。

<!-- mol:note:topic:ruff-first-party-cache -->
## 2026-09-07 — ruff 的 first-party 判定随文件存在与否翻转

ruff 的 isort 按**目标模块文件是否存在于 `src/` 下**判 first-party。于是
RED 阶段写的 import 块（模块尚不存在 → 判 third-party）会在 GREEN 之后变成
I001。更糟的是 `.ruff_cache` 会掩盖它：751e874 就这样带着
`tests/test_components/test_models.py` 的 I001 落库，本地暖缓存全绿而**干净
检出必然挂 CI lint**（已修，见 fb4c348）。

**Rule**: 提交前用 `rm -rf .ruff_cache && uv run ruff check src tests` 复核
——CI 与新克隆跑的都是冷缓存。TDD 写测试时，先造出目标模块的空壳或事后
`ruff check --fix`，别相信 RED 阶段的 lint 结果。

## 2026-08-02 — molvis provider = 工作台原语,不是接口翻译层

molmcp 对 molvis 的角色定位:**把「活着的 Python 会话」借给 agent,而不是替
molvis 说话**。词汇表永远是 molpy/molvis 自己的公开 API;molmcp 只提供
5 个通用原语:`open` / `close` / `list_sessions` / `exec`(持久命名空间,
`stage` 预绑定)/ `poll_events`(事件 journal)。

依次否决过的三种形态(不要再提):

1. **复合便捷工具**(`show_smiles` = parse+embed+store+draw)——一个名字
   多个动作,隐藏状态迁移。
2. **1:1 具名包装目录**(`parse_molecule`/`clear`/`draw`/…)——粒度对了,
   但仍是 molmcp 必须与上游同步维护的镜像词汇。
3. **agent-facing JSON-RPC `call(method, params)`**——把 molvis Python↔前端
   的**内部** wire 协议提升成公共 API;且 frame 编码仍需进程内 Python,
   回路闭合不了。

拍板约束:**严禁环境变量开关**;molvis workbench **不设权限/opt-in 门控**
(本地信任模型,与 Jupyter kernel 同级,docs 一句话说明,不做机制)。

molq 的受控写入是另一回事——`provider-design.md` 要求它默认关闭。环境变量
那半已删,门保留但改由 settings 开:`molmcp config set molq.allowSubmit true`。
必须是 settings:`discover_providers()` 一律 `cls()` 构造,构造函数关键字
没有任何 MCP 客户端能传,开不了的门等于永远失效的工具。

架构定型为**四方**(一个会话,两个表面,两个意志):**人操作**(浏览器
UI,意志之一)、**canvas 显示**(molvis viewer,投影表面)、**agent 控制**
(MCP 5 原语,意志之二)、**代码操作**(molmcp serve 进程内 Python 会话,
持有 mol + stage,一切领域调用只在此)。四条边:人↔canvas(molvis 前端)、
canvas↔代码(molvis 内部 WS 协议,molmcp 不碰不暴露)、agent↔代码
(molmcp 工作台契约:exec + namespace + journal)、人↔agent(host 对话)。
真相单源:mol 在代码层,canvas 只是投影;两个意志经会话状态异步汇合。
附着外部已启动会话不在设计内(out of scope,非分期承诺)。

**选区↔行号契约:成立**(2026-08-03 对 molvis 前端源码核实,推翻了
spec 期的「未定,阻塞局部编辑」判断)。三处独立证据:`entity_source.ts:196`
建原子时 `atomId: index`;`selection_manager.getSelectedBondIds` 注释
「和 atom ids 一样,都是当前 frame block 的行号」;`commands/selection.ts`
的 `getSelectedCommand` 直接以选中 id 作行号切片(「sliced by row index」)。
故 **clear + 单次 `draw_frame(mol)` 后,选区 `atom_ids` 即
`list(mol.atoms)` 下标**,基于选区的局部图编辑今天就能做。前提是那条
clear 纪律:不 clear 连画两次,live frame 累加,行号指向合并帧。

molvis 侧配套缺口(molvis 另 spec,molmcp 不代偿):① 上述行号契约**缺
回归保护** —— `stage/tests/` 无 `getSelectedCommand` 行号语义测试,欠
一条 pin test(不是欠 API);② `NotConnectedError` 替代未连接时的 10s
阻塞超时;③ serve 环境构造回归;④ EventBus 无通配订阅,journal 只能
列举已知事件名(`session.py SUBSCRIBED_EVENTS` 是唯一在册的上游词汇)。
人工验收剧本:`../molvis-agent-e2e/PLAYBOOK.md`(out-of-tree)。

## 2026-07-22 — Molq provider in molmcp

First-party molq MCP tools live in **molmcp**:

```
src/molmcp/providers/molq/
  __init__.py
  provider.py          # MolqProvider
```

```toml
[project.entry-points."molmcp.providers"]
molq = "molmcp.providers.molq:MolqProvider"
```

- molq (`molcrafts-molq`) is a pure job-queue library — no FastMCP.
- Provider lazy-imports molq; missing install → clear register/call error.
- Tools:
  - Read-only: `list_jobs`, `get_job`, `job_logs`, `list_destinations`,
    `list_queue`
  - Opt-in mutate (`molq.allowSubmit` 设置;`allow_submit=True` 仅供内嵌方
    与测试): `submit_job` (argv, no block-wait), `cancel_job`
- Cleanup/watch/daemon, full Submitor mirror, Nerve reverse-control, batch
  loops: out of MCP (CLI/script/molexp).

Same placement rule as `providers/molexp/`. Contract:
`docs/concepts/provider-design.md`.

## 2026-06-10 — discovery 三 spec 链落地时捕获的规则

- **SCHEMA_VERSION 链规则**：每个改变持久化图内容（节点/边集合或其语义）的
  spec 各自将 `SCHEMA_VERSION` +1（ranking→2，conventions→3）。只读消费图的
  改动（如 lint）不 bump。`ANALYZER_VERSION` 仅在分析器输出变化时 bump。
- **overlay 哨兵单点定义**：合成文件哨兵 `"<catalog>"` 是 `node_id()` 的输入，
  在 `overlay/__init__.py` 定义一次（`CATALOG_FILE`），catalog.py 与
  conventions.py 复用——再次声明会让 overlay 节点 ID 命名空间静默分裂。
- **测试只用模块级 import**：函数内 import 仅限可选依赖。
- **窄域 `# type: ignore[<code>]` 在故意违型的测试（如 frozen dataclass
  突变测试）中允许；裸 `# type: ignore` 不允许。**
- **MCP payload 契约测试钉序列化字面量**（如 `"resolved"`），不引用枚举成员——
  测的是 wire format。

<!-- mol:note:topic:faked-seam-hides-broken-reader -->
## [2026-09-08] 缝把函数假掉时，至少要一条测试驱动真函数

`harness-evo-01-sources` 期间，`Settings.harness` 从 dict 改成 tuple 后
`server._harness_locator()` 对**每一个**安装都抛 `AttributeError`，`molmcp serve`
已断——而全量套件 1852 条全绿。原因：`tests/test_stack.py` 通过 `_wire` 缝注入
一个假的 locator，`grep -rn "_harness_locator" tests/` 唯一的命中是一个**测试
名字**，没有任何测试调用过真函数。缝越好用，越没人调用真货。

**Rule**：为某个函数造了测试缝之后，必须同时留至少一条不走缝、直接调用真函数的
测试。缝证明的是调用方编排正确，不是被缝掉的那个函数还能跑。

<!-- mol:note:topic:schema-type-flip-unlocks-writes -->
## [2026-09-08] 翻转 `_SCHEMA` 类型会静默解锁旧类型正在拒绝的写路径

`settings._SCHEMA["harness"]` 从 `dict` 改成 `list` 的瞬间，两条 CLI 写路径失去
保护：`_parse` 的 `expected is dict` 分支（抛 "set a member instead"）不再命中，
改走 `expected is list` 返回 `[value]`；`add_value` 的 `_SCHEMA.get(top) is not
list` 守卫不再触发，直接 append 裸字符串。两者都在 `write_settings_file` 之前
**无任何校验**。而 `_reject_unknown` 位于 `load_settings` 之下，于是下一条命令起
`config list/get/set/add/remove` 与 `serve` 全部 exit 2，**没有任何 CLI 能救回**，
只能手改 JSON。

**Rule**：改 `_SCHEMA` 里某个键的类型时，先列出 `_parse` / `set_value` /
`add_value` / `remove_value` / `_resolve` 中按**旧类型**分支的每一处，逐条确认新
类型下谁还在拒绝、谁开始放行。类型不只是校验规则，它同时是这些动词的调度键。
配套：元素是对象的 list 用 `_OBJECT_LISTS` 声明，两个字符串动词读表拒绝，
不在函数体里写死键名。

<!-- mol:note:topic:harness-layers-replace-not-union -->
## [2026-09-09] harness 列表跨层是「整份替换」，并集已放弃

链 01 把「跨层取并集」记为欠链 03 的债，但链 01 同时发布了钉住相反行为的东西：
`tests/test_settings.py` 的 `test_harness_is_a_list_setting_with_no_merge_channel`
与 `test_the_most_specific_layer_replaces_the_list_rather_than_merging`、
`settings.py` 里 `harness` 不属于任何合并通道、以及 `docs/concepts/harness.md`
的相应段落——最后一条还是构建强制的（`test_harness_catalog_fixture.py` 会解析
该页 JSON 并逐条构造真的 `HarnessSource`）。

链 03 的决定：**并集放弃，不是再往后推**。最具体的层整份胜出是自洽规则，没有任何
东西需要打破它；而 `harness` 不入任何合并通道，正是这条规则不用写代码就成立的原因
（`load_settings` 的默认分支「最后一次赋值胜出」+ `settings_layers` 低优先级在前）。

注意与 `_MERGED_LISTS` 成员方向相反：`excludes` / `knowledgeScope` 等用 `extend`
低→高累积，所以**用户文件**的条目活下来；`harness` 是**local 文件**的列表整份取代
用户文件的。两个 list 设置相隔十几行、方向相反，`ac-006` 在同一个测试里同时断言两者
就是为了让这件事写在测试里而不是留给人在安装时踩。

**Rule**：想让 harness 跨层合并之前，先改上面那两条测试和那页构建强制的文档；
它们是这个决定的落点，不是随手可绕的断言。

<!-- mol:note:topic:store-not-git-backed -->
## [2026-09-09] harness store 刻意不用 git 支撑，理由和触发阈值

问过一次：harness 仓本来就是 clone 下来的，`ImmutableGitStore` 为什么还要把每个
commit 解压成一份完整的树？实测过开销：demo 仓每个 commit 644K，真实 harness 仓
`.git` 12M / 工作区 13M，所以激活 N 个版本约等于 N 份工作区，而 git 用共享对象只需
一份历史。空间上 git 明显更省，`git worktree` 还能让多个 commit 同时物化（盲测 A/B
正需要两棵树并存），回滚也能到任意 commit 而不只是 `previous`。

**仍然不做，三个代价换不回来：**

1. **不可变性会丢。** 现在的树解压一次后永不改动——这是 `ImmutableGitStore` 里
   "Immutable" 的实质，服务中的 harness 不能被就地篡改。worktree 是活的检出。
2. **远程路径会多一个 git 依赖。** 今天远程是纯 HTTP + tarfile，没有 git 二进制也能
   跑；改成 clone 就必须有，且要处理 `--depth` / partial clone。
3. **落盘契约要变。** `<cache>/harness/commits/<sha>/tree` 与 `metadata.json` 的
   provenance + `ShaConflictError` 是 CLAUDE.md 列为「不可随意变更」的那类，需要
   bump、旧目录处理、迁移路径。

还有一个不显然的坑：直接在用户的工作检出里 `git worktree add`，会把 molmcp 的
worktree 写进**用户仓库**的 `.git/worktrees`。干净做法是 molmcp 在缓存里维护自己的
裸镜像（本地源可 `git clone --local` 硬链对象），再从镜像开 worktree——但那是又一个
要维护的东西。

**Rule**：在有人真的激活到几十个版本、或者 `molmcp cache` 清理不足以应付之前，不要
重开这个话题。真要做，先写 spec：上面第 1 条是真会丢的性质，必须先说清用什么补
（worktree 建完 `chmod -R a-w`？还是接受可改并说明为什么可以），而不是默认它无所谓。

<!-- mol:note:topic:evaluator-splits-harness-from-project -->
## [2026-09-10] 盲测评估器：机制随 harness 走，用例归项目

`/mol:evo`（skill）+ `harness-actor` + `harness-observer` 是 **harness 组件**，作为
`evo` bundle 随 harness 安装；`scripts/harness_cases.py` 与 `scripts/harness_eval.py`
留在 molmcp。

分界线是 `harness_cases.py` 自己写下的那句：「Every case tests a rule `CLAUDE.md`
already states」。用例编码的是**某个仓库**的规则——molmcp 的用例拿到 molpy 上就是
胡话。而盲测协议（manifest 先写、actor 只读且不见判据、observer 只见标签、只有
Python 门能判胜负）在哪个仓库都一样。

所以 skill 不许硬编码 molmcp 的路径：它声明自己需要什么（带 `id` / `graduated` /
`task` / `expect` / `forbid` 的用例集，加一个把 manifest + observation 变成裁决的
命令），把 molmcp 那两个文件只当**示例**写。项目两样都没有 → 停下来说清楚。

**Rule**：往 evaluator 里加东西前先问它是协议还是判据。协议进 harness 仓，判据留
项目仓。skill 里出现第二个写死的 molmcp 路径，就是这条被违反了。自己编用例来填空
等于什么都没测量。

**另见**：冠军/挑战者不是「两个激活的 commit」——激活指针每源只有一个 `active`。
成对的是 `previous`（冠军）与 `active`（挑战者），靠 store 的发布不可变且只增，
两棵树才能并存被读。`worse_tokens` / `worse_latency` 这条路走不到：两侧都钉死为 0。
