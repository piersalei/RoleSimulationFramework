# Agent 接口与智能体属性总览

本文按"接口在外、实现在内"的思路，说明角色 Agent 对外暴露什么、内部持有什么属性。目的是让维护者只需要关注**接口边界**：边界以内的实现可以替换，边界以外的运行器不受影响。

- 协议原文（环境组，2026-11-07）：[Agent接口说明_环境组_2026-11-07.md](../data/sources/contracts/Agent接口说明_环境组_2026-11-07.md)。它取代了此前的 [Agent接口定义_v1.md](../data/sources/contracts/Agent接口定义_v1.md)（act / perceive / 认知五字段），本仓库称新版为**接口 v2**
- 可执行定义：[rolesim/agent_api.py](../rolesim/agent_api.py)；我们内部各接口的契约：[rolesim/interfaces.py](../rolesim/interfaces.py)、[plan/10](../plan/10-interfaces.md)
- 与 v1、与框架设计的差异及对齐结果：[plan/08](../plan/08-framework-oop-design.md) 第 8 节；早先的对接记录：[plan/07](../plan/07-agent-adapter-integration.md)
- 证据层级：本文是工程对接说明（本地工程检查），不涉及任何心理机制有效性的结论。

## 1. 分层与职责

```
┌───────────────────────────────────────────────────┐
│ 运行器 / 环境（环境组；本地参考实现 loop.py）      │  导演调度、在场范围、行动校验、接收范围解析、
│                                                   │  生成记录、保存每个角色的观察记录
└───────────────┬───────────────────────────────────┘
                │  只依赖 Protocol：IRoleAgent / IEnvironment
┌───────────────▼───────────────────────────────────┐
│ 接口层  agent_api.py                              │  数据类型、双向校验、split / visible
└───────────────┬───────────────────────────────────┘
┌───────────────▼───────────────────────────────────┐
│ 公共层  adapter.py  RoleAgentBase                 │  interaction_id + character_id 路由、选角色、
│                                                   │  每角色每互动一个 Session、request_id 幂等、串行
└───────────────┬───────────────────────────────────┘
┌───────────────▼───────────────────────────────────┐
│ 实现    staged.py  StagedRoleAgent                │  入场（IRoleRepository）→ ITurnRunner：
│                                                   │  S1 / S3 / S2 → IStateStore → IExpresser
└───────────────┬───────────────────────────────────┘
                │  稳定画像（StableProfile） + 动态状态（AgentState）
```

| | 运行器 / 环境 | 角色 Agent |
|---|---|---|
| 导演调度、谁行动、在场范围 | ✔ | |
| 行动校验、接收范围解析、生成记录 | ✔ | |
| 保存每个角色的观察记录 | ✔ | 内部另存完整心理状态与轨迹，不交给运行器 |
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
| `mechanism` | `dict` | 分支指定的行动机制配置；可选 `mode`：消融模式（`profile_only` / `profile_state` / `profile_state_mechanisms`），可选 `enable_config`：启用配置 ID（先记录，未生效） |

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

## 3. 实现：`StagedRoleAgent`（`staged.py`）

`StagedRoleAgent(roles, registry, model, trace=None, repository=None, store=None, mode="profile_state_mechanisms", use_skills=True)` 是 `IRoleAgent` 的唯一实现，继承 `adapter.RoleAgentBase`。

**公共层（`RoleAgentBase`）**

- **选角色**：依次看 `bootstrap.personality.role_ref`、`character_id` 是否为角色库（`roles/`）里的 ID、`name` 是否匹配角色名。同一互动里一个角色文件只能对应一个角色 ID。
- **Session**：每个 `(interaction_id, character_id)` 一个，保存 bootstrap、消融模式、`enable_config`（只记录）、已吸收的记录编号 `seen` 和告警。
- **幂等与并发**：同一 `request_id` 返回第一次的结果；所有模型调用共用一把锁，在 `asyncio.to_thread` 里串行执行。

**`StagedRoleAgent` 自己做的**

