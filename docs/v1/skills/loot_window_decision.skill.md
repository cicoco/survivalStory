---
skill_id: "loot_window_decision"
name: "Loot Window Decision"
short_desc: "战利品窗口下决定 GET 或 TOSS。"
version: "1.0.0"
status: "active"
cost_tag: "medium"
enabled: true
priority: 100
match_conditions:
  loot_window: true
fallback: "RuleBot"
output_schema_ref: "schemas/choose_action_v1.json"
discovery_fields:
  - "time_state"
  - "self_state"
  - "action_mask.can_get"
  - "action_mask.can_toss"
intent_fields:
  - "self_state"
  - "inventory_vector"
  - "current_building_memory"
  - "action_mask"
  - "loot_context"
timeouts:
  discovery_timeout_ms: 400
  intent_timeout_ms: 1000
---

# Goal
在战利品窗口做保守且有效的收益决策。

# Decision Policy
1. 若 GET 能显著提升生存资源，优先 GET。
2. 若收益不明确或风险高，选择 TOSS。
3. 输出必须满足窗口约束与件数上限。

# Guardrails
- 必须遵守 `action_mask`
- 输出必须通过 `choose_action_v1.json`
- 非法输出直接回退 `RuleBot`

