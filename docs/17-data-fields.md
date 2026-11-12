# 数据与字段

一个角色从输入文件到落盘，每一类数据保存什么、字段是什么、谁写、什么时候变。本文按**代码里实际存在的字段**写（`rolesim/interfaces.py`、`rolesim/state/slots.py`、`data/registry.json`）；还没冻结的目标结构在 [plan/05](../plan/05-field-spec-draft.md)，接口签名在 [14-interfaces.md](14-interfaces.md)。

## 0. 总览

| 类别 | 回答什么 | 存在哪里 | 谁写 | 什么时候变 |
|---|---|---|---|---|
| 输入：角色文件 / 档案 | 这个人是谁、带着什么目标和秘密进场 | `roles/<id>.json`、`profiles/<id>.json` | 作者 | 不在运行中变 |
| 输入：剧情文件 | 在哪、谁在场、大家都知道什么 | `scenes/<id>.json` | 作者 | 不在运行中变 |
| 稳定画像 `StableProfile` | 通常怎样反应、重视什么、能做什么 | 内存；落盘 `roles/<id>/profile.json` | `IProfileCompiler` | 一场互动里只读（C1） |
| 动态状态 `AgentState` | 此刻的情绪、信念、目标、关系、策略、记忆 | 内存；每次提交一个新快照；落盘 `final_state.json` 和 `phases.jsonl` | 只有 `IStateStore`（经 11 个算子） | 每个阶段提交一次 |
| 过程对象 | 这一个阶段里怎么看、怎么想、打算怎么做 | `phases.jsonl` | 阶段（模型）、算子、选值 | 每个阶段生成一次，之后只读 |
| 对外记录 | 实际说了什么、做了什么、给谁 | `records.jsonl` | 运行器（环境组） | 每次行动追加 |

三条贯穿的规则：未知（unknown，值为 null）不等于 0（C6）；状态的每次变化都引用本角色看得见的记录编号 `action_id#order_index`（C3）；稳定画像只读，动态状态只经算子写（C1、C2）。

---

## 1. 输入文件

### 1.1 角色文件 `roles/<id>.json`（format `role-1.0`）

| 字段 | 内容 |
|---|---|
| `persona` | 人物档案（persona schema 2.0）：`id`、`name`、`public_description`（别人能看到的描述）、`self_view`（角色怎么看自己）、`stable`（`identity`、`traits`、`values`、`core_goals`、`speech_style`、`mechanism_priors`、`module_status`）、`provenance`（每个模块的来源） |
| `skills` | 绑定的人格 skill：`[{name, priority, note}]` |
| `setup` | 开场设定：`goal`（这场互动的目标）、`disclose`（想让对方知道的，`{id, text, markers}`）、`conceal`（知道但不想说的，带 `strategy`：`omission` 不提 / `strategic_ambiguity` 说得模糊）、`recap`（前情，写成入场记忆） |

没有档案时，角色文件里能一一对应到构念的部分被转换成档案：P1 分面、依恋、施瓦兹价值、核心目标、表达基线、社会角色。示例 role1 编译后：42 个稳定构念里 7 个 active、33 个 unknown、2 个 disabled（P2、P3）。

### 1.2 档案 `profiles/<id>.json`（format `profile-1`，可选，有则优先）

| 字段 | 内容 |
|---|---|
| `id`、`name`、`summary`、`self_view` | 身份与自我描述 |
| `text` | 自然语言原文（可选；由模型读取，推断必须带逐字引文） |
| `stable` | `{construct_id: {value, source, basis, confidence, phrase}}`：按稳定构念 ID 写；`source` 为 explicit / inferred / model_inferred / default；`phrase` 是条件化语句 |
| `entry` | 入场状态：`{construct_id: 值}`，形状同第 3 节 |
| `provenance` | 谁写的、依据是什么 |

### 1.3 剧情文件 `scenes/<id>.json`（scene schema 3.0）

| 字段 | 内容 |
|---|---|
| `background` | 在场各方都知道的背景（写事实，不写"你要……"），作为触发事实传给每个角色 |
| `agents` | 在场者：角色座位 `{kind: "role", id, title}`（2—6 个，按顺序轮流）；可选一个环境 agent `{kind: "environment", world, rules}` |
| `max_turns` | 一共行动几次 |
| `allowed_actions` | 交流动作范围（只校验，现在不约束计划） |
| `world_private` | 世界私有数据，永不进入任何角色的模型调用 |

---

## 2. 稳定画像 `StableProfile`

