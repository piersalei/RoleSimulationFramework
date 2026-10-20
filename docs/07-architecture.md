# 当前架构与实现总结

本文档描述 2026-10-20 时仓库里**已经实现**的东西：分几层、数据怎么流、哪些地方做了隔离和校验、哪些还是空的。规划与分工见 [06-plan.md](06-plan.md)，设计动机见 [01-design.md](01-design.md)，skills 调研见 [08-persona-skills-survey.md](08-persona-skills-survey.md)。

## 1. 一句话概括

把心理学组整理的两份库和回复，落成一条**可审计的数据通道**：两个角色文件（稳定档案 + skills + 开场设定）+ 剧情文件（共享背景 + 座位）→ 两个角色轮流发言，环境只转发 → 每个角色对收到的话做感知评价 → 带证据的状态更新 → 决策与表达 → 全程落盘 → 自动审计 → 盲化评审包。

通道已经打通，并有 147 项工程测试。2026-10-20 新增人格 skills、角色开场设定（交互目标、想透露 / 想隐瞒、前情提要）和记忆召回；输入整理成"一个角色一个文件、一个剧情一个文件"；随后去掉事件脚本，改为两个角色基于背景自主对话，并用本地 Qwen3-8B（思考模式）跑通。均不改动与两组约定的合同。**心理内容大多仍是空壳**：11 个更新算子只有写权限、没有幅度规则；机制卡数值是未校准的工程假设。所有验证都是工程层面的，没有心理有效性证据，mock 的输出差异也不能当证据。

## 2. 总体结构

```mermaid
flowchart TB
  subgraph IN["输入层（只读、有来源）"]
    SRC["两份心理学库<br/>data/sources + catalog.json"]
    PER["角色文件 roles/role1.json …<br/>档案 persona 2.0 + skills + 开场设定"]
    SKL["skill 库<br/>library/skills/*/SKILL.md"]
    SCN["剧情文件 scenes/test1.json …<br/>共享背景 + 两个座位 + 世界私有数据（无剧本）"]
  end

  subgraph CORE["角色核心 rolesim/"]
    VIEW["视图编译 persona.py<br/>系统真值 / 角色自知 / 他者可见 / 控制器约束"]
    AG["Agent agent.py（每个座位一个）<br/>感知评价 → step 提出行动 / perceive 确认已说出"]
    ST["动态状态 state.py + modules.py<br/>六域 · 模块状态 · 11 个算子 · 分组原子提交"]
    MECH["机制卡 mechanisms.py<br/>5 张调度卡"]
    SK["人格 skills skills.py<br/>三级加载 · 不写状态"]
    MEM["记忆 memory.py<br/>只增 · 前情固定 · 相似度召回"]
    BR["开场设定 briefing.py<br/>目标 · 想透露 · 想隐瞒 · 前情"]
    BK["模型后端 providers.py<br/>mock / 兼容 chat-completions"]
  end

  subgraph ENV["环境适配 runtime.py + contract.py"]
    SE["DialogueEnvironment<br/>开场 · 轮流 · 原样转发 · 收尾"]
  end

  subgraph OUT["记录与评审"]
    RUN["运行目录<br/>manifest / states / mechanism_log / audit …"]
    AUD["自动审计 audit.py"]
    PKG["盲化评审包 package.py"]
  end

  SRC -. 来源引用 .-> PER
  SRC -. 来源引用 .-> SKL
  PER --> VIEW --> AG
  PER --> SK
  SKL --> SK
  SCN --> SE
  PER --> BR
  BR --> AG
  BR -- "前情提要" --> MEM
  SE -- "对方的台词 Event" --> AG
  AG <--> ST
  AG <--> MEM
  AG <--> SK
  AG <--> MECH
  AG <--> BK
  AG -- "ProposedAction（台词 + 私下的动作）" --> SE
  SE -- "已说出的台词 Event" --> AG
  AG --> RUN
  SE --> RUN
  RUN --> AUD --> PKG
  RUN --> PKG
```

读图要点：

- **环境不写剧本、不编造结果。** 它只开场、按座位轮流请角色发言、把台词原样转给对方，到轮数上限或有人选 `close` 时收尾。一方说的话是另一方收到的唯一新事件；角色选的动作留在 `intent_note` 里，不进任何事件。
- **每个角色是独立的 Agent。** 各有各的状态链、记忆和模型调用；对方的开场设定、状态、推理永远不进入自己的提示。
- **只有 Agent 通过后端接触模型。** 状态、机制、画像编译都能在没有模型的情况下完整校验。
- 虚线表示只是来源引用：`catalog.json` 提供章节索引和哈希，档案里的 `source_ref` 必须指向其中的条目（有测试检查）。

