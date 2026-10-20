# 模块划分与实现计划

本文档回答两件事：整个系统分成哪几个模块、各自边界在哪；在规则数值到位之前我们能先做什么。

状态：2026-10-15 起草，2026-10-19 按心理学组回复修订，2026-10-20 补充人格线与社会情境线（阶段 D）。`data/sources/contracts/` 下的两份合同已与心理学组、环境组对接完成，本轮新增内容均不改动合同。

**口径说明。**
- 心理学组回复里的"拟改动方向"和问答答复，本文档一律采用为 **v1 目标口径**。
- 回复给出的是结构、分类和流程；规则卡的触发条件、幅度区间、衰减时间尺度、人格调节参数尚未交付，仍是空缺。
- 阶段 A 的结构升级已于 2026-10-19 完成（见第 5 节）：状态层是 `state-v1`，画像层是 persona schema 2.0。规则数值、评测协议和多方案比较仍空缺，各节"现状"逐项标明。
- 工程侧自行采用的默认假设只保留给场景/环境组的未决项（第 6 节）。

## 1. 心理学组回复的变更摘要

下表只列对工程有影响的变化。原文见心理学组回复（"接口合同 v0 修改意见"与待确认问题答复）。

| 项目 | v0（工程侧原口径） | v1 目标（回复结论） | 影响模块 |
|---|---|---|---|
| 主人格方案 | 选 P1，P2/P3 仅参考 | P1/P2/P3 互斥，**同一角色设定、三种主编码、独立运行、统一评测**后选一套；三者共用成人依恋层，输出同一个行为约束接口 | M2、M8 |
| stable 结构 | `traits`/`values`/`core_goals` 为粗容器 | 改为可追溯结构化对象：`identity`、`traits`（personality / attachment / motivation_baseline / affective_capacities / social_cognition_71d / cognitive_style / developmental_position / personality_function）、`values`、`core_goals`、`speech_style` | M2 |
| `values` | 自由文本或词表待定 | 固定构念词表 + 说明文本：施瓦兹 19 项，非负权重和为 1，另存总体价值投入度 | M2 |
| `core_goals` | 数量待定 | 1–3 个主目标，各带 1–2 个支持子目标，保存来源、优先级、稳定度、上下位链接 | M2 |
| stable 变更 | 待定 | 普通 StateDelta 永不写 stable；长期变化先记修订候选，经心理学组审核后版本迁移出新 `stable_snapshot`，保留旧快照 | M2、M7 |
| dynamic 结构 | 五块：emotion / beliefs / goals_now / relations / other_models | 六个状态域：`affect_state`、`cognition_state`（含原 other_models）、`goal_state`、`relation_state`、`group_state`、`policy_state` | M3 |
| 情绪 | label + valence/arousal/intensity | label、valence、arousal、intensity、duration_hint、confidence；dominance 默认 `not_modeled` | M3 |
| 关系 | trust / closeness | trust、closeness、dependence、commitment（0–1）+ `harm_history`（事件列表、严重度、是否承认、是否修复、最后激活时间）；`power_asymmetry` 为条件字段，存客观方向与主观感知，无权力差时 `not_applicable`；repair_state 等 v1.1 再加 | M3 |
| 心理理论 | 先做二阶 | 不固定阶数：记录 `tom_level`、适用对象、证据、置信度、允许任务；运行时受注意、记忆、情绪降级 | M3 |
| 信念 | 置信度刻度待定 | `confidence` 0–1 为主观确信；未形成判断用 `unknown` + null，不默认 0.5；每条带 belief_id、对象、情境、来源、冲突组；冲突可并存、不强制平均 | M3 |
| 更新机制 | 7 个（按五块设计的写回分类） | **11 个更新算子**，按"可写入的状态对象"划分：`emotion_update`、`belief_revision`、`other_model_update`、`self_state_update`、`relation_update`、`group_state_update`、`goal_update`、`commitment_update`、`strategy_learning`、`memory_update`、`decay_or_recovery`；决策机制库的 11 类过程通过 `decision_mechanisms` 映射，不另建平行分类 | M3、M4 |
| 提交方式 | 整条 delta 一错全拒 | 同一 `event_id` + `event_phase` 用同一份已提交快照；候选先进 `mechanism_log`；按依赖组原子提交；组内硬错或同路径无法裁决则整组拒绝，其余独立组可提交；幂等重试；禁止同批读取未提交候选 | M3、M6、M7 |
| 越界/软约束 | 拒绝；软约束标记 | 三级：硬错拒绝依赖组（范围越界、不可见证据、未登记算子、改 stable、无写权限、无法解析）；软错提交并标 `soft_violation`；重大事件可豁免规则卡明确允许的软边界，须记 `exception_approved`，**不能豁免硬错** | M3 |
| 单步变化量 | 工程占位值 | 混合方案：程序规定范围、事件等级和硬上限，模型在区间内选值并给理由；人格系数须先声明影响的是敏感度、峰值、持续时间还是恢复速度，不得用单一系数统领 | M3、M4 |
| 模块状态 | 无 | 每个子模块和机制配置记 status：`active` / `unknown`（null + 待补信息）/ `disabled` / `not_modeled` / `inactive` / `not_applicable`；真实的 0、中性值、空列表是有效观测，不能代替这些状态；默认先验须标 `default` 并留来源 | M2、M3 |
| 材料与版本 | 一份合同 | 三份材料：心理构念登记表（语义主来源，`construct_id`）、角色状态说明（生成运行时 JSON Schema）、心理变化规则表；合同只列启用的 `construct_id` 与版本。四类版本号：`construct_definition_version`、`state_schema_version`、`update_rule_version`、`evaluation_protocol_version` | 全部 |
| 评测 | 量表待定 | 四层数据（字段/变化/轨迹/对照）、9 个固定维度、0–3 级锚点；"自动审计 + 人工评分 + 复标"；样本包含事件前后状态、候选与最终 delta、拒绝原因、对方真实状态（仅供对照）；协议五阶段；真人基线非第一版前置 | M8 |
| 测试样例 | 待定 | 心理学组交付 2–3 个角色完整 stable、1 个角色的合理行为轨迹（含合理分歧与反例），另附场景事件表、角色可见性表（observed/heard/inferred/not_visible）、状态变化验收表 | M2、M8 |