- **第一次 `act` 时入场**（此时才知道在场者）：`IRoleRepository.load`（`profiles/<id>.json`，没有则转换角色文件，由 `IProfileCompiler` 编译）→ `enter`（档案入场状态 → 开场设定的目标与前情 → bootstrap 的总体目标与此前经历 → 角色状态说明 §8.2 的默认值）→ 取绑定的人格 skill 与开场设定 → `ITurnRunner.begin`。
- **每次 `act`**：只把没吸收过的记录交给 `ITurnRunner.run(key, 新记录, 在场者)`；别人的思考即使出现在观察记录里也不吸收，只记告警。一次行动内部的阶段顺序见 [06-runtime-flow.md](06-runtime-flow.md) 第 2 节。
- **返回**：`{thought, speech, action, order}`。思考只发给本人；发言和动作发给 S2 计划里的受众（没写即当前全部在场者）。不返回任何内部状态。
- **消融模式**：`bootstrap.mechanism.mode` 给出时，该角色在该互动里用这个模式，否则用构造时的默认模式；不认识的模式抛 `InterfaceError`。模式只决定提示和输出 schema，状态在三种模式下都照常提议、校验、写入和记录：

| 模式（`interfaces.MODES`） | 提示里的动态状态与感受 | 决策过程判断 |
|---|---|---|
| `profile_only` | 没有 | 没有（schema 限 0 条） |
| `profile_state` | 有 | 没有 |
| `profile_state_mechanisms`（默认） | 有 | 有 |

- **失败**：阶段或模型出错抛 `AgentCallError`；已提交的阶段保留，这次的新记录不标为已吸收；换一个 `request_id` 重试时从待做的反馈 / 计划接着做，不重复已提交的阶段。
- **记录**：每次成功的 `act` 在 `agent.log` 追加一条（路由键、吸收的记录编号、状态 ID、返回内容）；完整的阶段记录和模型调用由 `ITrace` 写到 `trace/<interaction_id>/<character_id>/`。
- **在场变化**：在场者随每次 `act` 传入；计划的受众只能是在场者。与某个角色的关系、对他的他人模型都按角色 ID 存，只由阶段按可见证据提议写入，入场时不预设（bootstrap 的关系句只进情境背景）。

## 4. 智能体属性

运行器只看到我们返回的行动内容；稳定画像和动态状态都留在内部。接口 v2 不回传认知摘要。

### 4.1 稳定画像（`StableProfile`，`profile.py`）

来源是 `profiles/<id>.json`（profile-1 档案）；没有档案时由 `profile.profile_from_role` 把角色文件（persona 2.0，见 [04-authoring.md](04-authoring.md)）转换过来，再由 `ProfileCompiler` 编译。

- 按稳定构念 ID（`s.*`）组织，每项是一个 `StableModule`：取值、状态（`active` / `unknown` / `inactive` / `disabled` / `not_modeled` / `not_applicable`）、来源类型（`explicit` / `inferred` / `model_inferred` / `default` / `unknown`）、置信度、是否待审核、原文依据、条件化语句及其理由。
- 缺失的测量是 `unknown`，不用中性数字代替；模型推断的项必须带原文引用，并进入待审核清单（`rolesim profile ROLE` 列出）。
- **整场不变**：状态里只记画像快照 ID；任何写稳定构念的提议都以硬错 `writes_stable` 拒绝；审计复核快照 ID 不变。
- **自知层**：提示里只放登记表标为能自知 / 部分自知的模块；不能自知的模块只以条件化语句出现在"说不出来、但会影响你的倾向"里，不带名字和数值。

### 4.2 动态状态（`AgentState`，`IStateStore`）

- 按构念 ID 存：42 个动态构念（`d.*`）和 3 个记忆构念（`m.*`），布局见 `slots.py`；入场状态的 ID 为 `{角色ID}@entry`，之后每次提交形如 `{角色ID}@{事件}#{序号}.{类型}`，前后相连成状态链。
- 唯一的写入口是 `ConstructStore.commit`：阶段的提议 → 登记表授权的算子 → 幅度档 → 验证 → 按依赖组原子提交。每条变化记下变化前后的值、证据（记录编号）、依据的决策过程、幅度档和软异常；越界一律拒绝，不截断。
- 动态状态只能由本角色可见的证据更新（证据门：本人行动或被明确发送的记录；别人的思考、本人的思考、没发给自己的记录都排除）。

### 4.3 开场设定与人格 skill

