# 一次运行是怎么跑起来的

本文跟着一条命令走完一次运行：读了哪些文件、调用了哪些函数、参数怎么传、写出了什么。各接口的签名与保证见 [plan/10](../plan/10-interfaces.md)，模块与接口的对应见 [08-modules.md](08-modules.md)，整体架构见 [07-architecture.md](07-architecture.md)。

```bash
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3 [--backend mock|chat] [--mode …] [--no-skills]
```

## 1. 一张图

```
__main__._run
  roles.load_role × N（角色文件：persona 2.0 + skills + 开场设定，逐项校验）、roles.load_scene（scene 3.0）
  loop.run_scene
    episode(roles, scene)          入座检查 fit_cast；角色 ID = 角色文件 ID；背景 → trigger_facts；环境 agent 规则 → 导演指令
    StagedRoleAgent（agent/staged.py）   IRoleAgent 的实现：一个角色在一场互动里一个 Session
    LocalRunner.run()              本地参考运行器（环境组运行器的替身），环境用 EchoEnvironment
      initialize × 每个角色         bootstrap：场景、背景、人物概况、关系句、mechanism.mode
      每轮：
        导演指令                   第 1 轮是开场；之后每个大轮开头由 WorldAgent 规则产生（报时、世界事件及其可见范围）
          EchoEnvironment.act      指令原样作为环境 action → split → 按接收者写进各角色的观察记录
        行动者 act                 StagedRoleAgent.act(interaction_id, character_id, {observations, present_characters})
          → validate_action_result → split → 按接收者写进各角色的观察记录
  结束：写 records / calls / 每个角色的 profile、setup、final_state → 自动审计 → transcript.md
```

## 2. 一次 act 内部

```
StagedRoleAgent.act
  第一次 act：ProfileRepository.load（profiles/<id>.json，没有则转换角色文件 → ProfileCompiler → 稳定画像 + 待审核项）
             + enter（档案入场状态 → 开场设定的目标与前情 → bootstrap 的目标与前史 → 角色状态说明 §8.2 的默认值）
             → TurnRunner.begin（带上模式、绑定的 skill、开场设定）
  TurnRunner.run（只交未吸收的记录；别人的思考丢弃并告警）
    每个记录组（同一 action_id）一个事件阶段：
      S1 = ModelStage("S1").run  ── EvidenceGate → 视图（只含自知层）→ IModel → parse_output
      ConstructStore.commit       ── 算子起草 → 幅度档 → 验证 → 按依赖组原子提交
      上次计划的受众对本角色的第一次回应（每个计划一次）：S3.run → commit
    S2.run → commit（计划自己的目标 / 承诺 / 策略被拒 → 带着原因重做一次）
    ModelExpresser.realize       ── 按需可见的表达调用 → {thought（只给自己）, speech, action, order}
  每次提交写 trace/<interaction_id>/<character_id>/phases.jsonl，每次模型调用写 model_calls.jsonl
```

每次行动的模型调用：每个记录组一次 S1，有反馈时一次 S3，一次 S2（被拒时两次），一次表达。`--backend mock` 用 `MockModel`（离线夹具，内容不是心理学证据），`--backend chat` 用 `ChatModel`（`ROLE_SIM_BASE_URL`、`ROLE_SIM_MODEL`、`ROLE_SIM_API_KEY`、`ROLE_SIM_MAX_TOKENS`、`ROLE_SIM_TIMEOUT`），失败直接报错，不回退 mock。

## 3. 各阶段看到什么

| 调用 | 看到 | 看不到 |
|---|---|---|
| S1 事件建模 | 自知层画像（能自知的模块，有条件化语句时用语句；不能自知的只以语句出现）、入场时被告知的情况与开场设定（含不打算说的事）、状态（`profile_only` 时没有）、取回的记忆、可见记录、本阶段可调用的决策过程（`profile_state_mechanisms` 时才有）与可写构念 | 别人的思考、没发给自己的记录、场景私有数据、别人的任何状态 |
| S2 行动计划 | 同上 + 上次的 S1 产物和计划、被拒的变化及原因、人格 skill（全部的名称与说明；被 S1 的线索或开场设定激活的至多 3 个附决策要点和不会做的事） | 同上 |
| S3 长期演化 | 同 S1 + 上次的计划 | 同上 |
| 表达 | 身份、核心目标、表达基线、不能自知模块的条件化语句、感受（情绪标签 + 低 / 中 / 高，`profile_only` 时没有）、最近 12 条可见记录、计划里的主行动 / 表达目标 / 受众 / 方式 / 承诺、开场设定里可以说出的部分、被激活 skill 的表达要点 | 不打算说的事、状态数值、构念与过程 ID、其他候选、假设 |