## 3. 分层与代码地图

| 层 | 文件 | 职责 | 状态 |
|---|---|---|---|
| 来源 | `catalog.py`、`data/` | 两份库的快照、章节索引（196 条）、哈希；其中的指令性文字没有执行权限 | 完成 |
| 画像 | `persona.py` | schema 2.0 校验、四种视图编译、条件化行为语句 | 结构完成，仅 P1 |
| 模块状态 | `modules.py` | 六种状态、状态与值一致性检查、转换审计；稳定与动态共用 | 完成 |
| 动态状态 | `state.py` | 六个状态域、11 个算子写权限、`commit_phase`、三级错误、四类版本号 | 结构完成，规则很少 |
| 机制 | `mechanisms.py` | 5 张调度卡、打分、取前 3 | 未校准 |
| 人格 skills | `skills.py`、`library/skills/` | SKILL.md 解析校验（兼容 Agent Skills 规范）、人物绑定、按线索 / 开场设定激活取前 3、表达要点分发 | 9 个 skill，内容待审定 |
| 开场设定 | `briefing.py` | 角色文件 `setup` 的校验；决策视图 / 表达视图分开；泄露关键词检测 | 完成 |
| 记忆 | `memory.py` | 只增；前情固定在前；较早条目按字符二元组相似度 + 显著性召回；最近 12 条完整保留 | 完成，未接 Mem0 服务 |
| 接口合同 | `contract.py` | Event / ProposedAction / AgentState / StateDelta 数据类与校验 | 完成（按环境组合同 v0） |
| 角色 | `agent.py` | `step` / `perceive`、可见性二次校验、决策与表达编排、状态链 | 完成 |
| 后端 | `providers.py` | mock 夹具；兼容 `/chat/completions` 的真实后端（评价、决策、表达三类调用；思考模式开启，推理只进运行记录），失败不回退 | 完成，已接本地 Qwen3-8B |
| 词表 | `vocab.py` | 七个交流动作、五种线索及其中文说明 | 完成 |
| 环境与运行 | `runtime.py` | scene schema 2.0 校验、`DialogueEnvironment`、`run_dialogue`、按角色落盘、git SHA | 两个座位 |
| 审计 | `audit.py` | 从落盘产物按角色重新推导七项自动检查 | 完成 |
| 可读记录 | `readable.py` | 运行结束后自动把运行目录整理成 `transcript.md`（背景、角色、逐句对话、结果）；只读落盘文件，不重新计算 | 完成 |
| 评审包 | `package.py` | 盲化评审包导出 | 完成 |
| 角色与剧情文件 | `roles.py`、`roles/`、`scenes/` | 角色文件与剧情文件的读取校验、`fit` / `fit_cast` 入座检查、`seat` 为每个座位组装 Agent、`run` | 完成 |
| 入口 | `__main__.py` | `catalog` `list` `validate` `compile` `skills` `run` `audit` `package` `readable` | 完成 |
| 示例与重建 | `roles/`、`scenes/`、`scripts/bootstrap_examples.py` | 两个虚构角色 role1（林澄，项目组一方）、role2（周越，合作方一方）+ 两个剧情 test1（交付沟通）、test2（剩余数据跟进），各最多 8 次发言 | 虚构设定 |

## 4. 一轮发言怎么被处理

```mermaid
sequenceDiagram
  participant E as 环境 DialogueEnvironment
  participant A as 轮到的 Agent
  participant S as 状态 commit_phase
  participant M as 模型后端

  E->>A: step(上次发言后新到的事件：开场旁白或对方的台词)
  A->>A: 二次校验可见性，audience 不含自己则丢弃并告警
  A->>M: appraise（事件不带环境线索时：从本角色立场评价感到哪些线索）
  M-->>A: cues（五种之一，0–1）+ reason
  A->>A: 校验线索名和范围
  A->>S: 候选变化（紧张；信任只认兑现证据）+ 证据 event_id
  S-->>A: 采纳 / 拒绝，写 mechanism_log
  A->>A: 召回记忆（前情 + 相关旧条目 + 最近 12 条），激活 skill
  A->>A: 按消融档位组装决策提示（开场设定、skill 各档都有；可含 current_state、机制卡）
  A->>M: decide
  M-->>A: action + evidence_ids + brief_reason
  A->>A: 校验：动作可行、证据可见
  A->>M: express（只给自知、风格约束、skill 表达要点、可透露信息、要回应的话、动作及其含义、自己最近说过的话、可见历史）
  M-->>A: speech
  A-->>E: ProposedAction（speech）+ 新状态快照
  E->>E: 原样发布为公开台词事件，放进对方的收件箱
  E->>A: perceive(自己刚说出的台词)
  A->>S: 候选变化（无新压力，紧张按恢复速率回落）
  S-->>A: 采纳 / 拒绝
  A-->>E: 新状态快照（prev_state_id 串链）
  E->>E: 轮到对方
```

