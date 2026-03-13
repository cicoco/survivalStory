# RuleBot + LLM 输入优化设计文档

## 1. 目标与范围
本文档只关注一件事：统一并优化 AI 决策输入（RuleBot 与 LLM 共用），在不破坏现有规则引擎权威性的前提下，提升决策质量与稳定性。

范围：
- `obs`/`action_mask` 输入契约
- RuleBot 决策输入改造
- LLM Prompt 输入改造
- 迁移步骤与验收口径

不在本次范围：
- 结算规则改写
- 事件协议重构
- AI 独立进程化

---

## 2. 当前现状（As-Is）

### 2.1 当前决策链路
1. 调度层为 AI 生成观测：`obs = get_player_view(...)`
2. 调度层调用统一入口：`decide(obs, action_mask, deadline_at)`
3. 策略层输出动作后，仍需经过服务端动作合法性校验
4. 任意异常路径回退 `RuleBot` 或 `REST`

### 2.2 当前输入结构的主要问题
1. 缺少短期轨迹记忆
- 无 `recent_positions`，容易出现 A<->B 来回移动。

2. 缺少邻格结构化信息
- 无可直接用于邻格比较的局部结构化输入，策略层难以稳定做移动打分。

3. 缺少局部地图摘要
- 以“当前格快照”为主，缺少 3x3/5x5 局部上下文。

4. 输入冗余
- `allowed_actions` 同时在 `obs` 与 `action_mask` 传递，语义重复。

---

## 3. 目标输入契约（To-Be）

## 3.1 设计原则
1. 约束与状态分离
- `action_mask` 是唯一动作约束来源。
- `obs` 仅承载状态与世界信息。

2. 有界记忆
- 不喂全量历史，避免 token/内存失控。

3. 双策略共用
- RuleBot 与 LLM 使用同一套 `obs_v2` 字段。

4. 规则权威在后端
- LLM 负责“选择”，不是“定义规则”。

## 3.2 obs_v2 字段定义
保留字段：
- `identity`（可选，便于追踪）
- `time_state`
- `round_timer`
- `position`
- `building_info_state`
- `building_snapshot`
- `self_status`
- `inventory`
- `attack_targets`
- `loot_window`

新增字段：
- `recent_positions`: 最近 N 步轨迹（建议 N=16，可配置 12~20）
- `local_map_summary`: 以自己为中心的 5x5（低配可 3x3）最新摘要

废弃字段：
- `obs.allowed_actions`（已移除）

---

## 4. 关键字段说明

## 4.1 local_map_summary（局部地图）
- 以当前位置为中心窗口，推荐 `5x5`。
- 每格只放“最新摘要”，不放历史流水。
- 每格摘要建议字段：
  - `tile_type`
  - `last_seen_round`
  - `info_state`（`UNEXPLORED/SNAPSHOT/STALE`）
  - `resource_hint`
  - `character_hint`
  - `risk_hint`

## 4.2 recent_positions（轨迹）
- 使用有界队列：`deque(maxlen=N)`。
- 记录至少：`x/y/round`。
- 主要用于“避免立即回头”和“短期去重”。

---

## 5. 内存与性能边界
1. 轨迹有界：`N` 固定上限。
2. 地图记忆去流水：仅保留每格最新摘要。
3. 决策输入窗口化：只下发 3x3/5x5，不下发全图全历史。
4. 时效治理：过旧线索标记 `STALE`，降低策略权重。

建议默认值：
- `recent_positions_maxlen = 16`
- `local_map_window = 5`

---

## 6. RuleBot 与 LLM 的使用方式

## 6.1 RuleBot
- 使用 `recent_positions + local_map_summary + self_status` 做启发式打分。
- 核心策略：
  1. 避免立即回头（无路可走除外）
  2. 优先安全格
  3. 在安全前提下优先“未探索/有资源线索”
  4. 连续无收益时触发降级（`REST` 或换向）

## 6.2 LLM
Prompt 固定结构：
1. `System Hard Rules`
2. `Skill Template`
3. `State JSON (obs_v2)`
4. `Constraint(action_mask/schema)`

硬约束不变：
- schema 校验
- `action_mask` 校验
- 服务端合法性校验
- 失败回退 RuleBot

---

## 7. 迁移方案（分步）

### Phase A：输入扩展
1. 在 `get_player_view` 增加新字段：
- `recent_positions`
- `local_map_summary`
2. 动作约束统一通过 `action_mask` 传递
3. 新增输入快照测试

