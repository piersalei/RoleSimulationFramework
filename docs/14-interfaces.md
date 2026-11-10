# 关键接口

框架面向接口组织：**4 个顶层接口，每个下面 2—4 个子接口**。实现可以替换，接口不变。本文给出每个接口的签名、关键类型、前置条件、保证和出错方式，以及现在由谁实现。

- 代码形式：[`rolesim/interfaces.py`](../rolesim/interfaces.py)（Protocol + 不可变数据类型），测试 `tests/test_interfaces.py`。本文与代码不一致时，以本文为准修改代码。
- 字段细节：状态布局以 [`rolesim/state/slots.py`](../rolesim/state/slots.py) 为准；完整的字段规格草案（待心理学组确认后冻结）在 [plan/05](../plan/05-field-spec-draft.md)。
- 设计依据：设计约束 C1—C8 见 [01-design.md](01-design.md) 第 2 节。接口是怎么演变到现在这样的，见 [16-history.md](16-history.md)。
- 还没实现、正在计划的部分在 [plan/01](../plan/01-engineering-backlog.md)。

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
  3.4 IExpresser         行动计划 → 对外的行动内容
4 IStateStore          状态的唯一写入口
  4.1 IUpdateOperator    11 个更新算子：判断 → 变化槽位
  4.2 IMagnitude         合法变化区间（选值之前算好）
  4.3 IValidator         硬错 / 软异常检查
  4.4 IValueChooser      在区间内选值
```

依赖方向只能向下或同层：`IRoleAgent → ITurnRunner → IStage / IStateStore`。

**分工（设计约束 C8）**：模型只在两处被调用，一是阶段调用（产物 + 决策过程的判断），二是选值调用（在程序给的区间内选一个值并给理由）。改哪个构念、哪个字段、往哪个方向、按几级事件，由更新算子按规则卡决定；合法区间由 `IMagnitude` 在选值前算好；能不能写入由 `IValidator` 和 `IStateStore` 决定。模型从不写构念 ID 或状态地址。

| 谁 | 做什么 | 不做什么 |
|---|---|---|
| 模型（阶段调用） | 产物（情境解释 / 行动计划 / 演化结果）；决策过程的判断：选项、本角色的事件等级、方向、对象、依据的记录、带内容的候选（信念命题、他心假设、承诺内容） | 不选构念 ID、键、字段；不写数值 |
| 更新算子（harness） | 按规则卡从判断和产物生成变化槽位：地址、变化前的值、方向、事件等级、依赖组、证据、依据的过程；记录型构念（信念、他心推断、承诺、记忆条目）由算子组装 | 不臆造判断；规则卡没写清的地方不写（记「未写」），不用默认值代替 |
| `IMagnitude`（harness） | 在选值之前给每个数值槽位算合法区间 | — |
| 模型（选值调用） | 每个数值槽位在区间内选一个值，写一句理由；每个阶段至多一次，批量处理 | 不能改地址、方向和区间 |
| `IStateStore`（harness） | 校验（区间外硬拒、不截断）；按依赖组原子提交 | — |

---

## 1 `IRoleAgent`（对外）

环境组定义的接口，签名不能改。环境组文档里叫 `AgentAdapter`，对面一侧叫 `EnvironmentAdapter`；我们统一命名为 `IRoleAgent` / `IEnvironment`（定义在 `agent/agent_api.py`，`interfaces.py` 再导出）。`IEnvironment` 由环境组实现，我们只在本地运行器里调用。数据类型、校验规则和双方约定见 [13-agent-interface.md](13-agent-interface.md)。

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
| 前置 | act 之前，同一 `interaction_id` 的所有角色都已 initialize |
| 保证 | 同一 `request_id` 返回同一结果；不返回任何内部状态；他人的思考不进入本角色的任何提示 |
| 出错 | 抛 `InterfaceError`（输入不合规）或 `AgentCallError`（内部失败），状态不前进 |
| 实现 | `agent/staged.py` `StagedRoleAgent`（唯一实现），基于 `agent/adapter.py` `RoleAgentBase`（选角色、按 `interaction_id + character_id` 的会话、`request_id` 重试、串行）；`bootstrap.mechanism.mode` 选每个角色的消融模式 |

`initialize` 选角色、记下 bootstrap。第一次 `act` 时才知道在场者，这时调 `IRoleRepository.load / enter` 得到入场状态，再 `ITurnRunner.begin`。之后每次 `act` 取出没吸收过的记录，交给 `ITurnRunner.run`。

### 1.1 `ITurnRunner`：一次 act 里怎样走三个阶段

```python
class ITurnRunner(Protocol):
    def begin(self, key: SessionKey, state: AgentState, stable: StableProfile,
              background: Optional[Mapping[str, Any]] = None, mode: str = FULL_MODE,
              skills: tuple = (), briefing: Optional[Mapping[str, Any]] = None) -> None: ...
    def run(self, key: SessionKey, new_records: list[InteractionRecord],
            present: dict[str, Present]) -> tuple[AgentActionContent, AgentState]: ...