回复里**没有**改变的约束：`stable` 轨迹内只读；`evidence` 只能引用角色可见的 event_id；越界不截断；世界私有数据和他人隐藏心理永不进入角色调用。

## 2. 人格方案：三案比较，P1 暂为实现基线

心智建模库 2.1 给出三套**互斥**的主人格编码方案：

| 方案 | 内容 | 状态 |
|---|---|---|
| P1 | 大五 / 五因素模型，五领域 + NEO 传统 30 分面 | 已实现，当前基线 |
| P2 | 16PF 十六个初级因素 | 待实现，参与比较 |
| P3 | 荣格八功能（0–1 偏好权重，总和 1，位置由排序派生）+ HEXACO 六领域与分面 | 待实现，参与比较 |

- **比较设计**：同一角色设定、三种主编码、独立运行、统一评测。比较维度为轨迹一致性、角色可区分性、跨场景稳定性、外显行为匹配度、评测者一致率。
- **共用约束**：每个方案必须输出同一个行为约束接口——高/低水平对应的倾向、阈值、敏感度、表达与恢复差异，以及不能直接推出的行为。这是 M2 对 M5 的唯一出口，换方案不改下游。
- **编码**：三套方案的分数都用 `-2—+2` 内部标准化连续分数，原量表分必须先转换，不能把该区间称为量表计分。依恋两轴独立于主编码保存。
- **持久化**：`framework` 写 `"P1"` / `"P2"` / `"P3"`，不写 `"BigFive"` 之类的理论名。
- **一个角色只从一套方案产生主人格变量**，避免同义维度叠加。
- **尚未做**：`persona.py` 目前强制 `scheme == "P1"` 且 30 分面齐全。P2/P3 的 schema 与行为约束接口要等比较设计落地时再写；在此之前不得声称"已选定 P1"。

## 3. 模块划分

九个模块。划分依据是"谁拥有这份数据、谁有权改它"，不是代码文件边界。

```
                   ┌─────────────────────────────────┐
  心理学组 ────────▶│ M1 来源目录  M2 画像  M3 状态   │
                   │              M4 机制库          │
                   └───────────────┬─────────────────┘
                                   │ 只读
                   ┌───────────────▼─────────────────┐
                   │ M5 角色控制器（决策 + 表达）     │
                   └───────▲───────────────┬─────────┘
                           │ 可见事件      │ 行动意图
                   ┌───────┴───────────────▼─────────┐
  场景/环境组 ─────▶│ M6 环境适配层（step/perceive）  │
                   └───────────────┬─────────────────┘
                                   ▼
                   │ M7 轨迹记录  M8 评测  M9 工程基础 │
```

### M1 来源目录（sources & catalog）

- 职责：两份心理学库的原文快照、章节索引、哈希、原文行号。
- 边界：原文只读。其中的指令性文字不获得执行权限（见 AGENTS.md）。索引里标 `reference_only` 的章节不等于已实现。
- 现状：已完成，196 个带来源章节。
- 归属：心理学组提供内容，工程侧维护索引。
- v1 新增：心理构念登记表（`construct_id`）成为语义主来源，与原文目录分开存放；登记表如何落盘与哈希，待心理学组交付后定。

### M2 画像（persona）