要点：

- 每个事件**用同一份已提交快照**生成候选，同一阶段内不读取未提交的候选。
- 决策或表达失败时返回 `status=error`，**状态不前进**，也不会换成 mock 或把内心独白当台词。
- `perceive` 之后下一次 `step` 读取的就是最新状态；`prev_state_id` 不匹配会被拒绝。
- 自己的台词在 `perceive` 时才进入记忆（环境确认"已说出"），记忆里自己的话标为"我"，对方的话标为对方的座位名。
- 感知评价提示在三档消融里完全相同：它决定的是三档都要计算的状态。

## 5. 状态结构

```mermaid
flowchart LR
  subgraph STABLE["stable（轨迹内只读，module_status 在内）"]
    ID["identity"]
    TR["traits<br/>personality · attachment · motivation_baseline<br/>affective_capacities · cognitive_style<br/>71 维 · 发展位置 · 荣格功能"]
    VA["values<br/>施瓦兹 19 项权重 · 投入度<br/>道德推理 · 文化取向"]
    CG["core_goals（1–3 个）"]
    SP["speech_style<br/>基线 + 情境规则"]
    MP["mechanism_priors"]
  end

  subgraph DYN["dynamic（可更新，须有可见证据）"]
    AF["affect_state<br/>情绪六字段"]
    CO["cognition_state<br/>beliefs · other_models · tom"]
    GO["goal_state<br/>goals_now"]
    RE["relation_state<br/>trust · closeness · dependence<br/>commitment · harm_history"]
    GR["group_state"]
    PO["policy_state"]
  end

  MEM["memory<br/>角色可见事件与自己的行动"]
  OPS["11 个更新算子<br/>只能写自己白名单内的路径"]

  STABLE -. 只读输入 .-> OPS
  MEM --> OPS
  OPS --> DYN
```

模块状态是贯穿两侧的一套规则：

| 状态 | 含义 | 值的要求 |
|---|---|---|
| `active` | 已启用且有值 | 必须有值，且稳定模块要有来源 |
| `unknown` | 已启用但证据不足 | 必须是 `null`，不能写成 0 或中性值 |
| `not_modeled` / `disabled` | 本版未建模 / 被关闭 | 不得持值，不参与计算 |
| `inactive` / `not_applicable` | 本轮未激活 / 场景不适用 | 不得持值 |

只有 `active` 模块可以被写入。状态转换写入审计记录；重新启用需要完成校验。

目前 `dynamic` 里实际有更新规则的只有两条：紧张（`affect_state.emotion.intensity`）和对合作方的信任（`relation_state.partner.trust`）。

## 6. 候选变化如何提交

```mermaid
flowchart TB
  C["候选变化列表<br/>每条带 path / before / after / 证据 / 算子 / 理由 / group"] --> H{"逐条硬检查"}
  H -- "结构 · 证据可见 · 算子已登记<br/>写权限 · 模块 active · 范围 · before 与快照一致" --> G["按 group 归并"]
  H -- 任一不过 --> R1["该 group 整组拒绝"]
  G --> K{"同路径冲突？"}
  K -- "完全相同的重复贡献" --> D["去重"]
  K -- "不同取值，且无聚合规则" --> R2["涉及的 group 整组拒绝"]
  D --> P["逐 group 试应用并校验"]
  K -- 无冲突 --> P
  P -- 通过 --> OK["原子提交 → 新 dynamic"]
  P -- 失败 --> R3["该 group 拒绝"]
  OK --> F["软约束：写入并标记 soft_violation<br/>豁免须规则卡允许并留 exception 记录"]
  R1 --> LOG["mechanism_log<br/>候选 · 采纳 · 拒绝原因 · 标记 · 版本号 · 输入/输出 state_id"]
  R2 --> LOG
  R3 --> LOG
  F --> LOG
```