```

| | 内容 |
|---|---|
| 输入 | `begin`：入场状态、稳定画像、情境背景、消融模式、绑定的人格 skill、开场设定。`run`：`SessionKey = (interaction_id, character_id)`、本次新看到的记录（已去掉吸收过的和别人的思考）、当前在场者 |
| 输出 | 对外的行动内容 + 最终提交的状态 |
| 流程 | 新记录按 `action_id` 分组，每组一个事件阶段，跑 S1；若该组是对本角色上次计划的反馈，再跑 S3。全部处理完后跑 S2，再 `IExpresser.realize`。每个阶段都是同样四步：阶段调用 → `IStateStore.draft` → `IValueChooser.choose` → `IStateStore.commit` |
| 什么算反馈 | 上次计划的受众（计划没指定受众时为除自己和环境以外的任何人）之后第一组本角色收得到的发言或行动。发言默认在场者都收到，所以多人场景里，受众的下一句公开发言（不论对谁说）都算反馈。每个计划只判断一次 |
| S2 被拒 | 计划自己的依赖组（目标、承诺、策略）有硬错：行动不按原样发出，在新阶段重做 S2，把拒绝原因放进 `upstream["rejected"]`；第二次仍被拒，整次 act 失败（行动计划 skill §4.3） |
| 保证 | 每个阶段读上一次提交后的快照；同一阶段内不读未提交的候选 |
| 出错 | 任一阶段失败即整次 act 失败；已提交的阶段保留，未提交的丢弃。选值调用失败不算阶段失败：那些数值槽位不写（`no_value`），行动照常 |
| 重试 | 已提交 S1 的记录组算已吸收；没做完的反馈判断、已提交但没表达出去的计划都记着，重试时接着做，不会把同一阶段提交两次 |
| 实现 | `agent/runner.py` `TurnRunner(store, stages, expresser, chooser, trace=None, replans=1)` |

### 1.2 `ITrace`：轨迹日志

```python
class ITrace(Protocol):
    def phase(self, key: SessionKey, log: PhaseLog) -> None: ...
    def model_call(self, key: SessionKey, call: ModelCallRecord) -> None: ...
