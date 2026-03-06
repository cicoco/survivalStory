# 末日废墟生存战（Broker + 房主托管）

## 1. 架构总览
- `survival-broker`：中介服务，仅负责房间发现。
- `survival`：统一客户端，玩家可创建/加入/观察房间。
- 对局权威在房主本机房间服务（结算、规则判定都在房主侧）。

## 2. 快速启动
1. 启动 broker：
```bash
cd /Users/tafiagu/code/survivalStory
uv sync
uv run survival-broker --bind 0.0.0.0 --port 9010 --web-bind 0.0.0.0 --web-port 9011
```
大厅页面默认地址：`http://<broker_ip>:9011/`
页面模板文件：`game/web/lobby.html`（可直接改 UI，不影响 broker 核心逻辑）。
开发前端建议加：`--dev-web`（关闭模板缓存，浏览器刷新即可看到修改）。

2. 启动玩家客户端：
```bash
uv run survival --broker-host <broker_ip> --broker-port 9010 --name 玩家A
```
默认是网页优先模式（不依赖终端输入）。若需要终端交互可加 `--console`。
开发本地页面建议加：`--dev-web`（关闭模板缓存，刷新页面即可生效）。
客户端会默认启动本地控制 API：`http://127.0.0.1:17890`（用于网页 Join/Create/Start/状态查询绑定）。
本地接口：`GET /me`、`GET /room`、`GET /events`（SSE 事件流）、`GET /host`、`GET /game`、`POST /join`、`POST /create`、`POST /start`、`POST /leave`、`POST /action`、`POST /members`。

## 3. 客户端命令
### 3.1 大厅命令
- `list`：查看房间列表（含状态：`等待中` / `游戏中`）
- `create [max_players] [max_ai]`：创建房间并成为房主（自动加入）
- `join <room_id>`：加入房间
- `watch <room_id>`：观察房间事件（直连房主，实时推送）
- `me`：查询当前客户端状态（姓名、当前房间、房间状态）
- `help`
- `quit`

### 3.3 网页大厅 Join（绑定本地客户端）
1. 启动 broker 并打开 `http://<broker_ip>:9011/`。
2. 在本机启动 `survival` 客户端（会启动 `127.0.0.1:17890` 控制 API）。
3. 页面点击 `刷新` 手动拉取房间列表（不自动轮询）。
4. 页面中点击某个房间的 `Join`，页面会请求本地 `POST /join`。
5. 页面中点击 `Create`，会请求本地 `POST /create`（参数 `max_players/max_ai`）。
6. 页面中点击 `我的状态`，会请求本地 `GET /me`（姓名、房间、房间状态、人数信息）。
7. 页面中点击 `Start`，会请求本地 `POST /start`（仅房主可成功开局）。
8. 页面中点击 `Leave`，会请求本地 `POST /leave`（普通玩家退出，房主关闭房间/解散房间）。
9. 进入游戏页后可通过 `POST /action` 提交动作，通过 `POST /members` 请求成员信息。
10. 本地客户端收到后自动执行对应命令（`join/create/start/leave/action/members`）。
11. 游戏页包含历史记录区，最多保留 300 条事件/操作；背包与当前建筑可见物资优先使用服务端下发的结构化 `view` 字段（`bag_text/visible_loot_text`），文本解析仅作兜底。

若页面提示无法连接本地控制接口，请检查客户端是否在当前机器运行，或手动输入大厅命令 `join <room_id>`。

### 3.2 房间内命令
- `start`：开始游戏（仅房主）
- `members`：查看房间成员（仅名字列表）
- `MOVE x y`
- `EXPLORE`
- `USE item_or_code`
- `TAKE item1 item2 item3`
- `REST`
- `ATTACK player_name`
- `help`
- `leave`

说明：输入解析兼容 `(),，,`，推荐使用空格格式。  
Item aliases/codes: `B/BREAD` `W/BOTTLED_WATER` `C/BISCUIT` `G/CANNED_FOOD` `T/BARREL_WATER` `Q/CLEAN_WATER`。  
示例：`USE W`、`USE BOTTLED_WATER`、`TAKE B W`。

内部统一标识：
- 系统内部（状态、结算、库存）统一使用 canonical ID：`BREAD` `BOTTLED_WATER` `BISCUIT` `CANNED_FOOD` `BARREL_WATER` `CLEAN_WATER`。
- 对外交互（玩家输入）可使用中文/缩写/编码，系统会自动映射到 canonical ID。
- 控制台输出（状态、提示、事件）统一展示中文物资名。
- `leave` 语义：
  - 房主未开局 `leave`：解散房间（从 broker 下线）。
  - 房主已开局 `leave`：关闭房间，踢下所有人，本局立即结束。
  - 普通玩家未开局 `leave`：退出房间，可继续加入/创建其他房间。
  - 普通玩家已开局 `leave`：退出当前对局，并在该局中判定死亡。
  - 已开局房间不允许新玩家 `join`。

配置字段语义（`game/constants.py`）：
- `zh`：控制台展示中文名。
- `aliases`：外部输入可接受的所有别名（中文/英文/缩写等），系统统一映射到 canonical ID。
- `effect`：物资使用效果（`USE` 时生效）。
- `tile_code`：建筑在地图上的格子代码（仅建筑配置有，例如 `J/B/S/W/M`）。
- `storage`：建筑初始库存（仅建筑配置有，值是 canonical 物资 ID -> 数量）。