- 一个 group 内的变化要么全部生效要么全部不生效；其他 group 不受影响。
- 同一个 `commit_key`（角色/事件/阶段）重试是空操作。
- 硬错**不能被豁免**；目前唯一的软规则（信任单步上限）也不允许豁免，因为还没有规则卡允许。

## 7. 谁能看到什么

| 信息 | 决策提示 | 表达提示 | 评审包·评测版 | 评审包·角色可见版 |
|---|---|---|---|---|
| 自我理解 `self_view` | 是 | 是 | 否 | 否 |
| 条件化行为语句（含表达风格规则） | 是 | 仅 `speech_style.*` 部分 | 否 | 否 |
| 动机基线、价值、核心目标 | 是 | 否 | 否 | 否 |
| 可见历史（含对方的台词） | 是 | 是 | 是 | 是（作为轨迹） |
| 对方的开场设定、状态、推理 | 否 | 否 | 仅在单独的对照文件 | 否 |
| 当前动态状态 | 仅 `profile_state` 及以上 | 否 | 是（前后状态） | 否 |
| 机制卡与控制器提示 | 仅 `profile_state_mechanisms` | 否 | 否（提示不导出，避免暴露档位） | 否 |
| 模型输出与推理摘要 | — | — | 是 | 否 |
| 世界私有数据、角色不可见事件 | 否 | 否 | 仅在单独的对照文件 | 否 |
| 开场设定：交互目标、想让对方知道的信息 | 是 | 是 | 是（`role_setup.json`） | 否 |
| 开场设定：不想让对方知道的信息 | 是（`keep_private`） | **否** | 是（`role_setup.json`） | 否 |
| 前情提要 | 是（记忆首条，可作证据） | 是 | 是（`role_setup.json`） | 否 |
| skill 名称与描述、决策要点、反模式 | 是 | 否 | 否（只列已绑定 skill 名，不写是否启用） | 否 |
| skill 表达要点 | 否 | 是（仅已激活的） | 否 | 否 |

**已知缺口：评测版样本目前不带稳定画像**（自我理解、特质、价值、目标）。评"稳定一致性""角色可区分性"需要它，导出时要补，补的时候仍须与方法标签分开。

三档消融入口：`profile_only`（相同历史，不注入状态与机制）、`profile_state`（加动态状态）、`profile_state_mechanisms`（再加机制卡，默认）。**状态在三档下都计算并记录**，档位只改变控制器提示。skill 属于人物画像，三档都注入；另有独立开关 `--no-skills`，保留绑定记录但不注入，用于 skill 的对照。

## 8. 运行产物与评审链路

```mermaid
flowchart LR
  RUNX["python -m rolesim run"] --> D["运行目录"]
  D --> F1["manifest.json<br/>版本 · git SHA · 模型 · 调用数"]
  D --> F6["public.jsonl<br/>双方台词"]
  D --> F7["environment_events.json<br/>全部事件"]
  D --> RD["roles/&lt;id&gt;/（每个角色一份）<br/>inputs · views · states · mechanism_log<br/>audit.jsonl（评价/决策/表达输入输出 · 推理）"]
  D --> AR["audit_report.json / .md<br/>每个角色七项自动检查"]
  D --> TR["transcript.md<br/>可读版：背景 · 角色 · 逐句对话 · 结果"]
  D --> PK["python -m rolesim package"]
  AR --> PK
  PK --> P1["R01/ evaluator_samples<br/>role_visible_trace<br/>counterpart_truth<br/>role_setup<br/>annotation_template"]
  PK --> P2["blind_key.json<br/>方法标签，不给评审者"]
```

- **自动审计**从落盘文件重新推导，不信任引擎自己的记录：字段有效性、证据与可见性、写权限、stable 一致、轨迹可追溯、隐瞒信息未泄露（隐瞒原文不进表达提示，关键词不出现在台词）、跨角色隔离（对方的隐瞒原文和世界私有数据不出现在本角色的任何调用里）；另外单列软约束、豁免、被拒依赖组、模块覆盖、主动透露次数。其余维度只标注"需人工评分"。
- **评审包**里每个运行的每个角色是一个盲化单元；模式、后端、模型、代码版本只在 `blind_key.json`。对方的开场设定和末态单独成文件，只供对照。
- 每次运行自带四类版本号：`construct_definition_version`（未登记）、`state_schema_version`、`update_rule_version`、`evaluation_protocol_version`（未冻结）。

