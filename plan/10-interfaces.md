# 关键接口（面向接口编程）

2026-11-07。只列关键接口：**4 个顶层接口，每个下面 2—4 个子接口**。每个接口给出签名、输入输出类型、前置条件和出错方式。实现可以替换，接口不变。

代码形式在 [`rolesim/interfaces.py`](../rolesim/interfaces.py)（Protocol + 不可变数据类型），测试在 `tests/test_interfaces.py`。两者不一致时以本文为准并改代码。

字段级细节不在这里：数据类型只写关键字段，完整字段见 [09](09-interface-and-field-spec.md)。对象划分与流程见 [08](08-framework-oop-design.md)。

```
1 IRoleAgent           对外：环境组调用的角色
  1.1 ITurnRunner        一次 act 内部的阶段编排
  1.2 ITrace             轨迹日志
2 IRoleRepository      角色从哪来：角色库、档案、入场状态
  2.1 IProfileCompiler   档案 → 稳定画像
  2.2 IRegistry          心理学登记表查询
3 IStage               三个心理阶段（S1 事件建模 / S2 行动计划 / S3 长期演化）共用的形状
  3.1 IEvidenceGate      本角色能用哪些证据
  3.2 IMechanism         一个决策过程，只出判断
  3.3 IModel             结构化模型调用
  3.4 IExpresser         行动计划 → 对外的行动内容（只有 S2 用）
4 IStateStore          状态的唯一写入口
  4.1 IUpdateOperator    11 个更新算子
  4.2 IMagnitude         合法变化区间
  4.3 IValidator         硬错 / 软异常检查
```

依赖方向只能向下或同层平级：`IRoleAgent → ITurnRunner → IStage / IStateStore`；`IStage` 不能写状态，只能把候选变化交给 `IStateStore`。

---

## 1 `IRoleAgent`（对外，已实现）

环境组定义的接口，签名不能改。环境组文档里叫 `AgentAdapter`，对面那一侧叫 `EnvironmentAdapter`；我们统一按 `I...` 命名为 `IRoleAgent` / `IEnvironment`（都定义在 `agent_api.py`）。`IEnvironment` 由环境组实现，我们只在本地运行器里调用。

```python
class IRoleAgent(Protocol):
    async def initialize(self, interaction_id: str, character_id: str,
                         bootstrap: CharacterBootstrap) -> None: ...
    async def act(self, interaction_id: str, character_id: str,
                  context: CharacterTurnInput, request_id: str) -> AgentActionContent: ...
```

| | 内容 |
|---|---|
| 输入 | `CharacterBootstrap`：setting、trigger_facts、name、profile_summary、core_goals、relationships、history_memory、personality、mechanism。`CharacterTurnInput`：bootstrap=null、observations（看到过的全部记录）、present_characters（含本人） |
| 输出 | `AgentActionContent`：thought / speech / action 各为 `{content, recipient_ids}` 或 null，至少一项；order 列出非空项 |
| 前置 | act 之前同一 `interaction_id` 的所有角色都已 initialize |
| 保证 | 同一 `request_id` 返回同一结果；不返回任何内部状态；他人的思考不进入本角色的任何提示 |
| 出错 | 抛 `InterfaceError`（输入不合规）或 `AgentCallError`（内部失败），状态不前进 |

实现要点：`initialize` 调 `IRoleRepository.enter` 得到入场状态；`act` 取出未处理的记录，交给 `ITurnRunner.run`。

现在有两个实现，共用 `adapter.RoleAgentBase`（角色库、按 `interaction_id + character_id` 的 Session、`request_id` 重试、一次一个调用）：`adapter.RoleAgentAdapter`（旧的 `Agent.step`，只出发言，CLI 默认）和 `staged.StagedRoleAgent`（新的分阶段管线，产出思考 / 发言 / 动作；`--pipeline stages`）。旧实现在第 7 步删除。

### 1.1 `ITurnRunner`：一次 act 里怎样走三个阶段

