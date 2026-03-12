---
skill_id: "night_survival_control"
name: "Night Survival Control"
short_desc: "夜晚优先保命与风险控制。"
version: "1.0.0"
status: "active"
cost_tag: "low"
enabled: true
priority: 60
match_conditions:
  phase: "NIGHT"
  loot_window: false
  emergency: false
fallback: "RuleBot"
output_schema_ref: "schemas/choose_action_v1.json"
discovery_fields:
  - "time_state.phase_onehot"
  - "self_state"
  - "self_position.tile_type_onehot"
  - "action_mask"
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
夜晚最大化存活概率，优先风险控制。

# Decision Policy
1. 优先执行降低死亡风险的动作（常见为 REST 或安全补给）。
2. 避免导致高暴露或高死亡概率的动作。
3. 无法确定高价值动作时选择 REST。

# Guardrails
- 必须遵守 `action_mask`
- 输出必须通过 `choose_action_v1.json`
- 非法输出直接回退 `RuleBot`