验收：
- 新字段稳定返回。

### Phase B：RuleBot 升级
1. 引入防回头规则。
2. 基于 `local_map_summary + recent_positions` 做邻格打分。
3. 打分权重抽为可配置常量（如回头惩罚、资源提示权重、未探索加分）。
4. 增加“连续无收益轮”降级策略：阈值达到后优先 `REST`（若可用），否则继续换向移动。
5. 增加决策日志（原因、候选、最终动作）。

验收：
- A<->B 往返次数下降。

### Phase C：LLM 对齐 obs_v2
1. Prompt 使用 `obs_v2 + action_mask`。
2. Skill 输入引用新字段（尤其 `recent_positions/local_map_summary`）。
3. 保持 fallback 语义不变。

验收：
- schema 与合法性校验通过率稳定。

### Phase D：去冗余收口
1. AI 决策链路先下线 `obs.allowed_actions`（统一改为显式传入 `action_mask`）。
2. 固化 `obs_v2` schema 文档与测试。
3. 清理旧字段依赖并删除废弃字段。

验收：
- 全链路仅依赖 `action_mask` 作为动作约束。

---

## 8. 验收指标
1. 行为质量
- 指标：连续 A<->B 往返次数（每局/每 100 回合）
- 目标：显著下降

2. 稳定性
- 指标：AI 决策超时率、fallback 率
- 目标：不高于当前基线

3. 性能
- 指标：单次决策输入大小、决策耗时 P95
- 目标：窗口化后稳定可控

4. 正确性
- 指标：非法动作提交率
- 目标：保持低位，且异常均可回退

---

## 9. 风险与注意事项
1. 字段突增引发体积膨胀
- 对策：严格窗口化与字段裁剪。

2. 变更落地风险
- 对策：按阶段执行并用回归测试兜底。

3. 观测滞后导致误导
- 对策：引入 `last_seen_round/info_state`，策略层降低 stale 信息权重。

4. LLM 输出不稳定
- 对策：强校验 + action_mask + 服务端校验 + RuleBot fallback。

---

## 10. 结论
本优化的本质是“把输入做对”：
- 用有界、结构化、可解释的 `obs_v2` 支撑 RuleBot 与 LLM。
- 用 `action_mask` 和服务端校验守住规则边界。
- 在不改权威结算的前提下，提升 AI 行为质量并降低维护成本。

---

## 11. 最终态输入输出契约（开发对齐）

### 11.1 `action_mask` 最终定义
`action_mask` 为 `list[str]`，表示“当前这一拍允许提交的动作类型”，是 AI 决策阶段的唯一动作许可来源。  
AI 输入中的 `obs_v2` 不再包含 `allowed_actions` 字段。

动作枚举：
- 核心动作：`MOVE`、`EXPLORE`、`USE`、`TAKE`、`REST`、`ATTACK`
- 战利品窗口动作：`GET`、`TOSS`

生成规则（服务端权威）：
1. 若玩家 `alive=false` 或 `phase_ended=true`：`[]`
2. 若存在 `loot_window_state`：
- 胜者且存活：`["GET", "TOSS"]`
- 其他玩家：`[]`
3. 普通行动阶段：
- 基础：`["MOVE", "USE", "REST"]`
- 当前地块为安全建筑格时，追加：`["EXPLORE", "TAKE", "ATTACK"]`

约束边界：
1. `action_mask` 仅约束“动作类型可不可选”，不描述 payload 细则。
2. payload 细则由 `choose_action_v1 schema` + 服务端业务校验共同保证。
3. 所有服务端校验以提交时状态为准，不依赖客户端/AI 缓存。

