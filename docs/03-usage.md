# 使用指南

怎么装、怎么跑、怎么接真实模型、跑完在哪看结果、怎么测试。不涉及原理；原理见 [06-runtime-flow.md](06-runtime-flow.md) 和 [07-architecture.md](07-architecture.md)。不熟的词见 [02-concepts.md](02-concepts.md)。

## 1. 环境

- Python 3.11+。运行时只用标准库，不需要装依赖；在仓库根目录执行。
- 也可以 `pip install -e .` 得到 `rolesim` 命令，下文都写成 `python3.11 -m rolesim`。
- 本机有 `~/venvs/rolesim`（Python 3.11.15，仅标准库）；自己的终端可以 `source ~/venvs/rolesim/bin/activate`。

## 2. 不用模型，先跑通

```bash
python3.11 -m unittest discover -s tests -v          # 全部测试
python3.11 -m rolesim list                           # 角色 role1–role3；剧情 test1、test2（两人）、test3（三人 + 环境 agent）
python3.11 -m rolesim validate role1                 # 校验一个角色文件
python3.11 -m rolesim skills role1                   # 校验 skill 库及该角色的 skill
python3.11 -m rolesim profile role1                  # 编译档案，列出待审核项
python3.11 -m rolesim run --roles role1 role2 --scene test1          # mock 后端跑一场
python3.11 -m rolesim run --roles role1 role2 --scene test2
python3.11 -m rolesim run --roles role1 role2 --scene test1 --no-skills   # 不注入 skill 的对照
python3.11 -m rolesim run --roles role1 role2 --scene test1 --mode profile_state   # 换消融模式
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3   # 三个角色 + 环境 agent
```

角色一律放在环境组接口 v2（`initialize` / `act`）后面，由本地参考运行器驱动；每次行动走分阶段管线（S1 / S3 / S2 → 状态存储 → 表达），见 [06-runtime-flow.md](06-runtime-flow.md)。

`mock`（`MockModel`）按关键词给出线索和一条信念、固定选"说明情况并提出方案"、用模板表达（"我想先……"），**只能用来验证流程**。角色输出不同不构成心理有效性证据。

`run` 的参数：

| 参数 | 含义 | 默认 |
|---|---|---|
| `--roles` | 每个角色座位一个角色编号或文件路径，按座位顺序（2–6 个） | `role1 role2` |
| `--scene` | 剧情编号或文件路径 | `test1` |
| `--backend` | `mock` 或 `chat` | `mock` |
| `--mode` | 消融模式：`profile_only`（提示里没有动态状态，也没有决策过程判断）/ `profile_state`（有状态，没有判断）/ `profile_state_mechanisms`（全部）；状态在三种模式下都照常计算和记录 | `profile_state_mechanisms` |
| `--no-skills` | 保留 skill 绑定记录但不注入提示（对照用）；没有任何角色绑定 skill 时报错 | 关 |
| `--out` | 运行目录 | `runs/<YYYYMMDD-HHMMSS>_<剧情>_<模型>/` |

运行目录的时间取 `TZ` 指定的本地时间，按名字排序即按时间排序；同一路径已有结果时程序拒绝覆盖。换角色、换剧情、新增文件的方法见 [04-authoring.md](04-authoring.md)。

## 3. 接服务器模型

