# Agent 接口与智能体属性总览

本文按"接口在外、实现在内"的思路，说明角色 Agent 对外暴露什么、内部持有什么属性。目的是让维护者只需要关注**接口边界**：边界以内的实现可以替换，边界以外的运行器不受影响。

- 协议原文（环境组，2026-11-07）：[Agent接口说明_环境组_2026-11-07.md](../data/sources/contracts/Agent接口说明_环境组_2026-11-07.md)。它取代了此前的 [Agent接口定义_v1.md](../data/sources/contracts/Agent接口定义_v1.md)（act / perceive / 认知五字段），本仓库称新版为**接口 v2**
- 可执行定义：[rolesim/agent_api.py](../rolesim/agent_api.py)
- 与 v1、与框架设计的差异及对齐结果：[plan/08](../plan/08-framework-oop-design.md) 第 8 节；早先的对接记录：[plan/07](../plan/07-agent-adapter-integration.md)
- 证据层级：本文是工程对接说明（本地工程检查），不涉及任何心理机制有效性的结论。

## 1. 分层与职责

```
┌──────────────────────────────────────────────┐
│ 运行器 / 环境（环境组；本地参考实现 loop.py）│  导演调度、在场范围、行动校验、接收范围解析、
│                                              │  生成记录、保存每个角色的观察记录
└───────────────┬──────────────────────────────┘
                │  只依赖 Protocol：IRoleAgent / IEnvironment
┌───────────────▼──────────────────────────────┐
│ 接口层  agent_api.py                         │  数据类型、双向校验、split / visible
└───────────────┬──────────────────────────────┘
┌───────────────▼──────────────────────────────┐
│ 适配层  adapter.py  RoleAgentAdapter         │  interaction_id + character_id 路由、
│                                              │  每角色每互动一个 Session、request_id 幂等、
│                                              │  串行加锁、记录→Event 转换
└───────────────┬──────────────────────────────┘
┌───────────────▼──────────────────────────────┐
│ 实现层  agent.py  Agent                      │  评价→机制卡→决策→表达→提交状态快照
└───────────────┬──────────────────────────────┘
                │  persona（稳定属性） + state（动态状态）
```

| | 运行器 / 环境 | 角色 Agent |
|---|---|---|
| 导演调度、谁行动、在场范围 | ✔ | |
| 行动校验、接收范围解析、生成记录 | ✔ | |
| 保存每个角色的观察记录 | ✔ | 内部另存完整心理状态，不交给运行器 |
| 角色初始化、思考、发言、外显行动 | | ✔ `IRoleAgent.initialize / act` |
| 环境反应（广播、电话、声音） | ✔ `IEnvironment.act` | |

## 2. 对外接口

两个 `Protocol`，全部为 `async`，同进程调用。环境组文档里叫 `AgentAdapter` / `EnvironmentAdapter`，我们这边统一按 `I...` 命名为 `IRoleAgent` / `IEnvironment`（定义在 `agent_api.py`，`interfaces.py` 再导出），签名完全一致：

```python
class IRoleAgent(Protocol):     # 我们实现（环境组：AgentAdapter）
    async def initialize(self, interaction_id: str, character_id: str, bootstrap: CharacterBootstrap) -> None
    async def act(self, interaction_id: str, character_id: str, context: CharacterTurnInput,
                  request_id: str) -> AgentActionResult

class IEnvironment(Protocol):    # 环境组实现（环境组：EnvironmentAdapter）；本地用 EchoEnvironment 桩
    async def act(self, interaction_id: str, context: EnvironmentActorView, request_id: str) -> AgentActionContent
```

| 方法 | 何时调用 | 输入 | 输出 |
|---|---|---|---|
| `initialize` | 互动开始时，每个角色一次 | 路由键 + 固定资料 `CharacterBootstrap` | 无 |
| `act` | 轮到该角色行动 | 路由键 + 该角色看到过的全部记录 + 当前在场角色 | 思考 / 发言 / 行动及顺序 |
| `IEnvironment.act` | 本轮有导演指令 | 环境视角 | 同样的行动内容结构 |