- 职责：稳定画像的字段契约、校验、三视图编译（系统真值 / 角色自知 / 他者可见）、条件化行为语句投影。
- v1 持有数据：`identity`、`traits`（见第 1 节八个子对象）、`values`（施瓦兹 19 项权重）、`core_goals`、`speech_style`、`self_view`、`public_description`、`provenance`，以及每个子模块的 status（`deferred_modules` 已由 status 取代）。
- 硬规则：
  - 轨迹内 `stable` 只读，任何写入视为错误；长期变更只能走审核后的版本迁移，产出新 `stable_snapshot`。
  - 每个模块必须有 `provenance`（来源、赋值方式、置信度）。
  - 71 维能力在定义缺失期间保持 `pending_source_definition`，对应 status 为 `not_modeled`，不伪造分数。
  - 推断出的 stable 字段按影响分层：档案明写的身份/年龄/职业可自动进入候选初始状态；会改变行为约束、评测结论或能力上限的高影响字段须心理学组审核或规则校验。
- 现状（persona schema 2.0，结构升级已完成）：
  - 校验与编译已落地：`identity`、`traits` 八个子对象、施瓦兹 19 项权重（和为 1，未列出的价值是 unknown）、`core_goals`（1–3 个，带优先级、稳定度、来源、至多 2 个子目标）、`speech_style`（基线 + 情境规则）。
  - 20 个稳定模块各有 status 与理由，`module_status` 属于 stable，所以 `stable_snapshot_id` 同时覆盖值和状态。active 必须有值和来源，unknown 必须为 null，没有适配器的模块（71 维、发展位置、荣格功能、道德推理、文化取向）不能设为 active。
  - 仍只执行 P1；P2/P3 的 schema 与行为约束接口未做。30 分面中只有 4 个参与条件化语句投影，其余仅存储；分面级还不能表达 unknown。
  - 情境表达规则会进入表达提示；`core_goals` 与价值只进入决策控制器的约束，不进入角色自知和表达提示。
  - 旧的 1.0 档案会被拒绝，没有做自动转换；`deferred_modules` 字段取消，其内容由 `module_status` 的 not_modeled 与理由承担。
  - 未做：stable 变更的修订候选与版本迁移结构。
- 归属：心理学组定义字段含义，工程侧实现校验与投影。

### M3 动态状态（state）

- 职责：逐轮心理状态、带证据的更新记录（StateDelta）、可见记忆。
- v1 持有数据：六个状态域（affect / cognition / goal / relation / group / policy）和 memory（event_id 列表，完整条目另存）。`dynamic` 只存会影响后续行为、需要回放的变量；决策过程、更新规则、机制日志不在 `dynamic` 里。
- 硬规则（硬错则整个依赖组拒绝）：
  1. 每条变化的 `evidence` 只能引用本角色可见的 event_id 或自己的 proposal_id，不可为空。
  2. path 以 `stable.` 开头一律拒绝。
  3. 每个更新算子只能改自己白名单内的路径，未登记算子拒绝。
  4. 数值越界直接拒绝，不截断。
  5. 无法解析、无写权限、同路径无法裁决，同样按硬错处理。
- 软约束：典型区间外、衰减过慢、调节过强、跨字段变化不典型，提交并标 `soft_violation`；仅规则卡明确允许的软边界可被重大事件豁免，须记 `exception_approved`。
- 同路径多条变化：先去重同一证据的重复贡献，再按规则卡指定的聚合方式、写权限和固定优先级裁决，不默认求和、平均或后写覆盖。动作竞争权重不能替代字段写权限。
- 没有规则的字段不得默认衰减；时间推进须登记为可审计的时间事件。
- 现状（`state-v1`，`rolesim/state.py` + `rolesim/modules.py`）：
  - 六个状态域和模块状态表已落地。unknown 必须为 null，active 不得为 null，not_modeled / disabled 不得持值，树中出现未登记模块的值一律拒绝。状态转换写入 `status_log`。
  - 11 个算子有写权限表；`memory_update` 不写 dynamic 路径。
  - `commit_phase` 按依赖组原子提交，同路径冲突整组拒绝，完全相同的重复贡献去重，`commit_key` 保证重试幂等。
  - 三级错误：硬错拒绝所在组，软错标记 `soft_violation`，豁免需 `exception` 记录且规则卡允许；目前唯一的软规则 `trust_step` 不允许豁免。
  - 运行中只有两条更新规则在写：`affect_state.emotion.intensity`（emotion_update / decay_or_recovery）和 `relation_state.partner.trust`（relation_update）。输入为 null 时跳过该分支，不用中性值代替。
  - 初始只有 `emotion.label`、`emotion.intensity`、`partner.trust` 三个原型默认值（origin 为 `default`，经审计转换写入）；其余关系维度、tom、情绪连续坐标为 unknown。
  - `power_asymmetry` 默认 `not_applicable`，没有值槽；客观方向属于系统侧数据，不进入 AgentState，角色主观感知待场景需要时再开。
  - 11 个算子的机制卡为空壳，只有写权限，没有幅度、时间尺度和聚合规则。
- 归属：心理学组定义字段与更新规则，工程侧实现与校验。

### M4 机制库（mechanisms）

