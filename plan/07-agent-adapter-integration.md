# 对接环境组的 Agent 接口（AgentAdapter / EnvironmentAdapter）

状态：**G1、G2、G3、G5、G6 已完成**（2026-10-21）；G7 文档已更新；实现记录与剩余工作见第 6 节。来源：[Agent组接口实现说明.md](../data/sources/contracts/Agent组接口实现说明.md)（环境组反馈，下称"新接口"）。
它实际取代了我们 10-15 发出的 [环境组接口合同_v0.md](../data/sources/contracts/环境组接口合同_v0.md)（下称"合同 v0"）里的 `step` / `perceive`。

## 0. 已定事项（2026-10-21，作者）

1. **本人身份由我们这边的程序控制**：角色用 `name` 标识，程序把 name 对到自己的角色（`agent_api.Directory`），不要求运行器给本人 ID。原来的 D4 / Q1 就此关闭。
2. **`personality` / `mechanism` 的内部字段以后再定**：作者先去问，现在当作任意对象原样透传，不做解析。
3. **环境接口归环境组实现**：我们只负责输入输出对得上。原来的 G4（我们实现环境适配器）取消，改为由我们把接口定义写出来交给对方，即 [Agent接口定义_v1.md](../data/sources/contracts/Agent接口定义_v1.md) 加上可执行的 `rolesim/agent_api.py`。

## 1. 新接口在说什么

环境组写好了运行器（runner），由它掌握回合、可见性和每个角色的认知快照；它调用三个接口：

| 接口 | 输入 | 输出 | 谁实现 |
|---|---|---|---|
| `AgentAdapter.act(CharacterView, request_id)` | 角色此刻的完整视角：场景、公共触发事实、人物总结、关系句、前史、人格/机制配置、当前认知、可见互动记录、在场角色 | 新认知 + 一组 thought / speech / action（任选、带 `order` 与 `recipient_ids`） | 我们 |
| `AgentAdapter.perceive({character_view, new_observations}, request_id)` | 行动前的视角 + 本轮新看到的记录 | 新认知（只有认知） | 我们 |
| `EnvironmentAdapter.act(EnvironmentActorView, request_id)` | 场景、故事背景、全部互动历史、导演给的 `environment_directive` | speech / action（无 thought、无认知） | 环境组（已定，见第 0 节） |

运行器每轮：调 `act` → 按 `order` 拆成 `InteractionRecord` → 按 `recipient_ids` 分发 → 对每个受影响的**非行动者**调一次 `perceive` → 把新记录追加进该角色的 `observations`，用返回的 `cognition` 替换旧认知。

## 2. 和我们现在实现的差异

