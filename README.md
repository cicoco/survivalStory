# 末日废墟生存战（联机版）

基于 Python 的文字生存游戏原型。  
当前版本重点验证规则引擎与多 Agent 决策，不依赖前后端分离。

## 功能概览
- 房间创建机制（房主创建房间）。
- 房间进人（真人玩家可加入房间）。
- 房主手动开始游戏（不足 6 人自动补 AI 到 6 人）。
- 支持多终端联机：
  - 房主运行服务端。
  - 其他玩家运行客户端并加入房间。
- 阶段制结算：
  - 白天/夜晚交替。
  - 阶段内可多动作。
  - `rest` 后该角色本阶段结束。
  - 阶段结束时只扣一次固定消耗（`水-1、食-1`）。
- 终局规则：真人玩家全部死亡立即结束（即使 AI 还活着）。
- SQLite 事件日志（`game.db`）。

## 运行环境
- `uv`（推荐，已用于本项目管理）
- Python 3.11+（由 `uv` 自动选择/创建环境）

## 快速启动
```bash
cd /Users/tafiagu/code/survivalStory
uv sync
uv run survival-host --name 房主A --bind 0.0.0.0 --port 9009
```

## OpenAI 配置（必须）
1. 复制示例配置：
```bash
cp config/openai.json.example config/openai.json
```
2. 编辑 `config/openai.json`，填写你的接口信息：
```json
{
  "base_url": "https://api.openai.com",
  "api_key": "sk-xxxx",
  "model": "gpt-4o-mini",
  "timeout_sec": 30,
  "system_prompt_file": "AI提示词.md",
  "user_prompt_file": "config/prompts/user_prompt.md",
  "agents_file": "config/agents.json"
}
```
3. 启动房主时可指定配置文件（可选）：
```bash
uv run survival-host --name 房主A --config config/openai.json
```

说明：
- `base_url` 不要带 `/v1`，程序会自动请求 `/v1/chat/completions`。
- `config/openai.json` 建议仅本地保存（含密钥）。
- 也支持环境变量覆盖：
  - `SURVIVAL_OPENAI_BASE_URL`
  - `SURVIVAL_OPENAI_API_KEY`
  - `SURVIVAL_OPENAI_MODEL`
  - `SURVIVAL_OPENAI_TIMEOUT_SEC`
  - `SURVIVAL_AI_SYSTEM_PROMPT_FILE`
  - `SURVIVAL_AI_USER_PROMPT_FILE`
  - `SURVIVAL_AI_AGENTS_FILE`

## 提示词模板（system + user）
现在是双模板：
- `system_prompt_file`：放稳定规则与约束（系统提示词）
- `user_prompt_file`：放当前局面状态（用户提示词）

两个模板都支持关键词替换。你可以在模板里使用这些占位符：
- `{{room_id}}`
- `{{phase_no}}`
- `{{phase}}` / `{{phase_cn}}`
- `{{player_id}}` / `{{player_name}}`
- `{{current_x}}` / `{{current_y}}`
- `{{water}}` / `{{food}}` / `{{exposure}}`
- `{{bag_text}}`
- `{{loot_text}}`
- `{{other_players}}`
- `{{persona_prompt}}`
- `{{current_situation}}`

建议：
- `system_prompt_file` 使用稳定规则、JSON输出约束、人格策略。
- `user_prompt_file` 使用实时状态（坐标、资源、背包、同楼玩家等）。

示例片段（system）：
```md
角色策略：{{persona_prompt}}
规则摘要：...
输出要求：只输出JSON
动作名统一英文：MOVE / EXPLORE / USE / TAKE / REST / ATTACK
```

示例片段（user）：
```md
你是 {{player_name}}，当前在({{current_x}},{{current_y}})，时段 {{phase_cn}}。
当前状态：水{{water}} 食{{food}} 曝光{{exposure}}。
同建筑其他玩家：{{other_players}}。
背包：{{bag_text}}
建筑物资：{{loot_text}}
当前局面补充：
{{current_situation}}
```

## 多终端联机玩法（推荐）
### 1. 房主开服务端
```bash
cd /Users/tafiagu/code/survivalStory
uv run survival-host --name 房主A --bind 0.0.0.0 --port 9009
```

房主启动后会看到：
- 房间号（`room_id`）
- 服务端监听地址和端口（默认 `0.0.0.0:9009`）
- 房主 Lobby 命令：`members | start | help`

### 2. 其他玩家加入房间（在各自终端）
```bash
cd /Users/tafiagu/code/survivalStory
uv run survival-client --host <房主IP> --port 9009 --room <房间号> --name 玩家B
```

客户端加入后会看到 `joined` 提示并等待房主开局。

### 3. 房主在 Lobby 中 `start`
房主输入 `start` 后开局。  
每个真人玩家会在各自终端收到自己的回合输入提示。

## 联机完整示例
房主终端：
```bash
uv run survival-host --name 房主A --bind 0.0.0.0 --port 9009
```

玩家B终端：
```bash
uv run survival-client --host 192.168.1.10 --port 9009 --room Z9NEGO --name 玩家B
```

玩家C终端：
```bash
uv run survival-client --host 192.168.1.10 --port 9009 --room Z9NEGO --name 玩家C
```

回到房主终端：
```text
(host-lobby)> members
(host-lobby)> start
```

## 游戏内命令
- `move x y`
- `explore`
- `use 物品名`
- `take 物品1 物品2 物品3`
- `rest`
- `attack 角色名`
- `status`
- `help`

注意：
- 非法动作会提示原因并要求重输。
- 联机模式下，远端玩家超时未输入会自动执行 `rest`。
- 联机模式下房主进程是权威服务端，必须保持在线。
- 联机模式下 `survival-host` 必须提供 `--name`，该名字会直接作为房主玩家名加入房间。
- 联机模式下 `survival-client` 也必须提供 `--name`，作为该客户端玩家名加入房间。
- 同一阶段内动作按 `action_seq` 先后生效（先到先结算）。例如同建筑同物资拿取，前序动作先扣库存，后序可能拿不到。

## 当前项目结构
```text
main.py
game/
  cli.py                # 控制台入口与交互
  lobby.py              # 房间创建/加入/开局
  engine.py             # 主循环与阶段推进
  net_host.py           # 房主服务端（多终端联机）
  net_client.py         # 远端客户端（加入房间）
  rules.py              # 规则校验与结算
  models.py             # 数据模型
  constants.py          # 地图、物资、消耗等常量
  agents/runtime.py     # AI 决策运行时（支持多 persona）
  memory/service.py     # 短期记忆
  hotl/service.py       # Human-on-the-loop 预留
  store/db.py           # SQLite 事件存储
```

## 数据与日志
- 运行后会生成 `game.db`。
- `event_log` 记录动作与阶段结算事件。
- `game_summary` 记录对局终局摘要。

## 版本说明
当前为控制台原型，优先保证规则正确和可回放。  
后续可在不改核心规则引擎的前提下接入 WebSocket 与网页前端。