```

| 类型 | 关键字段 |
|---|---|
| `PhaseLog` | event_id、phase_no、input_state_id、output_state_id、evidence、judgments、products、changes、commit（含 skipped）、versions |
| `ModelCallRecord` | stage（S1 / S2 / S3 / values / express / profile）、input（PromptView）、raw_output、reasoning |

只写不读，没有写状态的权限。实现：`agent/trace.py`。`JsonlTrace(root)` 写 `<root>/<interaction_id>/<character_id>/phases.jsonl`（每次提交一条）和 `model_calls.jsonl`（每次模型调用的视图、原始输出、推理）；`MemoryTrace` 在内存里收同样的记录。这份记录由角色一侧自己写，不依赖运行器。

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
| `enter` | 生成入场状态，依次取：档案入场状态 → 开场设定的目标与前情 → bootstrap（core_goals → 当前目标，history_memory → 入场记忆）→ 角色状态说明 §8.2 的默认值；没有依据的字段为 unknown。每个值的来源记在 `entry_notes[id]` |
| 关键类型 | `StableProfile`：snapshot_id、modules（`{construct_id: StableModule}`）。`StableModule`：取值、状态标记（六种）、来源类型（explicit / inferred / model_inferred / default / unknown）、置信度、needs_review、原文依据、条件化语句（phrase）及理由。`AgentState`：state_id、prev_state_id、stable_snapshot_id、dynamic、memory（都按构念 ID 存）、versions、status。`EnableConfig`：每个模块 / 决策过程是否启用 |
| 出错 | 角色不存在、档案结构错 → 抛错；缺证据不报错，写 unknown |
| 实现 | `role/repository.py` `ProfileRepository`：`profiles/<id>.json` 优先，否则把角色文件转换后编译；待审核项在 `review[id]`；另提供开场设定与绑定的人格 skill |

§8.2 默认值：情绪强度 0、标签空，效价与唤醒取情绪基线；压力与威胁 / 奖赏激活无额外负荷；调节 inactive；羞耻与面子成本 0；自我评价取自尊基线；心理理论上限按 71 维 1.1.4 / 1.5.2 和年龄（当前值 = 上限，待审核）；激活、威胁、冲突、修订候选为零；目标承诺取核心目标优先级；生命经历写成入场记忆；其余 unknown。

### 2.1 `IProfileCompiler`：档案 → 稳定画像

```python
class IProfileCompiler(Protocol):
    def compile(self, raw: str | dict, config: EnableConfig) -> tuple[StableProfile, list[ReviewItem]]: ...
```

| | 内容 |
|---|---|
| 输入 | profile-1 档案（格式见 [04-authoring.md](04-authoring.md) 第 6 节），可带自然语言原文 |
| 输出 | 画像 + 待审核清单（`ReviewItem`：construct_id、value、source_kind、原文依据、原因） |
| 规则 | 明写 → explicit；规则推断 → inferred；模型推断 → model_inferred（引文必须逐字出现在原文里，否则进待审核清单）；无依据 → unknown，不随机补值。人格方案三选一；71 维未写取 0（default）；条件化语句不得含系统 ID、数字或"总是 / 绝不" |
| 待审核 | 非明写的高影响字段、不能自知又没有语句的构念、没被采用的推断、被去掉的语句 |
| 实现 | `role/profile.py` `ProfileCompiler`；自然语言原文经 `IModel` 的 "profile" 调用读取 |

### 2.2 `IRegistry`：心理学登记表查询

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
| 实现 | `role/registry.py`：`build_registry()` 从 `data/sources/psychology_v1/` 抽取，`python3.11 -m rolesim registry` 写成 `data/registry.json`（87 个构念、75 个决策过程、11 个算子规则）；`Registry.load()` 读这个文件 |
| 抽取方式 | 结构规整的部分机械抽取：构念与决策过程条目、算子写入白名单、算子的时间与优先级、每个阶段可调用的算子和决策过程、机制卡。只写在叙述里的部分放在一张带原文引语的对照表里：依赖组、快慢变归类、衰减规则、自知程度、高影响字段，以及 12 个决策过程的可选项断句修正 |
| 保证 | 只读；`basis` 记录每个属性出自哪份文件、哪一节、哪句话；`registry.json` 与原文不一致时测试失败 |
| 出错 | 未登记的 ID → `KeyError`。原文改动使对照表的引语找不到、§九多出一条、声明的数量对不上、一个构念出现两个主写入算子 → 构建失败（`RegistryBuildError`），需要人工重读后再生成 |
| 不抽取的 | 字段的取值形式和范围（留给 `state/slots.py`）；决策过程读取哪些构念（原文只有中文描述）；§4.10 的先验映射表；幅度分档的数值（在 `state/magnitude.py`） |

生成时发现的问题记在 `registry.json` 的 `review` 里，待心理学组确认，见 [plan/02](../plan/02-psychology-questions.md)。

---

## 3 `IStage`：三个心理阶段

三个阶段实现同一个接口，只有产物类型不同。阶段**只判断，不写状态**。

```python
class IStage(Protocol[P]):
    name: Literal["S1", "S2", "S3"]
    def run(self, ctx: StageContext) -> StageOutput[P]: ...
