# Agent 接口与智能体属性总览

本文按"接口在外、实现在内"的思路，说明角色 Agent 对外暴露什么、内部持有什么属性。目的是让维护者只需要关注**接口边界**：边界以内的实现可以替换，边界以外的运行器不受影响。

- 协议原文（给环境组）：[Agent接口定义_v1.md](../data/sources/contracts/Agent接口定义_v1.md)，来源 [Agent组接口实现说明.md](../data/sources/contracts/Agent组接口实现说明.md)
- 可执行定义：[rolesim/agent_api.py](../rolesim/agent_api.py)
- 对接过程与差异记录：[plan/07](../plan/07-agent-adapter-integration.md)；调用流程见 [06-runtime-flow.md](06-runtime-flow.md)，模块位置见 [07-architecture.md](07-architecture.md)
- 证据层级：本文是工程对接说明（本地工程检查），不涉及任何心理机制有效性的结论。

## 1. 分层与职责

```
┌──────────────────────────────────────────────┐
│ 运行器 / 环境（环境组；本地参考实现 loop.py）│  回合、导演指令、记录拆分与分发、
│                                              │  保存每个角色的 observations 与 cognition
└───────────────┬──────────────────────────────┘
                │  只依赖 Protocol：AgentAdapter / EnvironmentAdapter
┌───────────────▼──────────────────────────────┐
│ 接口层  agent_api.py                         │  数据类型、双向校验、split / deliveries、Directory
└───────────────┬──────────────────────────────┘
┌───────────────▼──────────────────────────────┐
│ 适配层  adapter.py  RoleAgentAdapter         │  name→角色路由、每角色一个 Session、
│                                              │  request_id 幂等、串行加锁、记录→Event 转换
└───────────────┬──────────────────────────────┘
┌───────────────▼──────────────────────────────┐
│ 实现层  agent.py  Agent                      │  评价→机制卡→决策→表达→提交状态快照
│        cognition.py  project()               │  内部状态 → 五字段 CognitionBasis（模板，不调模型）
└───────────────┬──────────────────────────────┘
                │  persona（稳定属性） + state（动态状态）
```

| | 运行器 / 环境 | 角色 Agent |
|---|---|---|
| 谁来行动、导演指令、回合数 | ✔ | |
| 思考、发言、行动、认知 | | ✔ |
| 环境反应（广播、电话、声音） | ✔（`EnvironmentAdapter`） | |
| 把一次行动拆成记录并按接收者分发 | ✔ | |
| 保存 `observations` 与 `cognition` | ✔ | 内部另存完整心理状态，运行器不管 |

角色的输出直接成为事实，v1 不做行动裁决。

## 2. 对外接口

两个 `Protocol`，全部为 `async`，同进程调用：

```python
class AgentAdapter(Protocol):        # 我们实现
    async def act(self, context: CharacterView, request_id: str) -> AgentActionResult
    async def perceive(self, context: CharacterPerceptionInput, request_id: str) -> AgentPerceptionResult

class EnvironmentAdapter(Protocol):  # 环境组实现；本地用 EchoEnvironment 桩
    async def act(self, context: EnvironmentActorView, request_id: str) -> AgentActionContent
```

| 方法 | 何时调用 | 输入 | 输出 |
|---|---|---|---|
| `AgentAdapter.act` | 轮到该角色行动 | 该角色此刻的完整视角 `CharacterView` | 新认知 + 行动内容 |
| `AgentAdapter.perceive` | 别人行动后，该角色看到了新记录 | 行动前视角 + 本次新看到的记录 | 新认知（只有认知） |
| `EnvironmentAdapter.act` | 本轮有导演指令 | 环境视角 `EnvironmentActorView` | 行动内容（无思考、无认知） |

每次调用都带完整视角，认知和观察记录由运行器保存；适配器内部另有有状态的 `Agent`，按记录做增量。

### 2.1 数据类型（`agent_api.py`）

**输入：`CharacterView`**（运行器每次发给角色的完整视角）

