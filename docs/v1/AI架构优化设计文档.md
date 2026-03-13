# AI 架构优化设计文档（对齐最新代码）

## 1. 目标与范围
本文件用于后续研发执行，回答 5 个问题：
1. 现状是什么
2. 为什么要改
3. 怎么改（目标架构）
4. 改动细节是什么（模块、接口、事件、状态机）
5. 如何分步改（任务与验收）

范围：后端调度、结算、AI 决策链路与事件发布；不改前端交互设计。

---

## 2. 现状（基于当前代码）

### 2.1 当前核心实现
1. 结算核心在 `MatchService`
- 文件：`src/application/match_service.py`
- 负责：动作校验、动作入队、锁轮、统一结算、战利品窗口、死亡与终局判定、房间重置。

2. 调度与推送编排主要在 `RoundScheduler`
- 文件：`src/application/round_scheduler.py`（流程编排） + `src/api/app.py`（生命周期任务与依赖装配）
- 负责：后台 tick（`active_room_tick_loop`）驱动 scheduler；AI 自动提交、超时处理、事件发布、WS fanout 由 scheduler 统一执行。

3. AI 决策在同进程内调用
- 文件：`src/ai/llm_policy.py`、`src/ai/rule_bot.py`、`src/ai/skill_router.py`
- `LLMPolicy`：固定 4 段 prompt + schema 校验 + `action_mask` 校验 + 失败回退 `RuleBot`。

### 2.2 事件模型（当前已落地）
事件定义在 `src/api/constants.py`：
- `GAME_STARTED`
- `ROUND_STARTED`
- `ACTION_ACCEPTED`（私有）
- `ACTION_REJECTED`（私有）
- `LOOT_WINDOW_STARTED`
- `LOOT_WINDOW_RESOLVED`
- `ROUND_SETTLED`
- `GAME_OVER`
- `PLAYER_LEFT`
- `ROOM_DISBANDED`
- `ROOM_CLOSED`

发布机制在 `src/application/notification_service.py`：
- `publish`：广播，可附带 `private_payload`
- `publish_private`：单玩家私有事件

schema 校验现状：
- 已校验：`action_rejected_v1`、`round_settled_private_v1`、`game_over_summary_v1`、`loot_window_started_v1`、`loot_window_resolved_v1`
- 校验入口：`src/api/payload_validation.py`

### 2.3 当前行为细节（必须知晓）
1. 回合超时自动补动作目前只补真人 `REST`
- `resolve_round_timeout_if_needed` 仅对 `is_human=True` 生效。

2. AI 动作由调度流程主动触发
- `auto_submit_ai_actions` 会循环为未提交 AI 生成 `obs` 并调用策略。

3. 战利品窗口超时默认 `TOSS`
- `resolve_loot_window_timeout_if_needed` 到期后直接提交 `TOSS`。

4. 普通终局后会自动 reset 到 `WAITING`
- 在 `publish_game_over` 内触发 `reset_room_for_next_match`。

5. 存在手动触发入口
- `POST /internal/debug/rooms/{room_id}/tick-ai` 与后台 tick 并存（调试入口）。

---

## 3. 为什么要改

### 3.1 主要问题
1. 职责耦合
- 调度、结算、AI 触发、事件推送混在 `app.py + MatchService`，维护成本高。

2. 行为边界不清
- “谁是调度者、谁是结算者”在代码结构上不清晰，容易在后续需求中继续耦合。

3. 事件时序改动风险高
- 当前事件较多，若无契约化约束，重构容易破坏前端依赖。

4. 演进受限
- 未来若 AI 独立 worker 化，当前同进程强耦合会导致改造成本陡增。

### 3.2 改造目标
1. 保持当前业务行为不变（先稳定）
2. 完成三角色职责落位（调度者 / 结算者 / AI玩家）
3. 让事件契约可测试、可追踪、可回归
4. 为 AI 外置服务预留接口

---

## 4. 怎么改（目标架构）

### 4.1 三角色定义（目标）
1. 调度者（Orchestrator）
- 开轮、催收、超时补齐、触发结算、触发 AI、发布事件。
- 不改游戏权威状态。

2. 结算者（Referee）
- 维护权威状态、动作校验、锁轮、结算、终局判定。
- 不做事件编排，不做 AI 推理调用。

3. AI玩家（Agent）
- 输入：`obs + action_mask + deadline`
- 输出：`action_type + payload (+ metadata)`
- 失败必须回退（RuleBot/REST），不能阻塞回合。