- **开场设定**（角色文件的 `setup`，`briefing.py` 校验）：互动目标写成当前目标 `g-setup`，前情写成入场记忆；S1 / S2 / S3 看得到全部内容（包括不打算说出口的事和打算怎么不说），表达调用只看到目标和可以说出的部分。
- **人格 skill**（角色文件的 `skills`，库在 `library/skills/*/SKILL.md`）：S2 看到全部绑定 skill 的名称与说明，以及被 S1 的线索或开场设定激活的至多 3 个 skill 的决策要点和不会做的事；表达调用拿到被激活 skill 的表达要点。`use_skills=False`（命令行 `--no-skills`）时 skill 留在记录里，不进任何提示。

隔离原则：S1 / S2 / S3 和表达的提示都不含他人的任何状态、思考和开场设定，也不含 `world_private`；表达调用另外看不到状态数值、构念与过程 ID、其他候选、假设和不打算说的事。

## 5. 本地参考运行器（`loop.py`）

按环境组接口实现的参考运行器，供离线联调和回归测试使用：

- `LocalRunner(episode, agents, environment, on_call, interaction_id, mode)`：先对每个角色 `initialize` 一次；每轮先处理导演指令（调环境、拆分、按接收者写进各角色的观察记录），再让当前角色 `act`，拆分后同样写进观察记录。不调用任何 perceive。跑满剧情文件的 `max_turns`。
- `EchoEnvironment`：环境桩，把导演指令原样当作环境的 action 输出，可限定接收者。
- `episode()` 从角色文件和场景生成运行输入，`character_id` 取角色文件 ID；`bootstrap()` 生成每个角色的初始化资料（`mechanism.mode` 为本次运行的模式）；场景的环境规则变成导演指令；不读 `world_private`。
- `run_scene()`：把 `StagedRoleAgent` 放到这个运行器后面，写出运行目录（文件清单见 [06-runtime-flow.md](06-runtime-flow.md) 第 5 节），结束时写自动审计和 `transcript.md`。

```bash
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3
```

（具体参数见 [03-usage.md](03-usage.md)。）

## 6. 现在的限制与待定

| 项 | 现状 |
|---|---|
| 输出形态 | 思考（只给本人）、发言、动作，接收者来自 S2 计划的受众 |
| 感知时机 | 没有 perceive，别人的言行要等本角色下次行动时才评价；中间不行动的角色状态不变（已确认接受） |
| `personality` / `mechanism` | 读 `role_ref`、`mode`；`enable_config` 只记录 |
| 跨互动 | 每场互动从角色库的入场状态开始，不延续上一场的动态状态和记忆 |
| `core_goals` | 写成本次互动的当前目标 `g-entry`（优先级取中性默认值 0.5），也进情境背景；不写入稳定画像的核心目标 |
| 并发 | 模型调用全局串行 |
| 结束对话 | 接口没有这个信号，分阶段管线也没有"结束这次沟通"的动作；本地运行跑满 `max_turns` |
| 环境线索 | 接口没有 `signals`；S1 自己给出线索强度（压力、不确定、承诺、修复、冲突，0—1），只用来激活人格 skill |
| 审计与打包 | 只认分阶段的运行目录（`manifest.pipeline == "stages"`）：`rolesim audit` / `package` / `readable` |
| 待环境组确认 | 见 [plan/08](../plan/08-framework-oop-design.md) 第 8.1 节 |

## 7. 测试

- `tests/test_agent_api.py`：类型和校验、`split` / `visible`。
- `tests/test_runner.py`：每个记录组一次事件阶段、反馈只来自计划的受众且每个计划一次、已吸收的记录跳过、计划被拒重做、失败后接着做、会话按互动与角色分开、返回内容合规、别人的思考丢弃、阶段失败可重试、`world_private` 不外泄、私有的环境事件只进接收者的提示、命令行。
- `tests/test_ports.py`：开场设定入场与隔离、skill 进计划与表达、`--no-skills`、三种模式对提示的影响、按角色的模式、初始化路由、未初始化即行动被拒、互动之间隔离。
- `tests/test_offline.py`：运行目录的自动审计（篡改后能查出：提示泄露场景私有数据或别人的隐瞒条目、证据不可见、稳定画像变化、隐瞒条目进表达、表达里有系统 ID、状态链断开）、评审包、可读版。
- `tests/test_interfaces.py`：`StagedRoleAgent` 与本地环境符合 Protocol。

运行：

```bash
python3.11 -m unittest discover -s tests -v
```

测试包含本地 HTTP 服务，受限环境下需如实报告。使用 mock 后端的运行只说明管线可用，不说明心理机制有效。