## 4. 回合与可见性规则
- 同一轮每位存活玩家最多提交 1 次动作。
- 提交后进入等待态，直到所有存活玩家提交（或超时自动 `REST`）。
- 房主服务端在全员就绪后进行一次统一结算。
- 玩家收到：
  - 自己的 `action_ack`
  - 自己的 `action_prompt`
  - 本轮 `round_settled` + 自己的最新状态快照
- 不会向玩家广播其他玩家逐动作明细。
- 房主客户端与普通玩家视图一致，不享有额外可见数据。

## 5. 观察能力（房主直推）
`watch` 流程：
1. 客户端向 broker `lookup` 房主地址。
2. 观察者直连房主并发送 `watch_join`。
3. 房主在状态变化时推送 `watch_event`。

可观察事件：
- `game_started`
- `phase_started`
- `round_action`（每位玩家含 AI 的动作）
- `round_settled`
- `phase_settle`
- `phase_settled_summary`（阶段结算后全员状态）
- `game_over`

说明：broker 不存观察事件，压力主要来自房主连接数与推送频率。

## 6. OpenAI 配置
复制并编辑：
```bash
cp config/openai.json.example config/openai.json
```

示例：
```json
{
  "base_url": "https://api.openai.com",
  "chat_path": "/chat/completions",
  "api_key": "sk-xxxx",
  "model": "gpt-4o-mini",
  "timeout_sec": 30,
  "log_io": false,
  "log_max_chars": 1200,
  "system_prompt_file": "config/prompts/system_prompt.md",
  "user_prompt_file": "config/prompts/user_prompt.md",
  "agents_file": "config/agents.json"
}
```

说明：
- 请求地址为 `base_url + chat_path`（不强制 `/v1`）。
- 默认 `log_io=false`，避免 AI 日志刷屏。
- 房主执行 `create` 时读取此配置用于 AI 决策。

环境变量（可选）：
- `SURVIVAL_OPENAI_BASE_URL`
- `SURVIVAL_OPENAI_CHAT_PATH`
- `SURVIVAL_OPENAI_API_KEY`
- `SURVIVAL_OPENAI_MODEL`
- `SURVIVAL_OPENAI_TIMEOUT_SEC`
- `SURVIVAL_OPENAI_LOG_IO`
- `SURVIVAL_OPENAI_LOG_MAX_CHARS`

## 7. 常用参数
- `survival-broker`
  - `--bind`：Broker TCP 服务监听地址（房间发现协议）。
  - `--port`：Broker TCP 端口。
  - `--web-bind`：大厅网页监听地址。
  - `--web-port`：大厅网页端口；`<=0` 关闭网页服务。
  - `--dev-web`：开发模式，关闭大厅模板缓存，改页面后浏览器刷新即生效。
- `survival`
  - `--broker-host`：Broker 地址（必填）。
  - `--broker-port`：Broker 端口（默认 `9010`）。
  - `--name`：玩家名称（必填）。
  - `--public-host`：创建房间时向 Broker 注册的可达地址（局域网/公网 IP 或域名）。
  - `--room-bind`：本机房间服务监听地址（房主模式）。
  - `--room-port`：本机房间服务端口；`0` 自动分配。
  - `--config`：OpenAI 配置文件路径（房主创建房间时使用）。
  - `--ai-interval-ms`：AI 行动间隔毫秒。
  - `--human-timeout-sec`：真人动作超时秒数（超时自动处理）。
  - `--control-bind`：本地控制 API 监听地址（网页会调用）。
  - `--control-port`：本地控制 API 端口；同机多开客户端必须使用不同端口。
  - `--dev-web`：开发模式，关闭本地页面模板缓存（`/game`、`/host`）。
  - `--console`：开启终端交互模式；默认是网页优先模式。

### 7.1 推荐启动示例
1. 本机开发（Broker + 网页大厅）：
```bash
uv run survival-broker --bind 0.0.0.0 --port 9010 --web-bind 0.0.0.0 --web-port 9011 --dev-web
```

2. 玩家 A（网页优先）：
```bash
uv run survival --broker-host 127.0.0.1 --broker-port 9010 --name A --control-port 17890 --dev-web
```

3. 同机玩家 B（端口必须不同）：
```bash
uv run survival --broker-host 127.0.0.1 --broker-port 9010 --name B --control-port 17891 --dev-web
```

## 8. SQLite 字段说明
数据库：`game.db`

### `event_log`
- `id`：自增主键
- `game_id`：单局 ID
- `room_id`：房间 ID
- `phase_no`：阶段序号
- `phase`：`DAY` / `NIGHT`
- `action_seq`：阶段内动作序号
- `actor_id`：动作发起者（结算事件可空）
- `event_type`：事件类型
- `payload_json`：事件详情 JSON
- `created_at`：写入时间

### `game_summary`
- `id`：自增主键
- `game_id`：单局 ID
- `room_id`：房间 ID
- `survivors_text`：幸存者列表（逗号分隔）
- `finish_reason`：终局原因
- `created_at`：写入时间
