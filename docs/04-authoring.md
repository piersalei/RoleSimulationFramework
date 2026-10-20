# 角色与剧情怎么写

本文是写给作者的：怎么新增一个角色文件、一个剧情文件，怎么换着搭配运行。输入是两类文件，互相独立：

- `roles/<id>.json`：一个角色一个文件（档案 + skills + 开场设定）；
- `scenes/<id>.json`：一个剧情一个文件（共享背景 + 在场的 agent：角色座位与可选的环境 agent + 动作范围）。

心理学组要交付什么内容、按什么格式，见 [05-psychology-interface.md](05-psychology-interface.md)。命令怎么敲见 [03-usage.md](03-usage.md)。术语见 [02-concepts.md](02-concepts.md)。

## 1. 人物制作

复制 `roles/role1.json`，改名为新编号（如 `roles/role3.json`，文件名必须等于 `persona.id`），在其中的 `persona`（persona schema 2.0）里修改人物身份、公开描述、自我理解、相关分面、动机基线、价值权重和核心目标。`stable.module_status` 必须列出全部登记模块：有值的写 `active` 并在 `provenance` 里给来源；没有资料的写 `unknown` 且值为 `null`，不要写成 0 或中性值；本版没有建模的写 `not_modeled` 并说明原因。运行 `validate` 后再 `compile` 检查视图。模板内的数值是虚构示例，应逐项审定，不是默认人群常模。示例由 `PYTHONPATH=. python3.11 scripts/bootstrap_examples.py` 重建。

字段规则：

- `values.schwartz_priorities.weights`：只列已设定的施瓦兹价值，权重非负且和为 1；未列出的价值是 unknown，不是 0。
- `core_goals`：1–3 个，每个有 `priority`、`stability`、`source`，至多 2 个支持子目标（带 id 与上位链接）。
- `speech_style`：`baseline.directness` 加 `situational_rules`（`when` / `style` 列表），后者会进入表达阶段的风格约束。
- 71 维、发展位置、荣格功能、道德推理、文化取向：没有适配器，只能是 unknown / not_modeled / disabled。
- 旧的 1.0 档案会被拒绝；请按 2.0 重建，不做静默转换。
- P1 的 30 分面仍要求全部赋值；示例未重点设定项为显式合成中性 `0`，与 71 维的“定义缺失、未赋值”不同。分面级目前还不能写 unknown。

## 2. 角色文件：一个角色一个文件

角色放在 `roles/`，用编号命名（`role1`、`role2`……），具体是谁打开文件看。文件名必须等于 `persona.id`。

```json
{"format": "role-1.0",
 "persona": { "...": "persona schema 2.0，原样嵌入，与心理学组约定的格式一致" },
 "skills": [{"name": "verify-before-commit", "priority": 0.95, "note": "核查偏好高、审慎分面高"}],
 "setup": {"goal": "…", "disclose": [], "conceal": [], "recap": []}}
```

- `persona`：上面的人物档案，校验规则不变。
- `skills`：这个人物会用哪些 skill、优先级（0–1）、为什么（`note`）；可以为空列表。skill 本身在 `library/skills/<name>/SKILL.md`，格式见 [10-persona-skills-survey.md](10-persona-skills-survey.md) 第 4 节。skill 是工程侧拟定的，`note` 里写明未经心理学组审定。
- `setup`：角色进入剧情时的开场设定，见下节；可以为 `null`。

用 `python3.11 -m rolesim validate role1` 校验整个角色文件，`python3.11 -m rolesim skills role1` 只看 skill。新写一个 skill：在 `library/skills/` 下建同名目录和 `SKILL.md`，`rolesim-source` 必须引用 `data/catalog.json` 里存在的章节，正文必须有"决策要点 / 表达要点 / 不会做的事 / 边界"四节（纯表达类可省略决策要点）。

## 3. 开场设定（setup）

开场设定是角色自己知道和想要的东西，所以跟着角色走，不写在剧情里。

| 字段 | 含义 | 去向 |
|---|---|---|
| `goal` | 角色在这场互动里的目标 | 决策提示；表达提示 |
| `disclose` | 想让对方知道的信息（`id`、`text`、`markers`） | 决策提示（`want_known`）；表达提示（`may_share`） |
| `conceal` | 角色知道、但不想让对方知道的信息；`strategy` 只能是 `omission`（遗漏）或 `strategic_ambiguity`（战略模糊），不支持直接说谎 | **只进决策提示**（`keep_private`），表达提示拿不到原文 |
| `recap` | 前情提要，id 以 `recap:` 开头 | 作为记忆的第一批条目，可被决策引用为证据 |

