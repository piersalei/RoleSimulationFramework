# 下一步改动计划：多 agent 剧情与"状态—机制"闭环

2026-10-20 写计划；**同日晚 F1–F4、E1–E3 已实现**（E4 模型驱动的环境 agent 未做），实现记录见第 7 节。起因是对 [07-architecture.md](../docs/07-architecture.md) 所述现状提出的四点意见。第 0–4 节保留为计划原文，落地后的行为以 [docs/06-runtime-flow.md](../docs/06-runtime-flow.md) 为准。

**已确认的决定（2026-10-20）：** ① 感知评价（appraise）也读取状态，副作用与防护见第 2 节；② 状态的等级词先分 3 档（低 / 中 / 高），按工程假设使用。

## 0. 现状（为什么要改）

1. **只能两个角色。** `runtime.SEATS = 2`，`agent.PARTNER = "partner"`，`mechanisms.select_cards` 和 `agent.observe` 里写死 `relation_state.partner.trust`。环境 `DialogueEnvironment` 只会开场、轮流转发台词，没有"天气、时间流逝"这类环境本身的行为。
2. **状态没有进入表达。** `Agent._express_payload` 只传角色自知、风格约束、已选动作类型、可见历史。同一个动作类型（如 `set_boundary`）无论角色此刻紧张还是放松，表达模型看到的输入一样。决策提示里虽有 `current_state`，但 `providers.ChatBackend.decide` 的 system 提示没有说明它是什么、怎么用，且感知评价（appraise）完全不看状态。
3. **机制没有融入状态更新。** 5 张机制卡（`mechanisms.CARDS`）只决定"倾向哪些动作"。真正改状态的两条公式（紧张、信任）硬编码在 `agent.observe` 里，与机制卡无关；`mechanisms.CARD_MECHANISMS` 只是文档性映射，没有任何代码引用。
4. **状态变化不决定选哪张机制卡。** `select_cards` 的触发条件只有"当前事件的线索强度 ≥ 阈值"，状态（紧张、信任）只在分数里占 0.2 权重；角色先验 `mechanism_priors` 占 0.3。

## 1. 改动一：按剧本设定数量可变的多个 agent，含环境 agent

### 目标

- 剧本（scene 文件）决定有几个 agent、各是什么类型；数量不再固定为 2。
- agent 分两类：
  - **角色 agent**：现有的 `Agent`，有人格、心理状态、记忆，输出台词。
  - **环境 agent**：不是人，没有心理状态，管理**世界状态**（时间、天气等），用事件把世界变化告诉角色。
- 原则不变：环境 agent 不替角色说话、不替角色决定动作的结果、不写角色内心；角色只提出尝试，环境裁决发生了什么。

### 设计要点

1. **scene schema 3.0**（旧的 2.0 被拒绝，不静默转换）：`seats` 改为 `agents`，每项 `{id, kind: "role" | "environment", title}`；角色数量不限（先设上限，例如 6）；环境 agent 额外带 `world`（初始世界状态，如 `time`、`weather`）和 `rules`（如每轮推进多少分钟、天气变化表）。
2. **世界状态与心理状态分开存。** 世界状态归环境 agent 所有，只通过事件让角色知道；角色的 `dynamic` 里不放世界变量。
3. **调度器。** 把 `DialogueEnvironment.turns()` 的"轮流"抽成可替换的调度器：默认每一轮先让环境 agent `tick`，再按剧本给出的顺序让各角色发言；顺序策略的选择见第 5 节。
4. **环境 agent 的两种实现**（先做前者）：
   - 规则驱动：按 `rules` 确定性地推进时间、改变天气，产出 `narration` 事件；可重复、可审计。
   - 模型驱动：由模型提出世界变化，程序校验只能改它被声明可控的变量、不得产生角色台词或替角色决定结果。
5. **事件可见性。** 现在 `audience` 总是全体。多 agent 后需要公开 / 定向（只给被点名者）两种；时间和天气事件默认公开。
6. **角色侧去掉"唯一对方"假设。** `partner` / `partner_id` 改为"其他在场者"列表；`relation_state` 按每个其他角色各存一份（`state.new_dynamic(partner_ids)` 与 `add_partner` 已支持多个对象，是现成的基础）；记忆里的称呼表改成 N 方；`group_state` 在三人以上才可能启用，更新规则要等心理学组给。
7. **时间与环境事件如何影响心理。** 时间推进事件接 `decay_or_recovery`（紧张回落等）；天气等环境线索如果要影响情绪，必须通过"线索 → 机制卡"的路径，不得由环境 agent 直接写角色状态（见改动二）。
8. **合同。** 环境 agent 是我们本地运行环境里的一部分，复用合同 v0 的 Event 类型（`narration`、`outcome`）。`Event.time`、`interrupt` 等字段仍属 [03](03-timeline-and-interruption.md) 的下一版合同提案，本阶段不依赖它们。

