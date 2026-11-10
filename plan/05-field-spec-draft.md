# 字段规格草案（待确认后冻结）

2026-11-07 起草，2026-11-10 整理：只保留**还没冻结**的部分，即每一类角色数据要维护哪些字段。确认后移入 `docs/`，此后字段的增删改按角色状态说明第十部分的兼容规则走版本号。

- 来源：心理学组 v1 接口文件（[data/sources/psychology_v1/](../data/sources/psychology_v1/README.md)）。心理学含义以构念登记为准，本文不新增心理构念。凡是心理学文件没写死、由工程侧补的结构，标 **〔工程〕**，需要心理学组确认（F1）。
- 和代码的关系：动态状态与记忆的实际布局在 [`rolesim/state/slots.py`](../rolesim/state/slots.py)，字段名按本文第 2.4—2.5 节；稳定画像按构念 ID 组织（`role/profile.py`）。本文里比代码多出来的字段（例如 `AgentState` 的 `enable_config_id`、`phase`，第 1 节的部分元数据）是目标结构，**还没实现**。
- 已经定下来、已实现的部分不在这里：对外接口的字段见 [docs/13-agent-interface.md](../docs/13-agent-interface.md)；过程对象（证据集、判断、三个阶段的产物、变化槽位、状态变化、提交结果）、日志和内部接口见 [docs/14-interfaces.md](../docs/14-interfaces.md)。原稿第 2、4、5、6 节（对外接口、过程对象、日志、内部接口）已删除：其中按"模型提议状态"写的部分在 C8 之后不再成立，其余已写进上面两份文档。原稿在 Git 历史里。
- 证据层级：工程规格，不含任何心理有效性结论。

## 0. 记法

**取值**

| 记法 | 含义 |
|---|---|
| `z` | 连续倾向，-2—+2（很低 / 较低 / 参照中点 / 较高 / 很高）；0 不是"没有"，也不是原量表分 |
| `u` | 0—1：权重、敏感度、阈值、强度、概率、确信度 |
| `v` | -1—+1：效价 |
| `sev` | 0—4 整数：人格功能受损严重度 |
| `grade` | 1—4 整数：事件等级（日常 / 一般 / 显著 / 重大），伤害严重度同此刻度 |
| `ord(…)` | 有序类别，不做算术 |
| `enum{…}` | 无序类别 |
| `opt(dm)` | 决策过程 `dm` 在登记表第七部分的可选项之一 |
| `text` | 说明文本，只给人和提示词读，不参与数值计算 |
| `ref:event` | 记录编号（`action_id#order_index`）；`ref:role`、`ref:memory`、`ref:belief` 等同理 |
| `[T]`、`{K: T}` | 列表、映射 |
| `?` | 可为 null（null 的含义由所在字段的状态标记说明） |

**维护方**：谁有权写这个字段。

| 记法 | 含义 |
|---|---|
| 装配 | 角色装配时由档案写入（明写 / 规则推断 / 模型推断 / 先验），轨迹内只读 |
| 派生 | 由主字段按登记规则计算，不独立赋值，只在装配或快照换代时重算 |
| 场景 | 环境组 / 场景输入给出，角色侧只读 |
| 入场 | 角色进入互动时初始化（档案入场状态 → 场景初始条件 → 先验） |
| 算子名 | 运行中只能由该更新算子经提交写回（11 个之一） |
| 阶段名 | 过程对象由该阶段生成，生成后只读 |
| 审核 | 只能经心理学组审核、生成新 stable 快照改变 |

**状态标记**（每个模块、每个动态字段都有）：`active`（有值）、`unknown`（启用但证据不足，值为 null，写明缺什么）、`inactive`（已建模、本轮未激活）、`disabled`（被配置关闭）、`not_modeled`（本版未建模）、`not_applicable`（场景不适用）。非 `active` 时值必须为 null；`default` 不是状态，是来源标记。

---

## 1. 通用元数据

### 1.1 stable 模块元数据（每个 stable 模块一份）

| 字段 | 取值 | 说明 |
|---|---|---|
| `status` | 状态标记 | |
| `construct_refs` | `[construct_id]` | 本模块对应的构念 |
| `source_refs` | `[text]` | 档案原文位置或引文；`model_inferred` 必须留原文 |
| `source_kind` | `enum{explicit, inferred, model_inferred, default}` | 子项来源不同时在子项上另标 |
| `confidence` | `u?` | |
| `visibility` | 见 1.3 | |
| `derived_from` | `[construct_id]` | 派生字段填 |
| `version` | `text` | |
| `disabled_reason` | `text?` | 非 active 时必填 |
| `needs_review` | `bool` | 高影响字段且非档案明写时为 true |

