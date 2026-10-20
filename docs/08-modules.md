# 模块划分与实现单元

本文说明系统分成哪几个模块、各自管什么数据、谁有权改它、现在做到哪一步。划分依据是"谁拥有这份数据、谁有权改它"，不是代码文件边界。分层与信息隔离见 [07-architecture.md](07-architecture.md)，一次运行的调用链见 [06-runtime-flow.md](06-runtime-flow.md)。

阅读约定：
- 各模块的"v1 目标 / v1 补充"指心理学组回复确定的目标口径，**其中一部分尚未实现**；"现状"写的是当前代码实际做到的。两者的差距、阶段记录和下一步计划见 [plan/01-roadmap.md](../plan/01-roadmap.md)。
- 心理学组回复的变更摘要（v0 → v1 目标）也在 [plan/01-roadmap.md](../plan/01-roadmap.md) 第 1 节。

## 1. 九个模块

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
- v1 持有数据：`identity`、`traits`（八个子对象：personality / attachment / motivation_baseline / affective_capacities / social_cognition_71d / cognitive_style / developmental_position / personality_function）、`values`（施瓦兹 19 项权重）、`core_goals`、`speech_style`、`self_view`、`public_description`、`provenance`，以及每个子模块的 status（`deferred_modules` 已由 status 取代）。
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
  - 运行中只有两条更新规则在写，都挂在机制卡上（`mechanisms.py`）：`affect_state.emotion.intensity`（`appraise` 卡的 `tension_v1`，emotion_update / decay_or_recovery）和 `relation_state.<其他角色 id>.trust`（`trust` 卡的 `trust_v1`，relation_update）。输入为 null 时跳过该分支，不用中性值代替。
  - 初始只有 `emotion.label`、`emotion.intensity` 和对每个其他角色的 `trust` 这些原型默认值（origin 为 `default`，经审计转换写入）；其余关系维度、tom、情绪连续坐标为 unknown。
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
- 现状：五张机制卡（verify / appraise / trust / repair / boundary，card schema 2.0）。触发条件 `trigger.any_of` 读三类来源：线索（阈值 0.3）、状态值（紧张 ≥ 0.6667）、自上次决策以来的状态变化（对此人信任净降 ≥ 0.05）。`effects` 指向状态规则：appraise → `tension_v1`（gain 0.35），trust → `trust_v1`（gain 0.15），都是每个事件都算的连续过程；其余三张卡没有状态规则，日志记 `no_state_rule`。动作倾向打分 `score = 强度 × (0.5 + 0.3×prior + 0.2×moderator)`，取前 3。全部数值是未校准的工程假设，不是实证规律。原来的 `CARD_MECHANISMS` 文档映射已删除，并入卡的 `effects`。
- 后续版本才纳入的算子（先作为决策机制或日志标签；结果若要跨事件影响行为则须升为独立字段或算子）：emotion_regulation、learning_adaptation、memory_consolidation、self_evaluation_update、conflict_arbitration、reputation_update、norm_activation、repair_update。
- 归属：心理学组定义卡片内容，工程侧实现调度。

### M5 角色控制器（controller）

- 职责：把画像、状态、可见历史、机制卡组装成提示，调模型产出结构化行动意图，再产出台词。
- 两阶段：决策（输出 action / evidence_ids / brief_reason）→ 表达（输出 speech）。表达阶段只拿到角色自知、风格约束、已选动作、可见历史。
- 硬规则：世界私有数据和他人的隐藏心理字段永不进入任何一次调用；决策失败不回退成内心独白当台词。
- v1 补充：模型在规则卡给出的区间内选具体变化量并提交理由；程序做范围、权限、证据、软约束校验。
- 现状：`agent.py`（提示组装、校验、编排）+ `stateview.py`（状态的三档文字）+ `providers.py`（`MockBackend` 与兼容 `/chat/completions` 的 `ChatBackend`）。每次发言三类模型调用：感知评价、决策、表达，按档位都可读到状态视图（表达与评价只有文字，没有数值和算子名）；已用本地 vLLM + Qwen3-8B（思考模式）跑通两角色对话。
- 归属：工程侧。

