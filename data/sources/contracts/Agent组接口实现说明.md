# Agent 接口说明

本文只说明三个 Agent 接口的输入、输出和功能。

## 1. 角色行动接口

### 功能

根据角色当前拥有的资料、认知、可见历史和在场角色，模拟该角色完成一轮行动。一次调用可以同时返回思考、发言和外显行动，并由 `order` 指定三者的发生顺序。

### 接口

```python
class AgentAdapter:
    async def act(
        self,
        context: CharacterView,
        request_id: str,
    ) -> AgentActionResult:
        ...
```

### 输入：`CharacterView`

```python
CharacterView = {
    "setting": {
        "place": str | None,             # 当前互动地点
        "time_text": str | None,         # 当前互动时间表达
    },
    "trigger_facts": list[str],           # 所有角色共同知道的公开触发事实
    "name": str,                          # 当前角色姓名
    "profile_summary": str,               # 当前角色的稳定人物总结
    "relationships": list[str],           # 当前角色与其他角色的关系句
    "history_memory": str | None,        # 当前角色在互动前实际经历或获知的内容
    "personality": dict,                  # 当前分支的人格配置
    "mechanism": dict,                    # 当前分支的行动机制配置
    "cognition": CognitionBasis,          # 当前角色的最新认知
    "observations": list[InteractionRecord],
                                          # 当前角色实际可见的互动记录
    "present_characters": {
        "<character_id>": {"name": str},
    },                                     # 当前可直接互动的角色
}
```

其中：

```python
CognitionBasis = {
    "knowledge": str | None,              # 当前知道的事实
    "beliefs": str | None,                # 当前相信、怀疑或判断的内容
    "emotions": str | None,               # 当前情绪
    "motivations": str | None,            # 当前行动动机
    "goals": str | None,                  # 当前希望达成的目标
}
```

`observations` 中每条记录的结构为：

```python
InteractionRecord = {
    "action_id": str,                     # 同一轮相关记录的标识
    "turn_index": int,                    # 互动轮次
    "order_index": int,                   # 本轮内部发生顺序
    "actor": {
        "actor_id": str,                 # 行动者ID
        "name": str,                      # 行动者姓名
    },
    "kind": Literal["thought", "speech", "action"],
    "content": str,                      # 实际发生的内容
    "recipients": {
        "<character_id>": {"name": str},
    },                                    # 本条记录的可见对象
}
```

### 输出：`AgentActionResult`

```python
AgentActionResult = {
    "cognition": CognitionBasis,          # 完成本轮行动后的最新认知
    "action": {
        "thought": ActionPart | None,    # 思考；可以为空
        "speech": ActionPart | None,     # 发言；可以为空
        "action": ActionPart | None,     # 外显行动；可以为空
        "order": list[Literal[
            "thought", "speech", "action"
        ]],                               # 非空部分的实际发生顺序
    },
}
```

`ActionPart` 的结构为：

```python
ActionPart = {
    "content": str,                      # 思考、发言或行动的内容
    "recipient_ids": list[str] | None,   # 本部分希望被哪些角色看到
}
```

输出规则：

- `thought`、`speech`、`action` 至少有一个不为空。
- `order` 只填写不为空的部分，每种类型最多出现一次。
- `speech=None` 合法，例如只思考后行动：`order=["thought", "action"]`。
- `recipient_ids=None` 表示当前在场角色都可以看到；定向接收者使用 `present_characters` 中的角色 ID。
- 角色自身的思考应只对本人可见。
- `cognition` 必须包含 `knowledge`、`beliefs`、`emotions`、`motivations`、`goals` 五个字段。

## 2. 角色感知接口

### 功能

让角色吸收本轮新看到的互动内容，并更新自己的知识、信念、情绪、动机和目标。该接口只负责认知更新，不生成新的思考、发言或行动。

### 接口

```python
class AgentAdapter:
    async def perceive(
        self,
        context: CharacterPerceptionInput,
        request_id: str,
    ) -> AgentPerceptionResult:
        ...
```

### 输入：`CharacterPerceptionInput`

感知接口只接收一个完整的数据结构：

```python
CharacterPerceptionInput = {
    "character_view": CharacterView,
    # 本轮行动发生前的完整角色视角。
    # 其中 observations 不包含本轮新记录，cognition 是更新前的认知。

    "new_observations": list[InteractionRecord],
    # 本轮该角色实际看到或听到的记录，已经按 order_index 排列。
    # 同一轮的 thought、speech、action 合并在一个列表中，一次传入。
}
```

`character_view` 与角色行动接口的 `CharacterView` 结构完全相同。运行器在调用本接口前完成以下准备：

1. 按 `ActionRecord.order` 拆分本轮内容。
2. 根据每条记录的 `recipient_ids`，汇总每个角色实际可见的 `InteractionRecord`。
3. 为每个受影响的非行动者构造一个 `CharacterPerceptionInput`。
4. 每个受影响的非行动者只调用一次 `perceive`。

本接口内部应结合 `character_view["cognition"]` 与 `new_observations` 判断认知变化。接口调用成功后，运行器再将 `new_observations` 追加到该角色的 `observations`，并用返回的 `cognition` 替换旧认知。这样可以明确区分角色原来知道的内容和本轮新获得的信息。

### 输出：`AgentPerceptionResult`

```python
AgentPerceptionResult = {
    "cognition": CognitionBasis,          # 吸收新观察后的最新认知
}
```

输出规则：

- 必须返回完整的 `CognitionBasis`。
- 只能更新当前角色自己的认知。
- 不返回 `action`、`thought`、`speech` 或新的观察记录。
- 如果新观察没有改变某个认知字段，保留原值。

## 3. 环境行动接口

### 功能

根据当前故事条件、互动历史和导演给出的环境指导，生成本轮客观发生的环境反应，例如广播、电话、声音、门被推开或文件掉落。

### 接口

```python
class EnvironmentAdapter:
    async def act(
        self,
        context: EnvironmentActorView,
        request_id: str,
    ) -> AgentActionContent:
        ...
```

### 输入：`EnvironmentActorView`

```python
EnvironmentActorView = {
    "setting": {
        "place": str | None,             # 当前互动地点
        "time_text": str | None,         # 当前互动时间表达
    },
    "story_background": str,              # 故事背景
    "trigger_facts": list[str],           # 已成立的公开触发事实
    "present_characters": {
        "<character_id>": {"name": str},
    },                                     # 当前可接收环境信息的角色
    "interaction_history": list[InteractionRecord],
                                          # 全部互动历史，按实际顺序排列
    "environment_directive": str,         # 本轮希望环境产生的反应
}
```

### 输出：`AgentActionContent`

```python
AgentActionContent = {
    "thought": None,                      # 环境不返回思考
    "speech": ActionPart | None,          # 广播、电话或其他环境发言
    "action": ActionPart | None,          # 声音、物体变化等客观行动
    "order": list[Literal["speech", "action"]],
                                           # 非空部分的发生顺序
}
```

输出规则：

- 至少返回 `speech` 或 `action` 之一。
- 环境通常不返回 `thought`。
- `recipient_ids=None` 表示当前在场角色都能接收到该环境信息。
- 定向环境信息只能发送给 `present_characters` 中的角色。
- `order` 只填写不为空的部分，每种类型最多出现一次。
- 不返回 `cognition`。

