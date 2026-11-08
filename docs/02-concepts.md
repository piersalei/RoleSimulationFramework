# 概念与术语

读其他文档时遇到不熟的词，先查这里。每条只给一句解释和去哪里看细节；规则本身以对应文档和代码为准。

## 1. 输入：角色与剧情

| 术语 | 含义 | 详见 |
|---|---|---|
| 角色文件 | `roles/<id>.json`，一个角色一个文件：人物档案（persona 2.0）+ skills + 开场设定 | [04-authoring.md](04-authoring.md) |
| 人物档案（persona 2.0） | 角色文件里手写的稳定画像：`identity`、`traits`、`values`、`core_goals`、`speech_style`、`module_status` 等；其中的 `mechanism_priors` 是旧机制卡的先验，现行管线不读 | [04-authoring.md](04-authoring.md) §1 |
| 档案（profile-1） | `profiles/<id>.json`，按登记表的稳定构念 ID 组织，每项带来源类型、置信度和条件化语句；没有档案时由角色文件转换 | [04-authoring.md](04-authoring.md) §6 |
| 稳定画像（`StableProfile`） | 档案编译后的结果，整场只读；缺失的测量是 unknown，不用中性数字代替 | [13-agent-interface.md](13-agent-interface.md) §4.1 |
| 待审核项 | 编译时列出的需要人工确认的取值：非明写的高影响字段、模型推断、不能自知又没有条件化语句的构念 | [04-authoring.md](04-authoring.md) §6 |
| 条件化语句（phrase） | 心智建模库 5.4 的写法：通常倾向、典型触发、压力下的变化、例外、别人能看到的信号；不用术语和数字 | 同上 |
| 自知层 | 提示里只出现登记表标为能自知 / 部分自知的模块；不能自知的只以条件化语句出现，不带名字和数值 | [06-runtime-flow.md](06-runtime-flow.md) §3 |
| 剧情文件（scene） | `scenes/<id>.json`，一个剧情一个文件（scene schema 3.0）：共享背景、在场的 agent（角色座位 + 可选环境 agent）；没有剧本 | [04-authoring.md](04-authoring.md) §4 |
| 座位（seat） | 剧情里给角色的一个位置，如"项目组数据负责人"；一个剧情 2–6 个，角色按顺序入座、按这个顺序轮流行动，彼此只知道别人的座位名 | 同上 |
| 环境 agent（`WorldAgent`） | 剧情里可选的一个非人 agent：持有时间、天气等世界状态，每轮开头按写好的规则改变世界并发旁白（可只给部分角色）；没有心理状态，不替角色说话、不写角色状态 | 同上 |
| 开场设定（setup / briefing） | 跟着角色走的目标与信息：`goal`、想透露的 `disclose`、想隐瞒的 `conceal`、前情 `recap`。目标写成当前目标，前情写成入场记忆；S1 / S2 / S3 看得到全部，表达只看到目标和可以说出的部分 | [13-agent-interface.md](13-agent-interface.md) §4.3 |
| 世界私有数据（`world_private`） | 剧情里角色不应知道的真值，永不进入任何模型调用；运行目录的 `scene.json` 留一份供评审对照 | [07-architecture.md](07-architecture.md) §4 |
| skill | 人格策略库里的一个条目（`library/skills/*/SKILL.md`）：这个人惯用什么应对、怎么说。被 S1 的线索或开场设定激活（至多 3 个），只影响 S2 和表达的提示，不写状态 | [13-agent-interface.md](13-agent-interface.md) §4.3 |

## 2. 交互：接口与记录

| 术语 | 含义 | 详见 |
|---|---|---|
| 接口 v2 | 环境组定义的角色接口：`initialize`（每个角色一次）和 `act`（轮到它行动）；没有 perceive | [13-agent-interface.md](13-agent-interface.md) §2 |
| `IRoleAgent` / `IEnvironment` | 接口 v2 的两个 Protocol（环境组叫 `AgentAdapter` / `EnvironmentAdapter`）；我们的实现是 `StagedRoleAgent` | 同上 |
| 记录（`InteractionRecord`） | 一次行动拆出的一条：思考、发言或动作，带行动者和明确接收者；编号 `action_id#order_index` 是状态变化的证据引用 | [13-agent-interface.md](13-agent-interface.md) §2.1 |
| 记录组 | 同一 `action_id` 的记录（一次复合行动）；每组做一次事件建模（S1） | [06-runtime-flow.md](06-runtime-flow.md) §2 |
| 观察记录（observations） | 运行器替每个角色保存的、它看得到的全部记录；每次 `act` 整份交给角色，角色只处理没吸收过的 | 同上 |
| 本地参考运行器 | `loop.LocalRunner` + 环境桩 `EchoEnvironment`：环境组运行器的替身，离线联调和测试用 | [13-agent-interface.md](13-agent-interface.md) §5 |
| 导演指令 | 开场、报时、世界事件；由环境原样作为环境的 action 发出，按接收者分发 | [06-runtime-flow.md](06-runtime-flow.md) §1 |
| 发言次数（turn）/ 轮（round） | 一次行动算一个 turn，角色按座位顺序轮流；所有角色各行动一次是一轮（round），环境 agent 在第 2 轮起每轮开头行动；到 `max_turns` 结束 | 同上 |
| 可见性 | 角色只能看到自己是行动者或接收者的记录：运行器按接收者分发，接口再校验，证据门另外排除别人的思考 | [13-agent-interface.md](13-agent-interface.md) §2.2 |
| 交流动作词表 | `vocab.py` 的七个动作（索要证据、提问澄清、提出方案、说明边界、暂缓、接受、结束）：用于 skill 的可行性判断和剧情 `allowed_actions` 的校验；计划本身是自由文本 | `rolesim/vocab.py` |

