# PersonaForge 参考范围

核查日期：2026-09-30。参考项目：[fQwQf/PersonaForge](https://github.com/fQwQf/PersonaForge)。本地已读取版本为 `10363f65b9649ae258854568608da7add1d6cc98`，路径 `/Users/lei/projects/r/PersonaForge`。下面只描述该版本已经读取的代码；远端后续变化不能自动视为同一实现。

| 观察对象 | 本地代码实际做法 | 本项目采用或调整 |
|---|---|---|
| 人物结构 | 核心特质、语言风格与动态状态分层 | 保留分层思想，扩展来源与角色自知/他者可见投影 |
| 决策与表达 | 关键交互生成内心独白，再据风格生成回复 | 用结构化行动意图和一句审计摘要，再生成表达；不依赖逐步思维链 |
| 触发 | 读取首次交互、关键词和兴趣命中条件 | 首版使用可见事件的人工标注线索；自动检测待实验 |
| 人格提示 | 大五数值、阈值条件提示和防御机制进入生成提示 | 转成可审定的“触发—倾向—例外”语句，不称其为已验证的心理方程 |
| 状态更新 | `dynamic_state_manager.py` 包含关键词与固定增量规则 | 使用带事件来源、去重和更新记录的首版规则；参数仍是工程假设 |
| 评测输入 | 所查 `evaluate` 接口允许把内心独白并入人格评分文本 | 外显行为评分与系统内部审计分开，所有对照使用一致可见材料 |

可定位的固定版本来源：

- [dual_process_agent.py](https://github.com/fQwQf/PersonaForge/blob/10363f65b9649ae258854568608da7add1d6cc98/personaforge/dual_process_agent.py)
- [dynamic_state_manager.py](https://github.com/fQwQf/PersonaForge/blob/10363f65b9649ae258854568608da7add1d6cc98/personaforge/dynamic_state_manager.py)
- [personality_model.py](https://github.com/fQwQf/PersonaForge/blob/10363f65b9649ae258854568608da7add1d6cc98/personaforge/personality_model.py)
- [experiments/common/evaluation.py](https://github.com/fQwQf/PersonaForge/blob/10363f65b9649ae258854568608da7add1d6cc98/experiments/common/evaluation.py)

本项目是独立实现，没有复制上游代码、角色数据或模型权重，也未将其设置为运行依赖。原库的 Apache-2.0 许可与作者署名属于上游；未来若引入代码，应同时处理其 LICENSE/NOTICE 与版本记录。

当前没有在本项目复跑 PersonaForge 实验，不引用它的作者报告数值作为本地效果，也不把本项目的 `profile_only` 称为原论文的 Character-LLM、PersonaForge 或其他方法复现。

其最有用的启发是“人格信息在生成流程中的控制位置”。我们的额外问题是：角色究竟观察到了什么、机制为何在该事件被激活、行为产生了什么真实反馈、状态更新是否有证据。需要通过本项目实验回答，而不是从上游结果推断。
