# 与 Rise 对接（数据生成与对手驱动）

> **已被取代（2026-11-12）。** 不再从本仓库对接 Rise，改为在 Rise 里新建角色模拟模块。本文保留作 Rise 数据模型和字段对照的参考。

2026-11-10 起草，2026-11-11 改为“我们驱动、导出给 Rise”。**只有设计，没有实现。**

Rise（`/dataset-2041350733043007488/leichenhao/Rise`，老师的"社会心智后训练工厂"）把情境仿真、权重训练、诊断评测、配方沉淀和智能体自进化连成一个闭环。我们以**独立包**参与，不并入 Rise 仓库，两边通过调用互相适配。参与方式先做**数据生成**，能做到的话再做**对手驱动**：两者都属于 Rise 里"智能体模拟"的部分。

两者的接法不同（第 3 节）：
- **数据生成由我们驱动**：用我们自己的运行器跑完整场，再把结果导出成 Rise 的 `Episode` 和数据集清单。我们的信息一点不丢，交互也不被 Rise 的规则压扁。
- **对手驱动由 Rise 驱动**：Rise 训练时必须控制轮次，只能经它的后端接口调我们，压缩只在这里接受。

我们自己的字段见 [docs/17-data-fields.md](../docs/17-data-fields.md)。下面先列 Rise 的字段，再说两边怎么对应、要做什么。

## 1. Rise 的数据模型

都在 `src/simulator/datamodel.py`，是普通 dataclass，序列化成 git 树里的 YAML 产物。

| 类型 | 字段 | 说明 |
|---|---|---|
| `Persona` | `id`、`traits: [str]`、`motivation: str`、`speech_style: str`、`values: [str]`、`source`（auto / external） | 人格模板，和情境完全解耦。全部是文本，直接写进 system 提示 |
| `Scenario` | `id`、`background`、`role_slots: [RoleSlot]`、`conflict`、`norms: [str]`、`purpose`（training / deployment_simulation）、`source`、`opponent_script` | 社会情境 |
| `RoleSlot` | `slot_id`、`description` | 情境里的抽象角色位 |
| `RoleBinding` | `slot_id`、`persona_id`、`model_backend`（self_play / frozen_reference / external_api）、`model_ref`、`driver`（vllm / external_api / scripted） | 把角色位绑到人格和模型后端 |
| `RoleAssignment` | `scenario_id`、`bindings`、`max_turns` | 运行器的核心输入；绑定顺序就是发言轮转顺序 |
| `TurnRecord` | `turn_index`、`speaker_slot`、`utterance`、`inner_thought`、`activation_trace: dict` | 一轮一条；只有发言，没有接收者和动作 |
| `Episode` | `id`、`scenario_id`、`role_assignment`、`turns`、`terminated_reason`（max_turns / goal_reached） | 一次多轮交互；训练数据的单位 |

数据集与训练：

| 类型 | 字段 | 说明 |
|---|---|---|
| `DatasetManifest`（`simulator/dataset.py`） | `id`、`run_id`、`name`（sft / grpo_opd）、`episode_refs [{id, sha256}]`、`manifest_hash` | 数据集清单；episode 逐条进 git |
| GRPO 分组键 | `scenario_id` + 每个绑定的 `(slot, persona, backend, model_ref)` | 同一上下文的多条 rollout 为一组 |
| `TrajectoryBatch`（`mindtrainer/datamodel.py`） | `episodes`、`metadata` | 轨迹源交给奖励管线的东西 |
| `RewardedTrajectory` | `episode`、`group_id`、`per_turn_rewards`、`combined_reward` | 奖励管线的输出 |

智能体层（`src/simple_actio`，Rise 自己说明是正式版 Actio 的简化替身）：

| 类型 | 字段 |
|---|---|
| `Skill` | `id`、`version`、`level`、`trigger`（五算子谓词）、`policy_instructions`、`priority`、`description` |
| `ReasoningExperience` | `id`、`version`、`pattern`、`applies_when`、`primary` / `secondary` / `fallback`（各带 `instructions`、`when`） |
| `MemoryHit` / `MemoryEvent` | `id`、`text` / `key`、`text`、`interlocutor` |
| 激活轨迹（`simple_actio.trace.v2`） | `schema`、`signals`、`candidates`、`selected`、`conflict`、`memory_hits`、`experience {id, tier}`、`library_version {skill, memory}`、`memory {digest_before, remembered, extraction_failed}`；叶子只允许 str / None / bool |

所有产物外面套统一信封：`id`、`type`、`version`、`created_at`、`created_by {owner, visibility}`、`status`、`schema_version`、`links`、`payload`。

**后端接口**（对手驱动用它，见第 5 节）：

```python
class ModelBackend(Protocol):                      # simulator/backends.py
    def respond(self, persona: Persona, scenario: Scenario,
                history: list[TurnRecord], speaker_slot: str) -> tuple[str, str | None]: ...
# run_episode 也接受三元组 (utterance, inner_thought, activation_trace: dict)
```