| 字段 | 类型 | 含义 |
|---|---|---|
| `setting` | `{place, time_text}` | 地点、时间，均可为 null |
| `trigger_facts` | `list[str]` | 公共触发事实 |
| `name` | `str` | 本角色名，也是唯一标识 |
| `profile_summary` | `str` | 人物总结 |
| `relationships` | `list[str]` | 关系句 |
| `history_memory` | `str \| null` | 前史 |
| `personality` / `mechanism` | `dict` | 暂不解析，原样透传，字段待定 |
| `cognition` | `CognitionBasis` | 运行器保存的当前认知 |
| `observations` | `list[InteractionRecord]` | 本角色可见的互动记录，按时间升序 |
| `present_characters` | `{角色ID: {name}}` | 在场角色，不含本人 |

**认知：`CognitionBasis`**，五个键必须都在，值为文本或 null：`knowledge`、`beliefs`、`emotions`、`motivations`、`goals`。

**记录：`InteractionRecord`**

| 字段 | 含义 |
|---|---|
| `action_id` | 一次 `act` 产生的所有记录共用一个，不同调用不重复 |
| `turn_index` | 从 1 开始 |
| `order_index` | 从 0 开始，同一轮内跨行动者递增 |
| `actor` | `{actor_id, name}`；环境固定为 `{"env", "环境"}` |
| `kind` | `thought` / `speech` / `action` |
| `content` | 文本 |
| `recipients` | `{角色ID: {name}}`；思考的接收者只能是行动者本人 |

`action_id#order_index`（`record_id()`）是记录的全局唯一编号，内部心理状态变化都以它作为证据引用。

**输出**
- `AgentActionResult = {cognition, action}`
- `AgentActionContent = {thought, speech, action, order}`：前三项各是 `{content, recipient_ids}` 或 null；`order` 恰好列出非空的部分。`recipient_ids` 为 null 表示行动者当时的全部在场角色；思考的 `recipient_ids` 必须为 null。
- `AgentPerceptionResult = {cognition}`

**环境视角：`EnvironmentActorView`**：`setting`、`story_background`、`trigger_facts`、`present_characters`、`interaction_history`（已过滤掉所有思考）、`environment_directive`（导演给的一句指令）。

### 2.2 校验与运行器工具

校验函数双方各调一次：运行器发出前、收到后；我们收到请求时也同样校验。不通过一律抛 `InterfaceError`，不返回部分结果。

| 函数 | 检查要点 |
|---|---|
| `validate_character_view` | 字段齐全；记录有序、编号不重复、对本角色可见；本人不在在场名单；在场角色名不重复 |
| `validate_perception_input` | 新记录非空、对本角色可见、不是本人的、不与已有记录重复 |
| `validate_action_result` / `validate_action_content` | 至少一部分非空；`order` 与非空部分一致；`recipient_ids` 只引用在场角色；思考不带接收者 |
| `validate_perception_result` | 只有 `cognition`，五字段齐全 |
| `validate_environment_view` / `validate_environment_content` | 历史里没有思考；环境不返回思考 |

运行器侧工具：`split()` 把一次行动结果按 `order` 拆成记录；`deliveries()` 决定谁收到哪些记录（看不到任何新记录的角色不会被 `perceive`）；`sees()` 判断可见性；`environment_history()` 去掉思考。我们侧的 `Directory` 把角色 `name` 对到内部角色 ID，运行器不需要知道我们的 ID。

### 2.3 约定

1. 角色用 `name` 标识，同一场内必须唯一；角色 ID 归运行器，`"env"` 不得用作角色 ID。
2. 思考只对本人可见；环境读到的历史不含任何思考，环境也不产生思考。
3. 同一 `request_id` 重发返回第一次的结果（幂等）；同一个 id 配不同输入会被拒绝；出错时抛异常，且不记入缓存，可以重来。
4. 运行器原样保存并回传我们返回的认知。传回的认知若与我们上次返回的不同，只记告警，不改内部状态。

## 3. 适配层（`adapter.py`）

`RoleAgentAdapter(roles, backend, mode, use_skills, appraise_state, allowed_actions)`：