`markers` 是自动审计用来检测"说出来了"的关键词（至少两个字）。前情提要里不能出现隐瞒条目的关键词，因为前情会进入表达提示。审计项 `concealed_info_withheld` 检查隐瞒内容没有进入表达提示、没有被说出；`disclose` 条目被说出只统计不判错。关键词检测抓不到换了说法的泄露，这部分仍需人工评分。

开场设定是写给角色本人看的："你""你方"都指角色自己这一方。内容要和角色坐的座位对得上：role1 的设定是项目组一方的，role2 是合作方一方的，role3 是项目负责人（只在三方剧情 test3 里入座）。程序只能检查 id 是否与环境事件（`open`、`speech:`、`world:`）冲突，内容是否相符要作者自己保证。

## 4. 剧情文件：背景，不指导角色

场景放在 `scenes/`，同样用编号命名（`test1`、`test2`……），文件名必须等于场景 `id`。场景只给背景和在场者，**没有剧本，也不给角色下指令**；之后说什么、怎么发展由角色自己决定。正式格式以后按场景组的背景文件接口替换；目前用 scene schema 3.0 作为替身：

| 字段 | 含义 |
|---|---|
| `background` | 在场各方都知道的背景，作为合同里的 `scene_context` 传给每个角色；写事实，不写"你要……" |
| `agents` | 在场者列表。`kind: "role"` 是角色座位（2–6 个），各有 `id` 和 `title`（如"项目组数据负责人"）；角色按座位顺序入座、按这个顺序轮流发言，彼此只知道别人的座位名。`kind: "environment"` 是环境 agent（至多 1 个），另带 `world` 和 `rules`，见下 |
| `max_turns` | 最多发言几次（所有角色轮流，从角色数到 40）；任何一方选 `close` 也会结束 |
| `allowed_actions` | 可选的交流动作：索要证据、提问澄清、提出方案、说明边界、暂缓、接受、结束 |
| `world_private` | 世界私有数据，永不进入任何角色的模型调用 |

旧的 1.x 场景（带 `events`、`partner` 等脚本字段）和 2.0 场景（带 `seats`）都会被拒绝，不做静默转换。

环境做这几件事：开场时发一条旁白（有环境 agent 时附上世界状态）；把一个角色说的话原样转给所有在场角色；到轮数上限或有人结束时收尾。它不替任何一方说话，也不编造"对方同意了""记录已到"这类结果。

**环境 agent**（可选）不是人，没有心理状态，只按写好的规则改变世界并用旁白告诉角色：

| 字段 | 含义 |
|---|---|
| `world` | 初始世界状态，键只能是 `time`（`HH:MM`）、`weather`、`place`、`noise`，值是文字 |
| `rules.minutes_per_round` | 每轮（所有角色各说一句）过去多少分钟（0–240），需要 `world.time` |
| `rules.announce_time` | 每轮开头是否报时 |
| `rules.events` | 定时事件：`round`（≥ 2）、`text`（旁白），可选 `set`（改已声明的世界变量）、`audience`（座位 id 列表，只让这些角色知道）、`cues`（环境标注的线索，角色直接采用，不再调用模型评价） |

例子见 `scenes/test3.json`：三方在会议室商量，每轮过 10 分钟，第 2 轮下雨，第 3 轮只有项目组数据负责人收到同事的消息，第 4 轮前台提醒会议室快到时间（带压力线索）。环境 agent 的事件 id 是 `world:{轮}:time` 和 `world:{轮}:{序号}`；世界变化记在运行目录的 `world.jsonl`。

## 5. 换角色、换剧情

```bash
python3.11 -m rolesim list                                          # 列出全部角色和剧情
python3.11 -m rolesim list --roles role1 role2 --scene test2        # 查一组入座
python3.11 -m rolesim run --roles role1 role2 --scene test2
python3.11 -m rolesim run --roles role2 role1 --scene test1   # 换座位
python3.11 -m rolesim run --roles role1 role2 role3 --scene test3   # 三个角色 + 环境 agent
```

`--roles` 按座位顺序为每个角色座位写一个编号或文件路径，`--scene` 写一个。新增角色或剧情只需要放一个新文件，不用改代码。入座检查（`fit_cast`）里，错误会阻止运行（例如座位数不对、同一角色坐两个座位、机制先验与启用的机制卡不一致、开场设定用了环境保留的 id）；提示只报告，例如"这个 skill 在该剧情里永远不会被激活"。运行记录 `manifest.json` 的 `origin` 字段记下用了哪些角色文件和剧情文件及其哈希。

给环境组接入用的是 `rolesim.roles.seat(roles, scene, backend)`：每个角色座位返回一个配置好的 Agent（剧情的可选动作、自己和其他在场者的座位名、角色的 skill 和开场设定），以及首轮 `step` 要带的 `role_profile`。之后环境按合同 v0 调 `step` / `perceive`，接口不变。