### 1.2 dynamic 字段元数据（每个动态字段、每条记录一份）

| 字段 | 取值 | 说明 |
|---|---|---|
| `status` | 状态标记 | |
| `source` | `[ref:event] \| enum{profile, scene, default}` | 入场为档案 / 场景 / 先验；运行中为支持本次变化的可见事件 |
| `confidence` | `u?` | 写入方对这个值的把握；不同于信念自身的确信度 |
| `visibility` | 见 1.3 | |
| `last_updated_event` | `ref:event?` | |
| `version` | `int` | 每次提交 +1 |

memory 条目使用同一套元数据。

### 1.3 可见性

| 字段 | 取值 |
|---|---|
| `self` | `enum{aware, partial, unaware}`：能觉察并表述 / 有自我理解但可能偏差 / 不能觉察 |
| `others` | `enum{observable, presented, hidden}`：可直接观察 / 只见主动呈现部分 / 不可见 |

`self = unaware` 的字段可以驱动行为，但进入提示时必须转成条件化自然语言，不出现系统术语。

### 1.4 证据引用 `EvidenceRef`

| 字段 | 取值 | 说明 |
|---|---|---|
| `event` | `ref:event` | 本角色可见的记录，或本角色自己的行动提议 |
| `channel` | `enum{witnessed, hearsay, inferred, self_action, pre_entry}` | 记录本身对接收者为 `witnessed`；内容里转述的事实由 S1 从文字判为 `hearsay`（与环境组确认） |
| `claim` | `text?` | 同一记录里转述的具体命题；`channel=hearsay` 时必填 |
| `reliability` | `u?` | |

证据质量限档：只有 `hearsay` 的最多取"中"档，只有 `inferred` 的最多取"小"档。

---

## 2. 角色数据：维护字段

### 2.1 `AgentState`（每次提交一个，不可变）

| 字段 | 取值 | 维护方 | 说明 |
|---|---|---|---|
| `state_id` | `text` | 提交 | |
| `prev_state_id` | `text?` | 提交 | 串成状态链 |
| `interaction_id`、`character_id` | `text` | 入场 | 路由键 |
| `stable_snapshot_id` | `text` | 装配 | 指向 3.2，不内嵌可写副本 |
| `enable_config_id` | `text` | 入场 | 指向 3.3，轨迹内不变 |
| `versions` | `{construct_definition_version, state_schema_version, update_rule_version, evaluation_protocol_version}` | 入场 | 四类版本号 |
| `dynamic` | 3.4 | 算子 | 六域都必须出现 |
| `memory` | 3.5 | memory_update | |
| `phase` | `{event_id, phase_no}` | 提交 | 产生本快照的事件阶段 |

思考和阶段记录不放在这里，作为轨迹日志另存（`trace/<互动>/<角色>/`，见 docs/14 第 1.2 节）。

### 2.2 `StableProfile`（不可变快照）

外层：`snapshot_id`、`previous_snapshot_id?`、`revision_ref?`（由哪条审核通过的修订生成）、`assembled_at`、`assembly_order`、`derived_computed_at`。下列每个模块都带 1.1 的元数据，维护方均为"装配"（派生项为"派生"），轨迹内只读，改变只能"审核"。

**identity**

| 模块 | 字段 | 取值 | 说明 |
|---|---|---|---|
| `demographics` | `name` | `text` | 必填 |
| | `gender`、`gender_identity`、`pronoun` | `text?` | |
| | `age` | `int?` | 高影响 |
| | `life_stage` | `enum{儿童期, 青春期, 成年早期, 中年, 老年}?` | 可由年龄推断 |
| | `education` | `{highest, professional_training, informal}?` | |
| | `languages` | `[{language, proficiency, domains}]` | |
| | `cultural_background` | `{socialization, migration, multicultural}?` | |
| | `health_conditions` | `[{functional_impact, accommodation, others_visibility}]` | 写功能影响，不写医学标签；高影响 |
| `social_roles` | `roles` | `[{role, category, organization, position, obligations:[text], rights:[text], salience:u, identification:u}]` | 多角色全部保留 |
| | `institutional_power`、`public_prestige`、`professional_authority` | `u?` | 三项分开 |
| | `legal_status` | `text?` | 高影响 |
| `resources` | `items` | `[{kind, actual, presented, others_visible}]` | kind 含收入稳定性、可支配资源、社会保障、居住、时间自主、人际资源、身体呈现、穿着、声音、可见物质、沟通媒介、行动限制 |
| `life_history` | `events` | `[{event, stage, lasting_impact, current_meaning, meaning_source, others_visibility}]` | 重大失落 / 受害且非明写时需审核 |

