# 末日废墟生存战（Broker + P2P 房主托管）

## 架构
- `survival-broker`：中介服务，仅做房间发现（`create/list/lookup/heartbeat`），不做对局结算。
- `survival`：统一玩家客户端。任意玩家都可 `create` 成为房主并在本机托管房间服务；其他玩家 `list/join` 后直连房主。
- 对局权威在房主本机房间服务（不是 broker）。

## 快速启动
1. 启动 broker（任意一台可达机器）：
```bash
cd /Users/tafiagu/code/survivalStory
uv sync
uv run survival-broker --bind 0.0.0.0 --port 9010
```

2. 每个玩家启动统一客户端：
```bash
uv run survival --broker-host <broker_ip> --broker-port 9010 --name 玩家A
```

## 客户端大厅命令
- `list`：查看房间列表
- `create [max_players] [max_ai]`：创建房间并成为房主（本机自动启动房间服务并注册到 broker）
- `join <room_id>`：通过 broker 查询房主地址并直连加入
- `quit`：退出

## 对局命令
- 房主/玩家都一样：`MOVE/EXPLORE/USE/TAKE/REST/ATTACK`（兼容中文旧命令）
- 只有房主在房间连接后可 `start`
- `members`：查看当前房间成员信息（房主与玩家都可使用）

## 关键行为
- 房间最大人数由 `create [max_players]` 指定。
- 房间最大 AI 数量由 `create [max_players] [max_ai]` 指定。
- 开局时会按 `max_ai` 上限补 AI，不会无限补满。
- 开局后所有终端可并发提交动作，服务端按先到顺序（`action_seq`）结算。
- 人类玩家超时或离线会自动 `REST`（默认 120 秒）。

## Broker 注册字段（create）
房主客户端执行 `create` 时会向 broker 注册：
- `host_name`：房主玩家名
- `max_players`：房间最大人数
- `max_ai`：房间最大 AI 数
- `endpoint_host`：房主房间服务可达地址
- `endpoint_port`：房主房间服务端口

其他玩家 `list/lookup` 后可发现这些房间并 `join`。

## OpenAI 配置
1. 复制配置：
```bash
cp config/openai.json.example config/openai.json
```
2. 编辑 `config/openai.json`：
```json
{
  "base_url": "https://api.openai.com",
  "chat_path": "/chat/completions",
  "api_key": "sk-xxxx",
  "model": "gpt-4o-mini",
  "timeout_sec": 30,
  "system_prompt_file": "config/prompts/system_prompt.md",
  "user_prompt_file": "config/prompts/user_prompt.md",
  "agents_file": "config/agents.json"
}
```

说明：
- `base_url + chat_path` 组合请求地址，适配不同 router（不强制 `/v1`）。
- 房主客户端在 `create` 时会读取该配置并用于 AI 决策。

## 常用参数
- `survival-broker`：
  - `--bind`、`--port`
- `survival`：
  - `--broker-host`、`--broker-port`、`--name`
  - `--public-host`（房间注册给他人的可达地址）
  - `--room-bind`、`--room-port`（本机房间服务监听）
  - `--config`、`--ai-interval-ms`、`--human-timeout-sec`