`run_episode` 每轮把完整历史（抹掉别人的 inner_thought 和激活轨迹）交给当前角色位的后端。Rise 按结构识别后端，不要求继承或导入。

## 2. 两边对比

| 方面 | Rise | 我们 |
|---|---|---|
| 人格 | 4 个文本字段 | 42 个稳定构念，带状态标记、来源类型、条件化语句 |
| 心理状态 | 没有（每轮只有技能和记忆拼成的几行"当前要点"） | 42 个动态构念 + 3 个记忆构念，每次变化都有证据和区间 |
| 一轮的过程 | 一次模型调用 | S1 / S3 / S2 / 表达，加上选值，约 5 次调用 |
| 交互记录 | 只有发言，所有人都听得到 | 思考 / 发言 / 动作，带接收者 |
| 情境 | 背景、冲突、规范、角色位 | 背景、座位、可选环境 agent、世界私有数据 |
| 记忆 | Starling（语义召回）或替身 | `m.entry` + 字面相似度检索 |
| 输出给训练的 | `Episode`（话语序列） | 话语序列 + 每一步的结构化判断、产物、状态变化 |

## 3. 为什么数据生成不走后端接口

先区分两种"丢信息"：

- **我们自己的记录不会丢**，不管哪种接法。每个阶段提交时 `ITurnRunner` 都把 PhaseLog 写进 ITrace（运行目录的 `phases.jsonl` 等），和 `act` 返回什么无关；环境组调我们时也是这样。
- **交给 Rise 的那份会少**，不管哪种接法：`TurnRecord` 只装得下发言、内心独白和一个字典。这是 Rise 格式的限制，见第 6 节问题 3。

真正的差别在**交互本身**。让 Rise 的 `run_episode` 驱动，这场戏就只能按它的规则演：

| Rise 的 `run_episode` | 我们自己的运行器 |
|---|---|
| 按绑定顺序固定轮流，每轮一人一句 | 按剧情和环境推进 |
| 只有发言，所有人都听得到 | 思考 / 发言 / 动作，可以只给部分人 |
| 没有环境 agent | 可选的环境 agent 和世界私有数据 |
| `Scenario` 只有背景、冲突、规范 | 角色设定有目标、想瞒的事及隐瞒方式、前情 |

"对某人隐瞒、私下说、环境插入一个事件"在 Rise 的规则下根本不会发生，不是记录后丢了，而是没产生。所以数据生成由我们驱动，Rise 只接收导出的结果。

## 4. 数据生成：我们驱动，导出给 Rise（先做）

### 4.1 流程

```
情境（我们的剧情，或 Rise 规划器给的 Scenario 转来）
  → 我们的运行器跑完整场（与环境组调用同一条路，C8、审计不变）
  → 运行目录（主数据：状态链、判断、产物、交互记录）
  → 导出器 → Rise Episode（视图）→ write_episode
  → 一组 episode → build_manifest / write_manifest → 数据集清单
```

用到的 Rise 函数都能单独调用，不需要改 Rise：`simulator.store_adapter.write_episode`、`simulator.dataset.build_manifest` / `write_manifest`。不经过 `produce_grouped_dataset`（它内部调 `run_episode`，会把交互交给 Rise 驱动）。

### 4.2 输入：Rise 的 Scenario → 我们的剧情（只在用 Rise 的情境时）

方向是从少到多，不丢 Rise 的信息。

| Rise | 我们 |
|---|---|
| `background`、`conflict` | 共享背景与触发事实 |
| `role_slots[].slot_id`、`description` | 座位 ID 与座位名 |
| `norms` | 先并入背景文本；以后可补场景结构（F4，见 [03](03-environment-and-time.md)） |
| `RoleBinding.persona_id` | 角色库 ID（第一版只接受库里已有的；四个文本字段编译成画像放到之后，见第 6 节问题 1） |

Rise 的 `Scenario` 没有的东西（每个角色的目标、想瞒的事、前情）取自我们的角色文件。

### 4.3 输出：运行目录 → Rise Episode

| Rise 字段 | 取自 |
|---|---|
| `Episode.id` | `{run_id}-{scenario}-r{k}`，与我们的运行目录一一对应 |
| `scenario_id` | 情境 ID |
| `role_assignment.bindings` | 每个座位一条：`slot_id` = 座位，`persona_id` = 角色库 ID，`model_backend` / `model_ref` / `driver` = 我们的标记（如 `external_api` / `rolesim:<模型>` / `scripted`，待和老师定） |
| `role_assignment.max_turns` | 剧情的 `max_turns` |
| `turns[]` | 每条发言一个 `TurnRecord`，按交互记录顺序编号；`speaker_slot` = 发言者座位；`utterance` = 发言；`inner_thought` = 同一轮的思考 |
| `turns[].activation_trace` | 字符串叶子：`schema`（`rolesim.export.v1`）、`run_dir`、`state_id`、`action_id`、接收者、同一轮的动作、本轮各阶段判断摘要与写入的变化 |
| `terminated_reason` | `max_turns`（我们没有结束信号时）或 `goal_reached` |