整场只读，状态里只记它的快照 ID。

| 字段 | 内容 |
|---|---|
| `snapshot_id` | 画像快照的哈希 ID |
| `role_ref` | 角色库 ID |
| `modules` | `{construct_id: StableModule}`，42 个稳定构念各一个 |

每个 `StableModule`：

| 字段 | 内容 |
|---|---|
| `value` | 取值（形状因构念而异，例如 P1 是 `{分面: z}`，依恋是 `{anxiety, avoidance}`）；非 active 时为 null |
| `status` | active / unknown / inactive / disabled / not_modeled / not_applicable |
| `source_kind` | explicit（明写）/ inferred（按规则推断）/ model_inferred（模型推断）/ default（默认先验）/ unknown |
| `confidence` | 0—1 或 null |
| `needs_review` | 是否进待审核清单（非明写的高影响字段等） |
| `source_text` | 原文依据；model_inferred 必填 |
| `phrase` | 条件化语句（通常倾向、触发、压力下的变化、例外、别人能看到的信号）。提示里用它代替数值；不能自知的构念**只能**以它进入提示 |
| `reason` | 为什么 unknown / 关闭，或推断依据 |

42 个稳定构念（"自知"取自登记表；空着的是来源没写，按不能自知处理；★ 为高影响字段）：

| 模块 | 构念 | 自知 |
|---|---|---|
| 身份 | `s.identity.demographics` 人口学信息 ★ | 能 |
| | `s.identity.social_roles` 社会角色与制度位置 ★、`s.identity.resources` 资源与可观察表现 | — |
| | `s.identity.life_history` 生命历程 ★ | 部分 |
| 人格（三选一） | `s.traits.personality.p1_big_five` 大五 30 分面 ★、`.p2_16pf` ★、`.p3_jung_hexaco` ★ | 部分 |
| 依恋 | `s.traits.attachment.global` 焦虑 / 回避两轴 ★ | 部分 |
| 动机基线 | `s.traits.motivation.source_activation` ★、`.need_vector` ★、`.goal_computation` ★ | — |
| | `.conflict_regulation` ★ | 不能 |
| | `.self_efficacy` ★ | 能 |
| 情感能力 | `s.traits.affect.emotion_baseline`、`.appraisal_bias`、`.regulation_capacity` ★、`.empathy` ★、`.emotional_intelligence`、`.expression` | — |
| 社会认知 | `s.traits.social_cognition_71d` 71 维社会心智能力 ★ | 不能 |
| 认知风格 | `s.traits.cognitive.dual_process`、`.need_for_cognition`、`.need_for_closure`、`.ambiguity_tolerance`、`.flexibility`、`.metacognitive_calibration` | 部分 |
| 发展与自我 | `s.traits.development.position` 自我发展位置 ★ | 不能 |
| | `s.traits.self.self_concept`、`.self_esteem` ★、`.self_discrepancy`、`.identity_status` ★、`.role_identity`、`.social_identity` | — |
| 人格功能 | `s.traits.function.domains` ★、`.pathology_traits` ★、`.organization_label` ★ | 不能 |
| 决策偏好 | `s.traits.decision_preferences` ★ | 部分 |
| 价值 | `s.values.schwartz_priorities` 施瓦兹 19 项 ★、`.moral_reasoning` ★、`.cultural_orientation` ★ | 部分 |
| 核心目标 | `s.core_goals.primary` 1—3 个主核心目标 ★ | 能 |
| 表达 | `s.speech_style.baseline` 表达基线 | 部分 |

稳定画像在运行中起作用的地方：自知层进阶段提示；`IMagnitude` 读 P1 分面、依恋焦虑、认知灵活性来移档；入场时情绪基线、自尊基线、核心目标、71 维两项决定动态状态的初值。

---

## 3. 动态状态 `AgentState`

每次提交产生一个新快照，前后用 `prev_state_id` 串成链。

| 字段 | 内容 |
|---|---|
| `state_id` | 入场为 `{角色ID}@entry`，之后 `{角色ID}@{事件}#{序号}.{类型}` |
| `prev_state_id` | 上一个快照 |
| `stable_snapshot_id` | 指向稳定画像（不内嵌副本） |
| `dynamic` | `{construct_id: 值}`，42 个动态构念 |
| `memory` | `{construct_id: 值}`，3 个记忆构念 |
| `versions` | 四类版本号：`construct_definition_version`、`update_rule_version`（两份心理学原文的哈希）、`state_schema_version`（`construct-state-1`）、`evaluation_protocol_version`（尚未登记） |
| `status` | 每个构念（或 `构念/键` 的一个实例）的状态标记 |