```

| 类型 | 关键字段 |
|---|---|
| `StageContext` | phase（event_id、phase_no、kind）、state（本阶段唯一读的快照）、stable、records（本阶段的记录组）、history（之前看到过的记录，作提示上下文，也可作证据）、background（initialize 时告诉角色的场景与自身情况）、upstream（已提交的上游产物：S2 拿到最新的 S1 产物、线索和上次计划；S3 拿到计划和反馈的 S1 产物）、present、key、mode（消融模式）、skills、briefing |
| `StageOutput[P]` | product: P、judgments（[Judgment]，每个阶段至多 6 条）、evidence（本阶段能用的 EvidenceSet）、cues（只有 S1：五种线索强度 0—1，只用来激活人格 skill，从不写状态） |

三个产物的必备字段：

| 阶段 | 产物 `P` | 必备字段 |
|---|---|---|
| S1 事件建模 | `SituationInterpretation` | situation_definition、appraisal（受影响的目标及收益 / 损失 / 威胁 / 挑战）、attribution、hypotheses（1—3 个，各带 confidence、支持与反向证据、区分条件，可带 about 与 kind）、judgment_state（定论 / 多假设 / 暂缓）、information_needs、relation_signals |
| S2 行动计划 | `ActionPlan` | candidates（含被筛掉的及约束来源）、chosen、fallbacks、switch_conditions、depends_on、expected_outcomes、commitment（是否形成及内容）、expression（表达目标、受众、方式，可标 private） |
| S3 长期演化 | `EvolutionResult` | expected_vs_actual、commitment_rulings、relation_course、learning_attribution、carry_forward |

| | 内容 |
|---|---|
| 前置 | `ctx.state` 是已提交快照；S2 在 S1 产物提交之后运行；S3 只在有反馈时运行 |
| 保证 | 产物生成后只读；不读本角色不可见的内容 |
| 出错 | 模型失败或输出不合 schema / 登记表 → `StageError`，整阶段不产生任何东西 |
| 实现 | `stage/stages.py` `ModelStage(name)`：阶段与 phase.kind 对应（S1 ↔ event、S2 ↔ plan、S3 ↔ feedback），S3 要求 `upstream["plan"]`；`IEvidenceGate` → `PromptView` → `IModel` → 写 `ITrace`（在解析之前，失败的输出也留下）→ 解析；S2 的受众必须是在场的其他人 |

提示里的画像只含自知层：登记表里能自知 / 部分自知的稳定模块（有条件化语句时用语句代替数值）；不能自知（或来源没写）的模块只以条件化语句出现在"说不出来、但会影响你的倾向"下，不带名字和数值，没有语句就不进任何提示。

### 3.1 `IEvidenceGate`：本角色能用哪些证据

```python
class IEvidenceGate(Protocol):
    def build(self, ctx: StageContext) -> EvidenceSet: ...
```

| 类型 | 关键字段 |
|---|---|
| `EvidenceSet` | included（[EvidenceRef]）、excluded（[{event, reason}]）、retrieved_memories |
| `EvidenceRef` | event（记录编号 `action_id#order_index`）、channel（亲见 / 听说 / 推断 / 本人行动 / 入场前）、claim（听说时转述的命题） |

实现：`stage/evidence.py` `EvidenceGate`。本角色是行动者或被明确发送的记录进 included（本人的为 `own_action`，其余 `witnessed`）；排除原因 `other_thought`（他人的思考，即使被误发过来）、`own_thought`（本人的思考是过程记录，不是证据）、`not_recipient`；按刚看到的内容取回至多 4 条记忆（`m.entry`，字面相似度 × 重要性 × 可及性，`stage/memory.py`）。

S1 可以在 `evidence_notes` 里把某条记录的内容标为转述（hearsay，须写出被转述的命题）或推断，证据只能降级。想把证据改强、或标在本人行动上的备注直接忽略；标在看不到的记录上则阶段失败。

### 3.2 `IMechanism`：一个决策过程