- 职责：可执行机制卡的注册表、触发筛选、竞争排序。
- 边界：**与 M1 的机制目录分开**。目录保存原文；注册表只放已完成"输入—触发—候选行为—更新规则"定义的机制。
- v1 分层：
  - **决策机制**（注意、记忆、归因、评价、推断、选择、表达、学习）通过 `decision_mechanisms` 映射到更新算子。
  - **更新算子**是 11 个写回对象，每个须有一张机制卡，至少含：触发事件、输入状态、可修改路径、变化方向、幅度等级、时间尺度、优先级、正例、反例、证据格式、软约束、评测指标。
- 动作选择顺序：先按规则卡声明的安全、能力、制度、承诺门槛筛选，再比较机制支持、目标价值、关系后果和执行成本；选中动作不等于动作成功。
- 现状：五张调度卡（verify / appraise / trust / repair / boundary），公式 `score = cue × (0.5 + 0.3×prior + 0.2×moderator)`，阈值 0.3。这是未校准的工程假设，用于检验调度接口，不是实证规律。`CARD_MECHANISMS` 已改指 11 个算子，属待审工程假设。
- 后续版本才纳入的算子（先作为决策机制或日志标签；结果若要跨事件影响行为则须升为独立字段或算子）：emotion_regulation、learning_adaptation、memory_consolidation、self_evaluation_update、conflict_arbitration、reputation_update、norm_activation、repair_update。
- 归属：心理学组定义卡片内容，工程侧实现调度。

### M5 角色控制器（controller）

- 职责：把画像、状态、可见历史、机制卡组装成提示，调模型产出结构化行动意图，再产出台词。
- 两阶段：决策（输出 action / evidence_ids / brief_reason）→ 表达（输出 speech）。表达阶段只拿到角色自知、风格约束、已选动作、可见历史。
- 硬规则：世界私有数据和他人的隐藏心理字段永不进入任何一次调用；决策失败不回退成内心独白当台词。
- v1 补充：模型在规则卡给出的区间内选具体变化量并提交理由；程序做范围、权限、证据、软约束校验。
- 现状：mock 后端 + 兼容 `/chat/completions` 的真实后端，均已接通，真实模型未跑。
- 归属：工程侧。

### M6 环境适配层（environment adapter）

- 职责：实现发给场景组的那份接口合同——`step` / `perceive`、Event / ProposedAction / AgentState / StateDelta 的序列化、可见性过滤的二次校验。
- 边界：环境裁决"实际发生了什么、谁看见了"；我们只提交尝试。角色提出计划不等于计划被接受。
- v1 补充：
  - 行动反馈、新证据、权力变化或时间推进到达时，开启新的 `event_phase`，重新核定可见性并读取最新提交状态。
  - 重试以角色、事件、阶段和提交编号保证幂等。
  - `StateDelta` 的 `mechanism` 取值从 7 个改为 11 个算子，属于对环境组可见的变化，需要告知（见第 6 节）。
- 现状：`rolesim/contract.py` 定义合同附录里的数据类；`rolesim/agent.py` 的 `Agent.step` / `Agent.perceive` 按 v0 合同实现。`runtime.DialogueEnvironment` 是两个角色的对话环境：只给共享背景，开场后按座位轮流请角色发言，把台词原样转给对方，不编造结果。没有环境标注线索时，由角色自己的模型从本角色立场评价线索，程序按公式更新状态。两处工程侧扩展待环境组确认：`Event.signals` 可携带环境标注的线索（有则优先）；动作标签放在 `ProposedAction.intent_note`。
- 归属：场景组定义接口，工程侧实现适配器。

### M7 轨迹记录与回放（trace）

- 职责：manifest、输入快照、views、audit、public、state 落盘；按 state_id 回放；配置哈希。
- 边界：`inputs.json` 含世界私有数据，只能用于系统侧复现，不能进入任何评审包；`audit.jsonl` 是内部审计，不混入外显行为评分。
- v1 补充：
  - 新增 `mechanism_log`：实际调用的机制、中间判断、候选与最终变化、采纳/拒绝原因、优先级、输入/输出 `state_id`、异常标记。
  - 每条轨迹及回放日志记录四类版本号、启用配置和 `stable_snapshot` 标识。
  - 所有状态转换（含 status 转换）留审计记录。
- 现状：已落盘 `states.jsonl`（state_id 串链）、`environment_events.json`（含私有事件，仅系统侧）和 `mechanism_log.jsonl`（候选、采纳、拒绝原因、软标记、输入/输出 state_id、四类版本号）。manifest 记录四类版本号、`stable_snapshot_id`、`code_version`、`model_served`、`rejected_groups` 和 `status_transitions`；每个 AgentState 快照带 `versions`、`module_status`、`stable_snapshot_id`。仍缺按 state_id 的回放工具。
- 归属：工程侧。

### M8 评测（evaluation）

