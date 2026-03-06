# 末日废墟生存战：AI开发技术设计文档（V1）

## 1. 文档目标
本设计用于支撑“先做控制台文字版、后续可扩展网页版”的AI开发，确保规则一致、接口稳定、可逐步演进。

核心目标：
- 支持多Agent（不同提示词/人格/策略参数）。
- 服务端（引擎）作为唯一规则权威，AI只做“动作建议”。
- 支持 Human-on-the-loop（人工监督/覆盖），但默认全自动运行。
- 与当前产品规则保持一致：阶段制、多动作、阶段末一次固定消耗、真人全灭即终局。

---

## 2. 规则基线（必须和实现完全一致）

### 2.1 时序与结算
- 游戏按阶段推进：白天阶段、夜晚阶段交替。
- 一个阶段内，角色可执行多次动作。
- 执行“休息”后，该角色本阶段行动结束。
- 当该阶段所有存活角色都“阶段结束”后，触发阶段结算。

### 2.2 固定消耗
- 固定消耗只在阶段结算触发，每阶段一次：
  - 水分 -1
  - 饱食 -1
- 阶段内动作仅计算动作额外消耗，不触发固定消耗。

### 2.3 终局条件
- 任一结算节点检测到“真人玩家存活数 = 0”，立即终局。
- 不要求AI全部死亡。

---

## 3. 技术栈（当前实现）
- 包管理与运行：uv
- 语言：Python 3.11+
- 运行形态：控制台单体进程 + TCP 文本联机
- 并发网络：`socketserver.ThreadingTCPServer`
- 数据模型：`dataclass`（`game/models.py`）
- 持久化：SQLite（WAL）+ `sqlite3` 标准库

说明：
- 不使用Redis。
- 单机单进程运行（保证房间状态一致性）。
- FastAPI/WebSocket 作为后续网页化阶段再引入（非当前实现）。

---

## 4. 系统架构（单体）

1. `GameEngine`：游戏总调度（阶段开始、阶段结束、终局判定）
2. `RoomState`：房间内存状态（玩家、地图、物资、阶段上下文）
3. `RuleEngine`：合法性校验 + 动作效果 + 生死判定
4. `AgentRuntime`：多Agent决策执行器（按agent配置调用LLM或本地策略）
5. `MemoryService`：记忆写入与检索（短期内存 + SQLite）
6. `HOTLService`：人工监督入口（可覆盖动作或仲裁争议）
7. `EventStore`：事件日志落库（回放、审计、调试）

约束：
- 只有`RuleEngine`可以改游戏状态。
- `AgentRuntime`不能直接写状态，只能提交动作候选。

---

## 4.1 房间与开局机制（Lobby）
- 所有玩家使用统一客户端 `survival`。
- 客户端支持大厅命令：`list / create [max_players] [max_ai] / join <room_id> / start`。
- `create` 创建房间并自动加入；创建者为房主。
- 房间最大人数由创建命令参数指定（`max_players`）。
- 房间最大AI数由创建命令参数指定（`max_ai`）。
- 仅房主可执行`start`开始游戏。
- 开始游戏时，系统按`max_players`与`max_ai`共同约束补AI：
  - AI数不超过`max_ai`
  - 总人数不超过`max_players`
- 游戏开始后房间状态置为`started`，不再允许新玩家加入当前对局。

### 联机运行模式（V1）
- `survival-broker`：房间发现中介（不参与对局结算）。
- `survival`：统一玩家客户端，通过`broker_host/broker_port/name`连接大厅。
- 房主客户端本地启动房间服务并托管对局权威结算。
- 通信协议：TCP + JSON 行协议（一行一条消息）。
- 远端玩家超时未提交动作时，服务端自动执行`rest`。
- `survival`启动时要求`--name`，该名字作为玩家身份。
- OpenAI连接参数从`config/openai.json`或`SURVIVAL_OPENAI_*`环境变量读取。
- OpenAI接口地址拆分为`base_url + chat_path`，适配不同路由前缀（如`/chat/completions`或`/v1/chat/completions`）。
- 提示词拆分为`system_prompt_file`与`user_prompt_file`，两者均支持模板关键词替换（如`{{player_name}}`、`{{water}}`、`{{current_situation}}`）。
- 游戏开始后所有终端可并发提交动作，服务端按收到顺序（first-come）结算并分配`action_seq`。