```python
class ITurnRunner(Protocol):
    def begin(self, key: SessionKey, state: AgentState, stable: StableProfile,
              background: Optional[Mapping[str, Any]] = None) -> None: ...      # 入场
    def run(self, key: SessionKey, new_records: list[InteractionRecord],
            present: dict[str, Present]) -> tuple[AgentActionContent, AgentState]: ...
```

| | 内容 |
|---|---|
| 输入 | `SessionKey = (interaction_id, character_id)`；本次新看到的记录（已去掉吸收过的和思考）；当前在场者 |
| 输出 | 对外的行动内容 + 最终提交的状态 |
| 流程 | 新记录按 `action_id` 分组，每组一个事件阶段：`S1.run` → commit；若该组是对本角色此前行动 / 承诺的反馈，再 `S3.run` → commit。全部处理完后 `S2.run` → commit → `IExpresser.realize` |
| 保证 | 每个阶段读上一次提交后的快照；同一阶段内不读未提交的候选；S2 的依赖组被拒时不发出行动 |
| 出错 | 任一阶段失败即整次 act 失败，已提交的阶段保留，未提交的丢弃 |
| 实现 | `rolesim/runner.py` `TurnRunner`（已实现） |
| 什么算反馈 | 上次计划的受众（计划没指定受众时为除自己和环境以外的任何人）对本角色说话或行动的第一组记录；每个计划只判断一次 |
| S2 被拒 | 计划自己的依赖组（目标、承诺、策略）有硬错 → 行动不按原样发出，在新阶段重新做 S2，把拒绝原因放在 `upstream["rejected"]`；第二次仍被拒 → 整次 act 失败 |
| 重试 | S1 已提交的记录组算已吸收；没做完的反馈判断、已提交但没表达出去的计划都记着，重试时接着做，不会把同一阶段提交两次 |

### 1.2 `ITrace`：轨迹日志

```python
class ITrace(Protocol):
    def phase(self, key: SessionKey, log: PhaseLog) -> None: ...
    def model_call(self, key: SessionKey, call: ModelCallRecord) -> None: ...
```

| 类型 | 关键字段 |
|---|---|
| `PhaseLog` | event_id、phase_no、input_state_id、output_state_id、evidence（EvidenceSet）、judgments（[Judgment]）、products（S1/S2/S3 产物）、changes（[StateChange]）、commit（CommitResult）、versions |
| `ModelCallRecord` | stage、input、raw_output、reasoning |

只写不读，没有写状态的权限。按 `interaction_id` 落盘。

实现：`rolesim/trace.py`（已实现）。`JsonlTrace(root)` 写 `<root>/<interaction_id>/<character_id>/phases.jsonl`（每次提交一条 `PhaseLog`）和 `model_calls.jsonl`（每次模型调用的视图、原始输出、推理）；`MemoryTrace` 在内存里收同样的记录。接环境组的真实运行器后，这份记录由我们自己写，不依赖运行器。

---

## 2 `IRoleRepository`：角色从哪来

```python
class IRoleRepository(Protocol):
    def load(self, role_ref: str) -> StableProfile: ...
    def enter(self, profile: StableProfile, bootstrap: CharacterBootstrap,
              config: EnableConfig) -> AgentState: ...
```

| | 内容 |
|---|---|
| `load` | 按角色库 ID 取稳定画像快照；同一 ID 同一版本永远返回同一对象 |
| `enter` | 生成入场状态：dynamic 按"档案入场状态 → bootstrap / 场景 → 先验"取值，`core_goals` 文本 → 当前目标，`history_memory` → 入场记忆；没有依据的字段为 unknown |
| 关键类型 | `StableProfile`：snapshot_id、modules（`{construct_id: StableModule}`，每个带 status / source_kind / confidence / needs_review）。`AgentState`：state_id、prev_state_id、stable_snapshot_id、dynamic、memory（都按构念 ID 存，形状见 `rolesim/slots.py`）、versions、status（每个构念或实例的状态标记）。`EnableConfig`：每个模块 / 字段 / 决策过程是否启用 |
| 出错 | 角色不存在、档案结构错 → 抛错；缺证据不报错，写 unknown |