装不进 `TurnRecord` 主字段的：动作、只给部分人的发言、环境发言。第一版放进 `activation_trace`（环境发言挂在下一条 `TurnRecord` 的 trace 里，或单列一个 `environment` 座位，待定），完整内容始终在运行目录里。

### 4.4 GRPO 分组

Rise 按 `scenario_id` + 每个绑定的 `(slot, persona, backend, model_ref)` 分组，组内要有差异。同一情境跑 k 条，差异来自温度 / 随机种子；**不能**把消融模式写进 `model_ref`，否则每条自成一组。要做"完整机制 vs 消融"的对照，是另一种数据集（偏好对），要和老师商量。

### 4.5 我们多出来的数据

运行目录里每一步的结构化判断（S1 的情境解释与他心假设、S2 的计划）可以做成"社会心智推理"监督数据。以什么形式进 Rise 的训练，见第 6 节问题 2。

## 5. 对手驱动：Rise 驱动，经后端接口（之后）

Rise 训练时必须控制轮次，只能经它的后端接口调我们：

```python
class ModelBackend(Protocol):  # simulator/backends.py，按结构识别
    def respond(self, persona, scenario, history, speaker_slot) -> tuple[str, str | None]: ...
# 也可返回 (utterance, inner_thought, activation_trace: dict)
```

适配器 `RoleSimBackend` 写在我们这边，只读属性名、不导入 Rise，把每次 `respond` 翻译成我们的 `initialize` / `act`（与环境组同一条路）：

- 本局第一次调用：Persona + Scenario → 启动信息 → `initialize`。之后每次：`history` 里没吸收过的轮次 → 交互记录 → `act` → speech 作 `utterance`、thought 作 `inner_thought`、本轮摘要作 trace。
- `respond` 不带局 ID：一个后端实例就是一局（Rise 每条 rollout 重新取后端），`history` 为空时也当作新的一局。
- Rise 同步调用、我们的 `act` 是异步的：适配器自己维护事件循环。

这里第 3 节的压缩躲不掉（轮流发言、只有公开发言、没有环境），只在这个用途接受。还要改 Rise：在 `OPPONENT_DRIVERS = ("vllm", "external_api", "scripted")`（`simulator/datamodel.py`，`trainscheduler.config` 里另有一份）加 `rolesim`，在 `factory/opponents.py`、`factory/interact_policy.py` 里分派，需要老师同意。

难点是速度：InteractTrain 给对手每轮 90 秒、训练位 150 秒，Rise 的真机实测关掉思考后每轮 4—17 秒；我们每轮约 5 次调用、每次 30—90 秒（思考模式）。要先在 [04](04-session-memory-and-speed.md) 里把每轮调用数和思考长度降下来。

## 6. 步骤

| # | 做什么 | 在哪 | 依赖 |
|---|---|---|---|
| 1 | 导出器：运行目录 → Rise `Episode` 的字段（4.3），只依赖属性名 / 普通字典，不导入 Rise | 我们的仓库，新模块（如 `rolesim/bridge/rise_export.py`） | 无 |
| 2 | 不装 Rise 的测试：mock 跑一场 → 导出 → 检查字段、trace 叶子全是字符串、`run_dir` 能通过审计 | 我们的 `tests/` | 1 |
| 3 | 本地脚本：用 Rise 的 `write_episode` / `build_manifest` / `write_manifest` 把一组导出的 episode 写进临时 git 库，再用 Rise 自己的 `verify_manifest` 读回 | 本地脚本，不改 Rise | 1 |
| 4 | Scenario → 我们剧情的转换（4.2），让 Rise 规划器给的情境也能用 | 我们的仓库 | 1 |
| 5 | 对手驱动的后端适配器（第 5 节）与 Rise 侧的 `rolesim` 驱动 | 我们的仓库 + Rise 仓库 | 老师同意；速度（[04](04-session-memory-and-speed.md)） |

## 7. 要和老师商量的

1. `Persona` 只有四个文本字段，装不下心理学画像。是加一个档案引用字段（例如 `profile_ref`），还是约定 `persona.id` 就是我们的角色库 ID？
2. 我们多出来的结构化数据（判断、产物、状态链）以什么形式进 Rise：塞进 `TurnRecord.activation_trace`，还是另成一种数据集？
3. 动作、只给部分人的发言、环境发言在 `TurnRecord` 里没有位置：Rise 愿不愿意扩字段（如 `kind`、`recipients`），还是先放在 trace 里？
4. 我们导出的 episode 不是固定轮流发言：Rise 下游（奖励、训练）有没有假设"第 i 轮的发言者 = 绑定 i mod n"？`binding` 里我们该填什么 `model_backend` / `driver`？
5. 完整机制与消融的对照数据要不要做成偏好对，Rise 有没有对应的数据集类型？
6. Rise 的正式版 Actio 在设计中，我们的接口要不要一起对齐？