- 职责：对照条件（消融）、样本包导出、自动审计报告、标注工具。
- 两类质量分开记：工程正确性（可见性、证据、字段稳定、可复现）与模拟有效性。前者通过不说明后者。
- v1 口径：
  - **四层数据**：字段层、变化层、轨迹层、对照层。
  - **九个固定维度**：字段有效性、证据与可见性、机制与写回相容性、稳定一致性、动态合理性、角色可区分性、外显行为与隐状态匹配、错误推断的合理性、轨迹可追溯性。前两项由自动审计判定，其余人工评分。
  - **0–3 级锚点**；存在硬错则相关维度记 0 并选硬错类型；被拒候选仍计入错误率。
  - **样本包**：事件前状态、触发事件及角色可见信息、模型输出与 thought、事件后状态、全部候选与最终 StateDelta、拒绝原因、相关历史、对方真实状态。后者只供对照；判断推断是否合理仍限于该角色可见证据。
  - **协议五阶段**：样本包 → 自动审计 → 人工评分 → 复标校准 → 报告回写。评测者不得临时改评分口径。
  - 真人基线不是第一版前置条件。
- 现状：三档消融入口（`profile_only` / `profile_state` / `profile_state_mechanisms`）、自动审计报告（`rolesim/audit.py`）和盲化样本包导出（`rolesim/package.py`，`python3.11 -m rolesim package`）已有。自动审计覆盖字段有效性、证据与可见性、写权限、稳定一致性、轨迹可追溯性五项；其余维度列为"需人工评分"。样本包含评测版（前后状态、可见触发事件、模型输出、全部候选及去向）、角色可见轨迹、对方真实状态（仅供对照）和标注模板。缺标注工具与复标统计；评测协议本身尚未冻结。
- 归属：心理学组定维度与量表，工程侧提供数据与工具。

### M9 工程基础（infra）

- 职责：CLI、测试、CI、同步脚本、依赖与环境约束（标准库、Python 3.11+）。
- v1 补充：由登记表与状态说明生成运行时 JSON Schema，并在 CI 中检查三份材料的引用、范围与版本一致性。发现不一致时先冻结运行时发布。
- 现状：40 项测试，CI 工作流已配置。本地跑测试时需要回环监听权限。`ChatBackend` 对 localhost / 127.0.0.1 / ::1 不走 `HTTP(S)_PROXY`。
- 归属：工程侧。

### 协作流程

心理学组回复确定的流程：**登记 → 评审 → 投影 → 校验 → 发布 → 回放**。

1. 心理学组登记或修订构念。
2. 双方评审边界与可实现性。
3. 工程侧生成状态说明与规则表的机器投影。
4. 自动检查引用、范围、版本。
5. 用小剧本回放。
6. 发布带四类版本号的合同快照。

三份材料只允许一个语义主来源；合同只做追溯入口，不复制正文。字段删除或改名属于不兼容变化，需升版本。

### 细分实现单元（U1–U13）

九个模块按数据归属划分。落到代码和合同时再细分为 13 个实现单元，编号用 U 以免与 M 混淆。"现有代码"列写的是当前实现；与 v1 目标的差距见第 5 节。

| # | 单元 | 所属模块 | 职责 | 主要输入/输出 | 对应合同 | 现有代码 |
|---|---|---|---|---|---|---|
| U1 | 来源目录 | M1 | 两份库的快照、章节索引、哈希 | 原文 → `catalog.json` | — | `catalog.py`，已完成 |
| U2 | 人物档案 | M2 | stable schema 与校验：主人格（P1/P2/P3）、依恋、动机、施瓦兹价值、情绪/认知基线、71 维能力（待定义）、core_goals、来源与置信度、模块 status | 档案 JSON → 已校验 stable | 心理学组 2.1、4 | `persona.py`，schema 2.0（仅 P1） |
| U3 | 视图编译 | M2 | 系统真值 / 角色自知 / 他者可见 / 控制器约束四种投影；条件化行为语句；三方案统一的行为约束接口 | stable → views | 心理学组 2.1、环境组 1 | `compile_views`，部分 |
| U4 | 状态存储 | M3 | AgentState（stable/dynamic/memory）、StateDelta、版本号、落盘与按 state_id 回放 | — | 环境组 2.4–2.5、心理学组 6 | `state.py` + `modules.py` + `initial_state`；六域、模块状态、state_id 链已有，回放工具未做 |
| U5 | 感知与记忆 | M3、M6 | 可见性二次校验、去重、记忆条目带来源与类型（观察/他人说法/反馈/自己行动） | Event → memory | 环境组 3.2、3.3 | `observe`，部分 |
| U6 | 机制注册与调度 | M4 | 机制卡（触发、阈值、可行性门、候选动作、例外）、多卡竞争排序、`decision_mechanisms` 映射 | 事件 + 状态 + stable → 激活卡 | 心理学组 3；决策库 0.2、0.6 | `mechanisms.py`，五张调度卡 |
| U7 | 状态更新器 | M3 | 按 11 个算子生成候选 StateDelta；依赖组原子提交；硬错拒绝、软错标记、豁免记录 | 激活卡 + 事件 → StateDelta | 心理学组 3.1–3.3、7 | `commit_phase` 已有；只有紧张、信任两条规则 |
| U8 | 决策控制器 | M5 | 生成并筛选选项，选出 ProposedAction，产出私有 thought，在规则区间内选变化量 | 视图 + 状态 + 卡 → actions | 环境组 2.3、3.1 | `make_payload` + `decide`，部分 |
| U9 | 表达 | M5 | 按已选动作和风格生成台词，不泄露机制和分数 | action + 自知 + 风格 → speech | 环境组 2.3 | `express`，已有 |
| U10 | 环境适配层 | M6 | 实现 `step` / `perceive`，数据类；`event_phase` 与幂等；脚本环境作测试替身；多角色调度器 | 环境组请求 ⇄ Agent | 环境组 3、4 | `agent.py` + `contract.py` + `DialogueEnvironment`（两个座位）；三方以上未做 |
| U11 | 模型后端 | M5 | 兼容接口调用、严格 JSON、失败不回退 mock；回环地址不走代理 | — | — | `providers.py`，已有 |
| U12 | 运行记录 | M7 | manifest、inputs、audit、public、state 序列、`mechanism_log`、prompt 与原始输出、四类版本号、Git SHA | — | 环境组 5 | `run_dialogue`，每个角色一份记录，含思考模式推理；缺回放工具 |
| U13 | 评测 | M8 | 自动审计（越界、无证据、stable 被改、引用不可见事件、规则覆盖）、样本包导出、标注工具 | 运行目录 → 报告 | 心理学组 5、第三部分 | `audit.py`、`package.py` 已有；标注工具、复标统计未做 |