当前支持提供 `/chat/completions` 的兼容接口，默认用**结构化解码**：每次阶段调用把输出 schema 作为 `response_format: json_schema` 发给服务端（vLLM、OpenAI 支持），服务端不支持时设 `ROLE_SIM_STRUCTURED=0` 退回 JSON object。解码用的 schema 比提示里给模型看的更严：证据只能取本次调用看得到的记录 ID，S2 受众只能取在场其他人的 ID，每个决策过程只能取它自己的选项，每个提议只能落在状态布局里存在的构念 / 字段上并取该字段的值类型，键按层级写成定长列表（程序再拼回 `a/b`）。提示里只放精简版（约 5k 字符）。每次行动的请求：每个记录组一次事件建模（S1），对上次计划有回应时一次长期演化（S3），一次行动计划（S2，被拒时两次），一次表达；两人 8 次发言约 37 次请求（按 mock 运行计数），三人时每次行动要处理的记录组更多。档案里有自然语言原文时，入场时另有一次档案调用。思考模式保持开启：服务端返回的推理内容只写进 `trace/<互动>/<角色>/model_calls.jsonl` 的 `reasoning`，不进入任何提示。每次调用的输出先按 schema 和登记表检查，不合格整个阶段失败；失败时模型的原始回复也写进轨迹。提议引用了本阶段没判断过的决策过程不算输出不合格，而是由状态存储拒绝这条变化（`unjudged_mechanism`，连同依赖组）。不支持此协议的提供方需要另写适配器。

```bash
export ROLE_SIM_BASE_URL='http://127.0.0.1:8000/v1'
export ROLE_SIM_MODEL='your-served-model-name'
# 仅在需要鉴权时，通过服务器环境安全注入 ROLE_SIM_API_KEY
python3.11 -m rolesim run --backend chat --roles role1 role2 --scene test1 > /tmp/run.log 2>&1 &   # 目录名会先打印到日志
```

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `ROLE_SIM_BASE_URL` | 接口地址；远程必须 HTTPS，本机回环地址允许 HTTP 且不走代理 | 无 |
| `ROLE_SIM_MODEL` | 服务端模型名，必须显式设置 | 无 |
| `ROLE_SIM_API_KEY` | 需要鉴权时才设 | 空 |
| `ROLE_SIM_MAX_TOKENS` | 单次最大输出（含推理） | 8192 |
| `ROLE_SIM_TIMEOUT` | 单次请求超时（秒） | 600 |
| `ROLE_SIM_STRUCTURED` | `1` 结构化解码（json_schema），`0` 只要求 JSON object | 1 |

本地 vLLM 的启动方式（2026-11-09 用 vLLM 0.17.1 + Qwen3-8B 验证）：

```bash
vllm serve <Qwen3-8B 路径> --served-model-name Qwen3-8B --host 127.0.0.1 --port 8000 \
  --max-model-len 32768 --reasoning-parser qwen3 \
  --structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true, "disable_fallback": true}'
```

`disable_any_whitespace` 防止模型在 JSON 里无限输出空白直到撞上长度上限；`disable_fallback` 让不支持的 schema 报错而不是静默不约束。已知限制：schema 里出现 `pattern` 时 xgrammar 会整体不再约束（所以阶段 schema 不用正则）；Qwen3 有时在 JSON 结束后接着输出 `<tool_response>` 和第二份副本，结构化解码下程序只取第一个 JSON 值，丢掉的部分计入 manifest 的 `trailing_dropped`。conda 环境里 vLLM 若报 `CXXABI_1.3.15 not found`，启动前设 `LD_LIBRARY_PATH=<环境>/lib`。

`.env.example` 只提供变量说明，程序不会自动读取 `.env`。请求失败或输出不合法即停止并记录失败，**不会静默切回 mock**。

## 4. 跑完看什么

运行结束（成功或失败）都会自动生成可读版 `transcript.md`；成功的运行还会自动审计。

