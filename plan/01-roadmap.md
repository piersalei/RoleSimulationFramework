# 路线与阶段记录

本文档记录：心理学组回复带来的口径变化、已经做完的实现阶段、等对方交付的事项、仍待确认的接口问题、明确不做的事。模块划分（M1–M9）、实现单元（U1–U13）和模块间的硬边界是框架本身的说明，在 [docs/08-modules.md](../docs/08-modules.md)；表中"影响模块"一列的 M 编号对应那里。

状态：2026-10-15 起草，2026-10-19 按心理学组回复修订，2026-10-20 补充人格线与社会情境线（阶段 D），并把模块划分部分拆回 `docs/`。`data/sources/contracts/` 下的两份合同已与心理学组、环境组对接完成，本轮新增内容均不改动合同。

**口径说明。**
- 心理学组回复里的"拟改动方向"和问答答复，本文档一律采用为 **v1 目标口径**。
- 回复给出的是结构、分类和流程；规则卡的触发条件、幅度区间、衰减时间尺度、人格调节参数尚未交付，仍是空缺。
- 阶段 A 的结构升级已于 2026-10-19 完成（见第 2 节）：状态层是 `state-v1`，画像层是 persona schema 2.0。规则数值、评测协议和多方案比较仍空缺，[docs/08-modules.md](../docs/08-modules.md) 各模块的"现状"逐项标明。
- 工程侧自行采用的默认假设只保留给场景/环境组的未决项（第 3 节）。

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

## 2. 实现阶段记录

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
| 初始化：场景输入、交互目标、想让 / 不想让别人知道的信息 | 角色文件里的开场设定 `setup`：`goal`、`disclose`、`conceal`、`recap`（跟着角色走，剧情不指导角色） | `rolesim/briefing.py`，`docs/04-authoring.md` |
| 两个数据包，场景和人物分开 | `roles/role1.json`（一个角色一个文件：档案 + skills + 开场设定）与 `scenes/test1.json`（一个剧情一个背景文件），编号命名 | `scripts/bootstrap_examples.py` |
| 记忆用简单的 Mem0 式 | 只增记忆 + 召回视图（前情固定、相似旧条目、最近 12 条）；未接 Mem0 服务 | `rolesim/memory.py` |
| 前情提要放进记忆 | `recap` 作为记忆首批条目，可被决策引用为证据，审计承认 | `briefing.recap_entries`、`audit.py` |
| 人格 skills 的 GitHub 调研 | 四类项目对比、可借鉴与不可照搬之处 | `docs/10-persona-skills-survey.md` |
| 基础人格库从 skills 角度构建 | 9 个兼容 Agent Skills 规范的 SKILL.md + 两个人物的绑定 | `library/skills/`、`rolesim/skills.py` |
| 角色驱动怎么把 skills 融进去 | 三级加载：目录 → 按线索 / 简报激活 → 表达要点；`--no-skills` 对照 | `agent.make_payload`、`Agent.step` |
| 路线 test time 还是 context engineering | context engineering 为主，加轻量 test time，暂不训练 | `docs/10` 第 3 节 |
| 人格会隐藏，故意不给内容 | `conceal`（遗漏 / 战略模糊，不支持说谎）只进决策提示；审计 `concealed_info_withheld` | `briefing.py`、`audit.py` |
| 时间轴、打断 | 只写设计说明：v0 合同内的用法 + 下一版合同提案 | `plan/03-timeline-and-interruption.md` |
| 和情景的交互接口 | 合同 v0 已对接；本阶段没有改合同字段，简报和 skill 都在 Agent 构造时传入 | — |

验证：126 项测试通过（新增 skill 解析与绑定、激活、表达隔离、简报校验、隐瞒审计、记忆召回、评审包盲化）。四个 mock 运行（有 / 无 skill、两个人物、无绑定）审计全部通过。这些只说明数据通道和隔离成立。

补充：输入整理为一个角色一个文件（`roles/role1.json`、`role2.json`）、一个剧情一个背景文件（`scenes/test1.json`、`test2.json`），用编号命名；`rolesim/roles.py` 负责读取、搭配检查（`fit`）和组装（`seat`），换角色或剧情只改 `--role` / `--scene`。剧情格式是替身，后续按场景组的背景文件接口替换。用本地 Qwen3-8B 跑通了 5 轮（审计全过，但表达层台词复读，待修）。共 138 项测试。

本阶段没做、要注意的：

- skill 内容是工程侧拟写，需要心理学组审定；skill 不能写状态，也不替代机制卡。
- 记忆召回用字符二元组相似度，不是语义检索；真接 Mem0 需要 LLM 和向量服务，会破坏 mock 下的确定性，建议作为可选后端。
- 隐瞒泄露只按关键词查，换说法的泄露要人工评分。
- 会议第 9 条"记忆更新人格"与现有规则（轨迹内 stable 只读，长期变化走审核后的版本迁移）有张力，需要和心理学组对齐后再做。

### 阶段 E：多 agent 剧情与状态—机制闭环（2026-10-20 起草，未开工）

改动内容、分步（E1–E4、F1–F4）和待确认项见 [02-multi-agent-and-state-mechanism.md](02-multi-agent-and-state-mechanism.md)：剧本决定 agent 数量并加入环境 agent；状态摘要进入决策与表达输入；机制卡写明"触发条件 / 改哪个状态 / 怎么改"并接管状态更新。

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

## 3. 仍待确认的事项

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

## 4. 不做的事

- 不把 30 分面、71 维、价值、发展阶段一次性铺成一张任意赋值的大表。每个新增字段都要有对照情境才上线。
- 不把 mock 后端的输出差异当心理有效性证据。
- 不在 71 维定义到位前用零向量或虚构维度冒充"已启用能力模型"。
- 不把 `unknown`、`not_modeled` 的字段填成 0 或中性值来通过校验。
- 不用单一人格系数乘以变化量；系数须先声明影响敏感度、峰值、持续时间还是恢复速度。
- 不为了与 11 个算子一一对应而另写一套平行的理论分类。
- 不在规则卡幅度区间到位前，把工程占位数值说成校准结果。
- 不把工程测试通过说成"角色像人"。
- 不改 `profile_only` 的名字去对应上游任何方法，它不是 PersonaForge 或 Character-LLM 的复现。