**traits.personality**（`p1_big_five` / `p2_16pf` / `p3_jung_hexaco` 三选一，未启用方案不得出现）

| 字段 | 取值 | 说明 |
|---|---|---|
| `scheme` | `enum{P1, P2, P3}` | |
| P1 `facets` | `{领域.分面: z}`，30 个 | NEO 分面；领域分不派生 |
| P2 `primary_factors` | `{因素: z}`，16 个 | |
| P2 `global_factors` | `{外向性, 焦虑性, 坚韧性, 独立性, 自我控制: z}` | 派生（等权近似），不独立赋值 |
| P3 `jung_functions` | `{八功能: u}`，和为 1 | 偏好权重，不是能力 |
| P3 `hexaco_facets` | `{领域.分面: z}`，24 个 | |
| P3 `function_order`、`hexaco_domains`、`mbti_label?` | 派生 | 只作概览，不产生行为权重 |

**traits 其余模块**

| 模块 | 字段 | 取值 |
|---|---|---|
| `attachment.global` | `anxiety`、`avoidance` | `z` |
| | `type_label?` | 派生；任一轴近中点时为 null 并写"边界 / 过渡" |
| `motivation.source_activation` | `evolutionary_weights` | `{自我保存, 依恋亲和, 照护, 择偶, 地位, 资源获取, 探索, 群体归属: u}` |
| | `drive_sensitivity`、`drive_recovery`、`incentive_sensitivity` | `u` |
| | `optimal_arousal` | `{low: u, high: u}` |
| | `bas`、`bis`、`fffs` | 各 `{sensitivity: u, threshold: u, recovery: u}` |
| `motivation.need_vector` | `needs` | `{需要: {importance: u, minimum: u, deprivation_sensitivity: u}}`；含生理、安全、归属、尊重、自我实现、自我超越、SDT 自主、Murray 自主、胜任、成就、权力等；不要求和为 1 |
| | `power_need_target` | `enum{personal_status, collective_goal}?` |
| `motivation.goal_computation` | `cost_sensitivity` | `u` |
| | `achievement_orientation` | `{掌握接近, 掌握回避, 表现接近, 表现回避: u}` |
| | `promotion_focus`、`prevention_focus`、`approach`、`avoidance` | `u` |
| | `hierarchy_params` | `{…}` 〔工程：具体参数待心理学组列出〕 |
| `motivation.conflict_regulation` | `discrepancy_sensitivity`、`persistence_threshold`、`quit_threshold`、`goal_replacement` | `u` |
| | `conflict_themes`、`self_other_templates` | `[text]`；`self = unaware` |
| `motivation.self_efficacy` | `domains` | `{领域: {level: u, strength: u, generality: u}}` |
| `affect.emotion_baseline` | `family_thresholds` | `{喜悦, 信任, 恐惧, 惊讶, 悲伤, 厌恶, 愤怒, 期待: u}` |
| | `resting_valence`、`resting_arousal` | `v`、`u` |
| `affect.appraisal_bias` | `dimensions` | `{新奇性, 目标相关性, 目标一致性, 原因与责任主体, 控制与应对潜力, 确定性, 规范相容性: z}` |
| | `attribution_style` | `{internality, stability, globality: z}` 〔工程：拆法待确认〕 |
| `affect.regulation_capacity` | `abilities` | `{识别, 生成, 预测, 执行, 监测, 切换: u}` |
| | `stress_retention` | `u` |
| `affect.empathy` | `cognitive`、`affective`、`compassion`、`self_other_distinction` | `u` |
| `affect.emotional_intelligence` | `branches` | `{感知, 运用, 理解, 管理: {self: u, other: u}}` |
| | `stress_retention` | `u` |
| `affect.expression` | `recognition_accuracy`、`clarity`、`intensity`、`authenticity`、`control` | `u` |
| | `display_rules` | `[{context, rule: enum{放大, 减弱, 中和, 掩饰, 替代}}]` |
| `social_cognition_71d` | `items` | `{三级条目编号: z}`，71 项，清单见登记表附录；未赋值为 0 并标 default |
| | `level2`、`level1` | 派生 |
| `cognitive.*` | `dual_process`、`need_for_cognition`、`ambiguity_tolerance`、`flexibility` | `z` |
| | `need_for_closure` | `{urgency: z, permanence: z}` |
| | `metacognitive_calibration` | `{领域: {confidence_bias: z, evidence_quality: u, label: 派生}}` |
| `development.position` | `stage` | `ord(E2 … E10)`，16 类 |
| | `transition` | `{direction: enum{up, down}, toward: ord, confidence: u}?`，只限相邻位置 |
| | `domain_profile` | `{领域: ord?}` |
| | `evidence` | `[{text, events, span}]`；stage 有值时至少一条跨事件证据 |
| `self.self_concept` | `domains` | `{领域: {representation: text, clarity, complexity, differentiation, stability, coherence: u}}` |
| `self.self_esteem` | `global` | `{level: z, stability: u}` |
| | `domains` | `{领域: {level: z, stability: u}}` |
| | `contingencies` | `[{condition: text, impact: u}]` |
| `self.self_discrepancy` | `items` | `[{kind: enum{理想, 应该}, owner: enum{self, other}, standard: text, gap: u, importance: u, attainability: u}]` |
| `self.identity_status` | `domains` | `{领域: {exploration: z, commitment: z, label: 派生}}` |
| `self.role_identity` | `items` | `[{social_role, standard: text, salience: u, network_commitment: u, contexts: [text], behavior_range: text}]` |
| `self.social_identity` | `groups` | `{群体: {importance, affective, subjective_membership, prototypicality, ingroup_commitment, permeability, compatibility: u}}` |
| | `intersection` | `{complexity: u, integration: u}` |
| `function.domains` | 五域：自我功能、人际功能、现实检验、身份整合、防御成熟度 | 每域 `{baseline: sev, worst_under_stress: sev ≥ baseline, recovery_time: text, support_conditions: [text]}` |
| `function.pathology_traits` | `{负性情感, 疏离, 去抑制, 敌对, 非社会性, 强迫性, 精神质: z}` | |
| `function.organization_label` | `enum{健康, 神经症性, 边缘性, 精神病性, 混合未定}` | 派生 |
| `decision_preferences` | 27 子项，见下表 | |

