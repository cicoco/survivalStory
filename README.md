# survivalStory

末日废墟生存战（AI 对抗版）V1 项目代码仓库。

## 项目管理（uv）

- 创建环境并安装开发依赖：`uv sync --group dev`
- 运行单元测试（当前）：`uv run python -m unittest discover -s tests -p 'test_*.py'`
- 运行 pytest（可选）：`uv run pytest`
- 运行 ruff（可选）：`uv run ruff check .`
- 启动 API 服务（Phase 3）：`uv run uvicorn src.api.app:app --reload`
- 大厅页（同服务部署）：`http://127.0.0.1:8000/lobby`
- 游戏页（同服务部署）：`http://127.0.0.1:8000/game?room_id=xxx&player_id=yyy`
  - 地图资源图位于前端配置：`web/assets/game.js` 中 `TILE_IMAGE_URLS`（可替换为你的方形图片地址）
- 手动触发 AI 自动补动作：`POST /rooms/{room_id}/tick-ai`
- 玩家离开房间：`POST /rooms/{room_id}/leave`
- 终局摘要查询：`GET /rooms/{room_id}/summary`
- 终局后重置房间：`POST /rooms/{room_id}/reset`
- 房间列表（状态/人数/是否开局）：`GET /rooms`

## AI 策略运行方式

- 默认读取配置文件：`config/app.toml`
- 默认：`ai.policy = "rule"`（使用 RuleBot）
- OpenAI：`ai.policy = "llm"` 并填写 `openai.api_key`
- 可选模型：`openai.model = "gpt-4.1-mini"`
- 通知历史窗口：`notification.history_limit = 100`
- 后端调试日志：`backend.debug_log = false`
- 战利品窗口超时：`gameplay.loot_window_timeout_sec = 60`
- 核心动作超时：`gameplay.round_action_timeout_sec = 90`
- 白天阶段最大轮次：`gameplay.max_day_phase_rounds = 99`
- 夜晚阶段最大轮次：`gameplay.max_night_phase_rounds = 99`
- 房间最大人数：`gameplay.room_max_players = 6`
- AI 最大补全数：`gameplay.max_ai_players = 5`

示例：
`uv run uvicorn src.api.app:app --reload`

环境变量仍可覆盖配置文件（例如 CI）：
- `APP_CONFIG`（指定配置文件路径）
- `BACKEND_DEBUG_LOG`
- `AI_POLICY`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_BASE_URL`
- `NOTIFICATION_HISTORY_LIMIT`
- `LOOT_WINDOW_TIMEOUT_SEC`
- `ROUND_ACTION_TIMEOUT_SEC`
- `MAX_DAY_PHASE_ROUNDS`
- `MAX_NIGHT_PHASE_ROUNDS`
- `ROOM_MAX_PLAYERS`
- `MAX_AI_PLAYERS`

## 事件说明（WS/History）

- `ROUND_SETTLED` 事件包含按玩家隔离的 `private_payload`，字段包含：
  - `actions`：本人本轮动作明细（成本、前后状态、效果）
  - `events`：基础消耗、死亡等事件
  - `status_before` / `status_after`：本轮前后状态
- `ACTION_REJECTED` 事件 payload 为结构化对象：
  - `schema`：`action_rejected_v1`
  - `error_code`：稳定错误码
  - `reason`：人类可读原因
  - `allowed_actions`：当前建议可执行动作
- `GAME_OVER` 事件 payload 为终局统计摘要：
  - 角色统计：存活天数、获取物资总量、死亡原因、击杀/死亡
  - 真人战绩：最后存活真人、真人存活天数、真人击杀/死亡总计
  - 排名：按存活天数降序

参考 schema：
- `docs/v1/schemas/action_rejected_v1.json`
- `docs/v1/schemas/round_settled_private_v1.json`
- `docs/v1/schemas/game_over_summary_v1.json`

服务端会在出站前做 schema 校验；不合法 payload 会被拒绝下发并抛出错误。

## 房间离开规则（V1）

- 等待中：普通玩家可离开；房主离开会解散房间（`ROOM_DISBANDED`）。
- 游戏中：普通玩家离开按死亡处理（`PLAYER_LEFT` 且模式为 `LEFT_IN_GAME_AS_DEATH`）。
- 游戏中：房主离开会立即关局（`ROOM_CLOSED`，对局结束）。

## 房间人数配置（V1）

- 房间总人数上限：真人 + AI 不能超过 `room_max_players`。
- AI 自动补全上限：仅补到 `max_ai_players`，不会强制补满房间。
- 后台定时清理：每 3 分钟扫描一次“等待中且未开局”房间；等待超过 3 分钟会自动解散。

## 终局与重置（V1）

- 对局结束后可通过 `GET /rooms/{room_id}/summary` 获取终局统计与排名。
- 仅房主可调用 `POST /rooms/{room_id}/reset` 将房间重置为 `WAITING`，用于下一局。
- 重置会清空当前局状态与通知历史；保留真人玩家并移除 AI（下局 `start` 时自动补齐 AI）。

## 阶段计划

当前实现进度按阶段推进，详见：
- `docs/v1/实施计划.md`
- `docs/v1/技术选型清单.md`