| 文件 | 内容 | 用途 |
|---|---|---|
| `transcript.md` | 概况、各角色（画像计数、待审核项、开场设定、skill）、全部互动记录、各角色每个阶段的产物与写入 / 被拒的变化 | 人工阅读；含各方的私有设定，不能放进评审包 |
| `manifest.json` | 开始 / 结束时间、座位与角色、环境、场景哈希、登记表来源哈希、代码版本、后端与模型、模式、skill 开关、调用数、完成 / 失败状态、自动审计结论 | 复核本次运行的配置与证据范围 |
| `records.jsonl` | 运行器视角的全部记录（开场、环境、各角色的思考 / 发言 / 动作及接收者） | 读对话 |
| `calls.jsonl` | 每次 initialize / act / 环境调用的完整输入与输出 | 系统侧复现 |
| `scene.json` | 剧情文件原样（含角色不可见的 `world_private`） | 评审包对照；不能提供给角色或行为裁判 |
| `audit_report.json/.md`、`roles/<id>/audit_report.*` | 自动审计汇总与各角色的审计 | 一眼看是否通过 |
| `roles/<id>/profile.json` | 编译后的稳定画像、待审核项、入场取值来源 | 检查档案编译结果 |
| `roles/<id>/setup.json` | 开场设定与绑定的 skill | 复核角色拿到的设定 |
| `roles/<id>/final_state.json` | 最终状态（按构念 ID） | 检查状态 |
| `trace/<互动>/<id>/phases.jsonl` | 每次提交：证据集、判断、产物、写入与被拒的变化、软异常 | 开发与机制检查，不能混入行为表现评分 |
| `trace/<互动>/<id>/model_calls.jsonl` | 每次模型调用的视图、原始输出、推理 | 查某句话为什么这么说 |

出问题时先看哪里见 [06-runtime-flow.md](06-runtime-flow.md) 最后一节。

## 5. 运行之后的命令

```bash
python3.11 -m rolesim audit runs/<运行目录>                 # 重新审计（通过 0，未通过 2）
python3.11 -m rolesim readable runs/<运行目录>              # 重新生成 transcript.md
python3.11 -m rolesim package runs/a runs/b --out outputs/review --seed 1   # 导出盲化评审包
python3.11 -m rolesim catalog                               # 重建两份心理学库的章节索引
python3.11 -m rolesim registry                              # 从心理学组 v1 文件重建 data/registry.json（构念、决策过程、算子规则）
python3.11 -m rolesim profile role1 [--export profiles/role1.json]   # 编译档案并列出待审核项（见 04-authoring.md 第 6 节）
PYTHONPATH=. python3.11 scripts/bootstrap_examples.py       # 重建 roles/ 与 scenes/ 示例
```

`audit`、`package`、`readable` 只认分阶段管线的运行目录（`manifest.pipeline` 为 `stages`）；`package` 拒绝失败的运行。评审包里每个运行的每个角色是一个盲化单元；方法标签（模式、后端、模型、代码版本、是否启用 skill、画像快照）只在 `blind_key.json`。评测协议尚未冻结，见 [09-evaluation.md](09-evaluation.md)。

## 6. 测试与 CI

- `tests/` 下按模块分文件：`test_agent_api`、`test_interfaces`、`test_registry`、`test_store`、`test_model`、`test_stages`、`test_runner`、`test_profile`、`test_ports`（开场设定、skill、模式、路由）、`test_offline`（审计、评审包、可读版）、`test_scene`、`test_roles`、`test_persona`、`test_briefing`、`test_skills`、`test_memory`、`test_transport`；共用夹具在 `tests/fixtures.py`。
- `test_transport` 会在本机起一个临时回环 HTTP 服务器，验证请求格式；沙箱不允许监听时需要在有权限的环境运行，不能跳过后宣称全通过。
- `.github/workflows/ci.yml`（`push` / `pull_request`）：跑全部测试，校验三个角色，用 mock 跑默认、换座位、无 skill、只给画像、三角色加环境 agent 五种运行并分别审计。执行结果以远端 Actions 为准。
- 按 `AGENTS.md` 的要求，改了运行时或合同后要跑全部测试，并如实报告环境限制。

## 7. 不进入 Git 的东西

`runs/`、`outputs/`、`.env*`（除 `.env.example`）、密钥与凭据、`.claude/settings.local.json` 不进入 Git。版本控制保存源码、来源文档、模板和实验配置；大规模实验结果单独管理。