**decision_preferences 的 27 个子项**：每项 `{dominant: opt(dm), fallback: opt(dm)?, threshold: u?, params: {…}?}`，取值只能是对应决策过程的可选项；未获得值为 unknown；档案缺失时按登记表 4.10 的权重表生成先验（标 default）。

| 组 | 子项（决策过程） | 保存方式例外 |
|---|---|---|
| 情绪与防御 | 防御 `dm.affect.defense`、情绪调节 `emotion_regulation`、应对 `coping`、威胁奖赏 `threat_reward_policy`、反刍 `rumination_recovery` | 防御层级须落在防御成熟度允许范围内，不相容时审核 |
| 选择与学习 | 选择规则 `dm.choice.choice_rule` + `dm.cog.satisficing`、风险 `risk_ambiguity`、折扣 `temporal_discounting`、社会价值取向 `social_value_orientation`、公平 `fairness_allocation`、探索 `dm.learn.exploration_exploitation`、学习来源 `norm_culture_learning` | 风险存 `{gain, loss, ambiguity}` 三种态度；SVO 存四取向连续权重，主导项派生；公平存五规则完整排序 |
| 人际与博弈 | 信任 `dm.social.trust_reputation_update` + `dm.coop.trust_building`（新对象默认信任只存一份）、重复博弈 `repeated_game`、谈判 `negotiation`、冲突 `dm.conflict.conflict_mode`、受侵害反应 `aggression_punishment` | 重复博弈另存 `{initial_cooperation, defection_memory, retaliation, forgiveness_prob, recooperation_threshold, randomness}` |
| 沟通 | 面子 `dm.comm.politeness_face`、披露 `self_disclosure`、幽默 `humor_expression`、印象管理 `impression_management`、诚实 `deception_concealment`、边界 `assertion_boundary` | |
| 规范与责任 | 从众 `dm.norm.conformity`、权威服从 `role_authority`、责任 `dm.prosocial.responsibility_taking`、宽恕 `forgiveness` | |

