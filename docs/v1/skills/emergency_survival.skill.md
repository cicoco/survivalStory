---
skill_id: "emergency_survival"
name: "Emergency Survival"
short_desc: "濒死状态下优先保命决策。"
version: "1.0.0"
status: "active"
cost_tag: "high"
enabled: true
priority: 90
match_conditions:
  emergency:
    any:
      - "self_state.water <= 0.20"
      - "self_state.food <= 0.20"
      - "self_state.exposure >= 0.85"
fallback: "RuleBot"
output_schema_ref: "schemas/choose_action_v1.json"
discovery_fields:
  - "self_state.water"
  - "self_state.food"
  - "self_state.exposure"
  - "action_mask"
intent_fields:
  - "self_state"
  - "self_position"
  - "inventory_vector"
  - "current_building_memory"
  - "local_map_current_3x3"
  - "action_mask"
timeouts:
  discovery_timeout_ms: 500
  intent_timeout_ms: 900
---

# Goal
在紧急状态下最大化当前回合存活概率。

# Decision Policy
1. 优先恢复关键状态（water/food）。
2. 优先选择确定性保命动作。
3. 避免高波动与高风险决策。

# Guardrails
- 必须遵守 `action_mask`
- 输出必须通过 `choose_action_v1.json`
- 非法输出直接回退 `RuleBot`