**没有 perceive。** 角色看到的新记录不会被单独推送，而是在它下一次被要求行动时随 `observations` 一起到达，我们在那时补做感知评价。

### 2.1 数据类型（`agent_api.py`）

**`CharacterBootstrap`**（`initialize` 传入，整场不变）

| 字段 | 类型 | 含义 |
|---|---|---|
| `setting` | `{place, time_text}` | 地点、时间，均可为 null |
| `trigger_facts` | `list[str]` | 公共触发事实 |
| `name` | `str` | 本角色名 |
| `profile_summary` | `str` | 稳定人物总结 |
| `core_goals` | `str \| null` | 本次互动全过程不变的总体目标 |
| `relationships` | `list[str]` | 关系句 |
| `history_memory` | `str \| null` | 互动前实际经历或获知的内容 |
| `personality` | `dict` | 分支指定的人格配置；可选 `role_ref`：角色文件 ID（与 `character_id` 不同时才需要） |
| `mechanism` | `dict` | 分支指定的行动机制配置；可选 `mode`：消融档位（`profile_only` / `profile_state` / `profile_state_mechanisms`），可选 `enable_config`：启用配置 ID（先记录，未生效） |

**`CharacterTurnInput`**（`act` 传入）：`bootstrap`（恒为 null）、`observations`（本角色看到过的全部记录，按发生顺序）、`present_characters`（`{角色ID: {name}}`，当前在场者）。

**记录 `InteractionRecord`**

| 字段 | 含义 |
|---|---|
| `action_id` | 一次复合行动拆出的记录共用 |
| `turn_index` | 从 1 开始 |
| `order_index` | 本轮内部顺序，从 0 开始 |
| `actor` | `{actor_id, name}`；环境固定为 `{"environment", "环境"}` |
| `kind` | `thought` / `speech` / `action` |
| `content` | 文本 |
| `recipients` | `{角色ID: {name}}`，明确接收者 |

`action_id#order_index`（`record_id()`）是记录编号，内部心理状态变化都以它作为证据引用。

**输出 `AgentActionResult = AgentActionContent = {thought, speech, action, order}`**：前三项各是 `{content, recipient_ids}` 或 null，至少一项非空；`order` 恰好列出非空部分。`recipient_ids` 为 null 表示当前全部在场角色；只给自己看的思考写 `[自己的 ID]`。不返回认知、`action_id`、`turn_index`。

**环境视角 `EnvironmentActorView`**：`setting`、`story_background`、`trigger_facts`、`present_characters`、`environment_directive`。不含互动历史。

### 2.2 校验与运行器工具

不通过一律抛 `InterfaceError`，不返回部分结果。

| 函数 | 检查要点 |
|---|---|
| `check_id` | 路由用的 ID 非空，且不是 `"environment"` |
| `validate_bootstrap` | 字段齐全；`role_ref`、`mode`、`enable_config` 若给出须为非空字符串 |
| `validate_turn_input` | `bootstrap` 为 null；记录有序、编号不重复、至少一个接收者、对本角色可见（本人行动或在接收者里）；在场者包含本人，且除本人外至少还有一个 |
| `validate_action_result` / `validate_action_content` | 至少一部分非空；`order` 与非空部分一致；`recipient_ids` 为 null 或非空列表，只能引用在场角色或行动者本人 |
| `validate_environment_view` / `validate_environment_content` | 字段齐全；接收者只能是在场角色 |

运行器侧工具（本地运行器使用）：`split()` 把一次行动结果按 `order` 拆成记录；`visible()` / `sees()` 判断某角色能看到哪些记录。

### 2.3 约定与我们的解读

