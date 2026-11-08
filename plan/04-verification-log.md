# 验证记录

按时间记录**已经做过的检查**：跑了什么、覆盖什么、没有覆盖什么。评测怎么设计、怎么盲评见 [docs/09-evaluation.md](../docs/09-evaluation.md)。这里只记事实，不做"角色像不像人"的结论；所有条目都属于"本地工程检查"层级，不构成心理有效性证据。

## 2026-09-30：首轮工程检查

Python 3.11 本机执行 `python3.11 -m unittest discover -s tests -v`，**16 项测试通过**。

覆盖：私有环境信息与对方私有事件不进入决策/表达模型、画像视图隔离、重复事件去重、人物条件化状态变化、口头承诺不等于兑现、不可见记录不能被动作直接获取、无效动作与未知证据引用拒绝、失败运行留档且不切换 mock、画像不变、确定性离线轨迹可复现、对照模式输入隔离、数值/编码校验、来源索引一致性、兼容接口的两阶段 HTTP 请求。

HTTP 测试使用本机临时回环服务器，返回固定结果。它验证传输格式，不证明已连接任何真实模型服务。沙箱内不允许监听时，需要在有本机监听权限的环境运行该项测试；不能跳过后宣称全通过。

当时已运行四轮脚本环境离线示例。未执行真实模型推理、心理学专家验收、真实服务器部署或心理有效性实验。

## 之后的变化（引自 [01-roadmap.md](01-roadmap.md)，未在本文复测）

| 日期 | 测试数 | 说明 |
|---|---|---|
| 2026-10-19 | 61 | 阶段 A：状态 v1；mock 冒烟运行与升级前逐字一致 |
| 2026-10-19 | 96 | 阶段 A2：画像 schema 2.0，每个反例断言预期错误 |
| 2026-10-20 | 126 → 138 → 142 | 阶段 D：skills、开场设定、记忆、角色/剧情文件、两角色自主对话；四个 mock 运行审计全过 |

2026-10-20：用本地 vLLM + Qwen3-8B（思考模式）跑通两角色 5 轮对话，审计全过；表达层台词复读，待修。后续又有 `runs/qwen3-8b_thinking_dialogue_test1`、`..._v2` 两次运行留档，结论仅限"数据通道可用"。

## 2026-10-21：接口定义 v1 与本地交互循环

`~/venvs/rolesim/bin/python -m unittest discover -s tests`（Python 3.11.15；PATH 上没有 `python3.11`）：**203 项通过**，其中新增 `test_agent_api.py` 17 项、`test_adapter.py` 11 项。本地 HTTP 回环测试在本环境可以监听，正常执行。

另用 mock 后端跑了 `run --interface agent-api`：test3（三人 12 轮）和 test1（两人 8 轮），状态均为 `completed`；检查了 test3 只发给林澄的手机消息没有进入另外两人的记录，`world_private` 的哨兵文本没有出现在运行目录的任何文件里。

没覆盖：真实模型运行、环境组的真实运行器与环境实现（用的是本地替身）、这条路径的自动审计报告。

## 2026-11-07：改为环境组接口 v2（initialize / act）

`python3 -m unittest discover -s tests`（Python 3.13.12；本机没有 `python3.11`，也没有 `~/venvs/rolesim`）：**200 项通过**。`test_agent_api.py`、`test_adapter.py` 按 v2 重写（删去 perceive、认知相关用例，新增初始化路由、未初始化即行动被拒、他人思考不进提示、新到场角色建立关系、互动之间隔离等用例），所以总数从 203 变为 200。

另用 mock 后端跑了 `run --interface agent-api`：test3（三人 12 轮，19 条记录，22 次调用含 3 次 initialize）和 test1（两人 8 轮），状态均为 `completed`；test3 只发给林澄的手机消息只有 `project` 一个接收者；`world_private` 的哨兵文本没有出现在两个运行目录的任何文件里。

没覆盖：真实模型运行、环境组的真实运行器、在场者中途变化的整场运行（只有单元测试）、这条路径的自动审计报告。

同日按环境组答复调整（角色 ID 即角色库 ID、在场含本人、`mechanism.mode`）：`python3 -m unittest discover -s tests` **202 项通过**（Python 3.13.12）。mock 运行 test3、test1、test1 `--mode profile_only` 均 `completed`；`character_id` 为 `role1`…`role3`；`profile_only` 运行的审计记录里档位均为 `profile_only`；test3 只发给林澄的私有消息只出现在 role1 的记录里；`world_private` 哨兵不在任何运行文件中。

## 2026-11-07：关键接口落成代码（rolesim/interfaces.py）

新增 `rolesim/interfaces.py`：plan/10 的 15 个接口（4 个顶层 + 11 个子接口）的 Protocol，以及阶段产物、画像、状态、证据、判断、变化、提交结果等不可变数据类型；`AgentCallError` 移到这里，`adapter.py` 改为引用。新增 `tests/test_interfaces.py`（13 项）：接口与方法齐全、`RoleAgentAdapter` 签名与 `IRoleAgent` 一致、算子名与 `state.OPERATORS` 一致、数据类型的不变量（变化必须有证据、不写 stable、区间越界不截断、S1 假设 1—3 个、S2 只能选保留的候选、模型推断必须带原文），以及用玩具实现走一遍 S1 → 提交 → S2 → 提交 → 表达。