### 2.1 `IProfileCompiler`：档案 → 稳定画像

```python
class IProfileCompiler(Protocol):
    def compile(self, raw: str | dict, config: EnableConfig) -> tuple[StableProfile, list[ReviewItem]]: ...
```

| | 内容 |
|---|---|
| 输入 | 自然语言或半结构化档案 |
| 输出 | 画像 + 待审核清单（`ReviewItem`：construct_id、value、source_kind、原文依据、原因） |
| 规则 | 明写 → explicit；规则推断 → inferred；模型推断 → model_inferred（必须带原文）；无依据 → unknown，不随机补值。按登记表的装配顺序生成，派生字段最后计算 |

### 2.2 `IRegistry`：心理学登记表查询（已实现）

```python
class IRegistry(Protocol):
    def construct(self, construct_id: str) -> Construct: ...
    def mechanism(self, dm_id: str) -> DecisionMechanism: ...
    def operator(self, name: str) -> OperatorRule: ...
    def ids(self, kind: Literal["construct", "mechanism", "operator"]) -> tuple[str, ...]: ...
```

| 类型 | 关键字段 |
|---|---|
| `Construct` | construct_id、layer、module、section、name、writer（唯一主写入算子）、tempo（快变 / 慢变 / 类别）、decay（§九的时间规则原文）、self_visibility（能自知 / 部分 / 不能）、high_impact、basis |
| `DecisionMechanism` | dm_id、section、name、options（登记的可选项）、option_groups、stages、hands_to（交给哪些算子）、card（有无机制卡）、notes、basis |
| `OperatorRule` | name、section、writes（构念白名单）、stages（允许在哪些阶段调用）、group（依赖组）、timing、basis |

| | 内容 |
|---|---|
| 实现 | `rolesim/registry.py`：`build_registry()` 从 `data/sources/psychology_v1/` 抽取，`python3 -m rolesim registry` 写成 `data/registry.json`；`Registry.load()` 读这个文件，实现本接口 |
| 抽取方式 | 结构规整的部分机械抽取：构念与决策过程条目、算子写入白名单、算子的时间与优先级、每个阶段可调用的算子和决策过程、机制卡。只写在叙述里的部分放在一张带原文引语的对照表里：依赖组、快慢变归类、衰减规则、自知程度、高影响字段，以及 12 个决策过程的可选项断句修正 |
| 保证 | 只读；`basis` 记录每个属性出自哪份文件、哪一节、哪句话；`registry.json` 与原文不一致时测试失败 |
| 出错 | 未登记的 ID → `KeyError`。原文改动使对照表的引语找不到、§九多出一条、声明的数量对不上、一个构念出现两个主写入算子 → 构建失败（`RegistryBuildError`），需要人工重读后再生成 |
| 没有抽取的 | 每个字段的取值形式和范围（角色状态说明里只有叙述，留给 `IStateStore` 的字段表）；决策过程读取哪些构念（原文只有中文描述）；登记 §4.10 的先验映射表（留给 `IProfileCompiler`）；幅度分档的数值（留给 `IMagnitude`） |
| 待心理学组确认 | `registry.json` 里的 `review`：15 个没有任何阶段调用的决策过程、21 个没有归入快慢变的 dynamic 构念、20 个没写自知程度的构念（暂按不可自知处理）、19 个可选项里夹着说明的决策过程、15 处 §4.10 候选项与 §7 可选项写法不一致 |

---

## 3 `IStage`：三个心理阶段

三个阶段实现同一个接口，只有产物类型不同。

```python
class IStage(Protocol[P]):
    name: Literal["S1", "S2", "S3"]
    def run(self, ctx: StageContext) -> StageOutput[P]: ...
```