1. 路由键是 `interaction_id + character_id`；不同 `interaction_id` 是互不影响的会话（分支即不同互动）。`character_id` 是角色库 ID，提前设定、跨互动不变（2026-11-07 与环境组确认）。`"environment"` 不得用作角色 ID。
2. 一场互动里的角色都先 `initialize` 再 `act`；记录的行动者、接收者和在场者都按角色 ID 对到初始化时的角色，名字只用于提示词。
3. `present_characters` 包含行动者本人（已确认），不含本人的输入会被拒绝。
4. 原文允许思考发给别人；我们自己的思考只发给本人。若运行器把别人的思考放进我们的观察记录，不吸收、不进任何提示，只记告警。
5. 同一 `request_id` 重发返回第一次的结果；同一个 id 配不同输入会被拒绝；出错时抛异常且不记入缓存，可以重来。重复 `initialize` 同一份资料无副作用，资料不同则拒绝。

## 3. 适配层（`adapter.py`）

`RoleAgentAdapter(roles, backend, mode, use_skills, appraise_state, allowed_actions)`：

- **选角色**：依次看 `bootstrap.personality.role_ref`、`character_id` 是否为角色库（`roles/`）里的 ID、`name` 是否匹配档案名。同一互动里一个角色文件只能对应一个角色 ID。
- **消融档位**：`bootstrap.mechanism.mode` 给出时，该角色在该互动里用这个档位，否则用适配器的默认档位。
- **Session**：每个 `(interaction_id, character_id)` 一个，保存 bootstrap、`Agent`（第一次 `act` 时建立，此时才知道在场者）、已吸收的记录编号 `seen`、已选动作 `moves` 和告警。
- **在场变化**：新到场的角色在第一次相遇时建立关系状态；离场的角色仍保留在已知角色里，只是不再出现在接收者中。
- **记录转 Event**：未吸收的记录转成环境合同里的 `Event`，`event_id` 取 `record_id`，可见范围由行动者加接收者得出；覆盖本人和全部已知角色为 `public`，否则 `private`。思考一律不吸收。
- **act 流程**：校验 → 准备 Agent → 补入未吸收的记录（包括自己上一轮的发言）→ `Agent.step` → 返回 `{thought: null, speech: {内容, recipient_ids: null}, action: null, order: ["speech"]}`。
- `Agent` 失败时抛 `AgentCallError`，已标记为见过的记录会撤销，重试时可再次吸收。
- **并发**：所有模型调用共用一把锁，在 `asyncio.to_thread` 里串行执行。
- **审计**：每次成功的 `act` 在 `adapter.log` 追加一条，含路由键、评价、控制层输入、决策、状态变化、返回结果。

## 4. 智能体属性

运行器只看到我们返回的行动内容；稳定属性和动态状态都留在内部。接口 v2 不再回传认知摘要，原来的 `cognition.py` 已删除。

### 4.1 稳定属性（`persona.py`，schema 2.0）

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

### 4.2 动态状态（`state.py`，state-v1）

六个域：`affect_state`、`cognition_state`、`goal_state`、`relation_state`、`group_state`、`policy_state`。由 11 个更新算子（情绪更新、信念修正、他人模型、关系更新等）改写，每个算子只能写指定路径。每条变化都记录 `path`、`before`、`after`、`evidence`（事件 ID）、`mechanism`、`reason`；数值越界一律拒绝，不截断。每次调用提交一个快照，`state_id` 形如 `{角色ID}@t003`，快照串成状态链并可回放。动态状态只能由角色可见的证据更新。

## 5. 实现层内部流程（`agent.py`）

`Agent(backend, allowed_actions, others, mode, skills, briefing, seat, appraise_state)`