`python3 -m unittest discover -s tests`（Python 3.13.12；本机没有 `python3.11`）：**215 项通过**。

没覆盖：任何按这些接口写的真实实现；接口里的字段是否满足心理学组的要求（F1—F5 仍待确认）。

同日把对外接口改名：`AgentAdapter` → `IRoleAgent`、`EnvironmentAdapter` → `IEnvironment`（签名不变，环境组文档里仍是原名），`tests/test_interfaces.py` 加一项检查本地 `EchoEnvironment` 与 `IEnvironment` 签名一致。`python3 -m unittest discover -s tests`：**216 项通过**（Python 3.13.12）。

## 2026-11-07：IRegistry（心理学登记表）

新增 `rolesim/registry.py`、`data/registry.json` 和命令 `python3 -m rolesim registry`。构建从 `data/sources/psychology_v1/` 的 7 份文件抽取 87 个构念（42 stable / 42 dynamic / 3 memory）、75 个决策过程、11 个更新算子，数量与原文自述一致。另有几条结构检查：每个 dynamic / memory 构念恰有一个主写入算子，没有算子写 stable，衰减算子的写入范围就是 §九 列出的构念，各阶段 skill 引用的决策库章节都已登记。叙述性内容（依赖组、快慢变、衰减、自知程度、高影响字段、12 个决策过程的可选项断句）放在带原文引语的对照表里；引语在原文里找不到时构建失败。`interfaces.py` 的 `Construct` / `DecisionMechanism` / `OperatorRule` 按实际能抽取的内容调整了字段，`IRegistry` 加了 `ids(kind)`。

`tests/test_registry.py` 25 项：文件与原文一致（含命令行生成）、数量、写入归属、阶段权限与依赖组、机制卡、可选项、带出处的抽查、查询接口与只读，以及 6 项"改动原文必须构建失败"的检查（逐项确认失败原因是预期的那一条）。`python3 -m unittest discover -s tests`（Python 3.13.12；本机没有 `python3.11`）：**241 项通过**。

没覆盖：抽取结果是否符合心理学组的本意，要由他们核对 `review` 清单（5 类）和对照表；尚无任何运行时代码使用本接口（`state.OPERATORS` 仍是旧表）。

## 2026-11-07：状态写入（IStateStore / IUpdateOperator / IMagnitude / IValidator）

新增 `rolesim/slots.py`（45 个 dynamic / memory 构念的状态布局：键、字段、取值形式、幅度档字段、只追加与固定事实，字段名按 plan/09 §3.4—3.5）、`magnitude.py`、`validator.py`、`operators.py`、`store.py`。写入白名单、阶段权限、依赖组全部来自登记表；幅度档按状态变化规则 §5.0 / §七（事件等级、效价 ×2、自我评价 ×4、听说 / 推断封顶、四级扩展区间、人格敏感度最多移一档）。为落地调整了接口：`commit(ctx, output)`（输出带 EvidenceSet）、`ChangeProposal` 加 grade / evidence / mechanisms / operator、`AgentState` 加 status 且 memory 改为按构念 ID、`Band` 加 extended、`IStateStore.open`、`IMagnitude.range` 加字段与方向。

`tests/test_store.py` 35 项：布局与登记表一致（含"有幅度档的字段 ⇔ 登记表归入快慢变"）、幅度档数值与原文一致、各档 / 缩放 / 证据封顶 / 人格移档、提交流程（阶段权限、整组拒绝、判断提供证据、合并与冲突、unknown→active、重试幂等、过期快照、快照是副本）、每条硬错与软异常各至少一例。另做了 3 处变异检查（关掉整组拒绝、阶段权限、证据封顶），对应测试都会失败。`python3 -m unittest discover -s tests`（Python 3.13.12；本机没有 `python3.11`）：**276 项通过**。

没覆盖：新状态写入尚未接入运行路径（`Agent.step` 仍用旧的 `state.commit_phase`）；跨阶段累计上限、习惯化、按时间衰减等场景时间单位（F3）；"含对方真实隐状态"本方无法检查；数值都是心理学组的初始值，未标定。

## 2026-11-07：通用 IModel 与三个阶段的输出 schema

新增 `rolesim/schemas.py`（三个阶段的输出 schema：产物 + 判断 + 变化提议，S1 另有转述标注；judgments 的 dm_id、proposals 的 construct_id 按登记表限定在本阶段可用的范围；一个 JSON schema 子集校验器；把输出解析成产物、`Judgment`、`ChangeProposal` 的 `parse_output`）和 `rolesim/model.py`（`ChatModel`：每阶段一次调用，沿用 `ChatBackend` 的传输，本地按 schema 校验，失败抛 `ModelError`，不回退 mock；`MockModel`：离线夹具）。`ChatBackend` 拆出 `chat()`（同时返回原文），旧的 appraise / decide / express 不变。`PromptView` 加 self_id、present、task。