### 服务端与客户端职责
- 服务端（权威）：维护房间状态，执行规则校验与结算。
- 客户端：发送大厅命令与动作命令，不直接改状态。
- 事件广播：服务端向房间内所有客户端广播动作结果/结算结果。

### JSON 行协议（当前）
客户端 -> 服务端：
- `{"type":"list"}`
- `{"type":"create","name":"玩家A","max_players":8,"max_ai":5,"endpoint_host":"1.2.3.4","endpoint_port":10023}`
- `{"type":"join","room_id":"Z9NEGO","name":"玩家B"}`
- `{"type":"start"}`
- `{"type":"members"}`
- `{"type":"action","text":"move 2 3"}`

服务端 -> 客户端：
- `{"type":"rooms","rooms":[...]}`
- `{"type":"created",...}`
- `{"type":"joined",...}`
- `{"type":"members","members":[...]}`
- `{"type":"game_started","payload":{...}}`
- `{"type":"phase_started","payload":{...}}`
- `{"type":"state","payload":{...}}`
- `{"type":"event","message":"..."}`
- `{"type":"game_over","payload":{...}}`
- `{"type":"error","message":"..."}`

---

## 5. 当前目录（已实现）
```text
survivalStory/
  main.py
  pyproject.toml
  game/
    broker_server.py
    room_host.py
    net_client.py
    lobby.py
    cli.py
    engine.py
    models.py
    constants.py
    rules.py
    agents/
      runtime.py
    memory/
      service.py
    hotl/
      service.py
    store/
      db.py
```

---

## 6. 核心数据模型

### 6.1 RoomState（内存）
- `room_id`
- `phase`: `DAY | NIGHT`
- `phase_no`
- `players`: `list[PlayerState]`
- `map_state`
- `building_loot_state`
- `phase_status`: `ACTIVE | SETTLING | FINISHED`

### 6.2 PlayerState
- `player_id`
- `is_human: bool`
- `is_ai: bool`
- `alive: bool`
- `position: (x, y)`
- `water, food, exposure`
- `bag`
- `phase_ended: bool`（执行休息后置为 true）
- `attack_lock_take: bool`（被攻击后本阶段禁拿取）

### 6.3 Action
- `action_id`
- `phase_no`
- `player_id`
- `kind`: `MOVE | EXPLORE | USE | REST | TAKE | ATTACK`
- `payload: dict`
- `source`: `HUMAN | AI | HOTL_OVERRIDE`
- `ts`

---

## 7. 多Agent设计（支持不同提示词）

## 7.1 Agent配置表（建议）
每个AI一条配置：
- `agent_id`
- `display_name`
- `persona_prompt`
- `risk_profile`（保守/均衡/激进）
- `attack_threshold`
- `memory_window`（最近N条）
- `enabled`

## 7.2 Prompt分层（推荐固定结构）
1. `system_rules_prompt`：固定规则（共享，不因角色变化）
2. `persona_prompt`：角色策略风格（每个Agent不同）
3. `state_prompt`：当前局面（动态注入）

输出格式强制JSON：
```json
{"action":"移动(2,3)","reason":"..."}
```
建议统一英文动作名：`MOVE/EXPLORE/USE/TAKE/REST/ATTACK`。

## 7.3 Agent执行流程
1. 构造状态快照（当前阶段、位置、资源、同楼角色、建筑剩余物资）。
2. 检索记忆（短期+高重要度历史）。
3. 组装Prompt并调用模型。
4. 解析动作JSON。
5. 交给`RuleEngine`做二次合法性校验（不合法则降级策略，如休息）。