| # | 方面 | 合同 v0 / 现有代码 | 新接口 | 影响 |
|---|---|---|---|---|
| D1 | 调用形态 | 同步；`Agent` 有状态，靠 `prev_state_id` 串起状态链，事件增量传入 | `async`；每次传完整视角，`request_id` 标识请求；认知由运行器保存并回传 | 需要一层适配器：内部仍保留有状态的 `Agent`，按观察记录做增量 |
| D2 | 心理状态 | `state-v1` 六域数值状态，每条变化带 `evidence` 事件 ID、`mechanism`、依赖组提交 | `CognitionBasis` 五个自由文本字段（knowledge / beliefs / emotions / motivations / goals） | 内部状态仍是权威，`cognition` 是从它**投影**出的文本；运行器回传的 `cognition` 只用于校验 |
| D3 | 人物输入 | 人物档案 schema 2.0（`validate_persona`）、skills、briefing、座位称呼 | `name`、`profile_summary`、`relationships`、`history_memory`、`personality: dict`、`mechanism: dict` | 无法从 `profile_summary` 还原 schema 2.0；需约定 `personality` 里放什么（见第 5 节 Q2） |
| D4 | 自身 ID | `role_id` 每次都在请求里 | `CharacterView` 只有 `name`，没有本人 `character_id` | **已定**：用 name 路由，要求同一场内名字唯一、`present_characters` 不含本人；思考的 `recipient_ids` 固定为 null，由运行器只发给本人 |
| D5 | 输出 | 一次 `step` 只出一句 speech；先选离散动作（`allowed_actions`），再由表达层成句；`intent_note` 记动作 | thought / speech / action 三选多，带顺序与定向接收者；无动作标签字段 | v0 只出 thought + speech；动作标签只进我们自己的日志；`close` 无法再通知运行器结束（Q7） |
| D6 | 事件 ID | 环境生成 `event_id`，状态变化必须引用它 | `InteractionRecord` 只有 `action_id` + `order_index` + `kind` | 由适配器确定性地派生：`{action_id}#{order_index}`，保证"每条状态变化带事件 ID"仍成立 |
| D7 | 行动者感知 | 行动者也 `perceive` 自己被裁决后的事件，台词由此进入记忆 | 只有非行动者被 `perceive`；行动者的记录直接追加到它的 `observations` | 适配器在下一次 `act` 时把 `observations` 里未见过的记录（含自己的）补入记忆 |
| D8 | 裁决 | 环境裁决行动成败，发 `outcome` 事件 | 没有裁决，Agent 的输出直接成为事实 | `outcome` 路径闲置；角色只能"提议"这一原则在新接口里不再成立，需在 docs 说明 |
| D9 | 思考 | `thought` 永不分发，只进日志 | thought 作为 `InteractionRecord` 记录，只对本人可见 | 本人下一轮能看到自己的思考，可以接受；但 thought 里不能出现机制名、数值、world_private（现在的 `brief_reason` 是控制层理由，需改成角色口吻的内心话） |
| D10 | 环境 agent（环境组实现） | 规则驱动 `WorldAgent`：按回合推进时钟、按表投放事件，只发 narration | 导演指令驱动：每轮给一句 `environment_directive`，环境生成广播 / 电话 / 声音等 | 由环境组实现；我们只定义输入输出。本地参考循环里用一个桩：把导演指令原样当作环境的 action 输出 |
| D11 | 环境可见范围 | 环境只发事件，不读角色内心 | `interaction_history` 是"全部互动历史"，按字面会包含各角色的 thought | 已写入接口定义：运行器用 `environment_history()` 过滤，`validate_environment_view` 拒收含思考的历史 |
| D12 | 线索 | 环境可带 `signals.cues`；没有时角色自评 | 无 cues | 角色一律自评（现有 `_appraise` 已支持），无需改 |
| D13 | 失败 | `status="error"` + 空 actions，不回退输出内心独白 | 未定义 | 已写入接口定义：出错抛异常；同一 `request_id` 重发返回第一次的结果（适配器要实现缓存） |
| D14 | 分支 | 一次运行一个模式（三档消融） | `personality` / `mechanism` 是"当前分支"的配置，同一角色可能在多个分支并行 | 适配器内的 `Agent` 实例必须按（分支, 角色）区分，否则状态串线（Q3） |

## 3. 改动计划

不动现有 `Agent` 的核心（评价 → 机制卡 → 决策 → 表达），只在外面加适配层；合同 v0 的 `step` / `perceive` 保留给现有测试与 `run_dialogue`，直到新接口跑通再决定是否删除。