## 9. 核心不变量

这些由代码或测试强制，而不是约定：

1. `stable` 在一个轨迹内不可变；`StateDelta` 的路径以 `stable` 开头一律拒绝，运行中每轮还用哈希复核。
2. 每条状态变化必须带**本角色可见**的证据，且只能写 `active` 模块、自己算子白名单内的路径。
3. 数值越界直接拒绝，不截断；未知用 `null`，不用中性值代替。
4. 世界私有数据和他人的隐藏心理字段（包括对方的开场设定和状态）不进入任何模型调用；角色不可见的事件在入口被丢弃。角色自己要隐瞒的信息只进评价和决策提示，不进表达提示。
5. skill 只影响提示内容，不产生状态变化；记忆只增不改。
6. 他人的口头承诺不算"已兑现"的证据，只有叙述事实和环境裁决的结果可以改变可靠性判断；所以纯对话里信任不变。
7. 环境不替角色说话、不编造结果；模型给出的感知线索只能是登记过的五种、0–1，状态变化由程序按公式计算。
8. 模型调用失败就停下并记录，**不静默回退 mock**。
9. 旧 schema 的档案和轨迹被拒绝，不做静默转换（剧情只接受 scene schema 2.0，带事件脚本的 1.x 格式被拒绝）。

## 10. 完成度与未做

| 方面 | 已有 | 没有 |
|---|---|---|
| 画像 | schema 2.0，20 个稳定模块带状态，P1 30 分面，施瓦兹 19 项，核心目标 | P2/P3 及三方案比较；分面级 unknown；其余 26 个分面的行为投影；71 维；stable 变更的版本迁移结构 |
| 人格 skills | 9 个 SKILL.md、人物绑定、三级加载、`--no-skills` 对照 | 心理学组审定内容；从语料蒸馏 skill；纠正层与 skill 版本；由模型读描述自主激活的档位 |
| 开场设定与记忆 | 角色文件的开场设定（目标、透露、隐瞒、前情）；只增记忆与召回；隐瞒泄露审计 | 接入 Mem0 服务；换说法的泄露检测；记忆更新人格（需与心理学组对齐） |
| 动态状态 | 六域骨架，原子提交，三级错误，日志与版本 | 11 个算子的机制卡数值；信念、目标、他人模型的更新规则；时间事件与衰减规则 |
| 环境 | 两个角色基于背景轮流自主对话；合同 v0 已与环境组对接 | 三方以上；非对话行动与世界事实（如"文件已到"）；时间轴与打断（设计见 [09](09-timeline-and-interruption.md)） |
| 模型 | 评价 / 决策 / 表达三类调用、严格 JSON、思考模式、本机回环不走代理；本地 Qwen3-8B 已跑通 | 更大模型；多次重复运行的统计 |
| 评测 | 三档消融，自动审计，盲化评审包，标注模板 | 评审包带稳定画像；评测协议冻结；标注工具；复标统计；回放工具；真人基线 |

## 11. 常用命令

```bash
# 项目要求 Python 3.11+（CI 用 3.11）。本机用 ~/venvs/rolesim（Python 3.11.15，仅标准库）；
# 在新会话里 Claude 的 PATH 由 .claude/settings.local.json 指向它，自己的终端可 source ~/venvs/rolesim/bin/activate
python3.11 -m unittest discover -s tests -v
python3.11 -m rolesim list
python3.11 -m rolesim validate role1
python3.11 -m rolesim run --roles role1 role2 --scene test1 --mode profile_state   # → runs/<YYYYMMDD-HHMMSS>_test1_mock/
python3.11 -m rolesim run --roles role1 role2 --scene test2
python3.11 -m rolesim run --roles role1 role2 --scene test1 --no-skills
python3.11 -m rolesim readable runs/<运行目录>
python3.11 -m rolesim audit runs/demo
python3.11 -m rolesim package runs/a runs/b --out outputs/review --seed 1
PYTHONPATH=. python3.11 scripts/bootstrap_examples.py   # 重建 roles/ 与 scenes/ 示例
```

## 12. 证据边界

AGENTS.md 要求明确区分来源理论、工程假设、作者报告结果、本地工程检查、模型实验和人工验证。当前仓库里的结论**只到"本地工程检查"这一层**：结构、证据、隔离和可复现性可以被测试和审计验证，"角色像不像人"没有被验证，也不能由 mock 或审计通过来推出。