| 类型 | 关键字段 |
|---|---|
| `StageContext` | phase（event_id、phase_no、kind）、state（本阶段唯一读的快照）、stable、records（本阶段的记录组）、history（本阶段之前看到过的记录，提示上下文，也可作证据）、background（initialize 时告诉角色的场景与自身情况）、upstream（已提交的上游产物：S2 拿到最新的 S1 产物和上次 ActionPlan，S3 拿到 ActionPlan 和反馈的 S1 产物）、present |
| `StageOutput[P]` | product: P、proposals（[ChangeProposal]）、judgments（[Judgment]）、evidence（本阶段能用的 EvidenceSet，提交时据此核对证据可见） |
| `ChangeProposal` | construct_id、key、field、after、reason、grade（本角色对这一维度的事件等级，数值变化必填）、evidence / mechanisms（这条变化依据的证据和决策过程；不填则取交给该算子的判断）、operator（不填即主写入算子；衰减要写明 `decay_or_recovery`）：阶段只能**提议**，不写状态 |

三个产物的必备字段：

| 阶段 | 产物 `P` | 必备字段 |
|---|---|---|
| S1 事件建模 | `SituationInterpretation` | situation_definition、appraisal（受影响的目标及收益 / 损失 / 威胁 / 挑战）、attribution、hypotheses（1—3 个，各带 confidence、支持与反向证据、区分条件）、judgment_state（定论 / 多假设 / 暂缓）、information_needs、relation_signals |
| S2 行动计划 | `ActionPlan` | candidates（含被筛掉的及约束来源）、chosen、fallbacks、switch_conditions（可观察信号 → 改做什么）、depends_on（依赖的假设）、expected_outcomes、commitment（是否形成及内容）、expression（表达目标、受众、方式） |
| S3 长期演化 | `EvolutionResult` | expected_vs_actual、commitment_rulings、relation_course（走向 + 修复条件）、learning_attribution、carry_forward（下次观察什么） |

| | 内容 |
|---|---|
| 前置 | `ctx.state` 是已提交快照；S2 只在 S1 产物已提交后运行；S3 只在有反馈时运行 |
| 保证 | 产物生成后只读；后续阶段只能标"已被取代"，不能改；不读本角色不可见的内容 |
| 出错 | 模型输出不合 schema → 整阶段失败，不产生任何提议 |

### 3.1 `IEvidenceGate`：本角色能用哪些证据

```python
class IEvidenceGate(Protocol):
    def build(self, ctx: StageContext) -> EvidenceSet: ...
```

| 类型 | 关键字段 |
|---|---|
| `EvidenceSet` | included（[EvidenceRef]）、excluded（[{event, reason}]）、retrieved_memories |
| `EvidenceRef` | event（记录编号）、channel（亲见 / 听说 / 推断 / 本人行动 / 入场前）、claim（听说时转述的命题） |

只放本角色可见的；他人思考、他人隐状态、场景私有数据一律排除并记在 excluded。是否"听说"由 S1 从文字判断后补进 `channel`。

实现：`rolesim/evidence.py` `EvidenceGate`（已实现）。读 `ctx.history` 和 `ctx.records`：本角色是行动者或被明确发送的记录进 included（本人的为 `own_action`，其余 `witnessed`）；排除原因 `other_thought`（他人的思考，即使被误发过来）、`own_thought`（本人的思考是过程记录，不是证据）、`not_recipient`；按刚看到的内容取回最多 k 条记忆（`m.entry`，字面相似度 × 重要性 × 可及性）。他人隐状态和场景私有数据从不到达本方，所以没有可排除的。

### 3.2 `IMechanism`：一个决策过程

```python
class IMechanism(Protocol):
    dm_id: str
    def evaluate(self, ctx: StageContext, evidence: EvidenceSet) -> Judgment: ...
```

| 类型 | 关键字段 |
|---|---|
| `Judgment` | dm_id、option（登记的可选项之一或 null）、output（本过程的中间判断）、evidence、hands_to（交给哪些算子） |

没有写权限。可以由规则实现，也可以由模型实现（在 `IModel` 调用里作为检查项）。`option` 不在登记表里即拒绝。

现状：只有模型实现——各阶段调用的输出里有 judgments，`schemas.parse_output` 查登记表（过程必须本阶段可调用、option 必须登记、hands_to 由登记表填）。按规则实现的过程还没有。

### 3.3 `IModel`：结构化模型调用（已实现）