## 4. 状态怎么更新

阶段只**提议**（`ChangeProposal`：构念、实例键、字段、目标值、事件等级、证据、依据的决策过程）。`ConstructStore.commit`：

1. 检查 `ctx.state` 是当前快照；同一阶段重试返回第一次的结果。
2. 每条提议交给该构念的主写入算子（衰减要写明 `decay_or_recovery`），补上变化前的值、证据、依据的过程、幅度档、依赖组。
3. 验证：权限与阶段（来自登记表）、状态标记、取值形式与范围、证据可见、变化前的值、伤害事实与记忆只增不改、幅度档（按事件等级，人格敏感度最多移一档，只有听说 / 推断的证据封顶）、心理理论阶数、策略取值是登记的选项……
4. 有硬错的依赖组整组拒绝，其余照常提交；软异常照写并标记。

状态布局见 `rolesim/state/slots.py`；所有阈值与区间是心理学组的初始值，未标定。

## 5. 运行目录

| 文件 | 内容 |
|---|---|
| `manifest.json` | 配置、模式、skill 开关、后端、模型、版本、登记表来源哈希、各角色的最终状态 ID 与画像快照、自动审计结论 |
| `records.jsonl` | 运行器视角的全部记录 |
| `calls.jsonl` | 每次 initialize / act / 环境调用的完整输入与输出 |
| `scene.json` | 剧情文件原样（含角色不可见的 `world_private`，只供评审包对照） |
| `roles/<id>/profile.json` | 编译后的稳定画像、待审核项、入场取值来源 |
| `roles/<id>/setup.json` | 开场设定与绑定的 skill |
| `roles/<id>/final_state.json` | 最终状态（按构念 ID） |
| `trace/<互动>/<id>/phases.jsonl` | 每次提交：证据集、判断、产物、写入与被拒的变化、软异常 |
| `trace/<互动>/<id>/model_calls.jsonl` | 每次模型调用的视图、原始输出、推理 |
| `audit_report.{json,md}`、`roles/<id>/audit_report.*` | 自动审计 |
| `transcript.md` | 可读版：角色、全部记录、各角色的内部过程 |

## 6. 运行之后的命令

| 命令 | 入口函数 | 用途 |
|---|---|---|
| `rolesim audit RUN` | `audit.write_report` | 重新审计（通过 0，未通过 2） |
| `rolesim readable RUN…` | `readable.write_readable` | 重新生成 `transcript.md` |
| `rolesim package RUN… --out DIR --seed N` | `package.export_package` | 导出盲化评审包（方法标签只在 `blind_key.json`） |
| `rolesim profile ROLE [--export F]` | `repository.ProfileRepository` | 编译档案并列出待审核项 |
| `rolesim list [--roles … --scene …]`、`validate ROLE`、`skills [ROLE]` | `roles.fit_cast`、`load_role`、`load_library` | 列出 / 检查搭配 / 校验角色与 skill |

## 7. 排查时先看哪里

- 某句话为什么这么说：`transcript.md` 里该角色的内部过程，再看 `model_calls.jsonl` 里那次 S2 与表达调用的输入和原始输出。
- 状态为什么没变：`phases.jsonl` 里该阶段的 `commit.rejected`，每条带硬错代码（如 `over_band`、`stage_not_allowed`）。
- 审计没过：`audit_report.md`，每项失败带详情。
- 运行失败：`manifest.json` 的 `error_type`、`error`；阶段失败时 `model_calls.jsonl` 里留着那次不合规的输出。