单元间依赖：U2→U3→U8/U9；U5→U4；U6→U7→U4；U10 调用 U5、U6、U7、U8、U9；U12、U13 读取全部产物。M9 工程基础不单列实现单元。

## 4. 模块间的三条硬边界

1. **心理学组改 M2/M3/M4，不碰 M6。** `stable` / `dynamic` 的内部字段对环境组是不透明 JSON，改字段不需要环境组同步发版。例外：`StateDelta.mechanism` 的取值集合出现在跨组的数据里，变更须通知环境组。
2. **环境组改 M6，不碰 M2/M3。** 环境只存状态快照和 state_id，不读也不改内部字段。
3. **M5 是唯一接触模型的地方。** M2/M3/M4 必须能在没有模型的情况下完整跑校验，这样工程正确性可以独立于模型质量验证。

## 5. 实现计划

### 已完成（按 v0 合同，2026-10-18）

阶段 1、2 按 `state-v0` / `psy-v0` 完成，是 v1 迁移的起点。其中状态部分已被阶段 A 取代，环境接口部分（`step` / `perceive`、可见性二次校验、`outcome` 回写）沿用。

- 五块 dynamic 结构、范围、`source` 必填、`apply_delta` 四条硬约束和反例测试（`state.py`、`tests/test_state.py`）。
- 机制卡到 7 个旧机制名的映射 `CARD_MECHANISMS`。
- `step` / `perceive` 数据类与 Agent 实现、可见性二次校验、`type=outcome` 事件回写、`prev_state_id` 校验（`contract.py`、`agent.py`、`runtime.DialogueEnvironment`）。

### 阶段 A：结构升级到 v1（2026-10-19 完成，第 7 项和 Git SHA 除外）

目标是把结构和流程换到 v1 口径，规则数值留成可替换的参数表，等心理学组交付后填入。

1. **模块状态枚举。** 已完成：`rolesim/modules.py`，六种状态、转换审计、默认先验标 `origin: default`。
2. **dynamic 升为六个状态域，`state-v1`。** 已完成：情绪六字段、关系四项 + `harm_history`、`power_asymmetry` 条件字段、信念与他人模型结构、`tom` 槽位；其余子模块 not_modeled。旧版轨迹不做静默转换。
3. **11 个算子的注册表与写权限。** 已完成：`state.OPERATORS`；`CARD_MECHANISMS` 改映射；机制卡仍是空壳。
4. **候选 → 依赖组原子提交。** 已完成：同阶段共用快照、依赖组、同路径冲突整组拒绝、重复贡献去重、`commit_key` 幂等。`event_phase` 目前由 Agent 取 `step` / `perceive` 触发点，不由环境组下发。
5. **三级错误策略。** 已完成：硬错 / 软错 / 豁免；豁免只认规则卡声明的软边界，现无任何规则可豁免。
6. **`mechanism_log` 与四类版本号。** 已完成：写入每次提交、manifest 和快照。manifest 另记 `code_version`（git SHA 和是否有未提交改动）与 `model_served`（服务端响应里报告的模型名，mock 为空）。
7. **stable 变更流程。** 未做：修订候选记录与 `stable_snapshot` 迁移的数据结构。目前只有 `stable_snapshot_id` 哈希。