```python
class IModel(Protocol):
    def complete(self, stage: str, view: PromptView, schema: dict) -> ModelResult: ...
```

| 类型 | 关键字段 |
|---|---|
| `PromptView` | 只含本角色自知层的画像（不能自知的字段已转成条件化语句）、可见记录、当前状态的文字描述、上游产物；self_id、present（在场者）、task（程序按登记表生成：本阶段可调用的决策过程及其可选项、可提议修改的构念及字段、事件等级说明） |
| `ModelResult` | output（符合 schema 的 dict）、raw、reasoning |

每个阶段一次调用。证据集、心理理论上限、幅度区间、版本号由程序填，不进 schema。失败即抛错（`ModelError`），不回退 mock。

| | 内容 |
|---|---|
| 实现 | `rolesim/model.py`：`ChatModel`（沿用 `providers.ChatBackend` 的传输，每个阶段一段系统提示，载荷是 PromptView + task + output_schema，回复在本地按 schema 校验）、`MockModel`（离线夹具，三个阶段都给出合 schema、合登记表的输出，提议只新建条目，不碰需要当前数值的幅度档；内容不是心理学证据） |
| 输出 schema | `rolesim/schemas.py` `stage_schema(stage, registry)`：product（三个产物各自的必备字段）、judgments（dm_id 只能是本阶段可调用的过程）、proposals（construct_id 只能是本阶段有算子可写的构念）；S1 另有 evidence_notes（哪些记录的内容是转述 / 推断） |
| 解析 | `parse_output(stage, output, registry, evidence) -> StageOutput`：先按 schema 校验，再查登记表（option 必须是该过程的可选项；提议依据的过程必须在本阶段判断过；转述必须写出被转述的命题；证据只能降级，不能把转述升成亲见），任何一条不满足 → `StageError`，整阶段不产生提议。judgments 的 hands_to 由登记表填 |
| 还没做 | 结构化解码（服务端按 schema 约束生成）没有接，现在用 `json_object` + 本地校验；task 数据每阶段约 8—14K 字符，以后可按需裁剪 |

### 3.4 `IExpresser`：行动计划 → 对外行动内容

```python
class IExpresser(Protocol):
    def realize(self, plan: ActionPlan, ctx: StageContext) -> AgentActionContent: ...
```

只拿表达需要的信息：自知画像、表达基线、计划里的表达目标与受众、可见历史；不拿要隐瞒的内容、机制名和状态数值。我们自己的思考固定只发给本人。

实现：`rolesim/expresser.py` `ModelExpresser`（已实现），一次 `IModel.complete("express", …)`。给：身份、核心目标、表达基线（只限能自知的）、感受（情绪标签 + 低 / 中 / 高，不带数值）、最近 12 条可见记录、计划里的主行动、表达目标 / 受众 / 方式、承诺。不给：状态数值、构念和过程 ID、其他候选及其约束、假设。回复按 `schemas.EXPRESS_SCHEMA` 解析成 `AgentActionContent`：思考只发本人，发言和动作发给计划的受众（没写受众即在场所有人），并用环境组的 `validate_action_content` 检查。

### 3.5 三个阶段的实现（已实现）

`rolesim/stages.py` `ModelStage(name)`：阶段与 phase.kind 必须对应（S1 ↔ event、S2 ↔ plan、S3 ↔ feedback），S3 要求 `upstream["plan"]`；`IEvidenceGate` → `PromptView`（只含自知层：登记表里 aware / partial 的 stable 模块、active 的 dynamic 构念及其值、取回的记忆、可见记录、上游产物、在场者、task）→ `IModel` → 写 `ITrace`（在解析之前，失败的输出也留下）→ `parse_output`；S2 的受众必须是在场的其他人。模型失败或输出不合规 → `StageError`，整阶段不产生提议。

还没做：不能自知（或来源没写）的 stable 模块现在不进任何提示；按心智建模库 5.4 转成条件化语句是档案编译的事（第 6 步）。

---

## 4 `IStateStore`：状态的唯一写入口（已实现）

