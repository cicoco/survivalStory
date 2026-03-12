---
# 必填：技能唯一 ID（建议 kebab-case）
skill_id: "example_skill"

# 必填：技能展示名称
name: "Example Skill"

# 必填：一句话描述（用于 Discovery 层）
short_desc: "一句话说明这个技能解决什么问题。"

# 必填：语义化版本
version: "1.0.0"

# 必填：active|disabled|deprecated
status: "active"

# 必填：low|medium|high
cost_tag: "low"

# 必填：是否启用
enabled: true

# 必填：路由优先级（越大越先匹配）
priority: 50

# 必填：触发条件（示例）
match_conditions:
  phase: "DAY"          # DAY | NIGHT | ANY
  loot_window: false    # 是否战利品窗口
  emergency: false      # 是否紧急态

# 必填：失败兜底
fallback: "RuleBot"

# 必填：动作输出 Schema
output_schema_ref: "schemas/choose_action_v1.json"

# 必填：Discovery 层可读字段白名单（尽量少）
discovery_fields:
  - "time_state.phase_onehot"
  - "self_state"
  - "action_mask"

# 必填：Intent 层可读字段白名单（按需补充）
intent_fields:
  - "self_state"
  - "self_position"
  - "inventory_vector"
  - "current_building_memory"
  - "local_map_current_3x3"
  - "action_mask"

# 必填：两层超时
timeouts:
  discovery_timeout_ms: 600
  intent_timeout_ms: 1200
---

# Goal
写本技能的目标。示例：白天优先探索并补给，保证存活。

# Decision Policy
按编号写策略偏好，便于维护：
1. 第一优先级行为
2. 第二优先级行为
3. 不确定时的保守行为

# Guardrails
- 必须遵守 `action_mask`
- 输出必须满足 `choose_action` schema
- 非法动作必须回退 `RuleBot`