```python
class IMechanism(Protocol):
    dm_id: str
    def evaluate(self, ctx: StageContext, evidence: EvidenceSet) -> Judgment: ...
```

| 类型 | 关键字段 |
|---|---|
| `Judgment` | dm_id、option（登记的可选项之一或 null）、output（本过程的中间判断）、evidence、hands_to（交给哪些算子，由登记表填） |

没有写权限。接口允许规则实现或模型实现；**现在只有模型实现**：决策过程的判断在阶段调用的输出里（只有 `profile_state_mechanisms` 模式），每个过程一个 schema 分支，只能取本阶段可调用的过程和它自己的选项。

有机制卡的 6 个过程带类型化输出（`stage/schemas.py` 的 `JUDGMENT_OUTPUTS`），更新算子读这些字段；其余过程的 output 是一层标量的扁平对象：

| 决策过程 | 输出字段 | 读它的算子 |
|---|---|---|
| `dm.affect.primary_secondary_appraisal` | grade（1—4）、emotions（情绪标签）、valence、about | emotion_update |
| `dm.cog.belief_updating` | beliefs：[{belief_id, proposition, kind, about, change（新形成 / 增强 / 减弱 / 悬置）, confidence, grade}] | belief_revision |
| `dm.cog.attribution_responsibility` | event、about、locus、stability、globality、controllability、intent、self_share、others_share、confidence | belief_revision |
| `dm.social.trust_reputation_update`、`dm.coop.trust_building` | about、dimension（能力 / 善意 / 诚信 / 可靠）、direction、grade | relation_update |
| `dm.conflict.relationship_repair` | about、assessment（无 / 部分 / 完全）、outcome | 关系修复（规则卡未实现，记「未写」） |

### 3.3 `IModel`：结构化模型调用

```python
class IModel(Protocol):
    def complete(self, stage: str, view: PromptView, schema: dict) -> ModelResult: ...
```

| 类型 | 关键字段 |
|---|---|
| `PromptView` | self_profile（只含自知层，不能自知的已转成条件化语句）、records（可见记录）、state_text（当前状态的文字描述）、upstream、self_id、present、task（程序按登记表生成：本阶段可调用的决策过程及其选项、输出字段、事件等级说明；选值调用里是槽位说明）、background |
| `ModelResult` | output（符合 schema 的 dict）、raw、reasoning |

| | 内容 |
|---|---|
| 调用 | S1 / S2 / S3（阶段）、values（选值）、express（表达）、profile（档案原文）。失败即抛 `ModelError`，不回退 mock |
| 实现 | `stage/model.py`：`ChatModel`（chat-completions 传输在 `stage/providers.py`；每个调用一段系统提示，载荷是 PromptView + task + 精简 schema）；`MockModel`（离线夹具，各调用都给出合 schema、合登记表的输出；选值取区间中点；内容不是心理学证据） |
| 输出 schema | `stage/schemas.py` `stage_schema(stage, registry)`：product（产物必备字段）、judgments（anyOf，每个过程一个分支）；S1 另有 evidence_notes 和 cues。schema 里不用 `pattern` |
| 结构化解码 | 默认把 schema 作为 `response_format: json_schema` 发给服务端（`ROLE_SIM_STRUCTURED=0` 关闭）。每次调用把 schema 收窄：证据只能取本次可见的记录 ID，S2 受众只能取在场的其他人。提示里只放精简版（约 2—3k 字符），完整 schema 约 11—15k 字符只给解码器。只取第一个 JSON 值，丢弃的尾部计入 manifest 的 `trailing_dropped` |
| 解析 | 先按 schema 校验，再查登记表（option 必须是该过程的可选项；转述必须写出命题；证据只能降级）；不满足 → `StageError`。回复无法解析时原始回复也写进轨迹 |

### 3.4 `IExpresser`：行动计划 → 对外行动内容

```python
class IExpresser(Protocol):
    def realize(self, plan: ActionPlan, ctx: StageContext) -> AgentActionContent: ...
```

实现：`stage/expresser.py` `ModelExpresser`，一次 `IModel.complete("express", …)`。

