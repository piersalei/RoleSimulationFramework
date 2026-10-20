# 人格 skills 调研与路线选择

2026-10-20 调研。回答会议里的三个问题：GitHub 上的人格 skills 是怎么做的；角色驱动怎么把 skills 融进去；路线选 test time 还是 context engineering。实现见第 4 节和 [07-architecture.md](07-architecture.md)。

证据层级：本文对外部项目的描述来自其 README / SKILL.md（**作者自述**），没有复现任何一个项目的效果；第 4 节的设计是**工程假设**。

## 1. 先把"skill"说清楚

现在社区说的 skill 指 [Agent Skills 开放规范](https://agentskills.io/specification)（Anthropic 提出，Claude Code、Codex、Cursor 等 50+ 运行时兼容）：

- 一个 skill 是一个目录，至少有 `SKILL.md`：YAML 头（必填 `name`、`description`，可选 `license`、`compatibility`、`metadata` 字符串映射）+ Markdown 正文。
- **渐进加载**（progressive disclosure）是核心：启动时只把所有 skill 的 `name` + `description` 放进上下文（约 100 token/个）；判断相关时才加载正文（建议 < 5k token）；更细的 `references/`、`scripts/` 按需再读。
- `description` 同时写"做什么"和"什么时候用"，触发靠模型读描述做匹配。

这套机制解决的是"人格内容太多、一次塞不进提示"的问题，和我们"只在相关情境注入相关行为倾向"的需求同构。

## 2. GitHub 上的人格 skills：四类

| 类别 | 代表项目 | 做法 | 对我们的价值 |
|---|---|---|---|
| 人物蒸馏 | [女娲.skill](https://github.com/alchaincyf/nuwa-skill)（约 3.4 万星）、[同事.skill / dot-skill](https://github.com/titanwings/colleague-skill)、[前任.skill](https://github.com/therealXiaomanChu/ex-skill)、[forge-skill](https://github.com/YIKUAIBANZI/forge-skill)；索引见 [awesome-persona-skills](https://github.com/tmstack/awesome-persona-skills)、[awesome-human-distillation](https://github.com/mliu98/awesome-human-distillation) | 从聊天记录、著作、访谈里提炼一个人，产出一个可调用的 SKILL.md | **分层结构**和**诚实边界**可借鉴；数据来源与我们无关 |
| 人格框架 / 规范 | [OpenPersona](https://github.com/acnlabs/OpenPersona)、[PersonaNexus](https://github.com/PersonaNexus/personanexus)、[persona-spec](https://github.com/thiswind/persona-spec)、[Persona-Skill](https://github.com/Tomsawyerhu/Persona-Skill) | 声明式 persona 文件 → 编译成 SKILL.md / 系统提示；带校验、组合、版本 | **"档案编译成提示"**和**按场景切片加载**与我们一致 |
| 角色卡与创作 | [character-card-skills](https://github.com/foreverse-app/character-card-skills)、[character-sim](https://github.com/haowjy/creative-writing-skills/blob/main/skills/character-sim/SKILL.md)、[skill_everyone](https://github.com/MIMIFY/skill_everyone) | 面向陪伴 / 小说的角色扮演；SillyTavern 角色卡 + lorebook（关键词触发的世界设定） | **关键词触发注入**、**角色只按自己所知说话** |
| 人格测评 | [big-five-personality-skill](https://github.com/aqws6361/big-five-personality-skill)、arka-os 的 persona-build | 用 IPIP-50 等量表测人，或从语料给 OCEAN / DISC / MBTI 打分 | 基本不适用：我们的分数由心理学组给，不从语料估 |

### 2.1 值得借鉴的设计

**分层，而且决策层和表达层分开。**

- 女娲提取五层：怎么说话（表达 DNA）、怎么想（心智模型）、怎么判断（决策启发式）、什么不做（反模式）、知道局限（诚实边界）。
- 同事.skill 把人格写成五层："硬规则 → 身份 → 表达 → 决策 → 人际"，执行顺序是"人格决定态度 → 工作 skill 执行 → 用 ta 的口吻输出"。
- 学术上对应 TTM（[Test-Time-Matching, arXiv 2507.16799](https://arxiv.org/abs/2507.16799)）：把角色拆成人格、记忆、语言风格三部分，分三阶段生成。
- 这和我们已有的"决策 → 表达"两阶段一致。

**反模式与诚实边界是一等公民。** 女娲要求每个 skill 写明"绝对不会做什么"和"做不到什么"。我们的心理学组也要求机制卡带反例。

**按场景切片加载，不整包塞进提示。** Persona-Skill 把成品切成 `default_chat` / `decision_mode` / `conflict_mode` 等加载档位；角色卡的 lorebook 按关键词触发。这就是渐进加载在角色上的用法。

**纠正层与版本。** 同事.skill 有"纠正层"（"他不会这么做"写回）、每次更新存档可回滚；OpenPersona 有 `lineage.json`。这对应会议第 9 条"记忆更新人格"的工程形态：变化先成为**候选**，审核后出新版本，旧版本保留。

**only-what-they-know。** character-sim 要求"从角色此刻知道的东西出发说话，不从全知视角"，和我们的可见性隔离一致。

### 2.2 不能照搬的地方

- **它们的人格来自语料蒸馏，我们的人格来自心理学构念。** 蒸馏类 skill 的价值主张是"像某个具体的人"，靠聊天记录；我们要的是"由可审定的构念驱动、行为可追溯"。所以我们借结构，不借数据流程。
- **它们几乎都是单 skill 整包扮演**：一个人物一个 SKILL.md，进入后一直在角色里。我们需要的是**一个角色挂多个行为 skill，按事件激活**，并且每次激活都要能审计。
- **没有状态和证据约束。** 社区 skill 的"情绪""关系"写在提示里，由模型自由发挥；我们的状态更新必须有可见证据和写权限。skill 只能影响**选什么动作、怎么说**，不能写状态。
- **效果证据弱。** 除 TTM 有人工评测外，其余都是作者自述或社区口碑，不能当作有效性证据。

## 3. 路线：test time 还是 context engineering

| 路线 | 含义 | 代表 | 代价 | 适合我们吗 |
|---|---|---|---|---|
| 训练（SFT / 偏好对齐） | 把角色写进权重 | Character-LLM、RoleLLM、CharacterGLM、PCL（[arXiv 2503.17662](https://arxiv.org/abs/2503.17662)） | 每个角色要数据、要重训；人格一改就得重训；难审计 | 否（现阶段） |
| 激活引导 | 推理时改隐层激活 | SAE 人格引导（[arXiv 2602.19157](https://arxiv.org/abs/2602.19157)） | 需要开源权重和人格方向标定 | 以后可作对照实验 |
| test time（多阶段 / 多采样） | 推理时多花算力：分阶段生成、多候选再选 | TTM 三阶段；best-of-N + 评判 | 调用次数成倍增加 | **部分采用** |
| context engineering | 不动权重，靠组织输入：结构化档案、按需注入、记忆检索 | 社区所有 persona skills；本仓库现状 | 受上下文长度和模型遵循度限制 | **主路线** |

**结论：以 context engineering 为主线，在其上做轻量的 test time，暂不训练。**

理由：

1. **可审计是硬要求。** 心理学组要求每个行为能追溯到构念、证据和规则。写进权重的东西无法逐条追溯，也无法做"关掉某个模块看差异"的消融。context engineering 下，每一次注入了什么都落盘在 `audit.jsonl` 里。
2. **人格和规则还在变。** P1/P2/P3 三方案还要比较，规则卡数值还没交付。训练路线每改一次都要重训，context 路线改档案即可。
3. **我们已经在做 test time 的一种。** "决策 → 表达"两阶段本身就是 TTM 式的分阶段生成。下一步可加的 test time 手段：决策阶段多采样 + 一致性 / 证据校验后选择（N 次调用换稳定性），这不改变数据结构。
4. **训练留作对照。** 等评测协议冻结、有了标注数据，再考虑 SFT 或激活引导作为对照组；训练出的模型照样走同一套 `step` / `perceive` 和审计。

## 4. 本仓库的落地方式

对应会议任务"基础人格库从 skills 角度构建"和"角色驱动怎么把 skills 融进去"。

### 4.1 skill 库

- 位置：`library/skills/<name>/SKILL.md`，**格式兼容 Agent Skills 规范**：`name`、`description` 必填；我们自己的字段放在 `metadata` 里（规范允许的字符串映射），所以这些文件也能被 Claude Code / Cursor 直接识别。
- `metadata` 字段：`rolesim-kind`（decision / expression / disclosure）、`rolesim-cues`（触发线索）、`rolesim-actions`（支持的动作）、`rolesim-trigger`（简报触发，如 `conceal`）、`rolesim-source`（指向 `data/catalog.json` 的章节）、`rolesim-evidence`（证据层级，目前都是 `engineering_hypothesis`）。
- 正文固定四节：`## 决策要点`、`## 表达要点`、`## 不会做的事`（反模式）、`## 边界`（诚实边界）。运行时按节拆开：决策要点和反模式只进决策提示，表达要点只进表达提示。

### 4.2 人物与 skill 的绑定

- 人物档案（schema 2.0）的格式**不改**，这是和心理学组约定好的合同。
- 每个角色一个文件 `roles/<id>.json`：档案原样嵌在 `persona` 里，`skills` 列出这个人物会用的 skill 和优先级。剧情单独一个文件 `scenes/<id>.json`。
- 同一个 skill 可以被不同人物绑定，优先级不同；人物之间的差别来自绑定组合与已有的人格画像，不来自 skill 本身。

### 4.3 运行时三级加载

1. **目录级**：决策提示里给出该人物全部已绑定 skill 的 `name` + `description`（`skill_catalog`）。
2. **激活级**：当前事件的线索强度达到阈值、且 skill 支持的动作当前可行时激活；简报类 skill（隐瞒 / 透露）在简报里有对应条目时激活。按"优先级 × 线索强度"取前 3 个，正文进入 `active_skills`。
3. **表达级**：表达阶段只拿到已激活 skill 的"表达要点"，拿不到决策要点、反模式和 skill 名称。

skill 与机制卡的分工：机制卡属于心理学组的动力机制线（为什么倾向某类动作、状态怎么变），skill 属于人格线（这个人习惯用什么策略、怎么说）。**skill 不写状态**，只影响决策提示和表达提示。`--no-skills` 可以关掉 skill 做对照，状态在两种情况下照常计算。

### 4.4 还没做

- skill 内容是工程侧拟写的通用策略（9 个），需要心理学组审定措辞和适用条件。
- 没有从语料蒸馏 skill 的流程；也没有"纠正层"和 skill 版本迁移。
- 激活靠线索阈值，不靠模型读 `description` 自主选择；后者更接近社区做法，但会让激活不可复现，等接上真实模型后可作为一个对照档位。