### 分步

| 步 | 内容 | 验收 |
|---|---|---|
| E1 | scene 3.0 + 角色数量可变（先不要环境 agent）：去掉 `SEATS`、`PARTNER`；调度器接口；N 方称呼表 | 3 个角色的 mock 运行跑通；2 角色旧示例改成 3.0 后输出与现在等价 |
| E2 | 规则驱动的环境 agent：世界状态、`tick`、时间与天气事件、定向可见性 | 世界状态逐轮落盘；事件对角色可见性正确；环境 agent 不能产出 `speech` |
| E3 | 审计 / 评审包 / `readable.py` 推广到 N 个角色（"对方真值"改成"其他在场者"；跨角色隔离检查对每一对角色成立） | 现有 7 项审计在 3 角色运行上全部可算；环境 agent 的事件进入审计 |
| E4 | 模型驱动的环境 agent（可选）：校验器限制可改变量 | 越权输出被拒绝并记录 |

### 影响到的文件

`runtime.py`（环境、调度、manifest）、`roles.py`（`fit_cast`、`seat`）、`agent.py`（`PARTNER`、称呼表、`initial_state`）、`mechanisms.py`（`relation_state.partner`）、`state.py`（relation 键）、`audit.py`、`package.py`、`readable.py`、`__main__.py`、`scenes/*.json`、`scripts/bootstrap_examples.py`、对应测试。

## 2. 改动二：状态进入模型输入（含表达）

### 目标

模型在**三个调用点**都能读到"这个角色此刻处于什么状态"，而不是只有决策提示里一份没有说明的原始 JSON。

### 做法

1. **新增 `state_view`（状态的可读摘要）。** 由程序从 `dynamic` 生成：只列 `active` 的字段；`unknown` 写"未知"、`not_modeled` 不列；数值转成**三档等级词：低 / 中 / 高**，按 [0, 1/3)、[1/3, 2/3)、[2/3, 1] 等分（取值 −1 到 1 的字段同样三等分）；再附"本轮变化"（哪个字段从哪一档变到哪一档、由哪个机制引起）。分档是工程假设，须标注，只在生成摘要时计算，**不写进状态本身**。例：原型初始值紧张 0.15 → 低，信任 0.55 → 中。
   - 注意区分：这里的"等级词三档"是状态的显示粒度，与 `--mode` 的三个消融档位无关。
2. **三个调用点各注入什么：**
   - 决策（decide）：`state_view`（等级词 + 数值）+ system 提示增加"如何使用状态"的说明（状态影响倾向，不是命令）。
   - 表达（express）：`state_view` 中与说话方式相关的部分（情绪强度、信任）**只给等级词，不给数值** + 本轮变化。表达提示里仍**不出现**机制名、分数名和隐瞒原文。
   - 感知评价（appraise）：**也读状态**（已确认），只给等级词；system 提示明确"状态只影响感受的强弱，不能凭空产生事件里没有依据的线索"。副作用与防护见下。
3. **消融档位语义随之调整：** `profile_only` 三处都不给状态；`profile_state` 给状态摘要；`profile_state_mechanisms` 在此基础上再给机制卡倾向。**因为 appraise 读状态，状态轨迹本身在不同档位下不再相同**（见下"副作用 3"），这条要写进评测说明。
4. **mock 后端同步**：mock 的 `decide` / `express` 要能响应状态（例如紧张偏高时措辞不同），否则离线无法验证这条通路。

### appraise 读状态：副作用与防护

好处：更接近"情绪影响感知"（紧张的人更容易把中性的话听成压力）。代价如下。