### M6 环境适配层（environment adapter）

- 职责：实现发给场景组的那份接口合同——`step` / `perceive`、Event / ProposedAction / AgentState / StateDelta 的序列化、可见性过滤的二次校验。
- 边界：环境裁决"实际发生了什么、谁看见了"；我们只提交尝试。角色提出计划不等于计划被接受。
- v1 补充：
  - 行动反馈、新证据、权力变化或时间推进到达时，开启新的 `event_phase`，重新核定可见性并读取最新提交状态。
  - 重试以角色、事件、阶段和提交编号保证幂等。
  - `StateDelta` 的 `mechanism` 取值从 7 个改为 11 个算子，属于对环境组可见的变化，需要告知（见 [plan/01-roadmap.md](../plan/01-roadmap.md) 第 3 节）。
- 现状：`rolesim/contract.py` 定义合同附录里的数据类；`rolesim/agent.py` 的 `Agent.step` / `Agent.perceive` 按 v0 合同实现。`runtime.DialogueEnvironment` 是 2–6 个角色的对话环境：只给共享背景，开场后按座位固定轮转请角色发言，把台词原样转给所有在场角色，不编造结果；场景可带一个规则驱动的环境 agent（`runtime.WorldAgent`），每轮开头推进时间、按规则改变世界并发旁白，可指定只给部分角色（私有可见性）。没有环境标注线索时，由角色自己的模型从本角色立场评价线索，机制卡的规则更新状态。两处工程侧扩展待环境组确认：`Event.signals` 可携带环境标注的线索（有则优先）；动作标签放在 `ProposedAction.intent_note`。
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
- 现状：三档消融入口（`profile_only` / `profile_state` / `profile_state_mechanisms`）、自动审计报告（`rolesim/audit.py`）和盲化样本包导出（`rolesim/package.py`，`python3.11 -m rolesim package`）已有。自动审计按角色覆盖七项：字段有效性、证据与可见性、写权限、稳定一致性、轨迹可追溯性、隐瞒信息未泄露、跨角色隔离；其余维度列为"需人工评分"。样本包含评测版（前后状态、可见触发事件、模型输出、全部候选及去向）、角色可见轨迹、对方真实状态（仅供对照）和标注模板。缺标注工具与复标统计；评测协议本身尚未冻结。
- 归属：心理学组定维度与量表，工程侧提供数据与工具。

### M9 工程基础（infra）

- 职责：CLI、测试、CI、依赖与环境约束（标准库、Python 3.11+）。
- v1 补充：由登记表与状态说明生成运行时 JSON Schema，并在 CI 中检查三份材料的引用、范围与版本一致性。发现不一致时先冻结运行时发布。
- 现状：`tests/` 下按模块分文件，CI 工作流已配置（见 [03-usage.md](03-usage.md) 第 6 节）。本地跑测试时需要回环监听权限。`ChatBackend` 对 localhost / 127.0.0.1 / ::1 不走 `HTTP(S)_PROXY`。
- 归属：工程侧。

## 2. 人格方案：三案互斥，P1 暂为实现基线

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

## 3. 实现单元（U1–U13）

九个模块按数据归属划分。落到代码和合同时再细分为 13 个实现单元，编号用 U 以免与 M 混淆。"现有代码"列写的是当前实现；与 v1 目标的差距见 [plan/01-roadmap.md](../plan/01-roadmap.md) 第 2 节。