**values、core_goals、speech_style**

| 模块 | 字段 | 取值 |
|---|---|---|
| `values.schwartz_priorities` | `items` | 19 项全在，每项 `{weight: u, conflict_threshold: u, concession_range: u}`，`weight` 和为 1 |
| | `investment` | `u`（总体价值投入度） |
| | `note` | `text`（角色化含义与来源） |
| | `ranking`、`higher_order` | 派生 |
| `values.moral_reasoning` | `dominant_stage`、`highest_stable_stage`、`adjacent_stage?` | `ord(1…6)` |
| | `domains`、`evidence` | `[text]`；有值时须附理由结构证据 |
| `values.cultural_orientation` | `overall` | `{个人主义集体主义, 权力距离, 不确定性规避, 成就竞争关怀合作, 长期短期, 克制放纵: z}` |
| | `by_domain` | `{家庭 \| 工作 \| 公共: 同上}`，未单列的领域用 overall |
| | `scripts` | `[{culture, domains, familiarity: u, identity_meaning: text}]` |
| `core_goals.primary` | `goals` | 1—3 个，每个 `{goal_id, definition, priority_baseline: u, stability: enum{低, 中, 高}, source_refs, identity_links, goal_version: int, object, time_horizon, direction: enum{approach, avoidance}, sdt_regulation: enum{外部, 内摄, 认同, 整合, 内在}, parent?, conflicts: [ref], subgoals: [≤2 同结构]}` |
| | `extended` | 档案中多出的长期目标，低激活，不参与冲突裁决 |
| `speech_style.baseline` | `description` | `text` |
| | `tone`、`verbosity`、`politeness`、`directness`、`pace`、`volume`、`formality`、`narrative_preference`、`lexicon` | `text` 或 `z` 〔工程：取值形式待确认〕 |
| | `situational_changes` | `[{when: text, change: text}]` |

### 2.3 `EnableConfig`（每条轨迹一份，轨迹内不变）

| 字段 | 取值 | 说明 |
|---|---|---|
| `config_id`、`version` | `text` | 由 `bootstrap.mechanism.enable_config` 指定 |
| `stable_modules` | `{construct_id: {state: enum{enabled, disabled, not_modeled}, reason?}}` | |
| `dynamic_fields` | 同上 | |
| `decision_mechanisms` | 同上，按 `dm` | |
| `personality_scheme` | `enum{P1, P2, P3}` | |

### 2.4 `DynamicState`

> 2026-11-07：本节与 3.5 已落成 `rolesim/state/slots.py`（状态布局，由 `IStateStore` 使用）。与下表的差异：`d.policy.mechanism_preference_state` 改为按 `entry_id` 的条目，便于定位；`d.policy.action_commitment` 的阶段暂不收〔工程〕的“延误”“重新协商”（待 F1）；1.2 的逐字段元数据（source、confidence、last_updated_event、version）还没有放进状态，变化的来源与证据记在 `StateChange` 和提交历史里。

每条都带 1.2 的元数据。"维护方"是唯一主写入算子；`decay_or_recovery` 只回落标出的字段。

**affect_state**

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `d.affect.emotion` | `label` | `[enum{喜悦, 信任, 恐惧, 惊讶, 悲伤, 厌恶, 愤怒, 期待, 轻蔑}]?`，复合情绪列出组成 + `attribution`、`norm_evaluation`、`object` | emotion_update；回落 |
| | `valence` / `arousal` / `intensity` | `v` / `u` / `u` | 同上 |
| | `duration_hint` | 时间单位待环境组 | 同上 |
| | `confidence` | `u`（对自身情绪标签的识别确信） | 同上 |
| | `dominance` | `not_modeled` | — |
| `d.affect.stress` | `level` | `u` | emotion_update；回落 |
| `d.affect.threat_reward_activation` | `bas`、`bis`、`fffs` | `u` | emotion_update；回落 |
| `d.affect.regulation_state` | `items` | `[{process: dm, option: opt, started: ref:event, target_emotion, effect: text, load: u}]`，无则 inactive | emotion_update |