| # | 副作用 | 为什么 | 防护 |
|---|---|---|---|
| 1 | **正反馈、情绪螺旋** | 紧张高 → 评价出更多压力 → 紧张更高；8B 小模型尤其容易顺着"你现在很紧张"去评价 | system 提示限定状态只调强弱；`observe` 的 0–1 范围限制与恢复率保持不变；加测试：连续中性输入下紧张必须回落，连续高压下不得单调冲到上限而无回落 |
| 2 | **凭空产生线索** | 模型可能为"符合当前情绪"而编造事件里没有的压力 | `reason` 必须引用对方原话中的依据；审计抽查"有线索但对方话中没有对应内容"；必要时只允许状态把已有线索的强度上下浮动，不允许新增线索种类（此为备选，先用提示约束） |
| 3 | **消融对比被混淆** | 现在"状态在三档下都计算，只有提示不同"是成立的；appraise 读状态后，`profile_only` 里 appraise 不读状态，状态轨迹因此和另两档不同，比较的就不只是"提示里有没有状态" | 在评测说明里写明：三档的状态轨迹不可直接对比；对比以外显行为为主；若需要干净对照，另设"appraise 不读状态"的开关（`--appraise-state off`）作为对照 |
| 4 | **双重计数** | 紧张公式里本来就有"旧紧张 × (1−恢复率)"，状态已通过惯性影响下一步；appraise 再读状态会让同一份状态影响两次 | 记录在案；校准阶段由心理学组决定是否保留其一；在日志里记下 appraise 输入含状态，便于事后拆开 |
| 5 | **归因更难** | 一条状态变化 ← 线索 ← 受状态影响的评价，因果链变长 | `audit.jsonl` 的 appraisal 输入里保留当时的 `state_view`；归因审计（F4）检查时一并读它 |
| 6 | **提示变长、与三档消融的"appraise 提示相同"约定冲突** | 现在 07 文档写明 appraise 提示在三档里完全相同 | 同步修改 07 的相关表述与 `audit.py` 里依赖该约定的检查 |

### 要检查的风险

- 状态摘要里的数字不能泄露给对方：对方永远看不到本角色的状态（现有隔离不变），审计的"跨角色隔离"检查要覆盖 `state_view`。
- 表达提示新增内容后，"不说出机制、心理分数"的约束要补测试（台词里不应出现"紧张值 0.62"之类）。

## 3. 改动三：机制融入状态更新（触发条件 / 改哪个状态 / 怎么改）

### 目标

一张机制卡不仅说"倾向哪些动作"，还必须写清三件事：**什么情况下触发、触发后改哪个状态、怎么改**。状态更新由触发的机制卡产生，不再硬编码在 `observe` 里；状态变化反过来决定下一次触发哪些机制。

### 机制卡新结构（草案）

```
card:
  id, source_ref, status: "uncalibrated_engineering_hypothesis"
  trigger:                      # 什么情况下触发
    any_of / all_of:
      - cue:   {name: pressure, op: ">=", value: 0.3}        # 来自本轮感知评价
      - state: {path: dynamic.affect_state.emotion.intensity, op: ">=", value: 0.6}   # 来自当前状态
      - delta: {path: dynamic.relation_state.*.trust, op: "<=", value: -0.05}         # 来自上一轮的状态变化
  effects:                      # 触发后改哪个状态、怎么改
    - operator: emotion_update
      path: dynamic.affect_state.emotion.intensity
      rule: {kind: formula, expr: "old*(1-recovery) + k*pressure*sensitivity", params: {k: 0.35}}
      group: affect
  action_bias:                  # 现有部分：倾向哪些动作
    actions: [...], modulator: ...
  priority, evidence_format
```

### 做法

1. **把现有两条公式搬进卡里。** 紧张更新 → `appraise` 卡的 effect（含 `decay_or_recovery` 分支）；信任更新 → `trust` 卡的 effect。数值沿用现在的 0.35、0.15，标 `uncalibrated`，不新编数值。
2. **没有规则的卡不编造 effect。** 其余卡（`verify`、`repair`、`boundary`）先只有触发条件和 `action_bias`，`effects` 为空，并在日志里显式标"无状态更新规则"，等心理学组交付后再补。
3. **`observe` 改为：** 感知评价得到线索 → 找出触发的卡（线索 / 状态 / 状态变化三类条件）→ 执行各卡的 effects 生成候选 → 仍走 `commit_phase`（分组、原子提交、硬错软错）→ 写 `mechanism_log`。
4. **选卡也读状态：** `select_cards` 改用同一套触发判断，状态条件和状态变化条件真正能触发或抑制卡片；人格先验 `mechanism_priors` 只调权重，不再是唯一的"谁的选择"。
5. **防循环：** 同一事件阶段内只做一次"触发 → 更新"，更新后的状态要到下一个阶段才能触发新的卡（与现有"同阶段不读未提交候选"一致）。
6. **日志与审计：** `mechanism_log` 每条候选记录 `card_id` 与"哪个条件触发"；新增自动审计项"每条状态变化都能追溯到触发的卡，且当时触发条件成立"。
7. **版本：** `update_rule_version` 升为 `engineering-v2`；`mechanism_hash` 变化；`CARD_MECHANISMS` 删除（并入卡的 `effects`）。示例角色档案的 `mechanism_priors` 必须与卡 id 一致，若卡 id 变动要重建 `roles/`。