验证：61 项测试通过（含每条硬约束的反例）；mock 冒烟运行的 `public.jsonl` 与升级前逐字一致，状态数值轨迹与决策一致。这只说明升级没有改变工程行为，不构成心理有效性证据。

阶段 A 没有做、需要后续注意的点：`mechanism_log` 目前没有"未选候选动作及原因"；模型自选变化量的区间校验仍只有 trust 单步 0.3 的占位上限；`goal_state`、`cognition_state` 里除信念和他人模型外没有更新规则。

### 阶段 A2：画像 schema 2.0（2026-10-19 完成）

1. **顶层结构。** 已完成：`identity` / `traits`（八个子对象）/ `values` / `core_goals` / `speech_style` / `mechanism_priors`，旧的 `needs`、`emotion`、`cognition`、`communication`、`abilities` 分别并入 `traits.motivation_baseline`、`traits.affective_capacities`、`traits.cognitive_style`、`speech_style.baseline`、`traits.social_cognition_71d`。
2. **模块状态。** 已完成：`stable.module_status` 覆盖 20 个登记模块；状态与值互相约束；`provenance` 只给 active 模块。`modules.check_consistency` 与动态状态共用。
3. **价值与目标。** 已完成：施瓦兹 19 项、权重和为 1、可选冲突阈值与可让步范围；核心目标 1–3 个。
4. **表达。** 已完成：情境表达规则进入表达提示。
5. **迁移。** 示例档案由 `scripts/bootstrap_examples.py` 重建；1.0 档案被拒绝，不做静默转换。

验证：96 项测试通过，每个反例断言的是预期错误而非任意错误；示例运行的审计报告全部通过。
未做：P2/P3 与三方案比较；分面级 unknown；stable 变更的修订候选与版本迁移结构；`moral_reasoning`、`cultural_orientation`、`developmental_position` 等适配器。

### 阶段 B：实验基础设施

8. **消融入口拆开。** 已完成：`profile_only` / `profile_state` / `profile_state_mechanisms` 三档，旧名 `mechanisms` 不再接受。状态在三档下都计算，只有提示内容不同。
9. **样本包导出。** 已完成：每个运行一个盲化编号目录，方法标签（模式、后端、模型、代码版本）只在根目录 `blind_key.json`，控制器提示不进入样本包（其键名会暴露消融档位）。对方真实状态单独成文件；角色可见轨迹不含状态、推理和世界私有数据。标注模板含 0–3 级锚点和硬错类型（类型表为工程侧拟定，回复未枚举）。已知限制：首个样本没有前状态，因为运行不记录初始状态，只能看各 change 的 `before`。
10. **自动审计报告。** 已完成：每次运行自动产出 `audit_report.json/.md`，并可用 `rolesim audit` 重审；软约束、豁免、被拒依赖组单列且计入错误率，隐藏事件内容进入控制器输入会判失败。审计从落盘产物重新推导，不信任引擎自己的记录。
11. **回放工具**：按 `state_id` 和四类版本号回放。

### 阶段 D：人格线与社会情境线（2026-10-20）

工作分三条线：社会情境、人格、动力机制。本阶段做前两条，**动力机制线暂停**（会议第 7、9 条，等心理学组共建）。对应会议记录与分工：

| 会议 / 分工项 | 做了什么 | 位置 |
|---|---|---|
| 初始化：场景输入、交互目标、想让 / 不想让别人知道的信息 | 角色文件里的开场设定 `setup`：`goal`、`disclose`、`conceal`、`recap`（跟着角色走，剧情不指导角色） | `rolesim/briefing.py`，`docs/02-authoring.md` |
| 两个数据包，场景和人物分开 | `roles/role1.json`（一个角色一个文件：档案 + skills + 开场设定）与 `scenes/test1.json`（一个剧情一个背景文件），编号命名 | `scripts/bootstrap_examples.py` |
| 记忆用简单的 Mem0 式 | 只增记忆 + 召回视图（前情固定、相似旧条目、最近 12 条）；未接 Mem0 服务 | `rolesim/memory.py` |
| 前情提要放进记忆 | `recap` 作为记忆首批条目，可被决策引用为证据，审计承认 | `briefing.recap_entries`、`audit.py` |
| 人格 skills 的 GitHub 调研 | 四类项目对比、可借鉴与不可照搬之处 | `docs/08-persona-skills-survey.md` |
| 基础人格库从 skills 角度构建 | 9 个兼容 Agent Skills 规范的 SKILL.md + 两个人物的绑定 | `library/skills/`、`rolesim/skills.py` |
| 角色驱动怎么把 skills 融进去 | 三级加载：目录 → 按线索 / 简报激活 → 表达要点；`--no-skills` 对照 | `agent.make_payload`、`Agent.step` |
| 路线 test time 还是 context engineering | context engineering 为主，加轻量 test time，暂不训练 | `docs/08` 第 3 节 |
| 人格会隐藏，故意不给内容 | `conceal`（遗漏 / 战略模糊，不支持说谎）只进决策提示；审计 `concealed_info_withheld` | `briefing.py`、`audit.py` |
| 时间轴、打断 | 只写设计说明：v0 合同内的用法 + 下一版合同提案 | `docs/09-timeline-and-interruption.md` |
| 和情景的交互接口 | 合同 v0 已对接；本阶段没有改合同字段，简报和 skill 都在 Agent 构造时传入 | — |

