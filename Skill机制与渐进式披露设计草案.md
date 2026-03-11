# Skill机制与渐进式披露设计草案

## 1. 文档目标
- 为当前生存游戏的 AI 决策系统定义可落地的 `skill` 机制。
- 明确“三级渐进式披露”加载策略，控制上下文成本并提高决策稳定性。
- 定义“受限代理循环”以处理动作拒绝、信息不足等异常情况。
- 本文档为方案草案，不涉及本次代码改造。

## 2. 当前系统背景（简述）
- Broker 负责房间发现与信令，不承载对局权威。
- 房主进程（host）负责规则结算与对局状态推进。
- AI 通过 OpenAI 接口按阶段提交动作。
- 已有提示词模板与 canonical code（动作、物资、建筑）体系。

## 3. 设计原则
- 规则优先：任何 AI 决策不得绕过服务端规则校验。
- 小步增量：先引入最小 skill 集，保证可观测、可回滚。
- 成本可控：默认最小上下文，按需升级披露级别。
- 失败可恢复：动作被拒绝时，允许有限次修正而非无限循环。
- 输出一致性：内部统一 code，控制台继续中文渲染。

## 4. Skill 机制设计

### 4.1 Skill 定位
- Skill 不是 OpenAI API 原生参数。
- Skill 是“决策前后处理插件层”，运行在游戏服务端代码内。
- Skill 产出结构化 hints / constraints，注入到 LLM user prompt 或后处理校验。

### 4.2 Skill 生命周期
1. `collect`：读取当前状态、记忆、阶段信息。
2. `pre_decide`：产出建议与硬约束（hints/guards）。
3. `llm_decide`：LLM 生成候选动作。
4. `post_validate`：动作合法性与风险校验。
5. `retry_or_finalize`：失败则有限重试，成功则提交。

### 4.3 Skill 接口（建议）
```text
Skill {
  name: str
  priority: int
  enabled: bool
  pre_decide(context) -> SkillHint
  post_validate(context, action) -> ValidationResult
}
```

### 4.4 建议首批 Skill（MVP）
- `survival_guard`：即时死亡风险约束（资源/阶段/位置）。
- `loot_memory`：建筑库存可见性与记忆可信度管理（含 UNKNOWN）。
- `action_validator`：动作语法/参数/code/规则前置校验。

## 5. 三级渐进式披露（Progressive Disclosure）

### 5.1 Level-1 全局摘要（默认）
- 输入内容：角色状态、phase、位置、已知库存摘要、可用动作约束。
- 目标：低 token 成本下完成大多数决策。

### 5.2 Level-2 领域细节（按触发）
- 触发示例：
  - 当前建筑库存为 `UNKNOWN`。
  - 计划跨建筑移动。
  - 存在攻击候选且收益不明确。
- 输入内容：目标 skill 的结构化细节（记忆条目、局部路径、风险评分）。

### 5.3 Level-3 证据明细（兜底）
- 触发示例：
  - 连续动作被拒绝。
  - 关键信息冲突（记忆与当前结果不一致）。
- 输入内容：近期事件日志摘要、关键冲突证据、最近失败原因。

### 5.4 升级规则
- 默认从 L1 开始。
- 若 `post_validate` 失败或“不确定性高”，升至 L2。
- 若 L2 仍失败，再升至 L3。
- 每次升级必须记录 `upgrade_reason`（便于调试与复盘）。

## 6. 受限代理循环（Agent Loop）

### 6.1 目标
- 在不牺牲实时性的前提下，提升动作一次通过率。

### 6.2 默认循环
1. 组装上下文（L1/L2/L3）。
2. 调用 LLM 产出候选动作。
3. `action_validator` 校验。
4. 失败则按规则升级并重试。

### 6.3 硬限制（建议默认）
- 最大重试次数：`2`（即总尝试最多 3 次）。
- 最大披露级别：`L3`。
- 超时预算：单次决策不超过配置阈值（例如 2~4 秒）。
- 超限兜底：输出保守动作（通常 `REST` 或安全 MOVE）。

### 6.4 触发重试条件
- 动作语法不合法。
- 参数不合法（code、坐标、目标不存在）。
- 规则冲突（不可进入、不可探索、资源不足）。
- 信息不足（例如 `UNKNOWN` 且风险偏高）。

## 7. 配置设计（建议）

### 7.1 全局配置（新增建议）
- `skill_enabled`: 是否开启 skill 管线。
- `max_retries`: 最大重试次数。
- `disclosure_max_level`: 最大披露层级。
- `decision_timeout_ms`: 单次决策超时。
- `fallback_action`: 超限兜底动作。

### 7.2 角色级配置
- 不同 AI 角色可启用不同 skill 组合与权重。
- 不同 AI 角色可绑定不同系统提示词模板。

## 8. 可观测性与日志
- 记录项建议：
  - `attempt_no`
  - `disclosure_level`
  - `enabled_skills`
  - `validation_errors`
  - `upgrade_reason`
  - `final_action`
- 生产默认记录摘要日志；详细日志建议可开关。

## 9. 风险与权衡
- 风险：上下文升级可能增加 token 成本与延迟。
- 风险：过多 skill 可能导致规则冲突或提示词冗余。
- 权衡：先做 3 个 MVP skill，确认收益后再扩展攻击/交易等领域 skill。

## 10. 分阶段实施建议

### Phase A（最小可用）
- 接入 `survival_guard + loot_memory + action_validator`。
- 上线 L1/L2 披露，不启用 L3。
- 重试上限 1（总尝试 2 次）。

### Phase B（稳定性增强）
- 启用 L3。
- 完整重试上限 2。
- 增加失败原因统计面板/日志分析。

### Phase C（策略多样化）
- 角色化 skill 组合。
- 攻击/交易专项 skill。
- A/B 对比不同 skill 策略收益。

## 11. 待讨论问题（你后续可逐条拍板）
- `fallback_action` 统一用 `REST`，还是按阶段动态选择？
- L2/L3 的具体 token 预算上限如何定？
- `loot_memory` 的过期判定窗口（按轮次还是按 phase）？
- 是否需要将关键决策日志写入数据库（用于复盘与调优）？