- 给：身份、核心目标、表达基线（只限能自知的）、不能自知模块的条件化语句、感受（情绪标签 + 低 / 中 / 高，不带数值；`profile_only` 时没有）、最近 12 条可见记录、计划里的主行动 / 表达目标 / 受众 / 方式 / 承诺、开场设定里可以说出的部分、被激活 skill 的表达要点。
- 不给：状态数值、构念和过程 ID、其他候选及其约束、假设、开场设定里不打算说的事。
- 回复按 `schemas.EXPRESS_SCHEMA` 解析，再用环境组的 `validate_action_content` 检查。思考只发本人；发言和动作默认发给在场所有人（受众只表示对谁说）；计划把表达标为 `private` 时才只发给受众。

---

## 4 `IStateStore`：状态的唯一写入口

```python
class IStateStore(Protocol):
    def open(self, key: SessionKey, state: AgentState) -> None: ...          # 入场状态
    def snapshot(self, key: SessionKey) -> AgentState: ...
    def draft(self, ctx: StageContext, output: StageOutput) -> tuple[list[ChangeSlot], list[Skipped]]: ...
    def commit(self, ctx: StageContext, changes: Sequence[StateChange], evidence: EvidenceSet,
               skipped: Sequence[Skipped] = (), unvalued: Sequence[RejectedChange] = ()) -> CommitResult: ...
```

| 类型 | 关键字段 |
|---|---|
| `ChangeSlot` | slot_id、construct_id、key、field、before、direction（+1 / −1；记录型为 0）、grade、band、evidence、update_operator、decision_mechanisms、stage、group、basis（依据规则卡哪一条）、content、value_range |
| `Skipped` | update_operator、reason、detail：规则卡会写、但这一版没写的变化 |
| `StateChange` | construct_id、key、field、before、after、evidence（非空）、update_operator、decision_mechanisms、stage、band、group、reason、flag、grade |
| `CommitResult` | output_state_id、accepted、rejected（[{change, group, error}]）、soft_flags、skipped |

槽位有三种：

| 种类 | 条件 | 怎样得到值 |
|---|---|---|
| 数值 | `band` 有值，`before` 是数 | `IValueChooser` 在区间内选变化量 |
| 首次值 | 原值未知：`band` 为 None、`before` 为 None、`value_range` 有值 | `IValueChooser` 在取值范围内选首次值（未知不等于 0）；有符号的字段（效价）范围只在判断指向的一侧 |
| 固定 | `content` 有值 | 算子组装好的记录或类别（新信念、承诺、承诺阶段），原样写入 |

| | 内容 |
|---|---|
| `draft` | 本阶段允许的每个算子读判断和产物，返回槽位（区间已算好）和「未写」；同一地址的多个槽位：同方向合并、取最高等级，方向相反或内容不同则本阶段不写（`conflict_unresolved`）。不写任何东西 |
| `commit` | `IValidator` 逐条检查 → 按依赖组原子提交；没变的值丢弃 |
| 保证 | 只有这里能产生新 `AgentState`；stable 永远不写；`ctx.state` 必须是当前快照；同一 `(角色, 事件, 阶段, kind)` 重试返回第一次的结果，载荷不同则拒绝；交出去的快照是副本；写入过的 unknown 构念转为 active |
| 出错 | 某依赖组有硬错 → 整组拒绝，其他组照常提交；软异常照写并标记。两项看历史的软检查：无新证据却反复翻转、四个关系维度同比例变化 |
| 实现 | `state/store.py` `ConstructStore`；状态布局 `state/slots.py`（45 个 dynamic / memory 构念的键、字段、取值形式、哪些字段有幅度档、只追加 / 固定事实） |

### 4.1 `IUpdateOperator`：11 个更新算子

```python
class IUpdateOperator(Protocol):
    name: str                         # emotion_update、belief_revision、… 共 11 个
    def propose(self, ctx: StageContext, output: StageOutput) -> tuple[list[ChangeSlot], list[Skipped]]: ...
```

只读交给本算子的判断和产物，按规则卡决定写不写、写哪里、往哪个方向、按几级事件，并向 `IMagnitude` 要区间；从不从模型取值。白名单、阶段权限、依赖组来自登记表（例如 relation_update 只能在 S3）。字段剩下的空间比最低档还小时不写（`no_room`）；字段快到头时区间只给剩下的部分（值本身不截断）。

