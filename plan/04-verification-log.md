# 验证记录

按时间记录**已经做过的检查**：跑了什么、覆盖什么、没有覆盖什么。评测怎么设计、怎么盲评见 [docs/09-evaluation.md](../docs/09-evaluation.md)。这里只记事实，不做"角色像不像人"的结论；所有条目都属于"本地工程检查"层级，不构成心理有效性证据。

## 2026-09-30：首轮工程检查

Python 3.11 本机执行 `python3.11 -m unittest discover -s tests -v`，**16 项测试通过**。

覆盖：私有环境信息与对方私有事件不进入决策/表达模型、画像视图隔离、重复事件去重、人物条件化状态变化、口头承诺不等于兑现、不可见记录不能被动作直接获取、无效动作与未知证据引用拒绝、失败运行留档且不切换 mock、画像不变、确定性离线轨迹可复现、对照模式输入隔离、数值/编码校验、来源索引一致性、兼容接口的两阶段 HTTP 请求。

HTTP 测试使用本机临时回环服务器，返回固定结果。它验证传输格式，不证明已连接任何真实模型服务。沙箱内不允许监听时，需要在有本机监听权限的环境运行该项测试；不能跳过后宣称全通过。

当时已运行四轮脚本环境离线示例。未执行真实模型推理、心理学专家验收、真实服务器部署或心理有效性实验。

## 之后的变化（引自 [01-roadmap.md](01-roadmap.md)，未在本文复测）

| 日期 | 测试数 | 说明 |
|---|---|---|
| 2026-10-19 | 61 | 阶段 A：状态 v1；mock 冒烟运行与升级前逐字一致 |
| 2026-10-19 | 96 | 阶段 A2：画像 schema 2.0，每个反例断言预期错误 |
| 2026-10-20 | 126 → 138 → 142 | 阶段 D：skills、开场设定、记忆、角色/剧情文件、两角色自主对话；四个 mock 运行审计全过 |

2026-10-20：用本地 vLLM + Qwen3-8B（思考模式）跑通两角色 5 轮对话，审计全过；表达层台词复读，待修。后续又有 `runs/qwen3-8b_thinking_dialogue_test1`、`..._v2` 两次运行留档，结论仅限"数据通道可用"。

## 2026-10-21：接口定义 v1 与本地交互循环

`~/venvs/rolesim/bin/python -m unittest discover -s tests`（Python 3.11.15；PATH 上没有 `python3.11`）：**203 项通过**，其中新增 `test_agent_api.py` 17 项、`test_adapter.py` 11 项。本地 HTTP 回环测试在本环境可以监听，正常执行。

另用 mock 后端跑了 `run --interface agent-api`：test3（三人 12 轮）和 test1（两人 8 轮），状态均为 `completed`；检查了 test3 只发给林澄的手机消息没有进入另外两人的记录，`world_private` 的哨兵文本没有出现在运行目录的任何文件里。

没覆盖：真实模型运行、环境组的真实运行器与环境实现（用的是本地替身）、这条路径的自动审计报告。

## 2026-11-07：改为环境组接口 v2（initialize / act）

`python3 -m unittest discover -s tests`（Python 3.13.12；本机没有 `python3.11`，也没有 `~/venvs/rolesim`）：**200 项通过**。`test_agent_api.py`、`test_adapter.py` 按 v2 重写（删去 perceive、认知相关用例，新增初始化路由、未初始化即行动被拒、他人思考不进提示、新到场角色建立关系、互动之间隔离等用例），所以总数从 203 变为 200。

另用 mock 后端跑了 `run --interface agent-api`：test3（三人 12 轮，19 条记录，22 次调用含 3 次 initialize）和 test1（两人 8 轮），状态均为 `completed`；test3 只发给林澄的手机消息只有 `project` 一个接收者；`world_private` 的哨兵文本没有出现在两个运行目录的任何文件里。

没覆盖：真实模型运行、环境组的真实运行器、在场者中途变化的整场运行（只有单元测试）、这条路径的自动审计报告。

同日按环境组答复调整（角色 ID 即角色库 ID、在场含本人、`mechanism.mode`）：`python3 -m unittest discover -s tests` **202 项通过**（Python 3.13.12）。mock 运行 test3、test1、test1 `--mode profile_only` 均 `completed`；`character_id` 为 `role1`…`role3`；`profile_only` 运行的审计记录里档位均为 `profile_only`；test3 只发给林澄的私有消息只出现在 role1 的记录里；`world_private` 哨兵不在任何运行文件中。

## 2026-11-07：关键接口落成代码（rolesim/interfaces.py）

新增 `rolesim/interfaces.py`：plan/10 的 15 个接口（4 个顶层 + 11 个子接口）的 Protocol，以及阶段产物、画像、状态、证据、判断、变化、提交结果等不可变数据类型；`AgentCallError` 移到这里，`adapter.py` 改为引用。新增 `tests/test_interfaces.py`（13 项）：接口与方法齐全、`RoleAgentAdapter` 签名与 `IRoleAgent` 一致、算子名与 `state.OPERATORS` 一致、数据类型的不变量（变化必须有证据、不写 stable、区间越界不截断、S1 假设 1—3 个、S2 只能选保留的候选、模型推断必须带原文），以及用玩具实现走一遍 S1 → 提交 → S2 → 提交 → 表达。

`python3 -m unittest discover -s tests`（Python 3.13.12；本机没有 `python3.11`）：**215 项通过**。

没覆盖：任何按这些接口写的真实实现；接口里的字段是否满足心理学组的要求（F1—F5 仍待确认）。

同日把对外接口改名：`AgentAdapter` → `IRoleAgent`、`EnvironmentAdapter` → `IEnvironment`（签名不变，环境组文档里仍是原名），`tests/test_interfaces.py` 加一项检查本地 `EchoEnvironment` 与 `IEnvironment` 签名一致。`python3 -m unittest discover -s tests`：**216 项通过**（Python 3.13.12）。

## 维护方式

每次做出新的验证（测试数变化、新的模型运行、人工评审）就在这里加一节，写明日期、命令、结果、没覆盖的部分。测试数请以实际运行输出为准，上表来自计划文档的记录，不是本次重新统计。