---

## 8. 记忆系统（先轻量，后扩展）

## 8.1 记忆分层
1. 短期记忆（内存）：最近N条事件（默认20）
2. 局内长期记忆（SQLite）：完整事件日志
3. 跨局画像（SQLite）：Agent统计特征（风险、攻击率、资源短缺率）

## 8.2 SQLite表建议
`event_memory`
- `id, game_id, room_id, phase_no, actor_id, event_type, payload_json, importance, created_at`

`agent_profile`
- `agent_id, games_played, attack_rate, survival_round_avg, risk_score, updated_at`

## 8.3 检索策略（V1）
- 最近事件10条
- 高重要度事件（importance>=7）最多5条
- 与当前地点/目标角色相关事件最多5条

---

## 9. Human-on-the-loop（HOTL）设计

目标：人工监督，不阻塞主流程。

## 9.1 默认模式
- 自动执行，不人工审批每步动作。

## 9.2 触发条件（建议）
- 冲突争议（同资源并发冲突且规则无法唯一判定）
- 异常动作（重复提交、可疑作弊）
- 运营干预（强制动作/强制结算）

## 9.3 处理流程
1. 生成`review_request`
2. 进入`PENDING_REVIEW`
3. 人工给出`approve/reject/override`
4. 写审计日志后恢复流程
5. 超时未处理则执行默认策略并恢复

---

## 10. 阶段主循环（伪代码）
```text
while game_not_finished:
  start_phase(DAY or NIGHT)
  mark all alive players phase_ended = false

  while exists alive player with phase_ended == false:
    receive actions from all human terminals concurrently
    process actions in arrival order (action_seq++)
    periodically let alive AI submit actions
    apply_action_via_rule_engine()
    if action == REST: phase_ended = true
    if player dead: phase_ended = true

  settle_phase_once():
    apply_fixed_cost(water-1, food-1) to alive players
    run_death_checks()
    persist_events()

  if alive_human_count == 0:
    finish_game()
  else:
    switch_phase()
```

---

## 11. 控制台交互协议（V1）

玩家输入命令：
- `move x y`
- `explore`
- `use <item>`
- `take <item1> [item2] [item3]`
- `rest`
- `attack <player_name>`
- `status`

系统每步输出：
- 当前阶段、阶段编号
- 角色状态（水/食/曝光）
- 背包
- 当前建筑物资
- 同建筑其他角色
- 本步动作结果

联机补充：
- 客户端大厅命令：`list | create [max_players] [max_ai] | join <room_id> | start | members`
- 开局后所有客户端可并发提交动作

---

## 12. 测试策略（必须先做）

1. 规则单测
- 动作合法性、消耗正确性、攻击胜负、死亡判定

2. 阶段单测
- “休息即阶段结束”
- 阶段末固定消耗仅一次
- 真人全灭即终局

3. 回放测试
- 同一事件序列回放结果一致（可复现）

---

## 13. 迭代里程碑

M1（可玩）
- 控制台房间 + 多终端联机 + N个AI补位
- 阶段机制、动作执行、终局判定

M2（可调试）
- SQLite事件日志
- 对局回放（文本）

M3（可扩展）
- 多套Agent提示词配置化
- HOTL人工覆盖接口

M4（网页化）
- 复用同一引擎，增加FastAPI页面与WebSocket

---

## 14. 非目标（V1不做）
- 多进程分布式房间调度
- 复杂交易市场/拍卖系统
- Agent间自然语言聊天协商
- 向量数据库与复杂RAG链路

---

## 15. 关键实现原则
- 规则优先于AI：AI从不直接改状态。
- 先可复现再智能化：所有关键事件可回放。
- 先轻量再扩展：SQLite + 单进程先跑通。
- 文档和提示词同源更新：规则变更必须同步到实现与Prompt。