- `begin(profile)`：校验并载入稳定档案，建立初始状态；适配器在角色第一次 `act`（在场名单已知）时调用。
- `meet(role_id, title)`：中途新到场的角色在第一次相遇时建立关系状态（与开场时同样的工程初值），记入状态日志。
- `step`（对应 `act`）：吸收新事件 → 若事件没有环境线索，由模型自己评价（`backend.appraise`）→ 机制卡把线索转为状态变化 → 选卡 → 决策（`backend.decide`，在动作词表里选一个）→ 表达（`backend.express`，生成台词，上限 600 字）→ 提交状态快照。
- `perceive`：吸收新事件 → 提交快照。只在环境合同 v0 的 `run_dialogue` 流程里使用；接口 v2 没有 perceive。
- 模式 `MODES`：`profile_only`（提示词里不出现状态）、`profile_state`（出现状态摘要）、`profile_state_mechanisms`（再加机制卡）。状态在三档下都计算并记录，模式只决定提示词内容。
- 动作词表（`vocab.py`）：`request_evidence`、`ask_clarification`、`propose_plan`、`set_boundary`、`wait`、`agree`、`close`。线索词表：`uncertainty`、`pressure`、`commitment`、`repair`、`conflict`。
- Agent 与适配器之间沿用环境合同 v0 的数据类（`contract.py`：`Event`、`StepRequest` / `StepResponse`、`PerceiveRequest` / `PerceiveResponse`）。这一层是内部接口，不对运行器暴露。

隔离原则：控制层和表达层的提示词不含他人隐藏心理、`world_private`、机制名或状态数值；表达层只拿到角色口吻的状态描述。

## 6. 本地参考运行器（`loop.py`）

按环境组接口实现的参考运行器，供离线联调和回归测试使用：

- `LocalRunner(episode, agents, environment, on_call, interaction_id)`：先对每个角色 `initialize` 一次；每轮先处理导演指令（调环境、拆分、按接收者写进各角色的观察记录），再让当前角色 `act`，拆分后同样写进观察记录。不调用任何 perceive。
- `EchoEnvironment`：环境桩，把导演指令原样当作环境的 action 输出，可限定接收者。
- `episode()` 从角色文件和场景生成运行输入，`character_id` 取角色文件 ID；`bootstrap()` 生成每个角色的初始化资料（`mechanism.mode` 为本次运行的档位）；场景的环境规则变成导演指令；不读 `world_private`。
- `run_agent_api()`：落盘到运行目录：`manifest.json`（含 `interaction_id`）、`records.jsonl`（全部记录）、`calls.jsonl`（每次调用的完整输入输出，含 initialize）、`roles/<角色>/audit.jsonl`、`states.jsonl`、`mechanism_log.jsonl`、`transcript.md`。

```bash
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3 --interface agent-api
```

（具体参数见 [03-usage.md](03-usage.md)。）

## 7. 现在的限制与待定

| 项 | 现状 |
|---|---|
| 输出形态 | 只出发言；`thought`、`action` 恒为 null |
| 感知时机 | 没有 perceive，别人的言行要等本角色下次行动时才评价；中间不行动的角色状态不变（已确认接受） |
| `personality` / `mechanism` | 读 `role_ref`、`mode`；`enable_config` 只记录 |
| 跨互动 | 每场互动从角色库的入场状态开始，不延续上一场的动态状态和记忆 |
| `core_goals` | 作为本次互动的总体目标写进情境背景文本，不写入稳定画像的 `core_goals` |
| 并发 | 模型调用全局串行 |
| 结束对话 | 接口没有这个信号，本地运行器读 `adapter.closed_by(interaction_id)` |
| 审计与打包 | `agent-api` 运行目录暂不能用 `audit_report` 和 `rolesim package` |
| 环境线索 | 接口没有 `signals`，角色一律自评线索 |
| 待环境组确认 | 见 [plan/08](../plan/08-framework-oop-design.md) 第 8.1 节 |

## 8. 测试

`tests/test_agent_api.py` 覆盖类型和校验、`split` / `visible`；`tests/test_adapter.py` 覆盖初始化路由、未初始化即行动被拒、输出只有行动内容、幂等、自己的记录下次吸收、他人思考不进提示、状态变化可追溯到记录、新到场角色建立关系、互动之间隔离、失败可重试、私有事件只到指定角色、`world_private` 不外泄。运行：

```bash
python3.11 -m unittest discover -s tests -v
```

测试包含本地 HTTP 服务，受限环境下需如实报告。使用 mock 后端的运行只说明管线可用，不说明心理机制有效。
