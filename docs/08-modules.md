# 模块划分

代码按 [plan/10](../plan/10-interfaces.md) 的 4 个顶层接口组织，每个接口一组模块。接口不变，实现可以替换；接口的签名、保证、出错方式以 plan/10 为准，契约的代码形式在 `rolesim/interfaces.py`。

## 1. 接口与模块

| 接口 | 模块 | 职责 |
|---|---|---|
| **1 IRoleAgent**（对外，环境组接口 v2） | `agent/agent_api.py`、`agent/adapter.py`、`agent/staged.py` | 数据类型与双向校验；选角色、按 `interaction_id + character_id` 的会话、`request_id` 重试；`StagedRoleAgent` 是唯一实现 |
| 1.1 ITurnRunner | `agent/runner.py` | 一次行动里各阶段的编排、反馈判断、S2 被拒重做、失败后接着做 |
| 1.2 ITrace | `agent/trace.py` | 阶段记录与模型调用按互动、按角色落盘 |
| **2 IRoleRepository** | `role/repository.py` | 档案文件优先、否则转换角色文件；入场状态（档案 → 开场设定 → bootstrap → §8.2 默认值）；开场设定与人格 skill |
| 2.1 IProfileCompiler | `role/profile.py` | profile-1 档案 → 稳定画像 + 待审核清单；条件化语句检查 |
| 2.2 IRegistry | `role/registry.py` | 心理学组 v1 文件 → `data/registry.json`，只读查询 |
| **3 IStage** | `stage/stages.py` | S1 / S2 / S3：视图（自知层、开场设定、skill、模式）、调用、解析 |
| 3.1 IEvidenceGate | `stage/evidence.py` | 可见证据、排除原因、记忆取回 |
| 3.2 IMechanism | （在阶段调用里由模型判断）`stage/schemas.py` | 判断的 schema 与登记表核对；按规则实现的过程还没有 |
| 3.3 IModel | `stage/model.py`、`stage/providers.py`、`stage/schemas.py` | 每个调用一次结构化请求；输出 schema；chat-completions 传输 |
| 3.4 IExpresser | `stage/expresser.py` | 按需可见的表达调用 → 思考 / 发言 / 动作 |
| **4 IStateStore** | `state/store.py`、`state/slots.py` | 按构念 ID 的状态布局；唯一的写入口 |
| 4.1 IUpdateOperator | `state/operators.py` | 11 个算子（规则全部来自登记表） |
| 4.2 IMagnitude | `state/magnitude.py` | 合法变化区间 |
| 4.3 IValidator | `state/validator.py` | 硬错 / 软异常 |

不属于某个接口的支撑模块：`role/roles.py`、`role/persona.py`、`role/briefing.py`、`role/skills.py`、`run/scene.py`、`vocab.py`（输入文件的格式与校验），`run/loop.py`（本地参考运行器与运行目录），`offline/audit.py`、`offline/package.py`、`offline/readable.py`（离线工具），`catalog.py`（来源索引），`stage/memory.py`（字面相似度检索），`__main__.py`（命令行）。

## 2. 人格方案：三案互斥

心智建模库 2.1 给出三套**互斥**的主人格编码方案：P1 大五 30 分面、P2 16PF 十六个初级因素、P3 荣格八功能 + HEXACO 分面。

- 档案（profile-1）里一个角色最多启用一套，另外两套自动关闭；三套的分数都是 `-2—+2` 内部标准化分数，原量表分必须先转换。
- 现状：角色文件（persona 2.0）只支持 P1；编译器只检查 P1 分面的取值形式；幅度档的人格敏感度只读 P1 分面。P2 / P3 要等比较设计落地时补检查与映射。
- 比较设计（尚未做）：同一角色设定、三种主编码、独立运行、统一评测。

## 3. 模块间的硬边界

1. **心理学组的定义只经登记表进入代码。** 构念、决策过程、算子的写入白名单与阶段权限都从 `data/registry.json` 读；心理学组发新版时重新生成，原文改动而没重新生成时测试失败。
2. **环境组只看到接口 v2。** 内部状态、画像、轨迹对环境组不透明；`act` 只返回思考 / 发言 / 动作。
3. **只有 `IModel` 接触模型。** 登记表、状态存储、验证、档案编译都能在没有模型的情况下完整跑校验，工程正确性独立于模型质量验证。
4. **阶段只判断，`IStateStore` 才写（C8）。** 算子（`IStateStore.draft`）按规则卡把判断变成变化槽位，`IValueChooser` 在区间内取值，`IStateStore.commit` 写入；没有别的地方能产生新的 `AgentState`。