**怎么读下面的表。** 一个状态值的地址是 `(构念, 键, 字段)`。"键"是构念下面的实例层级，例如信任按对象分，键是对方的角色 ID：`d.relation.trust → {role2: {value: 0.55}}`。"字段"是一个实例里的项。取值记法：`u` 0—1、`v` −1—1、`z` −2—2、`grade` 1—4、`text`、`refs`（记录编号列表）、`opt`（登记的决策过程选项）、`enum(...)`；`?` 表示可为 null。"有档"的字段一次变化受幅度档约束（模型只在区间内选值）。"现在写"一列：● 运行中由已实现的算子写入；○ 只在入场时取初值；空白表示这一版不写（算子没有规则，或要等场景结构 / 时间单位）。

### 3.1 情绪状态（emotion_update）

| 构念 | 键 | 字段 | 有档 | 现在写 |
|---|---|---|---|---|
| `d.affect.emotion` 当前情绪 | — | `label`（情绪家族列表：喜悦、信任、恐惧、惊讶、悲伤、厌恶、愤怒、期待、轻蔑）、`valence:v`、`arousal:u`、`intensity:u`、`duration_hint`、`confidence:u`、`attribution`、`norm_evaluation`、`object`；`dominance` 未建模 | valence（×2）、arousal、intensity | ● |
| `d.affect.stress` 压力水平 | — | `level:u` | level | ● |
| `d.affect.threat_reward_activation` 威胁与奖赏激活 | — | `bas`、`bis`、`fffs`（都是 u） | 三项 | ○ |
| `d.affect.regulation_state` 调节状态 | — | `items`（列表） | — | |

### 3.2 认知状态

| 构念 | 键 | 字段 | 有档 | 写入算子 | 现在写 |
|---|---|---|---|---|---|
| `d.cognition.belief` 命题信念 | belief_id | `proposition`、`kind`（fact / interpretation）、`about:refs`、`context`、`time`、`confidence:u?`、`conflict_group`、`adopted`、`isolated`、`superseded_by`、`evidence`；只追加 | confidence | belief_revision | ● |
| `d.cognition.attribution` 归因与责任判断 | attribution_id | `event`、`about`、`locus`、`stability`、`globality`、`controllability`、`intent`、`responsibility:map`、`confidence:u` | — | belief_revision | ● |
| `d.cognition.attention` 注意焦点与忽略项 | — | `focus`、`neglect` | — | belief_revision | |
| `d.cognition.other_model` 他人模型 | 对方/inference_id | `inference`、`kind`（goal / belief / intention / emotion / ability / constraint）、`order`（几阶）、`confidence:u?`、`evidence`、`hypothesis_ref`、`superseded_by`；只追加 | — | other_model_update | ● |
| `d.cognition.tom_level` 心理理论可用层级 | — | `ceiling`、`current`、`per_object.*`、`allowed_tasks`、`downgrade_reason` | — | other_model_update | ○ |
| `d.cognition.self_evaluation` 当前自我评价 | — | `global:z`、`domains.*:z` | 两项（×4） | self_state_update | ○（有自尊基线时） |
| `d.cognition.task_self_efficacy` 任务自我效能 | task | `level:u`、`efficacy_domain` | — | self_state_update | |
| `d.cognition.shame` 羞耻负荷 | — | `load:u` | load | self_state_update | ○ |
| `d.cognition.face_cost` 面子成本 | — | `damage:u`、`maintenance_need:u`、`audience:refs` | 前两项 | self_state_update | ○ |
| `d.cognition.self_responsibility` 自我责任评价 | entry_id | `event`、`degree:u`、`type`（factual / moral / relational / institutional）、`mechanism` | — | self_state_update | |

### 3.3 目标状态（goal_update，这一版没有规则）