实现：`state/operators.py`，`Operator` 一个类加每个算子的规则函数（`RULES`）。已实现 7 个：

| 算子 | 阶段 | 读什么 | 写什么 |
|---|---|---|---|
| emotion_update | S1 | 评价判断 | 情绪标签；强度、唤醒上升；效价按判断的正负；损失 / 威胁时压力上升 |
| belief_revision | S1 | 信念更新、归因判断 | 新信念记录；已有信念的确信度升降；归因记录（记录里的确信度取自判断，是内容不是幅度） |
| other_model_update | S1 | S1 产物里带 about 的假设 | 他心推断记录（一阶） |
| relation_update | S3 | 两个信任判断 | 信任 |
| commitment_update | S2、S3 | S2 计划形成的承诺；S3 的承诺裁定 | 承诺记录；承诺阶段（兑现 / 违约 / 撤销） |
| strategy_learning | S2 | S2 选中的过程选项 | 当前策略 `dm/对象` |
| memory_update | S3 | S3 产物 | 记忆条目 |

其余 4 个（self_state_update、group_state_update、goal_update、decay_or_recovery）和关系修复规则还没有规则：有判断交给它们时记「未写」（`not_implemented`），不写。

### 4.2 `IMagnitude`：合法变化区间

```python
class IMagnitude(Protocol):
    def range(self, construct: Construct, field: Optional[str], grade: int, direction: int,
              evidence: Sequence[EvidenceRef], stable: StableProfile, instance: Any = None) -> Optional[Band]: ...
```

`Band`：name（微小 / 小 / 中 / 大 / 扩展）、low、high、extended（只有四级事件有：落在 (high, extended] 的值照写，标软异常 `extended_range`）。

按事件等级取基准区间（状态变化规则 §5.0：效价 ×2、当前自我评价 ×4）；人格调节最多移一档（敏感度分面合并后 |z| ≥ 1.5 时移到相邻档的半区）；只有听说的证据最多到"中"，只有推断的最多到"小"。不属于幅度档的字段（类别、文字、未归快慢变的构念）返回 None。

实现：`state/magnitude.py`。人格敏感度现在只接 P1 分面、依恋焦虑、认知灵活性这几条无条件的映射。所有区间都是心理学组的初始值，未标定。

### 4.3 `IValidator`：硬错 / 软异常检查

```python
class IValidator(Protocol):
    def check(self, change: StateChange, snapshot: AgentState,
              evidence: EvidenceSet) -> list[Violation]: ...
```

`Violation`：kind（hard / soft）、code、detail。

- 硬错：证据为空或不可见、越出算子白名单或阶段权限、写 stable、写非 active 字段、before 与快照不一致、删除伤害事实或记忆、心理理论阶数超限、取值形式或范围不对、越出幅度档、策略取值不是登记的选项。
- 软异常：超出典型区间（扩展区间）、维度同比例联动、无新证据却反复翻转。

实现：`state/validator.py`，每条规则一个 code（如 `stage_not_allowed`、`over_band`、`rewrites_fact`、`tom_order_exceeded`、`unregistered_option`，完整清单在模块说明里）。"含对方真实隐状态"本方无法检查（本方从不持有别的角色的隐状态），以"证据必须可见"作结构上的替代。

### 4.4 `IValueChooser`：在区间内选值

```python
class IValueChooser(Protocol):
    def choose(self, ctx: StageContext, output: StageOutput,
               slots: Sequence[ChangeSlot]) -> tuple[list[StateChange], list[RejectedChange]]: ...
```

需要取值的槽位每个在区间（或取值范围）内得到一个值和一句理由；固定槽位直接转成 `StateChange`。拿不到合法值的槽位作为 `RejectedChange`（`no_value`）返回，不写；同一依赖组的其他变化随之一起不写。没有数值槽位时不调用模型。

实现：`state/chooser.py` `ModelValueChooser(registry, model, trace)`，一次 `IModel.complete("values", …)`：