### G1 数据类型与校验 —— `rolesim/agent_api.py`（**已完成**，`tests/test_agent_api.py` 17 个测试）
- 除下面列出的内容，另有：`Protocol` 形式的 `AgentAdapter` / `EnvironmentAdapter`；给运行器用的 `split()`（按 order 拆记录）、`deliveries()`（谁要 perceive、各看到什么）、`environment_history()`（去掉思考）；我们这边的 `Directory`（name → 角色 ID）。
- 给环境组的文字版：[Agent接口定义_v1.md](../data/sources/contracts/Agent接口定义_v1.md)。
- 用 `TypedDict` 写出 `CharacterView`、`CognitionBasis`、`InteractionRecord`、`ActionPart`、`AgentActionResult`、`CharacterPerceptionInput`、`AgentPerceptionResult`、`EnvironmentActorView`、`AgentActionContent`，字段与新接口一一对应。
- 校验函数：输入（必填字段、`present_characters` 键、`observations` 有序且 `(action_id, order_index)` 唯一）与输出（至少一部分非空、`order` 恰好列出非空部分且不重复、`recipient_ids` 只引用在场角色、`cognition` 五字段齐全、环境输出不含 thought / cognition）。
- `record_id(record) = f"{action_id}#{order_index}"`（D6）。

### G2 认知投影 —— `rolesim/cognition.py`（新）
- `project(state, persona, names) -> CognitionBasis`，由内部状态生成五段文字：
  - `emotions`：`affect_state.emotion` 的标签 + `stateview` 的等级词；
  - `beliefs`：`cognition_state.beliefs` / `other_models` 中 active 的条目；
  - `goals` / `motivations`：`goal_state.goals_now`、briefing 的目标，以及 stable 的 `core_goals`；
  - `knowledge`：记忆中已见过的关键事实（briefing recap + 召回的观察摘要）。
- 只用模板，不调模型，因此不会引入新的未审计状态变化；`None` 表示该模块未知或未建模。
- 回传校验：下一次调用时运行器给的 `cognition` 与我们上次返回的不一致 → 记 warning（可能是分支重放或运行器改了认知），**不**反向写入内部状态。

### G3 角色适配器 —— `rolesim/adapter.py`（新）
- `RoleAgentAdapter(backend, directory, roles)`，实现 `async act` / `async perceive`；`directory` 把 `view["name"]`、`present_characters` 和记录的行动者都对到我们的角色 ID，`roles` 是已加载的角色文件。
- 入口先跑 G1 的校验；按 `request_id` 缓存结果，重发时直接返回（接口定义第 6 节的承诺）。
- 内部字典 `{(branch_key, self_id): Agent}`，首次见到时用 `make_agent` 建立；`branch_key` 暂取 `digest({personality, mechanism})`，等对方给分支 ID 再换（Q3）。同一键加 `asyncio.Lock`，模型调用放进 `asyncio.to_thread`。
- 把视角转成合同 v0 事件后复用现有路径（这样审计、mechanism_log、状态链全部沿用）：
  - `observations` 里未见过的记录 → `Event(event_id=record_id, type=kind, actor, content, audience=[self]+recipients)`，thought 记录只在 actor 为本人时接收，其他人的 thought 出现在本人视角里视为运行器错误并告警；
  - `act`：补齐未见记录 → `Agent.step` → 输出 `{thought: 角色口吻的内心话(仅本人), speech: 台词(recipient_ids=None), action: None, order: ["thought","speech"]}`，`cognition = project(...)`；
  - `perceive`：`new_observations` → `Agent.perceive` → 返回 `project(...)`。
- 思考文本（D9）：在决策输出里加一个 `inner_thought` 字段，由模型用角色口吻写，限制长度；校验它不含机制卡名、操作符名与数字。`brief_reason` 继续只进日志。
- 人物输入（D3）：按 `name` 经 `directory` 找到 `roles/*.json`；`profile_summary` / `relationships` / `history_memory` 放进场景背景供提示词参考，`history_memory` 同时作为 briefing recap 的来源。正式方案等 Q2 答复。
- `personality` / `mechanism` 暂不解析；三档模式先由我们这边的运行参数给定，等字段定下来再改为从 `mechanism` 读取。

### G4 环境适配器 —— 取消
环境组实现。我们只在 G5 的本地循环里放一个桩（`EchoEnvironment`：把导演指令原样当作 action 输出给所有在场角色），保证离线测试能跑；对方的实现必须通过 `validate_environment_content`。