| 构念 | 键 | 字段 | 现在写 |
|---|---|---|---|
| `d.goal.goals_now` 当前目标 | goal_id | `content`、`priority:u`、`stage`（进行中 / 完成 / 受阻 / 降级 / 暂停 / 放弃）、`core_goal`、`source`、`reason`；只追加 | ○（开场设定的目标 `g-setup`、bootstrap 的 `g-entry`） |
| `d.goal.value_activation` 价值激活 | value | 值 u | |
| `d.goal.goal_activation` 目标激活 | core_goal | 值 u | |
| `d.goal.goal_commitment` 动态目标承诺 | goal | 值 u | ○（取核心目标优先级） |
| `d.goal.feasibility_estimate` 可行性估计 | goal | 值 u? | |
| `d.goal.threat_to_goal` 目标威胁 | goal | 值 u | |
| `d.goal.goal_conflict` 目标冲突 | conflict_id | `involved`、`order`、`non_negotiable`、`acceptable_compensation`、`resolution` | |
| `d.goal.action_orientation` 行动指向 | — | `direction`、`short_term_goal`、`toward` | |
| `d.goal.revision_candidate` 目标修订候选 | candidate_id | `core_goal`、`proposal`（降级 / 替换 / 合并 / 分裂）、`content`、`evidence`、`independent_evidence`、`window_start`、`self_commitment`、`review`；只追加 | |

### 3.4 关系状态（relation_update，只在 S3）

| 构念 | 键 | 字段 | 有档 | 现在写 |
|---|---|---|---|---|
| `d.relation.trust` 信任 | 对方 | `value:u`、`evidence_by_basis`（按能力 / 善意 / 诚信 / 可靠记证据） | value | ● |
| `d.relation.closeness` 亲近 | 对方 | `value:u` | value | |
| `d.relation.dependence` 依赖 | 对方 | `value:u` | value | |
| `d.relation.commitment` 关系承诺 | 对方 | `value:u` | value | |
| `d.relation.harm_history` 伤害历史 | 对方/harm_id | `event`、`description`、`severity:grade`、`acknowledged`（是 / 部分 / 否 / 不明）、`repair`（无 / 部分 / 完全）、`last_activated`、`salience:u`、`memory`；只追加，事实字段写后不改 | — | |
| `d.relation.power_asymmetry` 权力差异 | 对方 | `objective`、`subjective.direction`、`subjective.bases`、`subjective.confidence`、`note` | — | |

关系状态入场时不预设：与某个角色的关系在第一次有信任判断时才建立，原值未知时由选值调用在 0—1 里取首次值（下面例子里的 0.55，是 mock 取范围中点 0.5 后又按小档上调了一次）。

### 3.5 群体状态（group_state_update，这一版没有规则，要等场景结构）

`d.group.membership`（按群体：`degree`、`position`、`status`）、`d.group.identity_salience`（按身份：值 u）、`d.group.norm_salience`（按规范：`type` 描述性 / 命令性、`source`、`salience`，有档）、`d.group.alliance`（`allies`、`opponents`、`stability`、`basis`）、`d.group.boundary`（按群体：`us`、`them`、`clarity`）、`d.group.responsibility_allocation`（按情境：`self`、`others.*`）。

### 3.6 策略与承诺状态

| 构念 | 键 | 字段 | 写入算子 | 现在写 |
|---|---|---|---|---|
| `d.policy.action_commitment` 行动承诺 | commitment_id | `content`、`to`、`formed_at`、`deadline`、`stage`（形成 / 维持 / 兑现 / 违约 / 暂停 / 撤销）、`public`、`means`、`verification`；只追加 | commitment_update | ●（S2 形成；S3 改阶段） |
| `d.policy.current_strategy` 当前策略 | 决策过程/对象 | 值：该过程的一个登记选项 | strategy_learning | ●（S2） |
| `d.policy.fallback_strategy` 备用策略 | 决策过程/对象 | 值：登记选项 | strategy_learning | |
| `d.policy.switch_condition` 切换条件 | 决策过程/对象 | `signal`、`threshold`、`then` | strategy_learning | |
| `d.policy.strategy_weight` 策略权重 | 决策过程 | `{选项: u}`（有档） | strategy_learning | |
| `d.policy.learning_state` 学习状态 | 决策过程 | `recent_feedback`、`exploration`、`cumulative` | strategy_learning | |
| `d.policy.mechanism_preference_state` 短期决策偏好 | entry_id | `option`、`strength`、`caused_by`、`scope` | strategy_learning | |

### 3.7 记忆（memory_update）

| 构念 | 键 | 字段 | 现在写 |
|---|---|---|---|
| `m.entry` 记忆条目 | entry_id | `summary`、`source_events:refs`、`time`、`involved`、`channel`（亲见 / 听说 / 推断 / 入场前）、`importance:u`、`valence:v`、`interpretations`；只追加，原条目写后不改，后来的解释另加在 `interpretations` | ●（入场：前情与生命经历；S3：反馈） |
| `m.accessibility` 记忆可及性 | entry_id | 值 u | |
| `m.reactivation` 记忆再激活 | entry_id | 列表；只追加 | |