**cognition_state**

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `d.cognition.belief` | 每条 | `{belief_id, proposition: text, kind: enum{fact, interpretation}, about: [ref:role], context, time, confidence: u?, conflict_group?, adopted: bool, isolated: bool, superseded_by?: ref:belief, evidence: [EvidenceRef]}` | belief_revision；不回落 |
| `d.cognition.attention` | `focus` | `[{target, persistence: u}]` | belief_revision；焦点回落 |
| | `neglect` | `[{info, persistence: u, reminded: bool, phases_unattended: int}]` | 同上 |
| `d.cognition.attribution` | 每条 | `{event, about: ref:role, locus: enum{internal, external}?, stability: enum{stable, temporary}?, globality: enum{global, specific}?, controllability: enum{controllable, uncontrollable}?, intent: text?, responsibility: {factual, moral, relational, institutional: u?}, confidence: u}`，未定的轴为 null | belief_revision |
| `d.cognition.self_evaluation` | `global`、`domains` | `z`，偏离自尊基线 ≤ 1.0 | self_state_update；回落 |
| `d.cognition.task_self_efficacy` | `tasks` | `{任务: {level: u, efficacy_domain}}` | self_state_update；任务结束清除 |
| `d.cognition.shame` | `load` | `u` | self_state_update；回落 |
| `d.cognition.face_cost` | `damage`、`maintenance_need` | `u` | self_state_update；回落 |
| | `audience` | `[ref:role]` | 同上 |
| `d.cognition.self_responsibility` | 每条 | `{event, degree: u, type: enum{factual, moral, relational, institutional}, mechanism?: text}` | self_state_update |
| `d.cognition.other_model` | `{对象: [条目]}` | `{inference: text, kind: enum{goal, belief, intention, emotion, ability, constraint}, order: int, confidence: u?, evidence: [EvidenceRef], hypothesis_ref?, superseded_by?}` | other_model_update；不回落 |
| `d.cognition.tom_level` | `ceiling` | `int`，轨迹内不变 | 入场（需审核） |
| | `current` | `int ≤ ceiling` | other_model_update |
| | `per_object` | `{对象: int}` | 同上 |
| | `allowed_tasks` | `[text]` | 同上 |
| | `downgrade_reason` | `text?`，current < ceiling 时必填 | 同上 |

**goal_state**

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `d.goal.goals_now` | 每条 | `{goal_id, content, priority: u, stage: enum{进行中, 完成, 受阻, 降级, 暂停, 放弃}, core_goal?: ref, source: enum{核心目标, 情绪, 习惯, 规范, 关系, 安全, 场景, 其他}, reason: text}`；结束的保留 | goal_update |
| `d.goal.value_activation` | `{19 项价值: u}` | | goal_update；目标了结后回落 |
| `d.goal.goal_activation` | `{核心目标: u}` | | 同上 |
| `d.goal.goal_commitment` | `{目标: u}` | | goal_update |
| `d.goal.feasibility_estimate` | `{目标: u?}` | 未评估为 null | goal_update |
| `d.goal.threat_to_goal` | `{目标: u}` | | goal_update |
| `d.goal.goal_conflict` | 每条 | `{involved: [ref], order: [ref], non_negotiable: text, acceptable_compensation: text, resolution: text?}` | goal_update |
| `d.goal.action_orientation` | | `{direction: text, short_term_goal: ref, toward: [ref:role]}` | goal_update |
| `d.goal.revision_candidate` | 每条 | `{core_goal: ref, proposal: enum{降级, 替换, 合并, 分裂}, content: text, evidence: [ref:event], independent_evidence: int, window_start: ref:event, self_commitment: bool, review: enum{pending, approved, rejected}}` | goal_update；只追加 |

**relation_state**（按"本角色 → 对象"有向保存，`{对象: …}`）

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `d.relation.trust` | `value` | `u` | relation_update；不回落 |
| | `evidence_by_basis` | `{ability, benevolence, integrity, reliability: [EvidenceRef]}` 〔工程：只记证据，不另设分数〕 | 同上 |
| `d.relation.closeness` / `dependence` / `commitment` | `value` | `u`，彼此独立 | relation_update；不回落 |
| `d.relation.harm_history` | 每条 | `{harm_id, event: ref:event, description, severity: grade, acknowledged: enum{是, 部分, 否, 不明}, repair: enum{无, 部分, 完全}, last_activated: ref:event, salience: u, memory?: ref:memory}`；只追加 | relation_update；显著性回落 |
| `d.relation.power_asymmetry` | `objective` | `{职位, 资源, 信息, 身体: {higher: enum{self, other, none}?, known_to_self: bool}}` | 场景 |
| | `subjective` | `{direction: enum{self, other, none}, bases: [text], confidence: u}` | relation_update |
| | `note` | `text` | 入场 |

