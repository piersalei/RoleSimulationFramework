# 概念与术语

读其他文档时遇到不熟的词，先查这里。每条只给一句解释和去哪里看细节；规则本身以对应文档和代码为准。

## 1. 输入：角色与剧情

| 术语 | 含义 | 详见 |
|---|---|---|
| 角色文件 | `roles/<id>.json`，一个角色一个文件：档案 + skills + 开场设定 | [04-authoring.md](04-authoring.md) |
| 人物档案（persona） | 角色的稳定画像，schema 2.0；含 `identity`、`traits`、`values`、`core_goals`、`speech_style`、`mechanism_priors`、`module_status` | [01-design.md](01-design.md) |
| 稳定（`stable`） / 动态（`dynamic`） | 稳定部分在一次运行里只读；动态部分是随事件更新的心理状态 | [07-architecture.md](07-architecture.md) §5 |
| 视图 | 同一份档案编译出的四种投影：系统真值、角色自知、他者可见、控制器约束；不同调用只拿到各自该看的 | [07-architecture.md](07-architecture.md) §7 |
| 剧情文件（scene） | `scenes/<id>.json`，一个剧情一个文件（scene schema 3.0）：共享背景、在场的 agent（角色座位 + 可选环境 agent）、动作范围；没有剧本 | [04-authoring.md](04-authoring.md) §4 |
| 座位（seat） | 剧情里给角色的一个位置，如"项目组数据负责人"；一个剧情 2–6 个，角色按顺序入座、按这个顺序轮流发言，彼此只知道别人的座位名 | 同上 |
| 环境 agent（`WorldAgent`） | 剧情里可选的一个非人 agent：持有时间、天气等世界状态，每轮开头按写好的规则改变世界并发旁白（可只给部分角色）；没有心理状态，不替角色说话、不写角色状态 | 同上 |
| 开场设定（setup / briefing） | 跟着角色走的目标与信息：`goal`、想透露的 `disclose`、想隐瞒的 `conceal`、前情 `recap` | [04-authoring.md](04-authoring.md) §3 |
| 前情提要（recap） | 开场设定里的一组条目，作为记忆的第一批内容 | 同上 |
| 世界私有数据（`world_private`） | 剧情里角色不应知道的真值，永不进入任何模型调用 | [07-architecture.md](07-architecture.md) §9 |
| skill | 人格策略库里的一个条目（`library/skills/*/SKILL.md`）：这个人惯用什么应对、怎么说；只影响提示，不写状态 | [10-persona-skills-survey.md](10-persona-skills-survey.md) |

## 2. 交互：事件与动作

| 术语 | 含义 | 详见 |
|---|---|---|
| 环境（`DialogueEnvironment`） | 开场、安排谁发言、把台词原样转给所有在场角色、让环境 agent 按规则行动、收尾；不替角色说话，不编造结果 | [06-runtime-flow.md](06-runtime-flow.md) §5 |
| 事件（Event） | 环境交给角色的一条信息，类型为 `speech` / `action` / `narration` / `outcome`；带谁能看见（`visibility.audience`） | `rolesim/contract.py` |
| `step` / `perceive` | 与环境组约定的两个入口：`step` 是"轮到你，收到这些新事件，提出行动"；`perceive` 是"这件事发生了，更新你的状态" | [06-runtime-flow.md](06-runtime-flow.md) §5 |
| 行动提议（ProposedAction） | 角色提出的行动（目前只有台词）；环境决定它是否成为事件 | `rolesim/contract.py` |
| 交流动作（action） | 角色每次发言前从剧情允许的范围里选一个：`request_evidence` 索要证据、`ask_clarification` 提问澄清、`propose_plan` 提出方案、`set_boundary` 说明边界、`wait` 暂缓、`agree` 接受、`close` 结束 | `rolesim/vocab.py` |
| `intent_note` | 动作标签的存放处；只给环境看，不进入事件，别人只听到台词 | [06-runtime-flow.md](06-runtime-flow.md) §5 |
| 发言次数（turn）/ 轮（round） | 一次发言算一个 turn，角色按座位顺序轮流；所有角色各说一句是一轮（round），环境 agent 在第 2 轮起每轮开头行动；到 `max_turns` 或有人选 `close` 结束 | 同上 |
| 可见性 | 角色只能看到 `audience` 里包含自己的事件；环境过滤一次，Agent 再二次校验 | [07-architecture.md](07-architecture.md) §7 |

## 3. 心理：线索、状态与机制

