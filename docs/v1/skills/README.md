# Skills V1（Markdown 格式）

本目录采用固定格式：`*.skill.md`

- 头部：YAML Front Matter（机器读取）
- 正文：自然语言策略（给 LLM 使用）

先看模板文件：

- `TEMPLATE.skill.md`
- `技能设计与覆盖评估.md`

建议加载顺序：

1. 读取 `index.yaml`
2. 按 `enabled=true` 过滤
3. 按 `priority` 排序
4. 根据 `match_conditions` 路由到具体 `*.skill.md`
5. 解析 Front Matter + 正文段落（Goal/Decision Policy/Guardrails）

当前模板：

- `day_explore_collect.skill.md`
- `night_survival_control.skill.md`
- `loot_window_decision.skill.md`
- `emergency_survival.skill.md`