验证：126 项测试通过（新增 skill 解析与绑定、激活、表达隔离、简报校验、隐瞒审计、记忆召回、评审包盲化）。四个 mock 运行（有 / 无 skill、两个人物、无绑定）审计全部通过。这些只说明数据通道和隔离成立。

补充：输入整理为一个角色一个文件（`roles/role1.json`、`role2.json`）、一个剧情一个背景文件（`scenes/test1.json`、`test2.json`），用编号命名；`rolesim/roles.py` 负责读取、搭配检查（`fit`）和组装（`seat`），换角色或剧情只改 `--role` / `--scene`。剧情格式是替身，后续按场景组的背景文件接口替换。用本地 Qwen3-8B 跑通了 5 轮（审计全过，但表达层台词复读，待修）。共 138 项测试。

本阶段没做、要注意的：

- skill 内容是工程侧拟写，需要心理学组审定；skill 不能写状态，也不替代机制卡。
- 记忆召回用字符二元组相似度，不是语义检索；真接 Mem0 需要 LLM 和向量服务，会破坏 mock 下的确定性，建议作为可选后端。
- 隐瞒泄露只按关键词查，换说法的泄露要人工评分。
- 会议第 9 条"记忆更新人格"与现有规则（轨迹内 stable 只读，长期变化走审核后的版本迁移）有张力，需要和心理学组对齐后再做。

### 阶段 C：等对方交付后才动

| 事项 | 等什么 | 来自 |
|---|---|---|
| 三方案（P1/P2/P3）比较实现 | 比较设计与评测集，P2/P3 行为约束接口 | 心理学组 |
| 其余 26 个分面的行为投影 | 行为锚点 | 心理学组 |
| 11 个算子机制卡的数值 | 触发条件、幅度区间、时间尺度、优先级 | 心理学组 |
| 人格调节系数标定 | 低/中/高敏感度标定样本 | 心理学组 |
| 构念登记表落盘与 Schema 生成 | 登记表与角色状态说明 | 心理学组 |
| 71 维能力适配 | 原引用定义文件 | 心理学组 |
| 2–3 个角色 stable + 1 个标准轨迹 | 测试样例包，含事件表、可见性表、验收表 | 心理学组、场景组 |
| 评测量表落地与复标流程 | 校准集与评分表单 | 心理学组 |
| 真实多角色调度、轮转顺序 | 依次还是同时行动 | 场景组 |

## 6. 仍待确认的事项

心理学组的待定项已有答复，原先的工程侧默认假设随之失效。仍未决的只剩环境侧，以及回复引出的新问题。

| 待定项 | 影响模块 | 我们的默认假设 |
|---|---|---|
| 一轮内依次还是同时行动 | M6 | 先做依次行动，同时行动作为裁决器的另一种实现 |
| 历史传全量还是增量 | M6 | 按增量实现，收到全量按 event_id 去重，两种都能跑 |
| 是否需要 `intent_note` | M6 | 先实现，默认不填 |
| `StateDelta.mechanism` 枚举改为 11 个算子是否影响环境组 | M6 | 视为对环境组可见的变更，需通知；环境组示例用到 `belief_revision`，该名称在 v1 保留 |
| `event_phase` 是否由环境组下发 | M6 | 由工程侧在 Agent 内推导，环境组不感知；若环境组需要显式阶段再协商 |
| 登记表等三份材料何时交付、以何种格式 | M1、M3、M9 | 在交付前，阶段 A 的字段名按回复原文拼写，改名成本低 |
| 规则卡幅度区间交付前，模型自选变化量的校验上限 | M3 | 沿用 v0 占位上限（trust 单步 0.3），标 `engineering_placeholder`，不得当校准值 |

## 7. 不做的事

- 不把 30 分面、71 维、价值、发展阶段一次性铺成一张任意赋值的大表。每个新增字段都要有对照情境才上线。
- 不把 mock 后端的输出差异当心理有效性证据。
- 不在 71 维定义到位前用零向量或虚构维度冒充"已启用能力模型"。
- 不把 `unknown`、`not_modeled` 的字段填成 0 或中性值来通过校验。
- 不用单一人格系数乘以变化量；系数须先声明影响敏感度、峰值、持续时间还是恢复速度。
- 不为了与 11 个算子一一对应而另写一套平行的理论分类。
- 不在规则卡幅度区间到位前，把工程占位数值说成校准结果。
- 不把工程测试通过说成"角色像人"。
- 不改 `profile_only` 的名字去对应上游任何方法，它不是 PersonaForge 或 Character-LLM 的复现。