阶段开始时，证据门按字面相似度 × 重要性 × 可及性取回至多 4 条 `m.entry`。

### 3.8 例子：林澄跑完一场后的状态（mock，节选）

```json
{"state_id": "role1@plan#14.plan", "prev_state_id": "role1@t006-role2#13.feedback", "stable_snapshot_id": "ffa5c3b6…",
 "dynamic": {
   "d.affect.emotion": {"label": ["期待"], "intensity": 0.35, "arousal": 0.8, "valence": 0.975},
   "d.relation.trust": {"role2": {"value": 0.55}},
   "d.cognition.other_model": {"role2": {"i-t001-env1-1": {"inference": "对方在了解情况", "kind": "intention", "order": 1, "confidence": 0.5, "evidence": ["t001-env1#0"]}}},
   "d.goal.goals_now": {"g-setup": {"content": "只承诺能核实的部分，同时不让合作关系破裂。", "priority": 0.5, "stage": "进行中"}},
   "d.policy.action_commitment": {"c-plan-2": {"content": "核对后给出确切日期", "to": "role2", "stage": "形成", "public": true}},
   "d.policy.current_strategy": {"dm.conflict.conflict_mode": {"role2": "合作"}}},
 "memory": {"m.entry": {"recap:r1": {"summary": "上周例会上，合作方承诺本周一前补发数据说明；你答应收到说明后三天内交付全部数据。", "channel": "入场前"}}},
 "status": {"d.affect.emotion": "active", "d.relation.closeness": "unknown", "...": "..."}}
```

mock 的数值只说明流程，不是心理学证据。

---

## 4. 过程对象：一个阶段里产生什么

每个阶段（S1 / S2 / S3）生成一次，提交后只读，后面的阶段不能改（C5）。

### 4.1 阶段与证据

| 类型 | 字段 |
|---|---|
| `Phase` | `event_id`（记录组的 action_id，计划为 `plan:<request_id>`）、`phase_no`、`kind`（event → S1、plan → S2、feedback → S3） |
| `EvidenceRef` | `event`（记录编号）、`channel`（witnessed 亲见 / hearsay 听说 / inferred 推断 / own_action 本人行动 / pre_entry 入场前）、`claim`（听说时转述的命题） |
| `EvidenceSet` | `included`（可用的证据）、`excluded`（`{event, reason}`：other_thought、own_thought、not_recipient）、`retrieved_memories`（取回的记忆） |

### 4.2 模型给出的：产物与判断

**S1 事件建模 `SituationInterpretation`**

| 字段 | 内容 |
|---|---|
| `situation_definition` | 这件事是什么 |
| `appraisal` | `[{goal, kind（gain / loss / threat / challenge）, detail}]`：哪个目标受影响、怎样受影响 |
| `attribution` | 一层的归因摘要 |
| `hypotheses` | 1—3 个假设：`statement`、`confidence`、`support` / `counter`（支持与反向的记录编号）、`discriminator`（什么能区分）、`about`（关于谁）、`kind`（目标 / 信念 / 意图 / 情绪 / 能力 / 约束）。带 `about` 的假设变成他人模型 |
| `judgment_state` | 定论 / 多假设 / 暂缓 |
| `information_needs` | 还需要知道什么 |
| `relation_signals` | `{对方: 信号}` |

S1 另给 `cues`（不确定、压力、承诺、修复、冲突五种线索，0—1，只用来激活人格 skill）和 `evidence_notes`（把某条记录标为转述或推断）。

**S2 行动计划 `ActionPlan`**

| 字段 | 内容 |
|---|---|
| `candidates` | `[{action, kept, constraint}]`：候选，含被筛掉的及原因 |
| `chosen` | 选中的行动 |
| `fallbacks`、`switch_conditions` | 备选；`[{signal, then}]`：看到什么信号就改做什么 |
| `depends_on`、`expected_outcomes` | 依赖的假设；预期结果 |
| `commitment` | `{formed, content}`：是否形成承诺 |
| `expression` | `{goal, audience, manner, private}`：表达目标、对谁说、怎么说、是否只给受众听 |

**S3 长期演化 `EvolutionResult`**

| 字段 | 内容 |
|---|---|
| `expected_vs_actual` | 预期与实际的差距 |
| `commitment_rulings` | `[{commitment, ruling（kept / broken / pending / released）}]` |
| `relation_course` | `[{partner, course（improving / stable / worsening / rupture）, repair_conditions}]` |
| `learning_attribution` | 学到了什么、归因于什么 |
| `carry_forward` | 下次观察什么 |