- **Session**：每个角色一个，保存该角色的 `Agent`、本场互动已见过的记录编号 `seen`、上次返回的认知、已选动作 `moves` 和告警。在场角色集合以第一次调用为准，之后变化会被拒绝（v1 限制）。
- **记录转 Event**：未见过的记录转成环境合同里的 `Event`，`event_id` 取 `record_id`，可见范围由行动者加接收者得出；全员可见为 `public`，否则 `private`。别人的 `thought` 不会出现在视角里，本人的 `thought` 因为是自己产生的，跳过不再吸收。
- **调用流程**
  - `act`：校验 → 补入未见记录（包括行动者自己上一轮的台词）→ `Agent.step` → 取第一条提议 → 返回 `{thought: null, speech: {内容, recipient_ids: null}, action: null, order: ["speech"]}` 与 `project()` 生成的认知。
  - `perceive`：校验 → 吸收新记录 → `Agent.perceive` → 返回认知。
  - `Agent` 失败时抛 `AgentCallError`，已标记为见过的记录会撤销，重试时可再次吸收。
- **并发**：所有模型调用共用一把锁，在 `asyncio.to_thread` 里串行执行，因为后端对象保存每次调用的状态。
- **审计**：每次成功调用在 `adapter.log` 追加一条，含评价、控制层输入、决策、状态变化、返回结果。

## 4. 智能体属性

智能体的属性分三层。运行器只看到前两项中的文字摘要，其余留在内部。

### 4.1 接口可见层

即第 2.1 节的 `CharacterView` 与 `CognitionBasis`。`cognition` 由 `cognition.py` 的 `project(state, persona, names, briefing)` 用模板生成，不调模型，因此不会引入未审计的状态变化；不含数值、机制名和隐瞒内容。字段来源如下（属于工程映射，不是心理学组的决定）：

| 认知字段 | 内部来源 |
|---|---|
| `knowledge` | 前情摘要 + 最近 5 条记忆 |
| `beliefs` | 对他人的关系档位 + `cognition_state` 里的 beliefs / other_models |
| `emotions` | 情绪档位词 |
| `motivations` | 人物档案的 `core_goals` |
| `goals` | 开场目标 + `goal_state.goals_now` |

### 4.2 稳定属性（`persona.py`，schema 2.0）

整场互动内不可变：`Agent` 在每次调用时检查，档案变化会直接报错。

| 顶层字段 | 内容 |
|---|---|
| `id`、`name`、`public_description`、`self_view` | 标识、对外描述、角色自视 |
| `stable.identity` | `demographics`、`social_roles`、`resources`、`life_history` |
| `stable.traits` | `personality`（P1：30 个 facet，取值 -2 到 2）、`attachment`、`motivation_baseline`、`affective_capacities`、`cognitive_style` 等 8 个子对象 |
| `stable.values` | `schwartz_priorities`（19 个价值权重，和为 1）、`investment` 等 |
| `stable.core_goals` | 1 到 3 个主要目标，各带优先级、稳定性、至多 2 个子目标 |
| `stable.speech_style` | `baseline.directness`、`situational_rules` |
| `stable.mechanism_priors` | 各机制卡的先验权重 |
| `stable.module_status` | 每个稳定模块的状态：active / unknown / not_modeled / disabled |
| `provenance` | 每个 active 模块的来源、赋值方式、置信度 |

缺失的测量是 unknown（null），不用中性数字代替。`compile_views()` 从同一份档案编译出四个视图，做到按需可见：`system_truth`（完整，仅系统）、`role_self`（角色自己）、`other_visible`（别人能看到的公开描述）、`controller_only`（控制层的条件规则、需求、价值、目标）。

### 4.3 动态状态（`state.py`，state-v1）

六个域：`affect_state`、`cognition_state`、`goal_state`、`relation_state`、`group_state`、`policy_state`。由 11 个更新算子（情绪更新、信念修正、他人模型、关系更新等）改写，每个算子只能写指定路径。每条变化都记录 `path`、`before`、`after`、`evidence`（事件 ID）、`mechanism`、`reason`；数值越界一律拒绝，不截断。每次调用提交一个快照，`state_id` 形如 `{角色ID}@t003`，快照串成状态链并可回放。动态状态只能由角色可见的证据更新。