## 3. 心理：登记表、阶段与状态

| 术语 | 含义 | 详见 |
|---|---|---|
| 登记表（registry） | 从心理学组 v1 文件生成的 `data/registry.json`：87 个构念、75 个决策过程、11 个算子规则，每项带原文出处；代码只经它读心理学定义 | [08-modules.md](08-modules.md) §3 |
| 构念 | `s.*` 稳定、`d.*` 动态、`m.*` 记忆；每个有取值形式、主写入算子、阶段权限、自知性、依赖组 | `data/registry.json` |
| 决策过程（`dm.*`） | 心理学组列出的过程（如初级 / 次级评价、信念更新、冲突模式）；现在只在阶段调用里由模型判断（`profile_state_mechanisms` 模式），没有按规则实现的过程 | [07-architecture.md](07-architecture.md) §5 |
| 阶段 S1 / S2 / S3 | 事件建模 / 行动计划 / 长期演化，各是一次结构化模型调用；只提议变化，不写状态 | [06-runtime-flow.md](06-runtime-flow.md) §2 |
| 表达 | 按计划生成思考 / 发言 / 动作的那次调用，只拿到需要的信息 | [06-runtime-flow.md](06-runtime-flow.md) §3 |
| 证据门 | 每个阶段开始时决定什么能当证据：本人的行动、明确发给自己的记录；排除别人的思考、本人的思考、没发给自己的记录，并记原因；另取回至多 4 条相关记忆 | [06-runtime-flow.md](06-runtime-flow.md) §2 |
| 线索（cue） | S1 给出的五种情境特征强度：`uncertainty` 不确定、`pressure` 压力、`commitment` 承诺、`repair` 修复、`conflict` 冲突，0–1；只用来激活人格 skill | `rolesim/vocab.py` |
| 变化提议（`ChangeProposal`） | 阶段对一个构念实例的变化提议：构念、实例键、字段、目标值、事件等级、证据、依据的决策过程 | [06-runtime-flow.md](06-runtime-flow.md) §4 |
| 更新算子 | 11 个写入通道：`emotion_update`、`belief_revision`、`other_model_update`、`self_state_update`、`relation_update`、`group_state_update`、`goal_update`、`commitment_update`、`strategy_learning`、`memory_update`、`decay_or_recovery`；白名单、阶段与依赖组都来自登记表 | `rolesim/operators.py` |
| 幅度档（band） | 一次变化允许的范围：按事件等级分档，效价 ×2、自我评价 ×4，只有听说 / 推断的证据时封顶，人格敏感度最多移一档；越界拒绝，不截断 | [06-runtime-flow.md](06-runtime-flow.md) §4 |
| 依赖组 | 相互依赖的变化放一组，组内有硬错整组不写 | 同上 |
| 硬错 / 软异常 | 硬错拒绝写入（如 `over_band`、`stage_not_allowed`、`writes_stable`）；软异常照写并标记 | 同上 |
| 模块状态 | 每个构念的启用情况：`active` 有值、`unknown` 未知（值为 null）、`inactive` 当前没在起作用、`disabled` 关闭、`not_modeled` 未建模、`not_applicable` 不适用；未知不等于 0 | [13-agent-interface.md](13-agent-interface.md) §4.1 |
| 记忆 | `m.entry` 只增；入场时来自开场设定的前情、生命经历和 bootstrap 的此前经历，之后由 S2 / S3 按证据写入；阶段开始时按字面相似度、重要性和可及性取回 | `rolesim/memory.py` |
| 状态 ID | 入场为 `{角色ID}@entry`，之后每次提交形如 `{角色ID}@{事件}#{序号}.{类型}`，用 `prev_state_id` 串成链 | [13-agent-interface.md](13-agent-interface.md) §4.2 |

## 4. 运行与评测

| 术语 | 含义 | 详见 |
|---|---|---|
| 消融模式（`--mode`） | `profile_only`：提示里没有动态状态，也没有决策过程判断；`profile_state`：有状态，没有判断；`profile_state_mechanisms`：全部（默认）。状态在三种模式下都计算和记录 | [13-agent-interface.md](13-agent-interface.md) §3 |
| 后端（`--backend`） | `mock`：离线夹具，只验证流程，输出不是证据；`chat`：兼容 `/chat/completions` 的真实模型 | [03-usage.md](03-usage.md) |
| 运行目录 | 一次运行的全部产物：`manifest.json`、`records.jsonl`、`calls.jsonl`、`scene.json`、`roles/<id>/…`、`trace/<互动>/<id>/…`、`transcript.md` | [06-runtime-flow.md](06-runtime-flow.md) §5 |
| 自动审计 | 从落盘文件重新推导的检查：轨迹可追溯、写入权限、证据可见、提示隔离、隐瞒内容不进表达、表达里没有系统 ID、不能自知的数值不进提示、思考只给本人、稳定画像不变、字段有效；通过只说明工程正确 | [07-architecture.md](07-architecture.md) §4 |
| 评审包 / 盲化 | 把运行导出成给人评分的样本，方法标签只存在 `blind_key.json`，评审者看不到 | [09-evaluation.md](09-evaluation.md) |
| 四类版本号 | `construct_definition_version`、`update_rule_version`（两份心理学组原文的哈希）、`state_schema_version`、`evaluation_protocol_version`（尚未登记）；每个状态都带着，见 `final_state.json` | [plan/01-roadmap.md](../plan/01-roadmap.md) §1 |
| 证据层级 | 来源理论、工程假设、作者自述结果、本地工程检查、模型实验、人工验证；文档里写结论时要说明处于哪一层 | `AGENTS.md` |