### 4.2 目标状态机（简化）
1. 调度状态机
- `ROUND_OPENED -> COLLECTING -> (ALL_SUBMITTED | TIMEOUT_FILLED) -> SETTLING -> (NEXT_ROUND | GAME_OVER)`

2. 结算状态机
- `ACCEPT_ACTIONS -> LOCKED -> SETTLE_ACTIONS -> (LOOT_WINDOW | POST_FINALIZE) -> (NEXT_ROUND | NEXT_PHASE | GAME_OVER)`

3. AI状态机
- `WAIT_OBS -> DECIDE -> VALIDATE_LOCAL -> SUBMIT -> DONE`
- 异常分支：`DECIDE/VALIDATE_LOCAL` 失败 -> `FALLBACK_ACTION -> SUBMIT`

---

## 5. 改动细节（开发必须对齐）

### 5.1 模块拆分方案（先逻辑拆分，不立即拆进程）
1. 新增 `src/application/round_scheduler.py`
- 从 `app.py` 迁入：
  - `process_active_rooms_once`
  - `auto_submit_ai_actions`
  - `maybe_resolve_round_timeout`
  - `maybe_resolve_loot_window_timeout`
  - `settle_and_notify`
  - `publish_*` 事件方法

2. 保留/收敛 `src/application/match_service.py`
- 只保留结算者职责。
- 不新增网络推送、AI 调用、WS 相关逻辑。

3. 新增 `src/ai/agent_adapter.py`
- 统一 `decide(obs, action_mask, deadline)` 入口。
- 内部封装 `RuleBot/LLMPolicy` 选择和回退。

### 5.2 事件契约冻结（重构期间硬约束）
1. 不改事件名
2. 不改私有/广播语义
3. 不删现有 payload 必填字段
4. 保留 `message_id/server_seq` 语义
5. 顺序最低保证：
- 常规：`ROUND_STARTED -> ACTION_* -> ROUND_SETTLED -> (ROUND_STARTED|GAME_OVER)`
- 战利品：`LOOT_WINDOW_STARTED -> LOOT_WINDOW_RESOLVED -> ROUND_SETTLED`

### 5.3 接口与兼容策略
1. 对外 API 保持主流程稳定（`/rooms/*`、WS 协议保持）；调试接口统一到 `/internal/debug/*`
2. 新增字段只做可选扩展
- 建议新增：`trace_id`、AI 决策 metadata（`skill_id/confidence/fallback_used`）
3. 任何破坏性字段变更必须先双写兼容

### 5.4 关键实现注意事项
1. `publish_game_over` 自动 reset 行为要显式保留或显式开关化，不能隐式变更。
2. `resolve_round_timeout_if_needed` 的“仅真人自动补 REST”行为必须在文档和代码一致。
3. `tick-ai` 在迁移完成前必须保留（调试/补偿入口）。
3. `tick-ai` 仅保留 `POST /internal/debug/rooms/{room_id}/tick-ai`，避免业务路径混入调试入口。
4. LLM 仍保持固定 4 段 prompt 与 fallback 语义，先不改策略语义。

---

## 6. 如何分步改（任务拆解）

### Phase 0：基线冻结（1-2 天）
状态：已完成（2026-03-13）
任务：
1. 冻结事件契约文档（事件名、时序、payload、范围）。
2. 补当前行为回归测试（至少覆盖开局、常规回合、战利品、终局、离房）。
3. 记录关键指标基线（回合耗时、AI 回退率、事件量）。

完成标准：
1. 测试可稳定复现当前行为。
2. 文档与当前代码一致，无歧义口径。

落地改动清单（建议）：
1. 新增 `tests/test_event_sequence.py`
 - 用例：`start -> round -> settle -> next_round/game_over`
 - 用例：`loot_window_started -> loot_window_resolved -> round_settled`
2. 新增 `tests/test_timeout_paths.py`
 - 用例：真人超时自动 `REST`
 - 用例：战利品窗口超时自动 `TOSS`
3. 在文档中固化事件表（本文件 2.2 + 5.2 为唯一口径）

### Phase 1：调度层抽离（3-5 天）
状态：已完成（2026-03-13）
任务：
1. 新建 `RoundScheduler`，迁移 `app.py` 中流程编排函数。
2. `app.py` 改为“路由 + 组装 + 调用 scheduler”。
3. 保持对外 API 与事件行为不变。

完成标准：
1. 事件序列与旧实现一致。
2. 业务回归测试全通过。