### 11.2 `obs_v2` 最终字段说明（中文）
`obs_v2` 为 `dict`，由 `get_player_view()` 生成。字段契约如下：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `identity` | `object` | 是 | `{player_id, room_id}` |
| `time_state` | `object` | 是 | `{day:int, phase:str, round:int}` |
| `round_timer` | `object` | 是 | `{timeout_sec:int, opened_at:str\\|null, deadline_at:str\\|null, remaining_sec:int\\|null}` |
| `position` | `object` | 是 | `{x:int, y:int, tile_type:str}` |
| `building_info_state` | `string` | 是 | 当前格信息状态，当前实现：`UNEXPLORED`/`HAS_MEMORY` |
| `building_snapshot` | `object` | 是 | `{resources:dict, characters:list[str], snapshot_updated_at:str\\|null}` |
| `self_status` | `object` | 是 | `{water:int, food:int, exposure:int, alive:bool, phase_ended:bool}` |
| `inventory` | `object` | 是 | `dict[item_id, qty]` |
| `recent_positions` | `array` | 是 | 最近轨迹，元素 `{x,y,day,phase,round}`，有上限（默认16） |
| `local_map_summary` | `object` | 是 | 局部地图摘要，默认 `window_size=5`（25格） |
| `action_mask` | `array[string]` | 是 | 当前可提交动作类型 |
| `attack_targets` | `array[string]` | 是 | 当前可攻击候选玩家 ID 列表 |
| `loot_window` | `object\\|null` | 是 | 战利品窗口视图；无窗口时为 `null` |

`loot_window` 子结构：
- `is_open: bool`
- `winner_player_id: str`
- `loser_player_id: str`
- `expires_at: str(ISO8601)`
- `can_choose: bool`
- `loser_inventory: dict[str,int]`（仅胜者可见完整内容，其他玩家为 `{}`）

`local_map_summary` 子结构：
- `window_size: int`
- `center: {x:int, y:int}`
- `tiles: list[tile_row]`，其中 `tile_row` 包含：
`x,y,dx,dy,in_bounds,tile_type,is_safe,info_state,is_explored,known_resources,known_characters,last_seen_at`

字段语义（决策视角）：
1. `identity`
- 标识“当前是谁、在哪个房间”，主要用于追踪与隔离，不直接驱动策略优先级。

2. `time_state`
- 表示当前天数/昼夜/回合，用于判断生存节奏与风险窗口。

3. `round_timer`
- 表示本回合时间预算；`remaining_sec` 过低时应倾向低风险、可快速提交动作。

4. `position`
- 表示当前位置与地块类型，是 MOVE/EXPLORE/ATTACK 等动作可行性判断基线。

5. `building_info_state`
- 表示当前格认知状态；`UNEXPLORED` 倾向先探索，`HAS_MEMORY` 可基于记忆直接决策。

6. `building_snapshot`
- 表示当前格已知资源、已知人物和快照时间，用于判断 TAKE/ATTACK 的潜在收益与风险。

7. `self_status`
- 表示自身生存压力（食水/暴露/存活/阶段结束）；用于确定“生存优先”还是“机会优先”。

8. `inventory`
- 表示可用物资库存；直接决定 USE 能力与资源恢复路径。

9. `recent_positions`
- 表示最近移动轨迹；用于降低来回折返和无收益循环概率。

10. `local_map_summary`
- 表示周边局部认知（默认 5x5）；用于短程路径规划、避险和目标接近。

11. `action_mask`
- 表示当前可提交动作白名单；最终动作类型必须落在该集合内。

12. `attack_targets`
- 表示当前可攻击候选目标集合；用于避免无效或越权攻击意图。

13. `loot_window`
- 表示战利品窗口上下文与决策权限；窗口开启时仅胜者可在 GET/TOSS 间选择。

14. `loot_window.loser_inventory`
- 仅对可决策者提供完整信息，用于选择“拿取哪些物品”或“放弃”。

15. `local_map_summary.tiles[*]`
- 表示每个邻格的结构化事实（边界/地形/安全性/探索状态/已知资源人物/最后观察时间），是移动策略核心输入。

### 11.3 `decide` 最终调用形态
```python
action = agent_adapter.decide(
    obs=obs_v2,
    action_mask=action_mask,
    deadline_at=deadline_at,
)
```

返回：
```json
{
  "action_type": "MOVE",
  "payload": {"x": 4, "y": 3}
}
```

中文说明：
1. `action_type`
- 必须是动作枚举之一：`MOVE|EXPLORE|USE|TAKE|REST|ATTACK|GET|TOSS`
- 业务有效性要求：必须在当前 `action_mask` 中

2. `payload`
- 必须为对象；缺失或非对象时会被适配层归一化为 `{}`
- 各动作 payload 细则见 `choose_action_v1` 与服务端校验

3. 兜底行为
- 当 `action_mask=[]`：适配层直接返回 `{"action_type":"REST","payload":{}}`
- 当超时或主策略异常：回退到 fallback 策略输出，再归一化