```python
class IStateStore(Protocol):
    def open(self, key: SessionKey, state: AgentState) -> None: ...      # 入场状态
    def snapshot(self, key: SessionKey) -> AgentState: ...
    def commit(self, ctx: StageContext, output: StageOutput) -> CommitResult: ...
```

| 类型 | 关键字段 |
|---|---|
| `CommitResult` | output_state_id、accepted（[StateChange]）、rejected（[{change, group, error}]，change 在无法起草时是原提议）、soft_flags |
| `StateChange` | construct_id、key、field、before、after、evidence（非空）、update_operator、decision_mechanisms、stage、band、group、reason、flag、grade |

| | 内容 |
|---|---|
| 实现 | `rolesim/store.py` `ConstructStore`；状态布局 `rolesim/slots.py`（45 个 dynamic / memory 构念的键、字段、取值形式、哪些字段有幅度档、只追加 / 固定事实，字段名按 09 §3.4—3.5） |
| 流程 | 提议 → 交给对应构念的 `IUpdateOperator` 补全为 `StateChange` → `IMagnitude` 给区间 → `IValidator` 检查 → 按依赖组原子提交；没变的值丢弃；同一阶段别的算子已写的地址，衰减不再处理 |
| 保证 | 只有这里能产生新 `AgentState`；stable 永远不写；`ctx.state` 必须是当前快照；同一 `(角色, 事件, 阶段, kind)` 重试返回第一次的结果，载荷不同则拒绝；交出去的快照是副本；写入过的 unknown 构念转为 active |
| 出错 | 某依赖组有硬错 → 整组拒绝，其他组照常提交；软异常照写并标记。另有两项要看历史或多条变化的软检查：无新证据却反复翻转、四个关系维度同比例变化 |
| 还没做 | 跨阶段累计上限和快变构念的习惯化（要等场景时间单位，F3）；按经过时间计算衰减值（同上；现在衰减值由阶段提出，只查方向） |

### 4.1 `IUpdateOperator`：11 个更新算子

```python
class IUpdateOperator(Protocol):
    name: str                         # emotion_update、belief_revision、… 共 11 个
    def propose(self, ctx: StageContext, proposals: list[ChangeProposal],
                judgments: list[Judgment]) -> tuple[list[StateChange], list[RejectedChange]]: ...
```

每个算子只接收白名单内构念的提议，补上 before、evidence、decision_mechanisms、band、group；没有证据、地址不对、同一地址给出不同值的提议退回（同值的合并，证据去重）。阶段权限由 `OperatorRule.stages` 决定（例如 relation_update 只能在 S3）。`decay_or_recovery` 只在时间推进或阶段末运行。实现：`rolesim/operators.py`，11 个算子是同一个类，差别全部来自登记表的写入白名单、阶段权限和依赖组。

### 4.2 `IMagnitude`：合法变化区间

```python
class IMagnitude(Protocol):
    def range(self, construct: Construct, field: Optional[str], grade: int, direction: int,
              evidence: Sequence[EvidenceRef], stable: StableProfile, instance: Any = None) -> Optional[Band]: ...
```

`Band`：name（微小 / 小 / 中 / 大 / 扩展）、low、high、extended（只有四级事件有：落在 (high, extended] 的值照写并标软异常 `extended_range`）。按事件等级取基准区间（效价 ×2、当前自我评价 ×4），人格调节最多移一档（敏感度分面合并后 |z| ≥ 1.5 时移到相邻档的半区），证据只有听说的最多到"中"、只有推断的最多到"小"。模型的取值必须落在区间内，越界拒绝不截断。不属于幅度档的字段（类别、文字、登记表未归快慢变的构念）返回 None。实现：`rolesim/magnitude.py`；人格敏感度目前只接 P1 分面、依恋焦虑、认知灵活性这几条无条件的映射，其余（P2 / P3、违约后的尽责性、自尊不稳定性）等档案形状确定后再接。

### 4.3 `IValidator`：硬错 / 软异常检查

```python
class IValidator(Protocol):
    def check(self, change: StateChange, snapshot: AgentState,
              evidence: EvidenceSet) -> list[Violation]: ...
```