落地改动清单（建议）：
1. 新建 `src/application/round_scheduler.py`
 - 迁入函数：`process_active_rooms_once`、`auto_submit_ai_actions`、`settle_and_notify`
 - 迁入函数：`publish_round_started`、`publish_round_settled`、`publish_game_over`
 - 迁入函数：`publish_loot_window_started`、`publish_loot_window_resolved`
2. `src/api/app.py`
 - 保留 HTTP/WS 路由
 - 将流程调用替换为 `scheduler.*`
3. 保持接口不变
- `POST /internal/debug/rooms/{room_id}/tick-ai` 保留
 - 事件名与 payload 不变

### Phase 2：AI 适配层收口（2-3 天）
状态：已完成（2026-03-13）
任务：
1. 新建 `agent_adapter.py`，统一 AI 决策入口。
2. 将 `auto_submit_ai_actions` 对策略的直接调用替换为 adapter 调用。
3. 增加 AI 决策日志字段：`fallback_used/skill_id/latency_ms`。

完成标准：
1. AI 行为结果与旧实现一致。
2. 失败路径可观测、可回退。

落地改动清单（建议）：
1. 新建 `src/ai/agent_adapter.py`
 - `decide(obs, action_mask, deadline_at) -> action`
 - 内部实现：优先 `LLMPolicy`，失败回退 `RuleBot`
2. 新增 `tests/test_agent_adapter.py`
 - LLM 成功路径
 - LLM 失败回退路径
3. `round_scheduler.py`
 - 统一从 adapter 获取 AI 动作

### Phase 3：可观测性增强（2-3 天）
状态：已完成（2026-03-13）
任务：
1. 引入 `trace_id`，贯穿调度、AI、结算、事件。
2. 增加关键日志点：`ROUND_OPENED`、`AI_DECISION_*`、`ROUND_SETTLED`、`GAME_OVER`。
3. 增加指标：AI 超时率、fallback 率、结算耗时、推送延迟。

完成标准：
1. 能按 `trace_id` 回放一轮完整链路。
2. 指标可用于线上排障。

落地改动清单（建议）：
1. `NotificationService` message 增加可选 `trace_id`
2. `round_scheduler.py` 生成并传递 `trace_id`
3. 日志规范统一
 - 统一字段：`room_id/day/phase/round/player_id/trace_id`
4. 新增 `tests/test_trace_propagation.py`
 - 校验关键事件含同一 `trace_id`

### Phase 4：可选 AI 外置化（后续）
状态：待定（暂不执行）
任务：
1. 将 `agent_adapter` 替换为 RPC/队列客户端。
2. 增加 deadline 与重试策略。
3. 保留本地 RuleBot 兜底。

完成标准：
1. AI 外置后不影响回合稳定推进。
2. 事件与对外协议保持兼容。

---

## 6.1 执行顺序与依赖
1. 必须先完成 Phase 0 再进 Phase 1（先冻结口径）。
2. Phase 2 依赖 Phase 1（AI adapter 由 scheduler 调用）。
3. Phase 3 可与 Phase 2 并行，但 `trace_id` 字段最终要在 scheduler 汇总。
4. Phase 4 仅在 Phase 1-3 稳定后开启。

## 6.2 每阶段提交要求（DoD）
1. 代码 DoD
 - 对应模块有单元测试。
 - 不引入 breaking API 变更。
2. 文档 DoD
 - 本文档同步更新“现状/差异/回滚方案”。
3. 发布 DoD
 - 提供回滚开关或回滚步骤。
 - 提供事件顺序对账结果。

---

## 7. 开发红线（必须遵守）
1. 禁止在结算者内新增推送编排逻辑。
2. 禁止在调度者内直接改写地图/库存/玩家状态。
3. 禁止 AI 跳过 `submit_action` 直写状态。
4. 禁止在未兼容迁移前修改事件名或删除既有字段。
5. 任意 AI 异常不得阻塞回合推进。

---

## 8. 交付与验收

### 8.1 功能验收
1. 行为一致：同输入下结算结果与事件顺序一致。
2. 故障可恢复：AI 超时或失败时仍能按时推进回合。
3. 事件可消费：前端无需改动即可正常工作。

### 8.2 工程验收
1. 代码结构可清晰识别三角色模块。
2. `app.py` 复杂度显著下降（主要做路由装配）。
3. 回归测试与新增链路测试通过。

### 8.3 运维验收
1. 可按 `trace_id` 追踪单回合全链路。
2. 核心指标有图可看（至少日志可统计）。

---

## 9. 当前进展快照（已落地）
1. Phase 0 已落地
- 已新增事件时序与超时路径测试（`test_event_sequence.py`、`test_timeout_paths.py`）。