| # | 单元 | 所属模块 | 职责 | 主要输入/输出 | 对应合同 | 现有代码 |
|---|---|---|---|---|---|---|
| U1 | 来源目录 | M1 | 两份库的快照、章节索引、哈希 | 原文 → `catalog.json` | — | `catalog.py`，已完成 |
| U2 | 人物档案 | M2 | stable schema 与校验：主人格（P1/P2/P3）、依恋、动机、施瓦兹价值、情绪/认知基线、71 维能力（待定义）、core_goals、来源与置信度、模块 status | 档案 JSON → 已校验 stable | 心理学组 2.1、4 | `persona.py`，schema 2.0（仅 P1） |
| U3 | 视图编译 | M2 | 系统真值 / 角色自知 / 他者可见 / 控制器约束四种投影；条件化行为语句；三方案统一的行为约束接口 | stable → views | 心理学组 2.1、环境组 1 | `compile_views`，部分 |
| U4 | 状态存储 | M3 | AgentState（stable/dynamic/memory）、StateDelta、版本号、落盘与按 state_id 回放 | — | 环境组 2.4–2.5、心理学组 6 | `state.py` + `modules.py` + `initial_state`；六域、模块状态、state_id 链已有，回放工具未做 |
| U5 | 感知与记忆 | M3、M6 | 可见性二次校验、去重、记忆条目带来源与类型（观察/他人说法/反馈/自己行动） | Event → memory | 环境组 3.2、3.3 | `observe`，部分 |
| U6 | 机制注册与调度 | M4 | 机制卡（触发、阈值、可行性门、候选动作、例外）、多卡竞争排序、`decision_mechanisms` 映射 | 事件 + 状态 + stable → 激活卡 | 心理学组 3；决策库 0.2、0.6 | `mechanisms.py`，五张卡（card schema 2.0），触发读线索 / 状态 / 状态变化 |
| U7 | 状态更新器 | M3 | 按 11 个算子生成候选 StateDelta；依赖组原子提交；硬错拒绝、软错标记、豁免记录 | 激活卡 + 事件 → StateDelta | 心理学组 3.1–3.3、7 | `candidate_changes` + `commit_phase`；只有紧张、信任两条规则，挂在卡上，审计可复算 |
| U8 | 决策控制器 | M5 | 生成并筛选选项，选出 ProposedAction，产出私有 thought，在规则区间内选变化量 | 视图 + 状态 + 卡 → actions | 环境组 2.3、3.1 | `make_payload` + `decide`，部分 |
| U9 | 表达 | M5 | 按已选动作和风格生成台词，不泄露机制和分数 | action + 自知 + 风格 → speech | 环境组 2.3 | `express`，已有 |
| U10 | 环境适配层 | M6 | 实现 `step` / `perceive`，数据类；`event_phase` 与幂等；脚本环境作测试替身；多角色调度器 | 环境组请求 ⇄ Agent | 环境组 3、4 | `agent.py` + `contract.py` + `DialogueEnvironment`（2–6 个角色，固定轮转）+ `WorldAgent`（规则驱动）；动态发言顺序未做 |
| U11 | 模型后端 | M5 | 兼容接口调用、严格 JSON、失败不回退 mock；回环地址不走代理 | — | — | `providers.py`，已有 |
| U12 | 运行记录 | M7 | manifest、inputs、audit、public、state 序列、`mechanism_log`、prompt 与原始输出、四类版本号、Git SHA | — | 环境组 5 | `run_dialogue`，每个角色一份记录，含思考模式推理；缺回放工具 |
| U13 | 评测 | M8 | 自动审计（越界、无证据、stable 被改、引用不可见事件、规则覆盖）、样本包导出、标注工具 | 运行目录 → 报告 | 心理学组 5、第三部分 | `audit.py`、`package.py` 已有；标注工具、复标统计未做 |

单元间依赖：U2→U3→U8/U9；U5→U4；U6→U7→U4；U10 调用 U5、U6、U7、U8、U9；U12、U13 读取全部产物。M9 工程基础不单列实现单元。

## 4. 模块间的三条硬边界

1. **心理学组改 M2/M3/M4，不碰 M6。** `stable` / `dynamic` 的内部字段对环境组是不透明 JSON，改字段不需要环境组同步发版。例外：`StateDelta.mechanism` 的取值集合出现在跨组的数据里，变更须通知环境组。
2. **环境组改 M6，不碰 M2/M3。** 环境只存状态快照和 state_id，不读也不改内部字段。
3. **M5 是唯一接触模型的地方。** M2/M3/M4 必须能在没有模型的情况下完整跑校验，这样工程正确性可以独立于模型质量验证。