`Violation`：kind（hard / soft）、code、detail。硬错包括：证据为空或不可见、越出算子白名单或阶段权限、写 stable、写非 active 字段、before 与快照不一致、删除伤害事实或记忆、含对方真实隐状态、心理理论阶数超限、数值越界。软异常包括：超出典型区间、维度同比例联动、无新证据却反复翻转、累计超限。

实现：`rolesim/validator.py`，每条规则一个 code（完整清单在模块说明里），例如 `stage_not_allowed`、`over_band`、`rewrites_fact`、`tom_order_exceeded`、`unregistered_option`。"含对方真实隐状态"在本方无法检查（本方从不持有别的角色的隐状态），以"证据必须可见"作为结构上的替代。

---

## 5. 一次 act 的调用链

```
IRoleAgent.act
└─ ITurnRunner.run
   ├─ 对每组新记录：
   │  ├─ S1 = IStage.run ── IEvidenceGate.build → IMechanism.evaluate × n → IModel.complete
   │  ├─ IStateStore.commit ── IUpdateOperator.propose → IMagnitude.range → IValidator.check
   │  └─ 若是反馈：S3 = IStage.run → IStateStore.commit
   ├─ S2 = IStage.run → IStateStore.commit
   ├─ IExpresser.realize → AgentActionContent
   └─ ITrace.phase / ITrace.model_call（每个阶段）
```

## 6. 实现进度（2026-11-07）

契约（Protocol + 数据类型）已全部在 `rolesim/interfaces.py`。下表只看**实现**：✅ 已实现　◐ 旧代码承担了一部分　✗ 没有。

| 接口 | 状态 | 现在由谁承担 | 还缺什么 |
|---|---|---|---|
| **1 IRoleAgent** | ✅ | `staged.StagedRoleAgent`（新）；`adapter.RoleAgentAdapter`（旧，CLI 默认） | 旧实现在第 7 步删除；新管线还没有消融档位（`mechanism.mode`） |
| 　（IEnvironment） | ✅ 本地替身 | `loop.EchoEnvironment` | 环境组实现，我们只调用 |
| 1.1 ITurnRunner | ✅ | `runner.TurnRunner` | “反馈”目前按受众规则判断，不由模型判断 |
| 1.2 ITrace | ✅ | `trace.JsonlTrace` / `MemoryTrace` | 离线工具（审计、评审包、可读版）还读旧格式（第 7 步） |
| **2 IRoleRepository** | ◐ | 过渡实现 `entry.RoleFileRepository`：手写角色文件里能一一对应到构念的模块（P1 分面、依恋、施瓦兹价值、核心目标、表达基线、社会角色）；入场时 `core_goals` → 当前目标、`history_memory` → 入场记忆 | 正式实现等档案编译（第 6 步）：来源类型、待审核清单、自然语言档案、不能自知字段的条件化语句 |
| 2.1 IProfileCompiler | ✗ | `persona.validate_persona` 只做校验 | 不能从自然语言档案生成；字段没有来源类型、置信度、待审核清单 |
| 2.2 IRegistry | ✅ | `registry.Registry` + `data/registry.json` | 待心理学组核对 `review` 清单；`state.OPERATORS` 仍是旧的手写局部版，第 2 步改为查本接口 |
| **3 IStage** S1 | ✅ | `stages.ModelStage("S1")` | 尚未接入运行路径（旧的 `backend.appraise` 仍供 `Agent.step` 使用，第 5 步替换） |
| 　S2 | ✅ | `stages.ModelStage("S2")` | 同上（旧的 `backend.decide`） |
| 　S3 | ✅ | `stages.ModelStage("S3")` | 什么算“对本角色此前行动 / 承诺的反馈”由 `ITurnRunner` 判断（第 5 步） |
| 3.1 IEvidenceGate | ✅ | `evidence.EvidenceGate` | 记忆检索是字面相似度（沿用 `memory.search`），不是语义检索 |
| 3.2 IMechanism | ◐ | 决策过程在阶段调用里由模型判断（schema 的 judgments），解析时查登记表 | 没有按规则实现的过程；旧的 5 张机制卡仍只供 `Agent.step` 使用；各过程输出字段要等 F2 |
| 3.3 IModel | ✅ | `model.ChatModel` / `model.MockModel`，输出 schema 与解析在 `schemas.py` | 旧的 `ChatBackend.appraise / decide / express` 仍供 `Agent.step` 使用（第 5 步替换）；结构化解码未接 |
| 3.4 IExpresser | ✅ | `expresser.ModelExpresser`（第 4 次模型调用 "express"） | 尚未接入运行路径（旧的 `backend.express` 仍在用） |
| **4 IStateStore** | ✅ | `store.ConstructStore` + `slots.py` | 尚未接入运行路径：旧的 `Agent.step` 仍用 `state.commit_phase`（第 5 步替换）；跨阶段累计上限、习惯化、按时间衰减要等场景时间单位（F3） |
| 4.1 IUpdateOperator | ✅ | `operators.Operator`（11 个，规则来自登记表） | 规则卡里要判断事件的部分（情绪方向、习惯化）由阶段提出、验证器检查，不在算子里计算 |
| 4.2 IMagnitude | ✅ | `magnitude.Magnitude` | 人格敏感度只接了无条件映射（P1 分面、依恋焦虑、认知灵活性）；P2 / P3、违约后的尽责性、自尊不稳定性要等档案形状（第 6 步） |
| 4.3 IValidator | ✅ | `validator.Validator` | “含对方真实隐状态”本方无法检查；规则卡里的单条软规则（如“无调节过程而强度大幅下降”）未收 |

