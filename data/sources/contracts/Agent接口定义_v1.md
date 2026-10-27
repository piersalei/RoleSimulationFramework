---
title: "Agent 接口定义 v1（运行器 ⇄ 角色 Agent / 环境）"
version: "v1-draft"
date: "2026-10-21"
status: "草案，交环境组照此实现运行器与环境；标【待定】处后续补"
from: "角色 Agent 模块"
to: "环境组"
based_on: "Agent组接口实现说明.md（环境组反馈）"
code: "rolesim/agent_api.py（类型、校验函数、拆分与分发工具）；tests/test_agent_api.py（示例与边界）"
---

# Agent 接口定义 v1

本文在《Agent组接口实现说明》的基础上，把双方各自的义务写死，结构不变，只补充规则。
三个接口的字段与原说明**完全一致**；新增的只有"谁保证什么"。
Python 版本的类型和校验在 `rolesim/agent_api.py`，环境组可以直接拿去用：运行器发出前、收到后各调一次校验函数，不通过就是哪一方的问题，一目了然。

## 0. 分工

| | 运行器 / 环境（环境组） | 角色 Agent（我们） |
|---|---|---|
| 回合、谁行动、导演指令 | ✔ | |
| 角色的思考、发言、行动、认知 | | ✔ `AgentAdapter.act / perceive` |
| 环境反应（广播、电话、声音） | ✔ `EnvironmentAdapter.act` | |
| 把一次行动拆成记录、按接收者分发 | ✔ | |
| 保存每个角色的 `observations` 与 `cognition` | ✔ | 内部另存自己的心理状态，不需要运行器管 |

角色 Agent 的输出直接成为事实，v1 不做行动裁决。

## 1. 身份与命名

1. **角色用 `name` 标识**：同一场互动里名字必须唯一。我们这边由程序把 `name` 对到自己的角色，运行器不需要知道我们的内部 ID。
2. **角色 ID 归运行器**：`present_characters`、`recipients` 的键和 `recipient_ids` 都是运行器的角色 ID，格式随意，同一场内不变即可。
3. **`present_characters` 不含行动者本人**；所有角色名两两不同。
4. **环境的行动者**固定为 `{"actor_id": "env", "name": "环境"}`，`"env"` 不得用作角色 ID。

## 2. 记录（`InteractionRecord`）

1. `turn_index` 从 1 开始；`order_index` 从 0 开始，**在同一轮内跨行动者递增**（例如本轮环境用了 0、1，角色从 2 开始）。
2. 一次 `act` 调用产生的所有记录共用一个 `action_id`，不同调用的 `action_id` 不重复。
3. `action_id + "#" + order_index` 是一条记录的唯一编号，我们用它作为心理状态变化的证据引用，请保证它在整场互动里唯一、不复用。
4. 任何列表里的记录都按 `(turn_index, order_index)` 升序排列。
5. **思考只对本人可见**：`kind == "thought"` 的记录，`recipients` 只能是行动者本人。环境不产生思考。

## 3. 运行器每轮要做的事

用 `split()` 拆分记录，用 `deliveries()` 决定分发，用 `environment_history()` 过滤历史，三者都在 `agent_api.py` 里。

```
若本轮有导演指令：
    content = await env.act(环境视角, request_id)          # 历史中不含任何 thought
    records = split(content, "env", "环境", 在场角色, action_id, turn, 下一个 order_index)
    对 deliveries(records, "环境", 全部角色名) 中的每个角色 c：
        result = await agent.perceive({character_view: view[c], new_observations: 它可见的记录})
        view[c].observations += 这些记录；view[c].cognition = result.cognition
行动者 a：
    result = await agent.act(view[a], request_id)
    view[a].cognition = result.cognition
    records = split(result.action, a 的 ID, a 的名字, view[a].present_characters, action_id, turn, 下一个 order_index)
    view[a].observations += records                      # 行动者本人的记录（含思考）直接追加，不调 perceive
    对 deliveries(records, a 的名字, 全部角色名) 中的每个角色 c：同上，perceive 一次
```

- `recipient_ids = null` 表示行动者当时的全部在场角色；思考固定只给本人。
- 一个角色一轮最多被 `perceive` 一次；看不到任何新记录的角色不调用。
- 同一轮里对不同角色的 `perceive` 可以并行；`act` 依次进行。

## 4. 各方收到数据时的校验

| 时机 | 校验函数 | 主要检查 |
|---|---|---|
| 运行器调 `act` 前 | `validate_character_view` | 字段齐全；认知五个字段都在；`observations` 有序、不重复、每条对本角色可见（别人的思考不得出现）；本人不在 `present_characters` 里；在场角色名字唯一 |
| 运行器收到 `act` 结果后 | `validate_action_result(result, view)` | 认知五个字段都在；至少一部分非空；`order` 恰好列出非空部分；`recipient_ids` 为 null 或在场角色 ID 的非空列表；思考的 `recipient_ids` 必须为 null |
| 运行器调 `perceive` 前 | `validate_perception_input` | 新记录非空、对本角色可见、不是本人的记录、不在已有 `observations` 里 |
| 运行器收到 `perceive` 结果后 | `validate_perception_result` | 只有 `cognition`，且五个字段都在 |
| 运行器调环境前 / 收到后 | `validate_environment_view` / `validate_environment_content` | 历史中没有思考；环境不返回思考和认知；定向信息只发给在场角色 |

我们这边收到请求时也会跑同样的校验，不通过时抛 `InterfaceError`。

## 5. 认知（`CognitionBasis`）

- 五个键都必须出现，值为字符串或 `null`（`null` 表示这一项还没有内容）。
- 运行器原样保存我们返回的认知，下一次调用时原样传回，**不修改**。
- 角色的心理状态以我们内部记录为准，返回的认知是它的文字摘要；如果传回的认知和我们上次返回的不一致，我们会记录告警，但不会据此改动内部状态。
- 首次 `act` 前的初始认知：【待定】运行器给五个 `null`，或者给剧本作者写的初始值。

## 6. 调用约定

- 接口为 `async`，同一进程内直接调用。
- `request_id` 由运行器生成，整场互动内唯一。**同一个 `request_id` 重发时，我们返回第一次的结果**，所以超时重试是安全的；重试时请不要换新的 `request_id`。
- 出错时抛异常，不返回不完整的结果；我们不会在出错时把思考当发言输出。
- 一场互动里同一个角色名始终对应同一个角色。

## 7. 待定

1. `personality`、`mechanism` 的内部字段：暂时当作任意对象原样传入，字段后续再定。【待定】
2. 分支：同一角色在不同分支里是否会并行运行？如果会，需要在请求里带分支或会话标识，否则无法区分各分支的内部状态。【待定】
3. 结束对话：角色想结束对话时如何告诉运行器？可以考虑在行动结果里加一个可选字段。【待定】
4. 首次 `act` 前的初始认知（见第 5 节）。【待定】
