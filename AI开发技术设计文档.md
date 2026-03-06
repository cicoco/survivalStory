# 末日废墟生存战：技术设计文档（当前实现）

## 1. 目标
- 支撑当前控制台联机版本稳定开发。
- 明确房间、结算、可见性、观察链路。
- 为后续网页化复用同一引擎预留边界。

## 2. 技术栈
- Python 3.11+
- `uv` 管理运行
- TCP + JSON 行协议
- `socketserver.ThreadingTCPServer`
- SQLite（WAL）
- 无 Redis

## 3. 系统角色
- `survival-broker`
  - 房间发现（`create/list/lookup/heartbeat/remove`）
- 房主房间服务（`RoomHostServer`）
  - 对局权威：动作合法性、状态变更、结算、终局
  - 观察事件源：有变化时直接推送给观察者
- 玩家客户端（`survival`）
  - 大厅命令、房间命令、观察命令

## 4. 游戏规则基线
- 白天/夜晚阶段交替。
- 阶段内可多次动作。
- `REST` 后该玩家本阶段结束。
- 固定消耗仅在阶段结算触发，每阶段一次：水-1、食-1。
- 真人玩家全部死亡立即终局（`all_humans_dead`）。

## 5. 房间与联机流程
### 5.1 大厅命令
- `list`
- `create [max_players] [max_ai]`
- `join <room_id>`
- `watch <room_id>`
- `quit`

### 5.2 创建房间
- 创建者自动成为房主并自动加入房间。
- 创建参数：`max_players`、`max_ai`。
- 房主本地启动房间服务后把可达地址注册到 broker。

### 5.3 开局
- 仅房主可 `start`。
- 按 `max_players` 和 `max_ai` 生成 AI。
- 开局后不允许新玩家加入该局。

### 5.4 `leave` 语义
- 房主未开局 `leave`：解散房间（broker 移除房间）。
- 房主已开局 `leave`：关闭房间并结束本局，所有在线玩家收到 `room_closed`。
- 普通玩家未开局 `leave`：退出房间并从成员列表移除。
- 普通玩家已开局 `leave`：退出当前对局并判定死亡。

## 6. 回合同步模型（当前）
- 同一轮每位存活玩家最多提交 1 次动作。
- 提交后进入等待态。
- 当全部存活玩家提交（或超时自动 `REST`）后，统一结算。
- 按提交先后分配并执行 `action_seq`。
- 结算完成后进入下一轮。

## 6.1 建筑库存可见性（Fog-of-War）
- 玩家初到建筑时，`loot` 视图为 `UNKNOWN`。
- 执行 `EXPLORE` 后，玩家才获得该建筑库存快照。
- 玩家离开后记忆保留但可能过期；回到建筑可直接 `TAKE`（可能失败）或再次 `EXPLORE`。
- 每轮结算后，玩家会收到当前可见库存更新，用于更新其本地认知。

## 7. 可见性与隐私规则
### 7.1 玩家视角（含房主客户端）
- 仅接收自己的：
  - `action_prompt`
  - `action_ack`
  - `round_settled`（含自己的状态快照）
- 不接收其他玩家逐动作明细。
- 房主客户端与普通玩家完全一致，无额外可见性。

## 7.3 物资标识规范
- 内部统一使用 canonical ID：
  - `BREAD`
  - `BOTTLED_WATER`
  - `BISCUIT`
  - `CANNED_FOOD`
  - `BARREL_WATER`
  - `CLEAN_WATER`
- 对外交互允许别名/编码/中文，进入系统后必须归一化为 canonical ID。
- 控制台展示层统一使用中文物资名。

### 7.4 目录字段语义
- `zh`：中文显示名（仅展示用）。
- `aliases`：输入识别集合（中英文与缩写等），统一映射到 canonical ID。
- `effect`：物资效果（`USE` 时应用）。
- `tile_code`：建筑在地图中的格子代码（仅建筑配置有）。
- `storage`：建筑初始库存（canonical 物资 ID -> 数量）。