### 分步

| 步 | 内容 | 验收 |
|---|---|---|
| F1 | `state_view`（3 档）生成 + appraise / decide / express 注入 + 三处 system 提示补说明 + mock 响应状态 | 同一动作、不同状态下表达提示不同；表达与评价提示不含机制名与分数名；中性输入下紧张回落、高压下不失控的测试；审计里依赖"appraise 提示三档相同"的检查与 07 文档同步修改；既有测试通过 |
| F2 | 机制卡 schema v2 + 触发判断（线索 / 状态 / 状态变化）+ 状态条件参与选卡 | 状态达到阈值时对应卡被选中，未达到时不选；单元测试覆盖每类条件的正反例 |
| F3 | 两条公式迁入卡的 `effects`，`observe` 改为由卡驱动 | 在同样输入下，状态轨迹与改动前逐位一致（数值回归）；`mechanism_log` 带 `card_id` 与触发原因 |
| F4 | 审计新增归因检查；文档同步（07、01、02） | 新审计项对人为篡改的日志能报错 |

F1 与 F2–F4 互相独立，可先做 F1。

## 4. 建议顺序

1. F1（改动小、见效快，先让模型读到状态）
2. F2–F4（机制卡 schema，之后多 agent 里的环境线索才有地方接）
3. E1–E3（多 agent、环境 agent）
4. E4（可选）

理由：环境 agent 带来的天气、时间线索，最终要通过"线索 / 状态 → 机制卡 → 状态更新"这条路径影响角色，所以机制卡 schema 应先稳定。

## 5. 开工前需要作者确认

已确认（不再待定）：appraise 读状态；状态等级词分 3 档。

> 开工时下面 7 项按工程默认处理（2026-10-20 晚），均可改，改动点见第 7 节：① 保留 `--appraise-state off`；② 固定轮转，按剧情里的座位顺序，只支持依次发言；③ 只做规则驱动的环境 agent，世界变量限 `time` / `weather` / `place` / `noise`；④ 角色上限 6 个；⑤ 三档按取值范围等分；⑥ 沿用占位数值并标 `uncalibrated_engineering_hypothesis`；⑦ 尚未通知环境组，需要同步一次。

另有需要心理学组裁定、会影响 F1–F3 做法的问题（人格在评价和紧张公式里重复计算、线索是客观强度还是本人感受、对话中什么能改变信任等），见 [06-psychology-decisions.md](06-psychology-decisions.md)。

1. **是否保留 `--appraise-state off` 对照开关？** appraise 读状态会让三档消融的状态轨迹不可直接对比（第 2 节副作用 3）；开关能给出干净对照，代价是多一个选项和多一组运行。建议保留。
2. **多角色发言顺序**：固定轮转、剧本指定顺序，还是"被点名者先答"？是否允许一轮多人同时发言（合同 v0 是依次行动）。
3. **环境 agent 先做规则驱动，模型驱动是否需要？** 以及世界变量的清单（时间、天气之外是否还有地点、噪声等）。
4. **角色数量上限**（影响调用成本：每个角色每轮 3 次模型调用）。
5. **三档的分界点**：先用等分（1/3、2/3）。是否要请心理学组按各字段给不同分界（例如紧张和信任的"高"不一定都在 0.67），可以等校准阶段再说。
6. **机制卡的状态触发阈值与 effects 数值**：先沿用现有占位值并标 `uncalibrated`，还是等心理学组给数值再做 F3。
7. **环境 agent 的事件形式**需要告知环境组吗？它复用 v0 的 `narration` / `outcome`，不改合同字段，但"环境本身有 agent"这一点可能与环境组自己的实现重叠，建议至少同步一次。

## 6. 不做的事

