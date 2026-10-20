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
python3.11 -m rolesim compile role1 --out outputs/role1-views.json   # 导出四种视图
python3.11 -m rolesim skills role1                   # 校验 skill 库及该角色的 skill
python3.11 -m rolesim run --roles role1 role2 --scene test1          # mock 后端跑一场
python3.11 -m rolesim run --roles role1 role2 --scene test2
python3.11 -m rolesim run --roles role1 role2 --scene test1 --no-skills   # 不注入 skill 的对照
python3.11 -m rolesim run --roles role1 role2 --scene test1 --mode profile_state   # 换消融档位
python3.11 -m rolesim run --roles role1 role2 --scene test1 --appraise-state off   # 感知评价不看状态的对照
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3   # 三个角色 + 环境 agent
```

`mock` 按关键词标线索、按候选顺序选动作、用固定台词表达，**只能用来验证流程**（紧张为"高"时会在台词前加一句"我有点担心"，用来检查状态确实传到了表达）。角色输出不同不构成心理有效性证据。

`run` 的参数：

| 参数 | 含义 | 默认 |
|---|---|---|
| `--roles` | 每个角色座位一个角色编号或文件路径，按座位顺序（2–6 个） | `role1 role2` |
| `--scene` | 剧情编号或文件路径 | `test1` |
| `--backend` | `mock` 或 `chat` | `mock` |
| `--mode` | `profile_only` / `profile_state` / `profile_state_mechanisms` | `profile_state_mechanisms` |
| `--appraise-state` | `off` 时感知评价提示不含状态（对照用），记入 `manifest.appraise_state` | `on` |
| `--no-skills` | 保留 skill 绑定记录但不注入提示（对照用） | 关 |
| `--out` | 运行目录 | `runs/<YYYYMMDD-HHMMSS>_<剧情>_<模型>/` |

运行目录的时间取 `TZ` 指定的本地时间，按名字排序即按时间排序；同一路径已有结果时程序拒绝覆盖。换角色、换剧情、新增文件的方法见 [04-authoring.md](04-authoring.md)。

## 3. 接服务器模型

当前支持提供 `/chat/completions`、JSON object 输出的兼容接口。每次发言三类请求：对收到的每句别人的话做感知评价、决策、表达；两人 8 次发言约 25 次请求，三人时每次发言要评价的话更多。思考模式保持开启：服务端返回的推理内容只写进运行记录（`audit.jsonl` 的 `reasoning`），不进入任何提示。不支持此协议的提供方需要另写适配器。

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

`.env.example` 只提供变量说明，程序不会自动读取 `.env`。请求失败或输出不合法即停止并记录失败，**不会静默切回 mock**。

## 4. 跑完看什么

运行结束（成功或失败）都会自动生成可读版 `transcript.md` 和自动审计。

| 文件 | 内容 | 用途 |
|---|---|---|
| `transcript.md` | 概况、背景与环境、各角色的设定、逐句对话（环境旁白、每句前的感受、选的动作和理由、紧张与对每个人的信任变化，折叠的思考过程）、结果汇总 | 人工阅读；含各方的私有设定，不能放进评审包 |
| `manifest.json` | 开始 / 结束时间，座位与角色、环境 agent、场景与机制哈希，模型与采样、模式、`appraise_state`、调用数、完成/失败状态；每个角色的画像哈希与 skill 信息 | 复核本次运行的配置与证据范围 |
| `public.jsonl` | 开场与环境旁白、各角色台词（含各自选的动作）；只给部分角色的旁白带 `audience` | 读对话 |
| `environment_events.json` | 环境产生的全部合同 Event | 系统侧复现 |
| `world.jsonl` | 环境 agent 每轮的世界状态与变化（有环境 agent 时） | 看环境做了什么 |
| `audit_report.json/.md` | 各角色的自动审计汇总 | 一眼看是否通过 |
| `roles/<id>/inputs.json` | 该角色的输入快照（画像、座位、开场设定、场景含世界私有数据、机制卡快照） | 系统侧复现，不能提供给另一角色或行为裁判 |
| `roles/<id>/views.json` | 系统真值、角色自知、他者可见、控制器行为约束 | 检查画像编译结果 |
| `roles/<id>/audit.jsonl` | 每轮感知评价、决策与表达的输入输出、推理内容、ProposedAction、两个 StateDelta、状态 | 开发与机制检查，不能混入行为表现评分 |
| `roles/<id>/state.json`、`states.jsonl` | 最新与逐次 AgentState，按 state_id 串链 | 检查状态延续、回放 |
| `roles/<id>/mechanism_log.jsonl`、`audit_report.*` | 机制轨迹；该角色的审计 | 复核 |

出问题时先看哪里见 [06-runtime-flow.md](06-runtime-flow.md) 最后一节。

## 5. 运行之后的命令

```bash
python3.11 -m rolesim audit runs/<运行目录>                 # 重新审计（通过 0，未通过 2）
python3.11 -m rolesim readable runs/<运行目录>              # 重新生成 transcript.md
python3.11 -m rolesim package runs/a runs/b --out outputs/review --seed 1   # 导出盲化评审包
python3.11 -m rolesim catalog                               # 重建两份心理学库的章节索引
PYTHONPATH=. python3.11 scripts/bootstrap_examples.py       # 重建 roles/ 与 scenes/ 示例
```

评审包里每个运行的每个角色是一个盲化单元；方法标签（模式、后端、模型、代码版本、是否启用 skill）只在 `blind_key.json`。评测协议尚未冻结，见 [09-evaluation.md](09-evaluation.md)。

## 6. 测试与 CI

- `tests/` 下按模块分文件：`test_state`、`test_stateview`、`test_persona`、`test_agent`、`test_multiagent`、`test_roles`、`test_skills`、`test_briefing`、`test_memory`、`test_audit`、`test_package`、`test_readable`、`test_pipeline`、`test_transport` 等；共用夹具在 `tests/fixtures.py`。
- `test_transport` 会在本机起一个临时回环 HTTP 服务器，验证请求格式；沙箱不允许监听时需要在有权限的环境运行，不能跳过后宣称全通过。
- `.github/workflows/ci.yml`（`push` / `pull_request`）：跑全部测试，校验三个角色，用 mock 跑默认、换座位、无 skill、三角色加环境 agent 四种运行并分别审计。执行结果以远端 Actions 为准。
- 按 `AGENTS.md` 的要求，改了运行时或合同后要跑全部测试，并如实报告环境限制。

## 7. 不进入 Git 的东西

`runs/`、`outputs/`、`.env*`（除 `.env.example`）、密钥与凭据、`.claude/settings.local.json` 不进入 Git。版本控制保存源码、来源文档、模板和实验配置；大规模实验结果单独管理。