### 7.2 观察视角（直连房主）
流程：
1. 观察客户端向 broker `lookup` 房间地址。
2. 观察客户端直连房主并发送 `watch_join`。
3. 房主在状态变化时推送 `watch_event`。

可观察事件：
- `game_started`
- `phase_started`
- `round_action`（含人类和 AI）
- `round_settled`
- `phase_settle`
- `phase_settled_summary`（阶段结算后全员状态）
- `game_over`

## 8. 协议
### 8.1 客户端 -> broker
- `{"type":"list"}`
- `{"type":"create","host_name":"玩家A","max_players":8,"max_ai":5,"endpoint_host":"1.2.3.4","endpoint_port":10023}`
- `{"type":"lookup","room_id":"Z9NEGO"}`
- `{"type":"heartbeat","room_id":"Z9NEGO","status":"WAITING|RUNNING"}`
- `{"type":"remove","room_id":"Z9NEGO"}`

### 8.2 客户端 -> 房主服务
- `{"type":"watch_join","room_id":"Z9NEGO"}`
- `{"type":"join","room_id":"Z9NEGO","name":"玩家B"}`
- `{"type":"start"}`
- `{"type":"members"}`
- `{"type":"action","text":"MOVE 2 3"}`
- `{"type":"action","text":"USE W"}`（物资编码示例）
- `{"type":"leave"}`

### 8.3 服务端 -> 客户端
- `{"type":"watch_joined","room_id":"Z9NEGO","watcher_id":1}`
- `{"type":"watch_event","seq":12,"event":{...}}`
- `{"type":"joined",...}`
- `{"type":"members","members":[...]}`
- `{"type":"members_update","payload":{"members":[...]}}`
- `{"type":"game_started","payload":{...}}`
- `{"type":"phase_started","payload":{...}}`
- `{"type":"action_prompt","message":"..."}`
- `{"type":"action_ack","status":"accepted|rejected|auto","reason":"..."}`
- `{"type":"round_settled","payload":{...}}`
- `{"type":"left","message":"..."}`
- `{"type":"room_closed","room_close_reason":"host_left_before_start|host_left_room_closed","message":"..."}`
- `{"type":"game_over","payload":{...}}`
- `{"type":"error","message":"..."}`

## 9. 数据持久化
数据库：`game.db`

### 9.1 `event_log`
- `id`
- `game_id`
- `room_id`
- `phase_no`
- `phase`
- `action_seq`
- `actor_id`
- `event_type`
- `payload_json`
- `created_at`

### 9.2 `game_summary`
- `id`
- `game_id`
- `room_id`
- `survivors_text`
- `finish_reason`
- `created_at`

## 10. AI 与提示词
- 配置来源：`config/openai.json` + `SURVIVAL_OPENAI_*`。
- 请求地址：`base_url + chat_path`。
- 模板拆分：
  - `system_prompt_file`
  - `user_prompt_file`
- 默认 `log_io=false`。

## 11. 测试建议
- 规则层：动作合法性、消耗、攻击、死亡判定。
- 时序层：一轮一次提交、全员提交后统一结算、超时自动 `REST`。
- 隐私层：玩家端不出现他人动作细节；房主视角与普通玩家一致。
- 观察层：观察端直连房主后可持续接收 `watch_event`。
- 输入层：`USE/TAKE` 同时支持中文物品名与编码（B/W/C/G/T/Q）。
- 输入规范：命令关键字统一英文（`MOVE/EXPLORE/USE/TAKE/REST/ATTACK/STATUS/HELP`）。
- 物资输入支持英文别名与编码（`BREAD/B`, `BOTTLED_WATER/W`, `BISCUIT/C`, `CANNED_FOOD/G`, `BARREL_WATER/T`, `CLEAN_WATER/Q`）。