### 11.4 约束链路（最终）
1. 决策生成
- 主策略（RuleBot/LLM）产出候选动作

2. 策略内校验（LLM路径）
- `choose_action_v1` JSON Schema 校验
- `action_type ∈ action_mask` 校验
- 任一失败即回退 RuleBot

3. 适配层归一化
- 统一输出为 `{action_type, payload}` 结构
- 非法/缺失 payload 收敛为 `{}`，避免脏结构下游扩散

4. 服务端权威校验
- `submit_action()` 校验动作合法性、坐标相邻、目标可见、物品约束等
- 失败抛 `ValueError`，进入拒绝事件或调度回退

5. 调度兜底
- AI 自动提交失败时，调度层尝试提交 `REST` 作为最终兜底

链路结论：
- 模型可错判，但动作不会越过规则边界；
- 权威状态转移只由服务端校验通过后的动作驱动。

### 11.5 最小验收标准（联调口径）
1. `get_player_view()` 返回中：
- 必有 `action_mask`
- 不含 `allowed_actions`
- 必有 `recent_positions`、`local_map_summary`

2. `RoundScheduler` 调 AI：
- `decide(obs, action_mask, deadline_at)` 中 `obs` 不含 `allowed_actions`
- `action_mask` 与服务端实时可行动作一致

3. LLM 非法输出防护：
- schema 不合法 / `action_type` 越界时，必回退 RuleBot

4. 服务端防线：
- 任意绕过客户端的非法动作提交均被拒绝，不影响权威状态

### 11.6 兼容项与实现差异说明
1. 本契约中 “`obs_v2` 不携带 `allowed_actions`” 已在 AI 输入链路落地。  
2. 但 `ACTION_REJECTED` 事件 payload 目前仍包含 `allowed_actions`，用于前端错误提示与引导。  
3. 因此当前系统中：
- AI 输入契约：使用 `action_mask`
- 前端拒绝提示契约：仍可读取 `allowed_actions`（仅拒绝事件场景）

### 11.7 中文含义说明（联调口径）
1. `action_mask` 的中文含义
- 它是“当前这一拍允许做哪些动作”的白名单。
- AI 只能在这个列表里选动作；不在列表里的动作提交后会被拒绝。
- 它只描述“能不能做”，不描述 payload 参数细节。

2. `obs_v2` 的中文含义
- 它是 AI 在当前时刻看到的“局面快照”。
- 包含时间、位置、状态、背包、局部地图、轨迹、可攻击目标、战利品窗口等决策信息。
- 目标是让 AI 基于结构化事实决策，而不是依赖隐式上下文猜测。

3. `recent_positions` 的中文含义
- 它是“最近几步走位历史”。
- 主要用途是减少来回折返、无收益循环。
- 属于短期导航记忆，不是全局回放。

4. `local_map_summary` 的中文含义
- 它是“以玩家为中心的局部地图认知摘要”（当前默认 5x5）。
- 提供附近格子的地形、安全性、探索状态、已知资源与人物信息。
- 主要用于局部路径规划和风险规避。

5. `building_info_state` / `building_snapshot` 的中文含义
- 表示“当前格子的认知状态和记忆快照”。
- `building_info_state` 告诉 AI：这格是未探索还是已有记忆。
- `building_snapshot` 告诉 AI：已知资源、已知人物、快照更新时间。

6. `loot_window` 的中文含义
- 表示“战利品决策窗口是否开启，以及我是否有选择权”。
- 仅胜者在窗口期可提交 `GET/TOSS`，其他玩家不可操作。
- `loser_inventory` 仅对可决策者暴露完整信息。

7. `decide` 输出的中文含义
- `action_type` 是最终动作类别。
- `payload` 是该动作所需参数（如 MOVE 坐标、USE/TAKE/GET 物品）。
- 输出即候选动作，仍需通过服务端权威校验后才会生效。

8. 约束链路的中文含义
- AI 先给候选动作，再经过 schema 和 `action_mask` 校验，再进入服务端规则校验。
- 任一环节失败都会回退到安全动作（RuleBot 或 REST）。
- 因此“模型可犯错，但规则边界不可突破”。

9. 兼容差异的中文含义
- AI 输入层面已经统一使用 `action_mask`，不再依赖 `allowed_actions`。
- 但前端拒绝提示事件 `ACTION_REJECTED` 里仍保留 `allowed_actions`，用于错误提示与兼容旧逻辑。