### 6.1 旧代码里没有对应接口的部分

| 模块 | 是什么 | 打算 |
|---|---|---|
| `skills.py` | 角色技能（应对 / 说话习惯的提示，不写状态） | 和心理学组的"skill"（= 阶段）不是一回事。可作为 `IStage` / `IExpresser` 的输入保留，待定 |
| `briefing.py` | 角色入场设置：目标、想透露 / 想隐瞒的、前情 | 并入 `IRoleRepository.enter`；隐瞒内容只进 S2，不进 `IExpresser` |
| `runtime.py`、`contract.py` | 旧的环境契约 v0 路径（CLI 默认仍是它） | `ITurnRunner` 替换 `Agent.step` 后删除，CLI 默认改为 agent-api |
| `audit.py`、`package.py`、`readable.py` | 读运行目录的离线工具 | 跟着 `ITrace` 的新格式改 |

### 6.2 卡在外部的

F1—F5（见 [09 §7](09-interface-and-field-spec.md)）：〔工程〕结构、各决策过程的输出字段、时间单位、场景结构、第一版启用哪些模块。不影响先搭骨架，影响具体字段和规则。

另有 `data/registry.json` 的 `review` 清单（5 类，见 §2.2），需要心理学组逐条确认；确认后改原文或改对照表，再重新生成。

### 6.3 建议顺序

1. ~~`IRegistry`：抽取脚本 + 只读查询。其余接口都要查它。~~ 已完成（2026-11-07）。
2. ~~`IStateStore` + `IUpdateOperator` + `IValidator` + `IMagnitude`：状态改成按构念 ID，按登记表做权限和区间。~~ 已完成（2026-11-07），尚未接入运行路径。
3. ~~`IModel` 通用化，mock 能产出三个阶段的合规产物。~~ 已完成（2026-11-07），尚未接入运行路径。
4. ~~`IEvidenceGate` + 三个 `IStage` + `IExpresser`。~~ 已完成（2026-11-08），尚未接入运行路径。
5. ~~`ITurnRunner` 替换 `Agent.step`；`ITrace` 自己落盘。~~ 已完成（2026-11-08）：新管线可用（`--pipeline stages`），旧的仍是默认，第 7 步删除。
6. `IRoleRepository` / `IProfileCompiler`：入场状态和档案编译。
7. 删除旧的 v0 路径，离线工具改到新格式。

每一步结束时 `IRoleAgent` 都不变，现有测试保持通过。