`tests/test_model.py` 15 项：校验器、schema 与登记表一致（各阶段的过程、可写构念；S3 的情绪只能经衰减写）、task 数据、三个阶段的夹具输出都能解析成产物、各种违规整阶段失败（超出 schema、不在本阶段的过程、未登记的可选项、提议依据未判断的过程、选了被筛掉的候选、转述不写命题、证据升级、引用看不到的记录）、夹具三个阶段的提议进真实的 `ConstructStore` 全部接受且无软异常、`ChatModel` 用假传输和本地 HTTP 服务各测一遍。另做了 3 处变异检查（关掉可选项检查、关掉"提议须依据本阶段判断"、允许证据升级），对应测试都会失败。`python3 -m unittest discover -s tests`（Python 3.13.12）：**291 项通过**。

没覆盖：真实模型能否稳定给出合 schema 的输出（没有跑真实模型）；结构化解码未接；新模型调用尚未接入运行路径。

## 2026-11-08：证据门、三个阶段与表达（IEvidenceGate / IStage / IExpresser）

新增 `rolesim/evidence.py`（`EvidenceGate`）、`rolesim/stages.py`（`ModelStage`：自知层视图、状态文字、可见记录窗口、阶段与 phase 对应、S3 需要上次计划、S2 受众必须在场、调用先写轨迹再解析）、`rolesim/expresser.py`（`ModelExpresser`：第 4 次模型调用 "express"，按需可见）；`schemas.py` 加表达 schema 与 `parse_expression`，`model.py` 加表达提示与夹具。`StageContext` 加 `history`。

`tests/test_stages.py` 16 项：可见性与证据渠道（本人行动 / 亲见；他人思考即使被误发也排除；本人思考不是证据；未发送给本角色的排除）、记忆取回、自知层（不能自知或来源没写的模块不进视图）、状态文字、记录窗口、三个阶段经真实 `ConstructStore` 全部提交、阶段前置条件、模型失败与不合规输出、轨迹在解析前写入、受众检查、哨兵检查（他人思考、未发送给本角色的记录、不能自知的特质，在三个阶段的提示里都不出现）、表达的按需可见（不含状态数值、构念 / 过程 ID、被筛掉的候选与约束、假设）、表达违规不发送。测试抓到一个真实错误：记录窗口为 0 时 `history[-0:]` 会取全部，已修。另做了 3 处变异检查（让思考通过证据门、去掉自知过滤、在表达视图里放入状态数值），对应测试都会失败。`python3 -m unittest discover -s tests`（Python 3.13.12）：**307 项通过**。

没覆盖：真实模型；新链路尚未接入运行路径（第 5 步）；不能自知的模块转成条件化语句（第 6 步）。

## 2026-11-08：ITurnRunner、ITrace 与新的分阶段管线

新增 `rolesim/runner.py`（`TurnRunner`：按记录组 S1 → 提交；上次计划的受众对本角色的第一次回应 → S3 → 提交，每个计划一次；S2 → 提交，计划自己的依赖组被拒时带原因重做一次；表达；失败后已提交的阶段保留，重试接着做）、`rolesim/trace.py`（`JsonlTrace` / `MemoryTrace`）、`rolesim/entry.py`（过渡的 `RoleFileRepository`：角色文件中能一一对应的模块 → 稳定画像，bootstrap 的目标与前史 → 入场状态）、`rolesim/staged.py`（`StagedRoleAgent`），`loop.run_staged` 和 CLI `--pipeline stages`。适配器的公共部分抽成 `RoleAgentBase`，旧的 `RoleAgentAdapter` 行为不变。`StageContext` / `PromptView` 加 background，`ITurnRunner` 加 `begin`。

`tests/test_runner.py` 16 项：轨迹文件、过渡入场、一次行动的阶段与状态链、反馈只来自计划受众且每个计划一次、已吸收的记录跳过、S2 被拒重做与两次被拒失败、失败后接着做（表达失败、第二组 S1 失败、反馈判断失败三种）、接口后面的行动、别人思考被丢弃、档位被拒、阶段失败 → `AgentCallError` 后可重试、整场 test3 运行（`world_private` 不出现在任何输出文件里；思考只发给本人；只发给一个角色的环境消息只出现在该角色的模型调用里）、CLI。另做了 3 处变异检查（表达失败后重做计划、反馈判断不止一次、计划被拒不重做），对应测试都会失败。用 mock 跑了 test3：12 轮、31 条记录、68 次模型调用，没有被拒绝的变化，也没有软异常。`python3 -m unittest discover -s tests`（Python 3.13.12）：**323 项通过**。

没覆盖：真实模型；离线工具（审计、评审包、可读版）还不认新格式；新管线没有消融档位；“反馈”按受众规则判断。

## 维护方式

每次做出新的验证（测试数变化、新的模型运行、人工评审）就在这里加一节，写明日期、命令、结果、没覆盖的部分。测试数请以实际运行输出为准，上表来自计划文档的记录，不是本次重新统计。