2. Phase 1 已落地
- 已抽出 `RoundScheduler`，`app.py` 路由层薄化，保持外部接口和事件语义不变。

3. Phase 2 已落地
- 已引入 `AgentAdapter`，统一 AI 决策入口并接入调度层。

4. Phase 3 已落地
- 已打通 `trace_id` 在调度/消息链路透传，并补充调度标准日志与追踪测试。

---

## 10. Phase 4 启动前检查清单
说明：当前阶段按“单进程一体化启动”执行，Phase 4 暂不进入开发排期；保留本节作为后续扩展预案。

1. 事件兼容确认
- 前端仅依赖既有 `event_type` 与必填字段；新增 `trace_id` 为可选字段，不影响现有消费。

2. 运行稳定性确认
- 至少一轮完整压测/回放中无回合阻塞、无事件顺序回退。

3. 兜底策略确认
- 外置 AI 前必须保留本地 `RuleBot` fallback，且 deadline 过期可立即降级。

4. 接口契约确认
- 明确 AI 外部调用协议：输入（obs/action_mask/deadline/trace_id）与输出（action/payload/metadata）。

5. 回滚预案确认
- 外置化开关可快速关闭，关闭后自动切回本地 adapter + RuleBot 路径。

---

## 11. 结论
当前系统可运行，但调度、结算、AI 决策职责耦合，继续迭代风险会放大。建议按“先逻辑解耦、再能力增强、最后可选外置化”的路径推进，优先保证事件契约稳定与行为一致，再逐步提升可维护性和扩展性。

---

## 12. AI 输入契约优化（RuleBot + LLM）

### 12.1 现状问题（输入视角）
1. 缺少短期轨迹记忆
- 当前 `obs` 无 `last_position/recent_positions`，RuleBot 在局部路径上容易来回横跳。

2. 缺少邻格结构化信息
- 当前 `obs` 缺少可直接做邻格比较的局部结构化输入，策略层难以稳定做移动打分。

3. 缺少局部地图摘要
- 当前以“当前格快照”为主，缺少 3x3/5x5 局部视野摘要，不利于稳定移动决策。

4. 存在输入冗余
- `allowed_actions` 同时出现在 `obs` 与 `action_mask`，语义重复。

### 12.2 优化目标
1. 统一 RuleBot 与 LLM 的决策输入协议（同一份 `obs_v2` + `action_mask`）。
2. 控制输入规模与内存占用（有界记忆，不喂全量历史）。
3. 明确硬约束边界：动作合法性始终由 `action_mask + 服务端校验` 保证。

### 12.3 建议输入模型（obs_v2）
1. 保留字段
- `time_state`、`round_timer`、`position`、`self_status`、`inventory`、`loot_window`

2. 新增字段（核心）
- `recent_positions`: 最近 N 步轨迹（建议 `N=16`，可配置 12~20）
- `local_map_summary`: 以当前位置为中心的局部摘要（建议 `5x5`，低配可 `3x3`）

3. 去冗余规则
- `action_mask` 作为唯一动作约束输入。
- `obs` 内的 `allowed_actions` 标记为废弃字段（迁移期可双写，完成后移除）。

### 12.4 记忆与性能边界
1. 轨迹记忆：`deque(maxlen=N)`，严格有界。
2. 地图记忆：保存“每格最新摘要”而非完整历史流水。
3. 决策输入：仅下发局部窗口（3x3/5x5），不下发全图全历史。
4. 线索时效：按回合进行 stale 标记，避免旧信息误导决策。

### 12.5 执行策略（分步）
1. Step A：输入层扩展（不改策略）
- `get_player_view` 新增 `recent_positions/local_map_summary`
- 保持旧字段，确保前后兼容

2. Step B：RuleBot 升级
- 加入“避免立即回头”
- 基于 `local_map_summary + recent_positions` 做邻格打分，减少无效往返

3. Step C：LLM Prompt 对齐
- Prompt 使用 `obs_v2 + action_mask`
- 继续执行 `schema + action_mask + 服务端校验 + RuleBot fallback`

4. Step D：去冗余与收口
- 下线 `obs.allowed_actions`
- 固化 `obs_v2` schema 与回归测试

### 12.6 验收指标
1. 行为质量
- “连续 A<->B 往返”次数显著下降（可按局统计）。

2. 工程稳定性
- 决策超时率不升高；fallback 率可观测。

3. 性能边界
- 单次 AI 决策输入大小可控（窗口化后稳定）。