**group_state**（群体、规范来自场景或由 S1 从文字推断）

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `d.group.membership` | `{群体: {degree: u, position: text, status: enum{成员, 边缘, 候选, 被排斥, 曾属}}}` | | group_state_update |
| `d.group.identity_salience` | `{身份: u}` | | 同上；回落 |
| `d.group.norm_salience` | `{规范: {type: enum{描述性, 命令性}, source: text, salience: u}}` | | 同上；回落 |
| `d.group.alliance` | `{allies: [ref:role], opponents: [ref:role], stability: u, basis: text}` | | group_state_update |
| `d.group.boundary` | `{群体: {us: [ref:role], them: [ref:role], clarity: u}}` | | 同上 |
| `d.group.responsibility_allocation` | `{情境: {self: u, others: {对象: u}}}`，不要求和为 1 | | 同上 |

**policy_state**

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `d.policy.action_commitment` | 每条 | `{commitment_id, content, to: ref:role \| self, formed_at: ref:event, deadline: text?, stage: enum{形成, 维持, 兑现, 延误, 重新协商, 暂停, 违约, 撤销}, public: bool, means: text, verification: text?}`；结束保留 | commitment_update |
| `d.policy.current_strategy` / `fallback_strategy` | `{(dm, 对象或情境): opt(dm)}` | | strategy_learning（S2） |
| `d.policy.switch_condition` | `{(dm, 对象或情境): {signal: text, threshold: text, then: opt(dm)}}`，必须是可观察信号 | | strategy_learning（S2） |
| `d.policy.strategy_weight` | `{dm: {option: u}}` | 初值取决策偏好 | strategy_learning（S3）；短期偏离回落 |
| `d.policy.learning_state` | `{dm: {recent_feedback: [ref:event], exploration: u, cumulative: text}}` | | strategy_learning（S3） |
| `d.policy.mechanism_preference_state` | `[{option: opt, strength: u, caused_by: ref:event, scope: text}]` | | strategy_learning；回落 |

〔工程〕"延误""重新协商"两个承诺阶段来自长期演化 skill §3.5，角色状态说明的阶段表里没有，需心理学组确认是否加入。

### 2.5 Memory

| 构念 | 字段 | 取值 | 维护方 |
|---|---|---|---|
| `m.entry` | 每条 | `{entry_id, summary: text, source_events: [ref:event], time, involved: [ref:role], channel: enum{亲见, 听说, 推断, 入场前}, importance: u, valence: v, interpretations: [{text, at: ref:event, superseded_by?}]}`；只追加，不删除；原条目与后来的解释分开 | memory_update |
| `m.accessibility` | 每条一个 `u` | 初值按重要性 | memory_update；回落 |
| `m.reactivation` | 每条 `[{event: ref:event, cue: text}]` | 次数由列表长度得出 | memory_update |

---

## 3. 需要确认的地方

| # | 内容 | 谁确认 |
|---|---|---|
| F1 | 本文标〔工程〕的结构：目标层级参数、归因风格拆法、信任四基础只记证据、表达基线各项的取值形式、承诺阶段是否加"延误""重新协商" | 心理学组 |
| F2 | 决策过程 `Judgment.output` 的结构：目前只对高影响过程有机制卡（变化规则 §4.1），其余过程的输出字段需要逐个列出；第一版只列启用的过程 | 心理学组 + 我们 |
| F3 | 时间相关字段（`duration_hint`、承诺 `deadline`、半衰期）的单位 | 环境组（场景时间） |
| F4 | 场景结构（规范、群体、制度权力）是否会以字段形式提供；不提供则 group / power 的客观部分由 S1 从文字推断或标 unknown | 环境组 |
| F5 | 第一版启用哪些模块与决策过程（`EnableConfig` 默认值） | 我们 + 心理学组 |

F1、F2、F5 的讨论见 [02](02-psychology-questions.md)，F3、F4 见 [03](03-environment-and-time.md)。确认后把本文移入 `docs/` 并标为"v1 冻结"。