- 模型看到：自知层画像、状态文字、槽位引用的记录、本阶段产物，以及每个槽位的说明（构念中文名、字段、方向、变化幅度范围或取值范围、依据）。不看到构念 ID。
- 解码 schema（`values_schema`）里每个槽位一个分支，`change` 的上下限就是该槽位的区间（首次值则是 `value` 的范围），越界的数解不出来；本地再查一遍，越界拒绝、不截断。

---

## 5. 一次 act 的调用链

```
IRoleAgent.act
└─ ITurnRunner.run
   ├─ 对每组新记录：
   │  ├─ S1：IStage.run ── IEvidenceGate.build → IModel.complete（产物 + 判断）
   │  │      IStateStore.draft ── IUpdateOperator.propose × n → IMagnitude.range
   │  │      IValueChooser.choose ── IModel.complete("values")（有数值槽位时）
   │  │      IStateStore.commit ── IValidator.check → 按依赖组提交
   │  └─ 若是反馈：S3，同样四步
   ├─ S2，同样四步（计划自己的依赖组被拒 → 带原因重做一次）
   ├─ IExpresser.realize → AgentActionContent
   └─ ITrace.phase / ITrace.model_call（每个阶段、每次调用）
```

## 6. 接口与实现一览

✅ 已实现　◐ 部分实现。缺的部分怎么补在 [plan/01](../plan/01-engineering-backlog.md)。

| 接口 | 状态 | 实现 | 还缺什么 |
|---|---|---|---|
| **1 IRoleAgent** | ✅ | `agent/staged.py` `StagedRoleAgent`（基于 `agent/adapter.py` `RoleAgentBase`） | 接口没有结束对话的信号；`enable_config` 只记录 |
| （IEnvironment） | 本地替身 | `run/loop.py` `EchoEnvironment` | 由环境组实现 |
| 1.1 ITurnRunner | ✅ | `agent/runner.py` `TurnRunner` | "反馈"按受众规则判断，不由模型判断 |
| 1.2 ITrace | ✅ | `agent/trace.py` `JsonlTrace` / `MemoryTrace` | — |
| **2 IRoleRepository** | ✅ | `role/repository.py` `ProfileRepository` | 权力差、群体成员资格要等场景结构 |
| 2.1 IProfileCompiler | ✅ | `role/profile.py` `ProfileCompiler` | §4.10 决策偏好先验映射、派生字段；示例角色没有 profile-1 档案 |
| 2.2 IRegistry | ✅ | `role/registry.py` `Registry` + `data/registry.json` | `review` 清单待心理学组确认 |
| **3 IStage** | ✅ | `stage/stages.py` `ModelStage("S1" / "S2" / "S3")` | — |
| 3.1 IEvidenceGate | ✅ | `stage/evidence.py` `EvidenceGate` | 记忆检索是字面相似度，不是语义检索 |
| 3.2 IMechanism | ◐ | 在阶段调用里由模型判断；6 个过程有类型化输出 | 没有规则实现；其余过程的输出字段等心理学组 |
| 3.3 IModel | ✅ | `stage/model.py` `ChatModel` / `MockModel`；`stage/schemas.py`；`stage/providers.py` | — |
| 3.4 IExpresser | ✅ | `stage/expresser.py` `ModelExpresser` | 真实模型下后几轮台词重复 |
| **4 IStateStore** | ✅ | `state/store.py` `ConstructStore` + `state/slots.py` | 跨阶段累计上限、习惯化（要场景时间单位） |
| 4.1 IUpdateOperator | ◐ | `state/operators.py`：7 个算子有规则 | self_state、group_state、goal、decay、关系修复 |
| 4.2 IMagnitude | ✅ | `state/magnitude.py` `Magnitude` | P2 / P3、违约后的尽责性、自尊不稳定性的人格映射 |
| 4.3 IValidator | ✅ | `state/validator.py` `Validator` | 规则卡里的单条软规则（如"无调节过程而强度大幅下降"） |
| 4.4 IValueChooser | ✅ | `state/chooser.py` `ModelValueChooser` | 真实模型几乎总取区间上端 |