### G5 本地交互循环 —— `rolesim/loop.py`（新）
运行器归环境组，但我们需要一个**按新接口规则实现的参考运行器**做离线联调与回归测试，不和他们的实现争主导。

```
初始化：每个角色的 CharacterView（observations=[]，cognition=首轮投影或由运行器给定）
for turn in 1..max_turns:
    directive = director(turn)                    # 来自 scene 的环境规则表，没有则跳过
    if directive:
        content = await env.act(env_view(turn, directive))
        records = split(content, "env", "环境", present, action_id, turn, next_order)
        await deliver(records, actor="env")                  # 所有接收者都 perceive
    actor = speaker_order(turn)                   # 先沿用座位轮转
    result = await agents.act(view[actor])
    view[actor].cognition = result.cognition
    records = split(result.action, actor, turn)
    view[actor].observations += records           # 行动者自己的记录直接追加（含仅本人可见的 thought）
    await deliver(records, actor)                 # 每个非行动者：只收自己可见的，最多 perceive 一次

deliver(records, actor):
    for c in present - {actor}:
        seen = [r for r in records if c in r.recipients]
        if seen:
            res = await agents.perceive({character_view: view[c], new_observations: seen})
            view[c].observations += seen; view[c].cognition = res.cognition
```

- 同轮内各非行动者的 `perceive` 可以 `asyncio.gather` 并行（互不依赖）；行动严格串行。
- 落盘沿用现有 run 目录：`manifest.json`、每个角色的 `audit.jsonl` / `states.jsonl` / `mechanism_log.jsonl`，新增 `interaction_records.jsonl`（运行器视角的全部记录）和 `views/<role>/<request_id>.json`（每次调用的完整输入，便于和环境组对账）；`transcript.md` 与审计报告改为从 `interaction_records.jsonl` 生成。
- CLI：`python3.11 -m rolesim run --interface agent-v1`，默认仍是现有接口，跑通后再切换默认。

### G6 测试 —— `tests/test_adapter.py`、`tests/test_loop.py`（新）
- 输出合法性：`order` 与非空部分一致；`recipient_ids` 只含在场角色；thought 只对本人可见；`cognition` 五字段齐全。
- 隐私：
  - 角色 A 的 thought、`world_private`、其他角色的 stable / briefing 不出现在 B 的任何提示词、B 的 `cognition` 与环境提示词里；
  - 给 A 一条 recipients 不含 A 的记录，A 的状态与输出都不得引用它（沿用合同 v0 第 6 节的验收）。
- 状态链：每条状态变化的 evidence 都能在 `record_id` 集合里找到；stable 指纹全程不变；相同种子 + mock 后端两次运行结果一致。
- 行动者补齐（D7）：A 说的话在 A 的下一次 `act` 前进入 A 的记忆，且只进一次。
- 跑 `python3.11 -m unittest discover -s tests -v`。

### G7 文档
- `docs/06-runtime-flow.md`、`docs/07-architecture.md`：加新接口的调用流程与适配层位置，说明"无裁决、Agent 输出即事实"（D8）。
- `plan/01-roadmap.md` 的待确认接口问题：把合同 v0 第 7 节中被新接口回答的条目标为已定（历史传全量、依次行动、`intent_note` 不需要、同进程 async 调用）。

顺序：G1 → G2 → G3 → G5（环境用桩）→ G6 → G7，均已完成（第 6 节）。

## 4. 证据层级

本计划全部是**工程对接**：认知投影的措辞、等级词、思考文本都是工程假设，不代表心理学组认可的表示方式；mock 运行只说明管线可用，不验证任何心理机制。

## 5. 待定问题

已定：本人 ID（name 路由）、环境接口归属（环境组）、环境历史不含思考、失败约定（抛异常 + `request_id` 幂等）、谁行动与导演指令（运行器侧）。都已写进接口定义。