| 术语 | 含义 | 详见 |
|---|---|---|
| 线索（cue） | 角色从一句话里感受到的情境特征，只有五种：`uncertainty` 不确定、`pressure` 压力、`commitment` 承诺、`repair` 修复、`conflict` 冲突；强度 0–1 | `rolesim/vocab.py` |
| 感知评价（appraisal） | 决策前先让模型站在角色立场报告它感到哪些线索；程序校验后才用 | [06-runtime-flow.md](06-runtime-flow.md) §6 |
| 动态状态六域 | `affect_state` 情绪、`cognition_state` 认知、`goal_state` 目标、`relation_state` 关系、`group_state` 群体、`policy_state` 策略 | [07-architecture.md](07-architecture.md) §5 |
| 模块状态 | 每个模块的启用情况：`active` 有值、`unknown` 未知（值为 null）、`not_modeled` 未建模、`disabled` 关闭、`inactive` 本轮未激活、`not_applicable` 不适用；未知不等于 0 | 同上 |
| 更新算子 | 11 个"可以改哪类状态"的写入通道：`emotion_update`、`belief_revision`、`other_model_update`、`self_state_update`、`relation_update`、`group_state_update`、`goal_update`、`commitment_update`、`strategy_learning`、`memory_update`、`decay_or_recovery`；每个只能写自己白名单里的路径 | `rolesim/state.py` |
| 候选变化 / `StateDelta` | 一条"把某路径从 before 改成 after，依据哪个事件、哪个算子"的提议；先校验，通过才提交 | [07-architecture.md](07-architecture.md) §6 |
| 依赖组（group） | 相互依赖的候选变化放一组，同组要么全部生效要么全部不生效 | 同上 |
| `commit_phase` | 对候选变化逐条检查、按组原子提交、记录采纳与拒绝的函数 | [06-runtime-flow.md](06-runtime-flow.md) §7 |
| 机制卡 | 一条可执行的机制假设，共 5 张：verify 核查、appraise 事件评价、trust 信任、repair 修复、boundary 边界。每张卡写明何时触发（线索、状态值或状态变化）、改哪个状态以及怎么改（`effects`，目前只有 appraise、trust 两张有）、倾向哪些动作；数值是未校准的工程假设 | `rolesim/mechanisms.py`；[06-runtime-flow.md](06-runtime-flow.md) §7 |
| 机制 vs 机制卡 vs 算子 | 机制是心理学上的过程；机制卡是工程里的可执行条目；算子是写状态的通道。卡的 `effects` 指明用哪个规则、经哪个算子写哪条路径，审计据此逐条复算 | [plan/02](../plan/02-multi-agent-and-state-mechanism.md) §3 |
| 状态视图（三档） | 状态进入提示时的形式：按取值范围三等分换成"低 / 中 / 高"，加上自上次发言以来的净变化；决策另带数值和算子名，评价与表达只有文字 | `rolesim/stateview.py` |
| 机制先验（`mechanism_priors`） | 档案里每张卡对这个人的偏好强度，参与卡片排序；必须与启用的卡一一对应 | [05-psychology-interface.md](05-psychology-interface.md) |
| 记忆 | 角色可见事件与自己台词的只增列表；前情固定在前，较早条目按相似度召回，最近 12 条完整保留 | `rolesim/memory.py` |
| 状态快照（state_id） | 每次 `step` / `perceive` 后保存的一份完整状态，用 `prev_state_id` 串成链 | [06-runtime-flow.md](06-runtime-flow.md) §5 |

## 4. 运行与评测

| 术语 | 含义 | 详见 |
|---|---|---|
| 档位（`--mode`） | 三种消融设置：`profile_only` 只给画像、`profile_state` 评价 / 决策 / 表达都加状态视图、`profile_state_mechanisms` 决策再加机制卡（默认）。状态在三档下都计算；`--appraise-state off` 让评价提示不带状态 | [06-runtime-flow.md](06-runtime-flow.md) §6 |
| 后端（`--backend`） | `mock`：离线夹具，只验证流程，输出不是证据；`chat`：兼容 `/chat/completions` 的真实模型 | [03-usage.md](03-usage.md) |
| 运行目录 | 一次运行的全部产物：`manifest.json`、`public.jsonl`、`transcript.md`、每个角色一份 `roles/<id>/…` | [03-usage.md](03-usage.md) |
| 自动审计 | 从落盘文件重新推导的检查（字段有效、证据可见、写权限、稳定一致、可追溯、隐瞒未泄露、跨角色隔离、状态变化归因）；通过只说明工程正确 | [09-evaluation.md](09-evaluation.md) |
| 评审包 / 盲化 | 把运行导出成给人评分的样本，方法标签只存在 `blind_key.json`，评审者看不到 | 同上 |
| 四类版本号 | `construct_definition_version`、`state_schema_version`、`update_rule_version`、`evaluation_protocol_version`，每次运行都记录 | [plan/01-roadmap.md](../plan/01-roadmap.md) §1 |
| 证据层级 | 来源理论、工程假设、作者自述结果、本地工程检查、模型实验、人工验证；文档里写结论时要说明处于哪一层 | `AGENTS.md` |
