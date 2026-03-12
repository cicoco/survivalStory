---
skill_id: "day_explore_collect"
name: "Day Explore Collect"
short_desc: "白天优先探索与补给，避免高风险移动。"
version: "1.0.0"
status: "active"
cost_tag: "low"
enabled: true
priority: 40
match_conditions:
  phase: "DAY"
  loot_window: false
  emergency: false
fallback: "RuleBot"
output_schema_ref: "schemas/choose_action_v1.json"
discovery_fields:
  - "time_state.phase_onehot"
  - "self_state.water"
  - "self_state.food"
  - "self_state.exposure"
  - "action_mask"
  - "inventory_vector"
intent_fields:
  - "self_state"
  - "self_position"
  - "time_state"
  - "inventory_vector"
  - "current_building_memory"
  - "local_map_current_3x3"
  - "action_mask"
timeouts:
  discovery_timeout_ms: 600
  intent_timeout_ms: 1200
---

# Goal
白天优先提升生存稳定性与资源储备。

# Decision Policy
1. 当 water/food 偏低时优先 USE 或可达补给动作。
2. 可安全探索时优先 EXPLORE，再考虑 TAKE。
3. 避免高风险动作，无法确定时选择 REST。

# Guardrails
- 必须遵守 `action_mask`
- 输出必须通过 `choose_action_v1.json`
- 非法输出直接回退 `RuleBot`