## 5. 实现层内部流程（`agent.py`）

`Agent(backend, allowed_actions, others, mode, skills, briefing, seat, appraise_state)`

- `begin(profile)`：校验并载入稳定档案，建立初始状态；`perceive` 可能先于第一次 `act`，所以适配器在建立 Session 时调用它。
- `step`（对应 `act`）：吸收新事件 → 若事件没有环境线索，由模型自己评价（`backend.appraise`）→ 机制卡把线索转为状态变化 → 选卡 → 决策（`backend.decide`，在动作词表里选一个）→ 表达（`backend.express`，生成台词，上限 600 字）→ 提交状态快照。
- `perceive`：吸收新事件 → 提交快照。
- 模式 `MODES`：`profile_only`（提示词里不出现状态）、`profile_state`（出现状态摘要）、`profile_state_mechanisms`（再加机制卡）。状态在三档下都计算并记录，模式只决定提示词内容。
- 动作词表（`vocab.py`）：`request_evidence`、`ask_clarification`、`propose_plan`、`set_boundary`、`wait`、`agree`、`close`。线索词表：`uncertainty`、`pressure`、`commitment`、`repair`、`conflict`。
- Agent 与适配器之间沿用环境合同 v0 的数据类（`contract.py`：`Event`、`StepRequest` / `StepResponse`、`PerceiveRequest` / `PerceiveResponse`）。这一层是内部接口，不对运行器暴露。

隔离原则：控制层和表达层的提示词不含他人隐藏心理、`world_private`、机制名或状态数值；表达层只拿到角色口吻的状态描述。

## 6. 本地参考运行器（`loop.py`）

环境组的运行器还在对接中，所以本仓库有一份按同样规则实现的参考运行器，供离线联调和回归测试使用：

- `LocalRunner(episode, agents, environment, on_call)`：每轮先处理导演指令（调环境、拆分、分发），再让当前角色 `act`，拆分后分发给其他看得到的角色 `perceive`（同轮并行）。
- `EchoEnvironment`：环境桩，把导演指令原样当作环境的 action 输出，可限定接收者。
- `episode()` 从角色文件和场景生成运行输入：角色名取档案名，运行器角色 ID 取场景座位 ID；场景的环境规则变成导演指令；不读 `world_private`。
- `run_agent_api()`：落盘到运行目录：`manifest.json`、`records.jsonl`（全部记录）、`calls.jsonl`（每次调用的完整输入输出）、`roles/<角色>/audit.jsonl`、`states.jsonl`、`mechanism_log.jsonl`、`transcript.md`。

```bash
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3 --interface agent-api
```

（具体参数见 [03-usage.md](03-usage.md)。）

## 7. 现在的限制与待定

| 项 | 现状 |
|---|---|
| 输出形态 | 只出发言；`thought`、`action` 恒为 null |
| 在场角色 | 以角色第一次调用时为准，变化即拒绝 |
| 分支 | 一个适配器对应一条分支；`personality` / `mechanism` 不解析，三档模式由运行参数决定 |
| 并发 | 模型调用全局串行 |
| 结束对话 | 接口没有这个信号，本地运行器读 `adapter.closed_by()` |
| 审计与打包 | `agent-api` 运行目录暂不能用 `audit_report` 和 `rolesim package` |
| 环境线索 | 新接口没有 `signals`，角色一律自评线索 |
| 待环境组确认 | `personality` / `mechanism` 字段、分支标识、结束对话信号、首次 `act` 前的初始认知、一份真实 `CharacterView` 样例 |

## 8. 测试

`tests/test_agent_api.py`（17 个）覆盖类型和校验、`split` / `deliveries` / `environment_history`；`tests/test_adapter.py`（11 个）覆盖输出合法性、幂等、`perceive` 先于 `act`、自己的记录只吸收一次、状态变化可追溯到记录、私有事件只到指定角色、`world_private` 不外泄。运行：

```bash
python3.11 -m unittest discover -s tests -v
```

测试包含本地 HTTP 服务，受限环境下需如实报告。使用 mock 后端的运行只说明管线可用，不说明心理机制有效。