- 不让环境 agent 直接写角色的心理状态；世界变化只能经事件进入角色，再由机制卡转成状态变化。
- 不让环境 agent 替角色说话，或编造角色没有做过的行动结果。
- 不为没有心理学依据的卡编造 effect 数值。
- 不把状态数值或机制名放进表达提示里让角色"念出来"。
- 不静默转换旧版 scene / 机制卡格式。

## 7. 实现记录（2026-10-20 晚）

| 步 | 做了什么 | 验收结果 |
|---|---|---|
| F1 | `stateview.state_view`：active 且有值的字段换成三档文字，自上次发言以来的变化合并为每字段一条净变化。评价提示：三档文字（`profile_state` 及以上，`--appraise-state off` 时不给）；决策提示：三档 + 数值 + 变化与算子名，替换原来的原始 `current_state`；表达提示：`current_feeling`，三档 + 变化方向。三处 system 提示补了说明。mock 表达在紧张为"高"时加"我有点担心，" | `tests/test_stateview.py`：表达 / 评价提示不含数值与算子名；`profile_only` 三处都不给；开关只影响评价；同一动作不同状态的台词不同；中性输入下紧张单调回落；持续高压下有界并收敛到 `gain×敏感度/恢复率` |
| F2 | card schema 2.0：`trigger.any_of` 读线索 / 状态值 / 状态变化（`{other}` 指刚说话的人）；`effects`；`action_bias`；`select_cards(persona, ctx, allowed)`，`ctx.since` 是自上次决策以来已提交的变化 | 高紧张无压力线索也触发 appraise 卡；对说话人信任净降 0.1 触发 trust 卡、对别人不触发；无状态规则的卡在日志里写 `no_state_rule` |
| F3 | 两条公式迁入卡：appraise → `tension_v1`（gain 0.35），trust → `trust_v1`（gain 0.15），都是 `every_event`；`agent.observe` 改为 `candidate_changes` → `commit_phase`，`mechanism_log` 增加 `cards`；`CARD_MECHANISMS` 删除；`update_rule_version` 升为 `engineering-v2`；`inputs.json` 存卡快照 | 改动前录的 7 组 mock 运行（test1 / test2 × 三档，加换座位）：紧张、信任轨迹与动作**逐值一致** |
| F4 | 审计新增 `state_change_attribution`：卡存在、规则与路径模式 / 算子相符、输入等于档案的 `affective_capacities` 与本角色对该事件的线索（评价输出或环境标注）、按规则复算等于 `after`；决策提示里所示机制卡的 `matched` 条件从记录复核 | 篡改 `after`、篡改输入、换卡 / 删卡、虚报触发值、缺卡快照各有测试，均判失败；被篡改的日志不会让审计崩溃 |
| E1 | scene schema 3.0（`agents`，2–6 个角色、至多 1 个环境 agent；2.0 拒绝）；`Agent(others={id: 座位名})`；关系状态按其他角色各存一份；提示里 `partner` 改为 `others_present`；`roles` / CLI 同步；新增 role3（王岚，项目负责人）与 test3 | test1 / test2 改成 3.0 后 mock 轨迹与改动前一致；三角色 mock 运行审计全过 |
| E2 | `runtime.WorldAgent`：世界变量、每轮推进时间、按轮次的规则事件（可改世界变量、可只给部分角色、可带线索）；事件 `world:{轮}:…`，类型 narration；`world.jsonl`；manifest 记 `environment`；开设定 id 不得以 `world:` 开头 | 时钟跨零点、私有旁白只进指定角色的收件箱和提示、环境线索直接驱动状态不调评价 |
| E3 | 审计泄露检查覆盖评价 / 决策 / 表达全部提示；跨角色隔离对每一对角色都做；评审包把环境说话人标为"旁白"，盲键记 `appraise_state` 与 `environment`；`transcript.md` 写每个其他角色的信任、环境 agent 的规则与世界变化、私有旁白标"只有某某知道" | `tests/test_multiagent.py` |

全部测试 173 项通过（`~/venvs/rolesim/bin/python3.11 -m unittest discover -s tests`）。

**仍未做：** E4 模型驱动的环境 agent；动态发言顺序（点名先答、抢话）；`group_state` 规则；其余三张卡的状态规则；三档分界与全部数值的校准（等心理学组，见 [06](06-psychology-decisions.md)）；把"环境本身有 agent"告知环境组。