**判断 `Judgment`**（每个阶段至多 6 条，只在 `profile_state_mechanisms` 模式）：`dm_id`、`option`（登记选项之一或 null）、`output`、`evidence`、`hands_to`（交给哪些算子，由登记表填）。有机制卡的 6 个过程的 `output` 字段见 [14-interfaces.md](14-interfaces.md) 第 3.2 节，例如评价的 `{grade, emotions, valence, about}`、信任评估的 `{about, dimension, direction, grade}`。

### 4.3 程序给出的：变化槽位与提交

| 类型 | 字段 |
|---|---|
| `ChangeSlot` | `slot_id`、`construct_id`、`key`、`field`（地址）、`before`、`direction`、`grade`、`band`（`{name, low, high, extended}`，选值前算好）、`value_range`（原值未知时的取值范围）、`content`（算子组装好的记录）、`evidence`、`update_operator`、`decision_mechanisms`、`stage`、`group`、`basis`（依据规则卡哪一条） |
| `StateChange` | `construct_id`、`key`、`field`、`before`、`after`、`evidence`、`update_operator`、`decision_mechanisms`、`stage`、`band`、`group`、`reason`（模型选值的理由，或规则说明）、`flag`（软异常）、`grade` |
| `Skipped` | `update_operator`、`reason`（not_implemented、no_room、conflict_unresolved、unknown_belief、no_partner、unknown_commitment……）、`detail` |
| `RejectedChange` | `change`（变化或没拿到值的槽位）、`group`、`error`（`{kind, code, detail}`，例如 over_band、no_value） |
| `CommitResult` | `output_state_id`、`accepted`、`rejected`、`soft_flags`、`skipped` |

---

## 5. 对外记录

和环境组之间只交换这两样（完整字段见 [13-agent-interface.md](13-agent-interface.md) 第 2.1 节）：

| 类型 | 字段 |
|---|---|
| `InteractionRecord` | `action_id`、`turn_index`、`order_index`、`actor {actor_id, name}`、`kind`（thought / speech / action）、`content`、`recipients {角色ID: {name}}` |
| `AgentActionContent` | `thought`、`speech`、`action` 各为 `{content, recipient_ids}` 或 null；`order` |

内部状态、画像、过程对象都不出接口。

---

## 6. 落盘：运行目录

| 文件 | 顶层字段 |
|---|---|
| `manifest.json` | `schema_version`、`interface`、`pipeline`、`status`、`started_at` / `finished_at`、`backend`、`model`、`mode`、`use_skills`、`registry_sources`、`code_version`、`scene_id`、`scenario_hash`、`cast`、`environment`、`origin`、`evidence_boundary`、`turns`、`records`、`calls`、`interaction_id`、`roles`（各角色的最终状态 ID 与画像快照）、`model_calls`、`audit_passed`；真实模型另有 `decoding`、`trailing_dropped` |
| `records.jsonl` | 每行一条 `InteractionRecord` |
| `calls.jsonl` | 每次 initialize / act / 环境调用的完整输入与输出 |
| `scene.json` | 剧情文件原样 |
| `roles/<id>/profile.json` | `snapshot_id`、`role_ref`、`modules`（每个 `StableModule`）、`review`（待审核项）、`entry_notes`（每个入场值的来源） |
| `roles/<id>/setup.json` | 开场设定与绑定的 skill |
| `roles/<id>/final_state.json` | 最后一个 `AgentState` |
| `trace/<互动>/<id>/phases.jsonl` | 每次提交一行 `PhaseLog`：`interaction_id`、`character_id`、`event_id`、`phase_no`、`input_state_id`、`output_state_id`、`evidence`、`judgments`、`products`、`changes`、`commit`、`versions` |
| `trace/<互动>/<id>/model_calls.jsonl` | 每次模型调用一行：`interaction_id`、`character_id`、`stage`（S1 / S2 / S3 / values / express / profile）、`input`（`self_id`、`present`、`background`、`self_profile`、`state_text`、`records`、`upstream`、`task`）、`raw_output`、`reasoning` |
| `audit_report.*`、`transcript.md` | 自动审计；可读版 |

从 `phases.jsonl` 的 `input_state_id → output_state_id` 和每条 `changes` 能完整重建状态链：每个值什么时候、因为哪条记录、经哪个判断和算子、在哪个区间里、以什么理由变成现在这样。