还开着的：
1. **`personality` / `mechanism` 的字段**：作者去问；在此之前原样透传。
2. **分支**：同一角色在不同分支里是否会并行运行？如果会，请求里需要带分支或会话标识；在此之前，适配器假设一场互动只有一条分支。
3. **结束对话**：角色想结束时怎么通知运行器？是否在行动结果里加可选字段。
4. **初始认知**：首次 `act` 前，运行器给五个 null，还是给剧本作者写的初始值。
5. **联调样例**：请对方开工后给一份真实的 `CharacterView`（2～3 个角色、3 轮）。

## 6. 实现记录（2026-10-21）

| 项 | 文件 | 结果 |
|---|---|---|
| G1 | `rolesim/agent_api.py`、`tests/test_agent_api.py` | 类型、双方校验、`split` / `deliveries` / `environment_history`、`Directory`；17 个测试 |
| G2 | `rolesim/cognition.py` | 模板投影：knowledge = 前情 + 最近 5 条记忆；beliefs = 对他人的关系档位 + 信念条目；emotions = 情绪档位；motivations = 人物 `core_goals`；goals = 开场目标 + `goals_now`。不含数值、机制名、隐瞒内容 |
| G3 | `rolesim/adapter.py`；`Agent.begin()`（`agent.py`） | 见 [docs/06](../docs/06-runtime-flow.md) 第 12 节。`begin()` 让 `perceive` 可以先于第一次 `act` |
| G5 | `rolesim/loop.py`；`rolesim run --interface agent-api` | `LocalRunner` + `EchoEnvironment`；剧情的环境规则转成导演指令（含只给部分角色的事件） |
| G6 | `tests/test_adapter.py` | 11 个测试：输出合法、请求幂等、perceive 先于 act、自己的记录只进记忆一次且思考跳过、状态变化都能追溯到记录、未知角色与在场变化被拒、失败后可重来、运行器改写的认知不被采纳、三角色跑满且各视角合法、私有事件只到指定角色、world_private 和隐瞒内容不进运行器可见的数据与表达提示 |

全量测试：`~/venvs/rolesim/bin/python -m unittest discover -s tests`，203 个通过（本机 PATH 上没有 `python3.11`，用的是 docs/03 提到的 3.11.15 venv）。mock 后端跑过 test1（两人 8 轮）和 test3（三人 12 轮）；没有跑真实模型。

### 现在的限制（都是有意留下的，等对应问题定了再做）

1. **只出台词**：`thought`、`action` 恒为 null。要出思考，需要决策输出加一个角色口吻的 `inner_thought` 字段并校验它不含机制名和数值，这会改动 `ChatBackend` 的提示与 `validate_decision`；外显行动需要新的动作词表。
2. **在场角色固定**：以角色第一次被调用时的 `present_characters` 为准，之后变化就拒绝（关系状态按在场的人初始化）。
3. **单分支**：一个适配器实例对应一场互动的一条分支；`personality` / `mechanism` 不读。消融档位、skills 开关由我们这边的运行参数决定。
4. **模型调用串行**：适配器内部一把锁，因为后端对象记录了每次调用的状态（`last_reasoning`、调用计数）。`perceive` 在运行器侧是并行发出的，但实际排队执行。
5. **结束对话**：本地运行器读取适配器的 `closed_by()`，接口本身还没有这个信号。
6. **审计与打包**：这条路径暂不生成 `audit_report`，也不能用 `rolesim package` 打包，因为两者读取的是原路径的目录格式。
7. **环境线索**：新接口没有 `signals`，剧情规则表里给环境事件标的 `cues` 不再使用，角色一律自己评价线索。

### 下一步候选

- 让审计与可读版（`audit.py`、`readable.py`）也能读 `agent-api` 的运行目录。
- 加入角色思考输出（上面第 1 条）。
- 环境组给出真实的 `CharacterView` 样例后，加一个用该样例的联调测试。

