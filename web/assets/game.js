const byId = (id) => document.getElementById(id);
const qs = new URLSearchParams(location.search);

const MAP_MATRIX = [
  ["Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q"],
  ["Q", "X", "W", "X", "X", "J", "X", "J", "Q"],
  ["Q", "J", "X", "B", "B", "X", "B", "X", "Q"],
  ["Q", "X", "S", "M", "X", "W", "X", "J", "Q"],
  ["Q", "W", "X", "J", "X", "X", "W", "X", "Q"],
  ["Q", "B", "X", "X", "X", "W", "J", "J", "Q"],
  ["Q", "X", "J", "X", "X", "M", "S", "W", "Q"],
  ["Q", "X", "X", "X", "J", "B", "X", "B", "Q"],
  ["Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q"],
];

const TILE_NAMES = {
  Q: "城墙",
  X: "空地",
  J: "居民楼",
  B: "办公楼",
  S: "超市",
  W: "水井",
  M: "药店",
};

const TILE_IMAGE_URLS = {
  Q: "/assets/tiles/q.png",
  X: "/assets/tiles/x.png",
  J: "/assets/tiles/j.png",
  B: "/assets/tiles/b.png",
  S: "/assets/tiles/s.png",
  W: "/assets/tiles/w.png",
  M: "/assets/tiles/m.png",
};

const ACTION_LABELS = {
  MOVE: "移动",
  EXPLORE: "侦察",
  REST: "休整",
  ATTACK: "攻击",
  USE: "使用物品",
  TAKE: "拾取物品",
  GET: "掠夺",
  TOSS: "放弃",
};

const EVENT_LABELS = {
  GAME_STARTED: "对局开始",
  ROUND_STARTED: "回合开始",
  ACTION_ACCEPTED: "动作通过",
  ROUND_SETTLED: "回合结算",
  ACTION_REJECTED: "动作拒绝",
  LOOT_WINDOW_STARTED: "战利品窗口开启",
  LOOT_WINDOW_RESOLVED: "战利品窗口结算",
  PLAYER_LEFT: "玩家离开",
  ROOM_DISBANDED: "房间解散",
  ROOM_CLOSED: "房间关闭",
  GAME_OVER: "对局结束",
};

const END_MODE_LABELS = {
  ALL_DEAD: "全员死亡",
  HUMAN_ALL_DEAD: "真人全灭",
};

const DEATH_REASON_LABELS = {
  RESOURCE_ZERO: "资源耗尽",
  NIGHT_X_FAIL: "夜晚滞留危险区",
};

const ITEM_LABELS = {
  bottled_water: "瓶装水",
  bread: "面包",
  compressed_biscuit: "压缩饼干",
  canned_food: "罐头食品",
  barrel_water: "桶装水",
  clean_water: "净水",
};

const KEY_LABELS = {
  action_type: "动作",
  action_id: "动作ID",
  payload: "参数",
  response: "响应",
  target_id: "目标玩家",
  items: "物品",
  x: "横坐标",
  y: "纵坐标",
  event_type: "事件类型",
  result: "结果",
  result_type: "结果类型",
  choice: "选择",
  obtained: "获得物资",
  cost: "消耗",
  before: "变化前",
  after: "变化后",
  status_before: "状态前",
  status_after: "状态后",
  actions: "动作明细",
  events: "事件明细",
  winner_player_id: "胜者",
  loser_player_id: "败者",
  expires_at: "截止时间",
  is_open: "是否开启",
  player_id: "玩家",
  round: "轮次",
  phase: "阶段",
  day: "天数",
  server_seq: "序号",
  resources: "资源",
  characters: "角色",
  attack_targets: "可攻击目标",
  snapshot_updated_at: "快照更新时间",
  loser_inventory: "战败方背包",
  accepted: "提交是否成功",
  settled: "是否已结算",
  round_locked: "回合是否锁定",
};

let roomId = qs.get("room_id") || "";
let playerId = qs.get("player_id") || "";
let ws = null;
let lastSeq = 0;
let latestView = null;
let selectedTile = null;
let hasManualTileSelection = false;
let lastRoundPromptKey = "";
let lastSubmitted = null;
let lastSettlement = null;
let pendingMoveTarget = null;
let roundCountdownTimer = null;
let roundPromptRoundKey = "";
let roundPromptStartedAtMs = 0;
let submittedRoundKey = "";
let submittedActionLabel = "";
let submittedBySystem = false;
let roundPromptDeadlineAtMs = 0;
let roundPromptTimeoutSec = 90;
let wsReconnectTimer = null;
let wsHeartbeatTimer = null;
let wsReconnectAttempt = 0;
let combatPerspective = "";
let combatOpponentId = "";
let refreshViewInFlight = null;
let lastAutoRefreshRoundStartedKey = "";
let attackOutcomeModalLocked = false;
const handledCombatMessageIds = new Set();
const manuallyClosedSockets = new WeakSet();
let historyBootstrapped = false;
const IDENTITY_PLAYER_KEY = "survival_identity_player_id";
const WS_RECONNECT_BASE_DELAY_MS = 3000;
const WS_RECONNECT_MAX_DELAY_MS = 30000;
const WS_RECONNECT_JITTER_MS = 1000;
const WS_HEARTBEAT_MS = 20000;
const MAX_ITEM_SELECT_COUNT = 3;
const USE_ITEM_IDS = new Set(["bread", "bottled_water", "compressed_biscuit", "canned_food", "barrel_water", "clean_water"]);

function labelAction(actionType) {
  return ACTION_LABELS[actionType] || actionType || "未知动作";
}

function labelItem(itemType) {
  return ITEM_LABELS[itemType] || itemType;
}

function labelKey(key) {
  return KEY_LABELS[key] || key;
}

function labelEvent(eventType) {
  return EVENT_LABELS[eventType] || eventType || "事件";
}

function labelPhase(phase) {
  return phase === "DAY" || phase === "NIGHT" ? phaseLabel(phase) : phase || "-";
}

function labelEndMode(mode) {
  if (!mode) return "-";
  return END_MODE_LABELS[mode] || mode;
}

function labelDeathReason(reason) {
  if (!reason) return "存活";
  return DEATH_REASON_LABELS[reason] || reason;
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setTextIfExists(id, value) {
  const el = byId(id);
  if (!el) return;
  el.textContent = value;
}

function prettyValue(value) {
  if (value === null || value === undefined) return "无";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.join("、") : "无";
  if (typeof value === "object") return formatObjectToChinese(value, 0);
  return String(value);
}

function extractApiErrorText(data) {
  if (!data || typeof data !== "object") return "请求失败";
  if (typeof data.error === "string") return data.error;
  if (data.error && typeof data.error === "object") {
    if (typeof data.error.message === "string") return data.error.message;
    if (typeof data.error.error_code === "string") return data.error.error_code;
    return formatObjectToChinese(data.error);
  }
  if (typeof data.detail === "string") return data.detail;
  return "请求失败";
}

function parseErrorMessage(err) {
  const raw = String(err || "");
  if (!raw) return "请求失败";
  const jsonStart = raw.indexOf("{");
  if (jsonStart >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(jsonStart));
      return extractApiErrorText(parsed);
    } catch {
      return raw;
    }
  }
  return raw;
}

function explainInitError(err) {
  const msg = parseErrorMessage(err);
  if (msg.includes("room is not active")) {
    return "房间当前未开局（WAITING），请先在大厅点击“开局游戏”后再进入。";
  }
  if (msg.includes("room not found")) {
    return "房间不存在，可能已被关闭或清理。请返回大厅重新加入。";
  }
  if (msg.includes("unknown player")) {
    return "玩家不在当前房间，请返回大厅重新加入该房间。";
  }
  return `初始化失败：${msg}`;
}

function humanizeReason(reason) {
  const r = String(reason || "");
  if (!r) return "未知原因";
  if (r.includes("ATTACK target already rested in current phase")) {
    return "目标玩家本阶段已休整，不能被攻击";
  }
  if (r.includes("ATTACK target must be discovered first")) {
    return "攻击目标尚未被发现";
  }
  if (r.includes("ATTACK target must be in same tile")) {
    return "攻击目标不在同一格";
  }
  if (r.includes("player already submitted in this round")) {
    return "本轮你已提交过动作";
  }
  if (r.includes("player cannot act in current phase")) {
    return "你在当前阶段不可操作";
  }
  if (r.includes("round is locked")) {
    return "本轮已锁定，等待结算";
  }
  return r;
}

function formatItemMap(items) {
  if (!items || typeof items !== "object") return "无";
  const rows = Object.entries(items)
    .filter(([, qty]) => Number(qty) > 0)
    .map(([item, qty]) => `${labelItem(item)} x${qty}`);
  return rows.length ? rows.join("，") : "无";
}

function collectPositiveItems(items) {
  if (!items || typeof items !== "object") return [];
  return Object.entries(items).filter(([, qty]) => Number(qty) > 0);
}

function formatItemInline(items, { prefix = "", separator = " · " } = {}) {
  const rows = collectPositiveItems(items).map(([item, qty]) => `${prefix}${labelItem(item)}x${qty}`);
  return rows.length ? rows.join(separator) : "无";
}

function subtractItemMap(base, subtract) {
  const src = base && typeof base === "object" ? base : {};
  const sub = subtract && typeof subtract === "object" ? subtract : {};
  const result = {};
  for (const [item, qty] of Object.entries(src)) {
    const remain = Number(qty || 0) - Number(sub[item] || 0);
    if (remain > 0) result[item] = remain;
  }
  return result;
}

function formatObjectToChinese(obj, depth = 0) {
  if (obj === null || obj === undefined) return "无";
  if (typeof obj !== "object") return prettyValue(obj);
  const indent = "  ".repeat(depth);
  if (Array.isArray(obj)) {
    if (!obj.length) return "无";
    return obj
      .map((row) => {
        if (typeof row === "object" && row !== null) {
          return `${indent}-\n${formatObjectToChinese(row, depth + 1)}`;
        }
        return `${indent}- ${prettyValue(row)}`;
      })
      .join("\n");
  }

  const lines = [];
  for (const [key, value] of Object.entries(obj)) {
    const k = labelKey(key);
    if (key === "action_type") {
      lines.push(`${indent}${k}：${labelAction(value)}`);
      continue;
    }
    if (key === "items" || key === "obtained") {
      lines.push(`${indent}${k}：${formatItemMap(value)}`);
      continue;
    }
    if (typeof value === "object" && value !== null) {
      const nested = formatObjectToChinese(value, depth + 1);
      lines.push(`${indent}${k}：\n${nested}`);
      continue;
    }
    lines.push(`${indent}${k}：${prettyValue(value)}`);
  }
  return lines.length ? lines.join("\n") : "无";
}

function formatFeedPayload(message) {
  const eventType = String(message?.event_type || "").toUpperCase();
  const payload = message?.payload || {};
  const privatePayload = message?.private_payload || null;

  if (payload && typeof payload === "object" && "accepted" in payload && "action_id" in payload) {
    const accepted = payload.accepted === true;
    const settled = payload.settled === true;
    const locked = payload.round_locked === true;
    const lines = [
      `动作提交${accepted ? "成功" : "失败"}。`,
      settled ? "本次提交已触发并完成结算，已进入下一轮。" : "本次提交尚未触发结算，等待其他玩家。",
      `动作编号：${payload.action_id || "-"}`,
    ];
    if (!settled) {
      lines.splice(2, 0, `回合状态：${locked ? "已锁定" : "未锁定"}`);
    }
    return lines.join("\n");
  }

  if (eventType === "GAME_STARTED") {
    return `对局已开始。房间：${roomId || "-"}。`;
  }
  if (eventType === "ROUND_STARTED") {
    return `第${message.day || "-"}天 ${message.phase || "-"} 第${message.round || "-"}轮已开始，请选择动作。`;
  }
  if (eventType === "LOOT_WINDOW_STARTED") {
    return `战利品窗口开启：胜者 ${payload.winner_player_id || "-"}，败者 ${payload.loser_player_id || "-"}，请在时限内处理。`;
  }
  if (eventType === "LOOT_WINDOW_RESOLVED") {
    return `战利品结算完成：${payload.winner_player_id || "-"} 选择 ${labelAction(payload.choice || "-")}，获得 ${formatItemMap(payload.obtained || {})}`;
  }
  if (eventType === "ACTION_ACCEPTED") {
    const action = labelAction(payload.action_type);
    if (payload.auto) {
      return `系统已代为提交动作：${action}。`;
    }
    return `动作提交成功：${action}。`;
  }
  if (eventType === "ACTION_REJECTED") {
    const reason =
      payload.error_message || payload.reason || payload.error_code || formatObjectToChinese(payload);
    return `动作提交失败：${humanizeReason(reason)}`;
  }
  if (eventType === "ROUND_SETTLED") {
    if (privatePayload && typeof privatePayload === "object") {
      return formatRoundSettlementText(privatePayload);
    }
    return "本轮已完成结算。";
  }
  if (eventType === "PLAYER_LEFT") {
    return `玩家 ${payload.player_id || "-"} 已离开房间。`;
  }
  if (eventType === "ROOM_DISBANDED") {
    return `房间已被房主解散。`;
  }
  if (eventType === "ROOM_CLOSED") {
    return `房间已关闭，本局结束。`;
  }
  if (eventType === "GAME_OVER") {
    if (payload && typeof payload === "object") {
      return formatGameOverSummaryText(payload);
    }
    return "对局已结束。";
  }

  const body = message.private_payload ? { 公共信息: message.payload, 私有信息: message.private_payload } : message.payload;
  if (body && typeof body === "object") {
    return formatObjectToChinese(body);
  }
  return prettyValue(body);
}

function formatInventoryText(inv) {
  return `当前携带：${formatItemMap(inv)}`;
}

function formatSnapshotText(snapshot, infoState, timeState) {
  const hasSnapshot = snapshot && typeof snapshot === "object";
  const resources = hasSnapshot ? snapshot.resources : null;
  const characters = hasSnapshot ? snapshot.characters : null;
  const updatedAt = hasSnapshot ? snapshot.snapshot_updated_at : null;
  const explored = infoState !== "UNEXPLORED";

  const resourceText =
    resources && typeof resources === "object" && Object.keys(resources).length
      ? formatItemMap(resources)
      : "无";
  const characterText =
    Array.isArray(characters) && characters.length
      ? characters.join("、")
      : characters && typeof characters === "object" && Object.keys(characters).length
        ? formatObjectToChinese(characters)
        : "无";
  const updatedText =
    updatedAt && timeState
      ? `第${timeState.day}天 ${timeState.phase} 第${timeState.round}轮`
      : "无";

  return [
    `探索状态：${explored ? "已探索" : "未探索"}`,
    `资源：${resourceText}`,
    `角色：${characterText}`,
    `快照更新时间：${updatedText}`,
  ].join("\n");
}

function formatLootWindowText(lootWindow) {
  if (!lootWindow || !lootWindow.is_open) return "当前无战利品窗口。";
  return formatObjectToChinese(lootWindow);
}

function formatStatusBrief(status) {
  if (!status || typeof status !== "object") return "状态信息缺失";
  return `水分${status.water ?? "-"}，饱食${status.food ?? "-"}，暴露${status.exposure ?? "-"}，${
    status.alive ? "存活" : "终局"
  }`;
}

function formatActionResultText(result) {
  if (!result || typeof result !== "object") return "无结果信息";
  const type = result.result_type;
  if (type === "MOVE_RESULT") {
    const to = result.to || {};
    return `移动至(${to.x ?? "-"},${to.y ?? "-"}) ${TILE_NAMES[to.tile_type] || to.tile_type || ""}`.trim();
  }
  if (type === "EXPLORE_RESULT") {
    const snap = result.snapshot || {};
    return `侦察到资源：${formatItemMap(snap.resources)}；同格角色：${
      Array.isArray(snap.characters) && snap.characters.length ? snap.characters.join("、") : "无"
    }`;
  }
  if (type === "USE_RESULT") {
    return `使用${formatItemMap(result.used_items)}，恢复：水分+${result.gains?.water ?? 0}，饱食+${result.gains?.food ?? 0}`;
  }
  if (type === "TAKE_RESULT") {
    const obtained = result.obtained || {};
    const missing = subtractItemMap(result.requested, obtained);
    const missingCount = collectPositiveItems(missing).length;
    if (!missingCount) {
      return `拾取成功：${formatItemInline(obtained, { prefix: "+" })}`;
    }
    return `拾取：${formatItemInline(obtained, { prefix: "+" })}；未获得：${formatItemInline(missing)}`;
  }
  if (type === "REST_RESULT") {
    return "执行休整，当前阶段操作结束";
  }
  if (type === "ATTACK_RESULT") {
    const outcome = result.outcome || "UNKNOWN";
    if (outcome === "NO_TARGET") return "攻击未指定目标";
    if (outcome === "INVALID_TARGET") {
      if (result.reason === "TARGET_UNAVAILABLE") {
        return `攻击失败：${result.target_player_id || "目标"}不可锁定（已扣除攻击消耗）`;
      }
      if (result.reason === "TARGET_PHASE_ENDED") {
        return `攻击失败：${result.target_player_id || "目标"}当前不可攻击（可能已休整，已扣除攻击消耗）`;
      }
      if (result.reason === "TARGET_LEFT_TILE") {
        return `攻击失败：${result.target_player_id || "目标"}已不在当前建筑（已扣除攻击消耗）`;
      }
      return `攻击目标无效（目标：${result.target_player_id || "未知"}）`;
    }
    if (outcome === "STALEMATE") return `与${result.target_player_id || "目标"}僵持，未分胜负`;
    if (outcome === "WIN") return `对${result.target_player_id || "目标"}获胜，进入战利品窗口`;
    if (outcome === "LOSE") return `对${result.target_player_id || "目标"}失败，进入战利品窗口`;
    return `攻击结果：${outcome}`;
  }
  if (type === "LOOT_ACTION_RESULT") {
    return `战利品处理：${labelAction(result.choice)}，获得${formatItemMap(result.obtained)}`;
  }
  if (type === "INTERRUPTED") {
    return "动作被打断：你在本轮攻击对抗中落败";
  }
  return formatObjectToChinese(result);
}

function formatEventText(evt) {
  if (!evt || typeof evt !== "object") return "无事件";
  const t = evt.event_type;
  if (t === "BASE_UPKEEP") return "回合基础消耗已结算（水分/饱食）";
  if (t === "DEATH") return `角色终局，原因：${evt.reason || "未知"}`;
  return formatObjectToChinese(evt);
}

function extractLootResolutionEvent(settlement) {
  if (!settlement || typeof settlement !== "object" || !Array.isArray(settlement.events)) return null;
  return settlement.events.find((evt) => evt?.event_type === "LOOT_WINDOW_RESOLVED") || null;
}

function hasPersonalActionInSettlement(settlement) {
  return !!(settlement && Array.isArray(settlement.actions) && settlement.actions.length > 0);
}

function shouldShowSettlementPopup(message, suppressSettlementPopup) {
  if (suppressSettlementPopup) return false;
  const settlement = message?.private_payload;
  if (!settlement || typeof settlement !== "object") return false;
  if (!latestView?.self_status?.phase_ended) return true;
  return hasPersonalActionInSettlement(settlement);
}

function formatRoundSubmitText(submitted) {
  if (!submitted || typeof submitted !== "object") return "本轮尚未提交动作。";
  const actionType = submitted.action_type || "";
  const payload = submitted.payload || {};
  let detail = "";
  if (actionType === "MOVE") {
    detail = `目标坐标(${payload.x ?? "-"},${payload.y ?? "-"})`;
  } else if (actionType === "ATTACK") {
    detail = `目标玩家：${payload.target_id || "未填写"}`;
  } else if (["USE", "TAKE", "GET"].includes(actionType)) {
    detail = `物品：${formatItemMap(payload.items)}`;
  }
  const state = submitted.response?.settled ? "（已触发结算）" : "（等待结算）";
  return [
    "提交摘要",
    `- 动作：${labelAction(actionType)}`,
    detail ? `- 细节：${detail}` : null,
    `- 状态：${state.replace(/[（）]/g, "")}`,
  ]
    .filter(Boolean)
    .join("\n");
}

function formatRoundSettlementText(settlement) {
  if (!settlement || typeof settlement !== "object") return "本轮尚未结算。";
  const visibleEvents = Array.isArray(settlement.events)
    ? settlement.events.filter((evt) => evt?.event_type !== "LOOT_WINDOW_RESOLVED")
    : [];
  const parts = [];
  if (Array.isArray(settlement.actions) && settlement.actions.length) {
    parts.push("动作结果");
    settlement.actions.forEach((a, idx) => {
      parts.push(`- ${idx + 1}. ${labelAction(a.action_type)}：${formatActionResultText(a.result)}`);
    });
  } else {
    parts.push("动作结果\n- 无");
  }

  parts.push("");
  if (visibleEvents.length) {
    parts.push("事件结果");
    visibleEvents.forEach((e, idx) => {
      parts.push(`- ${idx + 1}. ${formatEventText(e)}`);
    });
  } else {
    parts.push("事件结果\n- 无");
  }

  parts.push("");
  parts.push("状态变化");
  parts.push(`- 变化前：${formatStatusBrief(settlement.status_before)}`);
  parts.push(`- 变化后：${formatStatusBrief(settlement.status_after)}`);
  return parts.join("\n");
}

function formatGameOverSummaryText(summary) {
  if (!summary || typeof summary !== "object") return "对局已结束。";
  const finalState = summary.final_time_state || {};
  const players = Array.isArray(summary.players) ? summary.players : [];
  const ranking = Array.isArray(summary.ranking) ? summary.ranking : [];

  const lines = [];
  lines.push("终局总览");
  lines.push(`- 房间：${summary.room_id || "-"}`);
  lines.push(`- 结束原因：${labelEndMode(summary.game_over_reason || summary.end_mode || "-")}`);
  lines.push(`- 终局时间：第${finalState.day || "-"}天 ${labelPhase(finalState.phase)} 第${finalState.round || "-"}轮`);
  lines.push("");
  lines.push("玩家结算");
  if (!players.length) {
    lines.push("- 无");
  } else {
    players.forEach((p) => {
      const role = p.is_human ? "真人" : "AI";
      lines.push(
        `- ${p.player_id || "-"}（${role}）：存活${p.days_survived ?? 0}天，探索${p.explored_tiles_count ?? 0}处，` +
          `搜集${p.resources_obtained_total ?? 0}件，战斗胜/负 ${p.combat_wins ?? 0}/${p.combat_losses ?? 0}，` +
          `击杀/终局 ${p.kills ?? 0}/${p.deaths ?? 0}，状态 ${labelDeathReason(p.death_reason)}`,
      );
    });
  }
  lines.push("");
  lines.push("生存排行（按存活天数）");
  if (!ranking.length) {
    lines.push("- 无");
  } else {
    ranking.forEach((row) => {
      lines.push(`- #${row.rank || "-"} ${row.player_id || "-"}：${row.days_survived ?? 0}天`);
    });
  }
  return lines.join("\n");
}

function formatGameOverSummaryHtml(summary) {
  if (!summary || typeof summary !== "object") {
    return '<div class="endgame-empty">对局已结束。</div>';
  }
  const finalState = summary.final_time_state || {};
  const players = Array.isArray(summary.players) ? summary.players : [];
  const ranking = Array.isArray(summary.ranking) ? summary.ranking : [];
  const reason = labelEndMode(summary.game_over_reason || summary.end_mode || "-");
  const finalTime = `第${finalState.day || "-"}天 ${labelPhase(finalState.phase)} 第${finalState.round || "-"}轮`;

  const overviewHtml = `
    <section class="endgame-section">
      <div class="endgame-section-title">终局总览</div>
      <div class="endgame-overview-grid">
        <div class="endgame-kv"><span>房间</span><strong>${escapeHtml(summary.room_id || "-")}</strong></div>
        <div class="endgame-kv"><span>结束原因</span><strong>${escapeHtml(reason)}</strong></div>
        <div class="endgame-kv endgame-kv-wide"><span>终局时间</span><strong>${escapeHtml(finalTime)}</strong></div>
      </div>
    </section>
  `;

  const playersHtml = players.length
    ? players
        .map((p) => {
          const role = p.is_human ? "真人" : "AI";
          return `
            <article class="endgame-player-card">
              <div class="endgame-player-head">
                <div class="endgame-player-id">${escapeHtml(p.player_id || "-")}</div>
                <span class="endgame-role-tag">${role}</span>
              </div>
              <div class="endgame-player-grid">
                <div><span>存活天数</span><strong>${p.days_survived ?? 0} 天</strong></div>
                <div><span>探索格数</span><strong>${p.explored_tiles_count ?? 0}</strong></div>
                <div><span>搜集物资</span><strong>${p.resources_obtained_total ?? 0} 件</strong></div>
                <div><span>战斗胜负</span><strong>${p.combat_wins ?? 0} / ${p.combat_losses ?? 0}</strong></div>
                <div><span>击杀终局</span><strong>${p.kills ?? 0} / ${p.deaths ?? 0}</strong></div>
                <div><span>状态</span><strong>${escapeHtml(labelDeathReason(p.death_reason))}</strong></div>
              </div>
            </article>
          `;
        })
        .join("")
    : '<div class="endgame-empty">暂无玩家数据</div>';

  const rankingHtml = ranking.length
    ? ranking
        .map(
          (row) =>
            `<div class="endgame-rank-row"><span>#${row.rank || "-"}</span><span>${escapeHtml(
              row.player_id || "-",
            )}</span><strong>${row.days_survived ?? 0} 天</strong></div>`,
        )
        .join("")
    : '<div class="endgame-empty">暂无排行</div>';

  return `
    <div class="endgame-summary">
      ${overviewHtml}
      <section class="endgame-section">
        <div class="endgame-section-title">玩家结算</div>
        <div class="endgame-player-list">${playersHtml}</div>
      </section>
      <section class="endgame-section">
        <div class="endgame-section-title">生存排行</div>
        <div class="endgame-rank-list">${rankingHtml}</div>
      </section>
    </div>
  `;
}

function openAttackOutcomeModal(text) {
  attackOutcomeModalLocked = false;
  setTextIfExists("attackOutcomeText", text);
  const section = byId("attackOutcomeLootSection");
  const submitBtn = byId("lootChoiceSubmitBtn");
  const okBtn = byId("attackOutcomeOkBtn");
  if (section) section.style.display = "none";
  if (submitBtn) submitBtn.style.display = "none";
  if (okBtn) okBtn.style.display = "inline-flex";
  const modal = byId("attackOutcomeModal");
  if (modal) {
    modal.style.display = "flex";
  }
}

function closeAttackOutcomeModal() {
  if (attackOutcomeModalLocked) return;
  const modal = byId("attackOutcomeModal");
  if (modal) {
    modal.style.display = "none";
  }
}

function openRoundSettlementModal(text, title = "本回合结算结果", options = {}) {
  const isRich = options && typeof options.html === "string" && options.html.length > 0;
  setTextIfExists("roundSettlementModalTitle", title);
  const textEl = byId("roundSettlementModalText");
  const richEl = byId("roundSettlementModalRich");
  const cardEl = document.querySelector("#roundSettlementModal .round-settlement-card");
  if (isRich) {
    if (textEl) textEl.style.display = "none";
    if (richEl) {
      richEl.style.display = "block";
      richEl.innerHTML = options.html;
    }
    cardEl?.classList.add("is-rich");
  } else {
    setTextIfExists("roundSettlementModalText", text || "暂无结算");
    if (textEl) textEl.style.display = "block";
    if (richEl) {
      richEl.style.display = "none";
      richEl.innerHTML = "";
    }
    cardEl?.classList.remove("is-rich");
  }
  const modal = byId("roundSettlementModal");
  if (modal) {
    modal.style.display = "flex";
  }
}

function closeRoundSettlementModal() {
  const modal = byId("roundSettlementModal");
  if (modal) {
    modal.style.display = "none";
  }
}

function renderLootChoicePicker() {
  const action = byId("lootChoiceAction")?.value || "GET";
  const picker = byId("lootChoiceItemsPicker");
  if (!picker) return;
  if (action === "TOSS") {
    picker.innerHTML = "已选择放弃掠夺，无需选择物品。";
    return;
  }
  const lootItems = getLootCandidateEntries();
  if (!lootItems.length) {
    picker.innerHTML = "对方背包里面没有东西，无法掠夺。";
    return;
  }
  picker.innerHTML = buildItemQuantityPickerHtml("lootChoiceItem", lootItems, MAX_ITEM_SELECT_COUNT);
  bindQuantityPickerLimit(picker, "lootChoiceItem", MAX_ITEM_SELECT_COUNT, "lootChoiceHint");
}

function openLootChoiceInOutcomeModal(opponentId) {
  attackOutcomeModalLocked = false;
  const lootItems = getLootCandidateEntries();
  const hasLoot = lootItems.length > 0;
  const loserBag = latestView?.loot_window?.loser_inventory || {};

  setTextIfExists(
    "attackOutcomeText",
    hasLoot
      ? `你在与 ${opponentId} 的战斗中获胜。可从对方背包掠夺总计不超过 ${MAX_ITEM_SELECT_COUNT} 件战利品，或选择放弃。`
      : `你在与 ${opponentId} 的战斗中获胜。对方背包里面没有东西。`,
  );
  setTextIfExists("lootChoiceOpponentInventory", formatItemMap(loserBag));
  setTextIfExists(
    "lootChoiceHint",
    hasLoot
      ? `说明：你最多可以掠夺总计 ${MAX_ITEM_SELECT_COUNT} 件战利品放入自己的背包。`
      : "说明：对方背包里面没有东西，本次只能选择放弃(TOSS)。",
  );
  const actionEl = byId("lootChoiceAction");
  if (actionEl) {
    actionEl.innerHTML = hasLoot
      ? '<option value="GET">掠夺</option><option value="TOSS">放弃</option>'
      : '<option value="TOSS">放弃</option>';
    actionEl.value = hasLoot ? "GET" : "TOSS";
  }
  renderLootChoicePicker();
  const section = byId("attackOutcomeLootSection");
  const submitBtn = byId("lootChoiceSubmitBtn");
  const okBtn = byId("attackOutcomeOkBtn");
  if (section) section.style.display = "block";
  if (submitBtn) submitBtn.style.display = "inline-flex";
  if (okBtn) okBtn.style.display = "none";
  const modal = byId("attackOutcomeModal");
  if (modal) modal.style.display = "flex";
}

function openLootPendingInOutcomeModal(opponentId) {
  attackOutcomeModalLocked = true;
  setTextIfExists("attackOutcomeText", `你在与 ${opponentId} 的战斗中失败，等待对方处理战利品。`);
  setTextIfExists("lootChoiceOpponentInventory", "待对方选择后显示结果。");
  setTextIfExists("lootChoiceHint", "提示：战利品结算期间你无法操作。");
  const section = byId("attackOutcomeLootSection");
  const submitBtn = byId("lootChoiceSubmitBtn");
  const okBtn = byId("attackOutcomeOkBtn");
  if (section) section.style.display = "none";
  if (submitBtn) submitBtn.style.display = "none";
  if (okBtn) okBtn.style.display = "none";
  const modal = byId("attackOutcomeModal");
  if (modal) modal.style.display = "flex";
}

async function submitLootChoice() {
  const action = byId("lootChoiceAction").value;
  if (action === "TOSS") {
    await submitAction("TOSS", {});
    closeAttackOutcomeModal();
    return;
  }
  if (getLootCandidateEntries().length === 0) {
    throw new Error("对方背包里面没有东西，无法掠夺。");
  }
  const items = readQuantityPickerValues("lootChoiceItem", MAX_ITEM_SELECT_COUNT);
  if (!Object.keys(items).length) {
    throw new Error("掠夺物品至少选择 1 种。");
  }
  await submitAction("GET", { items });
  closeAttackOutcomeModal();
}

function resolveIdentity() {
  const seededPlayerId = localStorage.getItem(IDENTITY_PLAYER_KEY) || "";
  playerId = playerId || seededPlayerId;
  roomId = roomId || "";
}

function setWsStatus(connected, text = "") {
  byId("wsDot").classList.toggle("live", connected);
  if (text) {
    byId("wsText").textContent = text;
    return;
  }
  byId("wsText").textContent = connected ? "已连接" : "未连接";
}

function wsCloseText(evt) {
  const reason = evt?.reason || "";
  if (reason === "room_not_found") return "房间不存在";
  if (reason === "player_not_in_room") return "玩家不在该房间";
  const code = evt?.code;
  if (code === 1000) return "连接已正常关闭";
  if (code === 1006) return "连接异常中断";
  if (code === 1008) return "连接被服务端拒绝";
  return `连接已断开(${code || "-"})`;
}

function tileName(tileType, x, y) {
  const name = TILE_NAMES[tileType] || "";
  return `(${x},${y}) ${name}`;
}

function tileLabelForMap(tileType, x, y) {
  if (tileType === "Q" || tileType === "X") {
    return "";
  }
  return TILE_NAMES[tileType] || "";
}

function phaseLabel(phase) {
  if (phase === "DAY") return "白天";
  if (phase === "NIGHT") return "黑夜";
  return phase || "未知阶段";
}

function showRoundPrompt(text, state = "pending") {
  const el = byId("roundPrompt");
  el.textContent = text;
  el.classList.remove("submitted");
  if (state === "submitted") {
    el.classList.add("submitted");
  }
  el.style.display = "block";
}

function hideRoundPrompt() {
  const el = byId("roundPrompt");
  el.style.display = "none";
}

function renderRoundPrompt() {
  if (!roundPromptRoundKey) {
    hideRoundPrompt();
    return;
  }
  const [day, phase, round] = roundPromptRoundKey.split("|");
  const selfAlive = latestView?.self_status?.alive !== false;
  const selfPhaseEnded = !!latestView?.self_status?.phase_ended;
  if (!selfAlive) {
    showRoundPrompt(`第${day}天 ${phase} 第${round}轮：您已终局，无法行动。`, "submitted");
    return;
  }
  if (selfPhaseEnded) {
    showRoundPrompt(`第${day}天 ${phase} 第${round}轮：你已休整，等待其他玩家。`, "submitted");
    return;
  }
  if (submittedRoundKey === roundPromptRoundKey) {
    const actionText = submittedActionLabel || "动作";
    if (submittedBySystem) {
      showRoundPrompt(`第${day}天 ${phase} 第${round}轮：系统已代为提交${actionText}动作，等待结算。`, "submitted");
    } else {
      showRoundPrompt(`第${day}天 ${phase} 第${round}轮：玩家已提交${actionText}动作，等待结算。`, "submitted");
    }
    return;
  }
  let leftSec = 0;
  if (roundPromptDeadlineAtMs > 0) {
    leftSec = Math.max(0, Math.ceil((roundPromptDeadlineAtMs - Date.now()) / 1000));
  } else {
    const elapsedSec = Math.max(0, Math.floor((Date.now() - roundPromptStartedAtMs) / 1000));
    leftSec = Math.max(0, roundPromptTimeoutSec - elapsedSec);
  }
  if (leftSec > 0) {
    showRoundPrompt(`第${day}天 ${phase} 第${round}轮开始，请选择动作（剩余 ${leftSec} 秒）`, "pending");
  } else {
    showRoundPrompt(`第${day}天 ${phase} 第${round}轮：操作时间已到，等待系统自动处理。`, "pending");
  }
}

function startRoundPrompt(day, phase, round, timer = null) {
  roundPromptRoundKey = `${day}|${phase}|${round}`;
  const openedAt = timer?.opened_at ? Date.parse(timer.opened_at) : NaN;
  const deadlineAt = timer?.deadline_at ? Date.parse(timer.deadline_at) : NaN;
  const timeoutSec = Number(timer?.timeout_sec);
  roundPromptStartedAtMs = Number.isFinite(openedAt) ? openedAt : Date.now();
  roundPromptDeadlineAtMs = Number.isFinite(deadlineAt) ? deadlineAt : 0;
  roundPromptTimeoutSec = Number.isFinite(timeoutSec) && timeoutSec > 0 ? Math.floor(timeoutSec) : 90;
  if (roundPromptRoundKey !== submittedRoundKey) {
    submittedRoundKey = "";
    submittedActionLabel = "";
    submittedBySystem = false;
  }
  renderRoundPrompt();
  if (roundCountdownTimer) {
    clearInterval(roundCountdownTimer);
  }
  roundCountdownTimer = setInterval(renderRoundPrompt, 1000);
}

function updateRoundResultPanel() {
  setTextIfExists("roundSubmit", formatRoundSubmitText(lastSubmitted));
  setTextIfExists("roundSettlement", formatRoundSettlementText(lastSettlement));
}

function positiveEntries(items) {
  if (!items || typeof items !== "object") return [];
  return Object.entries(items).filter(([, qty]) => Number(qty) > 0);
}

function getModalResourceEntries() {
  if (!latestView || latestView.building_info_state === "UNEXPLORED") return [];
  return positiveEntries(latestView.building_snapshot?.resources || {});
}

function getModalAttackTargets() {
  const targets = Array.isArray(latestView?.attack_targets) ? latestView.attack_targets : [];
  return targets.filter((pid) => pid && pid !== playerId);
}

function getModalUseItemEntries() {
  const inv = positiveEntries(latestView?.inventory || {});
  return inv.filter(([itemId]) => USE_ITEM_IDS.has(itemId));
}

function getLootCandidateEntries() {
  const bag = latestView?.loot_window?.loser_inventory || {};
  return positiveEntries(bag);
}

function getFilteredAllowedActions() {
  const allowedRaw = Array.isArray(latestView?.action_mask) ? latestView.action_mask : [];
  const allowed = allowedRaw.filter((action) => action !== "MOVE");
  const unexplored = latestView?.building_info_state === "UNEXPLORED";
  const useItems = getModalUseItemEntries();
  const resources = getModalResourceEntries();
  const attackTargets = getModalAttackTargets();
  const lootCandidates = getLootCandidateEntries();
  const canChooseLoot = !!latestView?.loot_window?.can_choose;

  let filtered = allowed.filter((action) => {
    if ((action === "TAKE" || action === "ATTACK") && unexplored) return false;
    if (action === "USE" && useItems.length === 0) return false;
    if (action === "TAKE" && resources.length === 0) return false;
    if (action === "ATTACK" && attackTargets.length === 0) return false;
    if ((action === "GET" || action === "TOSS") && !canChooseLoot) return false;
    if (action === "GET" && lootCandidates.length === 0) return false;
    return true;
  });

  if (!canChooseLoot) {
    filtered = filtered.filter((action) => action !== "GET" && action !== "TOSS");
  }
  return filtered;
}

function buildItemQuantityPickerHtml(name, entries, maxTotal) {
  if (!entries.length) return "<div>无可选目标</div>";
  const rows = entries
    .map(([itemId, qty]) => {
      const maxForItem = Math.max(0, Math.min(Number(qty) || 0, maxTotal));
      return (
        `<div class="selection-item">` +
        `<span>${labelItem(itemId)}（可选${qty}）</span>` +
        `<input type="number" name="${name}" data-item-id="${itemId}" min="0" max="${maxForItem}" step="1" value="0">` +
        `</div>`
      );
    })
    .join("");
  return `<div class="selection-list">${rows}</div>`;
}

function bindQuantityPickerLimit(container, name, maxTotal, hintId = "modalTargetHint") {
  const inputs = Array.from(container.querySelectorAll(`input[name="${name}"]`));
  const clampInput = (inputEl) => {
    const raw = Number(inputEl.value);
    const max = Number(inputEl.getAttribute("max") || "0");
    if (!Number.isFinite(raw) || raw < 0) {
      inputEl.value = "0";
      return;
    }
    if (raw > max) {
      inputEl.value = String(max);
    } else {
      inputEl.value = String(Math.floor(raw));
    }
  };
  const totalSelected = () => inputs.reduce((sum, node) => sum + (Number(node.value) || 0), 0);
  inputs.forEach((inputEl) => {
    inputEl.addEventListener("input", () => {
      clampInput(inputEl);
      let total = totalSelected();
      if (total > maxTotal) {
        const current = Number(inputEl.value) || 0;
        const overflow = total - maxTotal;
        inputEl.value = String(Math.max(0, current - overflow));
        total = totalSelected();
      }
      const hint = byId(hintId);
      if (hint) {
        hint.textContent = `说明：总件数最多 ${maxTotal} 件，当前已选 ${total} 件。`;
      }
    });
  });
}

function readQuantityPickerValues(name, maxTotal) {
  const inputs = Array.from(document.querySelectorAll(`input[name="${name}"]`));
  const items = {};
  let total = 0;
  inputs.forEach((node) => {
    const itemId = node.getAttribute("data-item-id") || "";
    const qty = Math.max(0, Math.floor(Number(node.value) || 0));
    if (!itemId || qty <= 0) return;
    const remaining = maxTotal - total;
    if (remaining <= 0) return;
    const takeQty = Math.min(qty, remaining);
    items[itemId] = takeQty;
    total += takeQty;
  });
  return items;
}

function renderModalTargetFields() {
  const actionType = byId("modalActionType").value;
  const targetBox = byId("modalTargetBox");
  const hint = byId("modalTargetHint");
  if (!targetBox || !hint) return;

  if (!actionType || ["REST", "EXPLORE", "TOSS"].includes(actionType)) {
    targetBox.innerHTML = "当前动作无需选择目标";
    hint.textContent = "说明：休整、侦察、放弃都不需要目标。";
    return;
  }

  if (actionType === "USE") {
    const items = getModalUseItemEntries();
    if (!items.length) {
      targetBox.innerHTML = "无可用物品";
      hint.textContent = "说明：背包里没有可用物品时，不能使用 USE。";
      return;
    }
    const options = items.map(([itemId, qty]) => `<option value="${itemId}">${labelItem(itemId)}（持有${qty}）</option>`).join("");
    targetBox.innerHTML = `<select id="modalUseItemSelect"><option value="">请选择</option>${options}</select>`;
    hint.textContent = "说明：使用物品是单选，从自己背包中选 1 种。";
    return;
  }

  if (actionType === "ATTACK") {
    const targets = getModalAttackTargets();
    if (!targets.length) {
      targetBox.innerHTML = "当前格没有可攻击目标";
      hint.textContent = "说明：攻击是单选，从当前建筑已发现角色中选 1 人。";
      return;
    }
    const options = targets.map((pid) => `<option value="${pid}">${pid}</option>`).join("");
    targetBox.innerHTML = `<select id="modalAttackTargetSelect"><option value="">请选择</option>${options}</select>`;
    hint.textContent = "说明：攻击是单选，仅可选当前建筑内角色。";
    return;
  }

  if (actionType === "TAKE") {
    const resources = getModalResourceEntries();
    targetBox.innerHTML = buildItemQuantityPickerHtml("modalTakeItem", resources, MAX_ITEM_SELECT_COUNT);
    hint.textContent = "说明：拾取按数量选择，总件数最多 3 件，来源于当前建筑资源。";
    bindQuantityPickerLimit(targetBox, "modalTakeItem", MAX_ITEM_SELECT_COUNT);
    return;
  }

  if (actionType === "GET") {
    const lootItems = getLootCandidateEntries();
    targetBox.innerHTML = buildItemQuantityPickerHtml("modalGetItem", lootItems, MAX_ITEM_SELECT_COUNT);
    hint.textContent = "说明：掠夺按数量选择，总件数最多 3 件，来源于战败方背包。";
    bindQuantityPickerLimit(targetBox, "modalGetItem", MAX_ITEM_SELECT_COUNT);
    return;
  }

  targetBox.innerHTML = "当前动作无需选择目标";
  hint.textContent = "说明：本动作无需目标。";
}

function updateModalActionOptions() {
  const allowed = getFilteredAllowedActions();
  const select = byId("modalActionType");
  const submitBtn = byId("modalSubmitBtn");
  if (!allowed.length) {
    select.innerHTML = `<option value=\"\">当前不可操作</option>`;
    byId("modalAllowedActions").textContent = "当前不可操作";
    submitBtn.disabled = true;
    renderModalTargetFields();
    return;
  }
  select.innerHTML = allowed.map((a) => `<option value=\"${a}\">${labelAction(a)}</option>`).join("");
  byId("modalAllowedActions").textContent = allowed.map(labelAction).join("、");
  submitBtn.disabled = false;
  renderModalTargetFields();
}

function openTileActionModal() {
  byId("modalTileInfo").textContent = tileName(selectedTile.tileType, selectedTile.x, selectedTile.y);
  updateModalActionOptions();
  byId("tileActionModal").style.display = "flex";
}

function closeTileActionModal() {
  byId("tileActionModal").style.display = "none";
}

function openMoveConfirmModal(x, y, tileType) {
  pendingMoveTarget = { x, y };
  byId("moveConfirmText").textContent = `确认移动到 ${tileName(tileType, x, y)} 吗？`;
  byId("moveConfirmModal").style.display = "flex";
}

function closeMoveConfirmModal() {
  byId("moveConfirmModal").style.display = "none";
  pendingMoveTarget = null;
}

function readModalPayload() {
  const actionType = byId("modalActionType").value;

  if (actionType === "MOVE") {
    return { x: Number(selectedTile.x), y: Number(selectedTile.y) };
  }

  if (actionType === "ATTACK") {
    const targetId = byId("modalAttackTargetSelect")?.value || "";
    return { target_id: targetId, loot: { type: "TOSS" } };
  }

  if (actionType === "USE") {
    const selected = byId("modalUseItemSelect")?.value || "";
    return { items: selected ? { [selected]: 1 } : {} };
  }

  if (actionType === "TAKE") {
    const items = readQuantityPickerValues("modalTakeItem", MAX_ITEM_SELECT_COUNT);
    return { items };
  }

  if (actionType === "GET") {
    const items = readQuantityPickerValues("modalGetItem", MAX_ITEM_SELECT_COUNT);
    return { items };
  }

  return {};
}

function validateModalPayload(actionType, payload) {
  if (actionType === "ATTACK" && !payload.target_id) {
    return "攻击需要选择目标玩家。";
  }
  if (actionType === "USE" && (!payload.items || !Object.keys(payload.items).length)) {
    return "使用物品需要选择 1 种物品。";
  }
  if (actionType === "TAKE" && (!payload.items || !Object.keys(payload.items).length)) {
    return "拾取物品至少选择 1 种目标资源。";
  }
  if (actionType === "GET" && (!payload.items || !Object.keys(payload.items).length)) {
    return "掠夺物品至少选择 1 种目标资源。";
  }
  return "";
}

async function callJson(url, options = {}) {
  const res = await fetch(url, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text };
  }
  if (!res.ok) {
    throw new Error(JSON.stringify(payload));
  }
  return payload;
}

function isRoundStartedEvent(message) {
  const eventType = String(message?.event_type || "").toUpperCase();
  return eventType.includes("ROUND_STARTED");
}

function isLootWindowStartedEvent(message) {
  const eventType = String(message?.event_type || "").toUpperCase();
  return eventType === "LOOT_WINDOW_STARTED";
}

function isLootWindowResolvedEvent(message) {
  const eventType = String(message?.event_type || "").toUpperCase();
  return eventType === "LOOT_WINDOW_RESOLVED";
}

function isRoundSettledEvent(message) {
  const eventType = String(message?.event_type || "").toUpperCase();
  return eventType.includes("ROUND_SETTLED");
}

function getMessageRoundKey(message) {
  const day = message?.day;
  const phase = message?.phase;
  const round = message?.round;
  if (day === undefined || phase === undefined || round === undefined) return "";
  return `${day}|${phase}|${round}`;
}

function updateRoundPromptFromEvent(message) {
  const payload = message?.payload || {};
  const timer = payload.round_timer && typeof payload.round_timer === "object" ? payload.round_timer : null;
  startRoundPrompt(message.day || "-", message.phase || "-", message.round || "-", timer);
}

function shouldSkipRoundStartedViewRefresh(message) {
  const current = latestView;
  if (!current || !current.time_state || !current.self_status) return false;
  const sameDay = Number(current.time_state.day) === Number(message?.day);
  const samePhase = String(current.time_state.phase || "") === String(message?.phase || "");
  const sameDayPhase = sameDay && samePhase;
  if (!sameDayPhase) return false;
  const phaseEnded = !!current.self_status.phase_ended;
  const alive = current.self_status.alive !== false;
  if (phaseEnded) return true;
  if (!alive) return true;
  return false;
}

function scheduleRoundStartedViewRefresh(message, { suppressViewRefresh = false } = {}) {
  if (suppressViewRefresh) return;
  const roundKey = getMessageRoundKey(message);
  if (roundKey && roundKey === lastAutoRefreshRoundStartedKey) return;
  if (roundKey) {
    lastAutoRefreshRoundStartedKey = roundKey;
  }
  if (shouldSkipRoundStartedViewRefresh(message)) return;
  refreshView().catch(() => {});
}

function isSelfTile(x, y, playerX, playerY) {
  return x === playerX && y === playerY;
}

function isOrthogonallyAdjacent(x, y, playerX, playerY) {
  return Math.abs(x - playerX) + Math.abs(y - playerY) === 1;
}

function renderMap(playerX = null, playerY = null) {
  const grid = byId("mapGrid");
  grid.innerHTML = "";

  for (let y = 1; y <= 9; y += 1) {
    for (let x = 1; x <= 9; x += 1) {
      const tileType = MAP_MATRIX[y - 1][x - 1];
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = `map-tile ${tileType.toLowerCase()}`;

      if (x === playerX && y === playerY) {
        tile.classList.add("active");
      }
      if (selectedTile && selectedTile.x === x && selectedTile.y === y) {
        tile.classList.add("selected");
      }

      const img = TILE_IMAGE_URLS[tileType];
      if (img) {
        tile.style.setProperty("--tile-image", `url(\"${img}\")`);
      }

      tile.innerHTML = `
        <div class="name">${tileLabelForMap(tileType, x, y)}</div>
      `;

      tile.addEventListener("click", () => {
        hasManualTileSelection = true;
        selectedTile = { x, y, tileType };
        renderMap(playerX, playerY);
        if (isSelfTile(x, y, playerX, playerY)) {
          openTileActionModal();
          return;
        }
        if (isOrthogonallyAdjacent(x, y, playerX, playerY)) {
          openMoveConfirmModal(x, y, tileType);
          return;
        }
      });

      grid.appendChild(tile);
    }
  }
}

function renderView(view) {
  latestView = view;
  if (!hasManualTileSelection || !selectedTile) {
    selectedTile = {
      x: view.position.x,
      y: view.position.y,
      tileType: view.position.tile_type,
    };
  }
  setTextIfExists("roomIdText", view.identity.room_id || "-");
  setTextIfExists("playerIdText", view.identity.player_id || "-");
  setTextIfExists("identity", `${view.identity.room_id} / ${view.identity.player_id}`);
  setTextIfExists("timeState", `第${view.time_state.day}天，${phaseLabel(view.time_state.phase)}，回合${view.time_state.round}`);
  setTextIfExists("position", `${view.position.x},${view.position.y}（${TILE_NAMES[view.position.tile_type] || view.position.tile_type}）`);
  setTextIfExists("infoState", view.building_info_state || "未知");
  setTextIfExists("allowedActions", (view.action_mask || []).map(labelAction).join("、") || "无");
  setTextIfExists("phaseEnded", String(view.self_status.phase_ended));
  setTextIfExists("statusWater", String(view.self_status.water));
  setTextIfExists("statusFood", String(view.self_status.food));
  setTextIfExists("statusExposure", String(view.self_status.exposure));
  setTextIfExists("statusAlive", String(view.self_status.alive));
  setTextIfExists("inventory", formatInventoryText(view.inventory));
  setTextIfExists("snapshot", formatSnapshotText(view.building_snapshot, view.building_info_state, view.time_state));
  setTextIfExists("lootWindow", formatLootWindowText(view.loot_window));

  renderMap(view.position.x, view.position.y);
  updateModalActionOptions();

  const key = `${view.time_state.day}-${view.time_state.phase}-${view.time_state.round}`;
  if (lastRoundPromptKey !== key) {
    lastRoundPromptKey = key;
    startRoundPrompt(view.time_state.day, view.time_state.phase, view.time_state.round, view.round_timer || null);
  } else if (view.round_timer?.deadline_at) {
    const nextDeadline = Date.parse(view.round_timer.deadline_at);
    if (Number.isFinite(nextDeadline)) {
      roundPromptDeadlineAtMs = nextDeadline;
      renderRoundPrompt();
    }
  }
}

function appendFeed(message, options = {}) {
  const { suppressSettlementPopup = false, suppressViewRefresh = false } = options;
  const messageId = message?.message_id;
  const isNewForUi = messageId ? !handledCombatMessageIds.has(messageId) : true;
  if (messageId) {
    handledCombatMessageIds.add(messageId);
  }
  if (message.server_seq && message.server_seq > lastSeq) {
    lastSeq = message.server_seq;
  }

  if (isRoundStartedEvent(message)) {
    updateRoundPromptFromEvent(message);
    if (isNewForUi) {
      scheduleRoundStartedViewRefresh(message, { suppressViewRefresh });
    }
    combatPerspective = "";
    combatOpponentId = "";
  }

  if (isLootWindowStartedEvent(message) && isNewForUi) {
    const payload = message.payload || {};
    const winner = payload.winner_player_id;
    const loser = payload.loser_player_id;
    if (playerId && winner && loser) {
      if (playerId === winner) {
        combatPerspective = "winner";
        combatOpponentId = loser;
        // 先刷新最新视图，确保读取到 loot_window.loser_inventory。
        refreshView()
          .catch(() => {})
          .finally(() => {
            openLootChoiceInOutcomeModal(loser);
          });
      } else if (playerId === loser) {
        combatPerspective = "loser";
        combatOpponentId = winner;
        openLootPendingInOutcomeModal(winner);
      }
    }
  }

  if (isLootWindowResolvedEvent(message) && isNewForUi) {
    const payload = message.payload || {};
    const winner = payload.winner_player_id;
    const loser = payload.loser_player_id;
    const choice = labelAction(payload.choice || "-");
    const obtained = formatItemMap(payload.obtained || {});
    if (playerId && winner && loser) {
      if (playerId === loser) {
        attackOutcomeModalLocked = false;
        openAttackOutcomeModal(`你被 ${winner} 战胜。对方选择 ${choice}，带走：${obtained}。`);
      } else if (playerId === winner) {
        attackOutcomeModalLocked = false;
        openAttackOutcomeModal(`战利品结算完成。你选择 ${choice}，获得：${obtained}。`);
      }
    }
    combatPerspective = "";
    combatOpponentId = "";
  }

  if (isRoundSettledEvent(message)) {
    lastSettlement = message.private_payload || message.payload || null;
    updateRoundResultPanel();
    if (isNewForUi && message.private_payload && extractLootResolutionEvent(message.private_payload)) {
      combatPerspective = "";
      combatOpponentId = "";
    }
    if (isNewForUi && shouldShowSettlementPopup(message, suppressSettlementPopup)) {
      const settlementText = [
        `第${message.day || "-"}天 ${message.phase || "-"} 第${message.round || "-"}轮`,
        "",
        formatRoundSettlementText(message.private_payload),
      ].join("\n");
      openRoundSettlementModal(settlementText);
    }
  }

  const eventType = String(message.event_type || "").toUpperCase();
  if (eventType === "GAME_OVER" && isNewForUi) {
    const summary = message.payload || {};
    openRoundSettlementModal(formatGameOverSummaryText(summary), "终局结算", {
      html: formatGameOverSummaryHtml(summary),
    });
  }
  if (eventType.includes("ACTION_ACCEPTED") && message.payload && message.payload.auto === true) {
    const autoRoundKey = `${message.day || "-"}|${message.phase || "-"}|${message.round || "-"}`;
    submittedRoundKey = autoRoundKey;
    submittedActionLabel = labelAction(message.payload.action_type || "REST");
    submittedBySystem = true;
    renderRoundPrompt();
  }

  const host = byId("feed");
  const item = document.createElement("div");
  item.className = "feed-item";
  const meta = `序号${message.server_seq || "-"}｜${labelEvent(message.event_type)}｜第${message.day || "-"}天 ${message.phase || "-"} 第${
    message.round || "-"
  }轮`;
  const metaEl = document.createElement("div");
  metaEl.className = "meta mono";
  metaEl.textContent = meta;
  const bodyEl = document.createElement("pre");
  bodyEl.className = "mono";
  bodyEl.textContent = formatFeedPayload(message);
  item.appendChild(metaEl);
  item.appendChild(bodyEl);
  host.prepend(item);
}

async function refreshView() {
  if (!roomId || !playerId) return;
  if (refreshViewInFlight) {
    return refreshViewInFlight;
  }
  refreshViewInFlight = (async () => {
    const view = await callJson(`/rooms/${encodeURIComponent(roomId)}/players/${encodeURIComponent(playerId)}/view`);
    renderView(view);
  })();
  try {
    await refreshViewInFlight;
  } finally {
    refreshViewInFlight = null;
  }
}

async function pullHistory() {
  if (!roomId || !playerId) return;
  const data = await callJson(
    `/rooms/${encodeURIComponent(roomId)}/players/${encodeURIComponent(playerId)}/history?last_seen_seq=${lastSeq}`,
    { method: "GET" },
  );
  const suppressPopup = !historyBootstrapped;
  let latestRoundStartedMessage = null;
  (data.items || []).forEach((row) => {
    if (isRoundStartedEvent(row)) {
      latestRoundStartedMessage = row;
    }
    appendFeed(row, { suppressSettlementPopup: suppressPopup, suppressViewRefresh: true });
  });
  if (latestRoundStartedMessage) {
    const latestRoundKey = getMessageRoundKey(latestRoundStartedMessage);
    if (!latestRoundKey || latestRoundKey !== lastAutoRefreshRoundStartedKey) {
      if (latestRoundKey) {
        lastAutoRefreshRoundStartedKey = latestRoundKey;
      }
      if (!shouldSkipRoundStartedViewRefresh(latestRoundStartedMessage)) {
        await refreshView();
      }
    }
  }
  historyBootstrapped = true;
}

async function preflightWsEligibility() {
  if (!roomId || !playerId) {
    setWsStatus(false, "缺少房间或玩家信息");
    return false;
  }
  try {
    await callJson(`/rooms/${encodeURIComponent(roomId)}/players/${encodeURIComponent(playerId)}/view`, { method: "GET" });
    return true;
  } catch (err) {
    setWsStatus(false, `无法连接：${parseErrorMessage(err)}`);
    return false;
  }
}

function clearWsReconnectTimer() {
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
}

function clearWsHeartbeat() {
  if (wsHeartbeatTimer) {
    clearInterval(wsHeartbeatTimer);
    wsHeartbeatTimer = null;
  }
}

function nextWsReconnectDelayMs() {
  const exp = Math.max(0, Math.min(wsReconnectAttempt, 4));
  const base = Math.min(WS_RECONNECT_MAX_DELAY_MS, WS_RECONNECT_BASE_DELAY_MS * 2 ** exp);
  const jitter = Math.floor(Math.random() * WS_RECONNECT_JITTER_MS);
  return base + jitter;
}

function scheduleWsReconnect() {
  if (!roomId || !playerId) return;
  clearWsReconnectTimer();
  const delay = nextWsReconnectDelayMs();
  wsReconnectAttempt += 1;
  wsReconnectTimer = setTimeout(() => {
    connectWs({ withPreflight: false }).catch(() => {});
  }, delay);
}

async function connectWs({ withPreflight = false } = {}) {
  if (!roomId || !playerId) return;
  if (withPreflight) {
    const ok = await preflightWsEligibility();
    if (!ok) return;
  }
  clearWsReconnectTimer();
  if (ws) {
    manuallyClosedSockets.add(ws);
    ws.close();
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws/${encodeURIComponent(roomId)}/${encodeURIComponent(playerId)}`;
  setWsStatus(false, "连接中");
  const socket = new WebSocket(url);
  ws = socket;
  socket.onopen = () => {
    if (ws !== socket) return;
    wsReconnectAttempt = 0;
    setWsStatus(true, "已连接");
    clearWsHeartbeat();
    wsHeartbeatTimer = setInterval(() => {
      try {
        if (ws === socket && socket.readyState === WebSocket.OPEN) {
          socket.send("ping");
        }
      } catch {
        // ignore heartbeat failures; onclose will handle reconnect
      }
    }, WS_HEARTBEAT_MS);
  };
  socket.onclose = (evt) => {
    const closedManually = manuallyClosedSockets.has(socket);
    if (closedManually) {
      manuallyClosedSockets.delete(socket);
    }
    if (ws === socket) {
      ws = null;
      clearWsHeartbeat();
    }
    if (closedManually) return;
    const closeText = wsCloseText(evt);
    setWsStatus(false, closeText);
    const shouldRetry = roomId && playerId && !(evt.code === 1008);
    if (shouldRetry) {
      scheduleWsReconnect();
    }
  };
  socket.onerror = () => {
    if (ws !== socket) return;
    setWsStatus(false, "连接异常（请检查服务端/代理WS）");
  };
  socket.onmessage = (evt) => {
    if (ws !== socket) return;
    if (evt.data === "pong") {
      return;
    }
    try {
      appendFeed(JSON.parse(evt.data), { suppressSettlementPopup: false });
    } catch {
      appendFeed({ event_type: "WS_TEXT", payload: evt.data });
    }
  };
}

async function initializeGameView() {
  if (!roomId || !playerId) {
    appendFeed({ event_type: "提示", payload: "缺少房间ID或玩家ID，请从大厅进入游戏页面。" });
    return false;
  }
  try {
    await refreshView();
  } catch (err) {
    const reason = explainInitError(err);
    appendFeed({ event_type: "初始化失败", payload: reason });
    setWsStatus(false, reason);
    return false;
  }
  try {
    await pullHistory();
  } catch (err) {
    appendFeed({ event_type: "历史拉取失败", payload: parseErrorMessage(err) });
  }
  return true;
}

function syncIdentityInputs() {
  resolveIdentity();
}

async function submitAction(actionType, payload = {}) {
  syncIdentityInputs();
  const currentRoundKey = roundPromptRoundKey;
  const [day = "-", phase = "-", round = "-"] = currentRoundKey ? currentRoundKey.split("|") : ["-", "-", "-"];
  const data = await callJson(`/rooms/${encodeURIComponent(roomId)}/actions`, {
    method: "POST",
    body: JSON.stringify({
      player_id: playerId,
      action_type: actionType,
      payload,
    }),
  });
  if (data && data.accepted === false) {
    throw new Error(extractApiErrorText(data));
  }

  lastSubmitted = { action_type: actionType, payload, response: data };
  if (currentRoundKey) {
    submittedRoundKey = currentRoundKey;
    submittedActionLabel = labelAction(actionType);
    submittedBySystem = false;
    renderRoundPrompt();
  }
  updateRoundResultPanel();

  appendFeed({ event_type: `动作提交(${actionType})`, day, phase, round, payload: data });
  await refreshView();
  await pullHistory();
}

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab-btn").forEach((x) => x.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      if (tab === "game") {
        byId("tabGame").classList.add("active");
      } else {
        byId("tabHistory").classList.add("active");
      }
    });
  });
}

function syncSidePanelHeight() {
  const layout = document.querySelector(".layout.game-stage");
  const mapPanel = document.querySelector(".map-stage");
  const sidePanel = document.querySelector(".side-stack");
  if (!layout || !mapPanel || !sidePanel) return;

  if (window.matchMedia("(max-width: 1280px)").matches) {
    sidePanel.style.height = "auto";
    sidePanel.style.maxHeight = "none";
    sidePanel.style.minHeight = "0";
    return;
  }

  const mapHeight = mapPanel.getBoundingClientRect().height;
  sidePanel.style.height = `${Math.round(mapHeight)}px`;
  sidePanel.style.maxHeight = `${Math.round(mapHeight)}px`;
  sidePanel.style.minHeight = `${Math.round(mapHeight)}px`;
}

document.addEventListener("DOMContentLoaded", () => {
  resolveIdentity();
  byId("roomIdText").textContent = roomId || "-";
  byId("playerIdText").textContent = playerId || "-";
  setWsStatus(false);
  renderMap();
  bindTabs();
  updateRoundResultPanel();
  syncSidePanelHeight();
  window.addEventListener("resize", syncSidePanelHeight);

  byId("connectBtn").addEventListener("click", () => {
    syncIdentityInputs();
    connectWs({ withPreflight: true }).catch((err) => {
      setWsStatus(false, `连接失败：${parseErrorMessage(err)}`);
    });
  });

  byId("refreshBtn").addEventListener("click", async () => {
    try {
      await refreshView();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("pullBtn").addEventListener("click", async () => {
    try {
      await pullHistory();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("historyRefreshBtn").addEventListener("click", async () => {
    try {
      await refreshView();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("historyPullBtn").addEventListener("click", async () => {
    try {
      await pullHistory();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("historyClearBtn").addEventListener("click", () => {
    byId("feed").innerHTML = "";
  });

  byId("startBtn").addEventListener("click", async () => {
    syncIdentityInputs();
    try {
      appendFeed({ event_type: "开始对局", payload: await callJson(`/rooms/${encodeURIComponent(roomId)}/start`, { method: "POST" }) });
      await refreshView();
      await pullHistory();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("tickBtn").addEventListener("click", async () => {
    syncIdentityInputs();
    try {
      appendFeed({
        event_type: "触发AI",
        payload: await callJson(`/internal/debug/rooms/${encodeURIComponent(roomId)}/tick-ai`, {
          method: "POST",
        }),
      });
      await refreshView();
      await pullHistory();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("leaveBtn").addEventListener("click", async () => {
    syncIdentityInputs();
    try {
      appendFeed({
        event_type: "离开房间",
        payload: await callJson(`/rooms/${encodeURIComponent(roomId)}/leave`, {
          method: "POST",
          body: JSON.stringify({ player_id: playerId }),
        }),
      });
      await pullHistory();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("summaryBtn").addEventListener("click", async () => {
    syncIdentityInputs();
    try {
      const payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/summary`, { method: "GET" });
      openRoundSettlementModal(formatGameOverSummaryText(payload), "终局结算", {
        html: formatGameOverSummaryHtml(payload),
      });
      appendFeed({
        event_type: "终局摘要",
        payload,
      });
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("resetBtn").addEventListener("click", async () => {
    syncIdentityInputs();
    try {
      appendFeed({
        event_type: "重置房间",
        payload: await callJson(`/rooms/${encodeURIComponent(roomId)}/reset`, {
          method: "POST",
          body: JSON.stringify({ player_id: playerId }),
        }),
      });
      await pullHistory();
    } catch (err) {
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("modalCloseBtn").addEventListener("click", closeTileActionModal);
  byId("modalActionType").addEventListener("change", renderModalTargetFields);

  byId("modalSubmitBtn").addEventListener("click", async () => {
    try {
      const actionType = byId("modalActionType").value;
      if (!actionType) {
        setTextIfExists("roundSubmit", "动作提交失败：当前回合你不可操作。");
        return;
      }
      const payload = readModalPayload();
      const reason = validateModalPayload(actionType, payload);
      if (reason) {
        setTextIfExists("roundSubmit", `动作提交失败：${reason}`);
        return;
      }
      await submitAction(actionType, payload);
      closeTileActionModal();
    } catch (err) {
      setTextIfExists("roundSubmit", `动作提交失败：${String(err)}`);
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("tileActionModal").addEventListener("click", (evt) => {
    if (evt.target === byId("tileActionModal")) {
      closeTileActionModal();
    }
  });

  byId("moveConfirmCancelBtn").addEventListener("click", closeMoveConfirmModal);
  byId("moveConfirmSubmitBtn").addEventListener("click", async () => {
    if (!pendingMoveTarget) return;
    const { x, y } = pendingMoveTarget;
    try {
      await submitAction("MOVE", { x, y });
      closeMoveConfirmModal();
    } catch (err) {
      setTextIfExists("roundSubmit", `移动提交失败：${String(err)}`);
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });

  byId("moveConfirmModal").addEventListener("click", (evt) => {
    if (evt.target === byId("moveConfirmModal")) {
      closeMoveConfirmModal();
    }
  });

  byId("attackOutcomeOkBtn").addEventListener("click", closeAttackOutcomeModal);
  byId("attackOutcomeModal").addEventListener("click", (evt) => {
    if (evt.target === byId("attackOutcomeModal")) {
      closeAttackOutcomeModal();
    }
  });

  byId("lootChoiceSubmitBtn").addEventListener("click", async () => {
    try {
      await submitLootChoice();
    } catch (err) {
      setTextIfExists("roundSubmit", `战后处理失败：${String(err)}`);
      appendFeed({ event_type: "错误", payload: String(err) });
    }
  });
  byId("lootChoiceAction").addEventListener("change", renderLootChoicePicker);

  byId("toLobbyBtn").addEventListener("click", (evt) => {
    evt.preventDefault();
    location.assign("/lobby");
  });

  byId("roundSettlementOkBtn").addEventListener("click", () => {
    closeRoundSettlementModal();
    const title = byId("roundSettlementModalTitle")?.textContent || "";
    if (title.includes("终局结算")) {
      location.href = "/lobby";
    }
  });
  byId("roundSettlementModal").addEventListener("click", (evt) => {
    if (evt.target === byId("roundSettlementModal")) {
      closeRoundSettlementModal();
    }
  });

  if (roomId && playerId) {
    initializeGameView()
      .then((ready) => {
        if (!ready) return;
        connectWs({ withPreflight: false }).catch(() => {});
      })
      .catch(() => {});
  } else {
    appendFeed({ event_type: "提示", payload: "缺少房间ID或玩家ID，请从大厅进入游戏页面。" });
  }

  const mapPanel = document.querySelector(".map-stage");
  if (window.ResizeObserver && mapPanel) {
    const ro = new ResizeObserver(() => syncSidePanelHeight());
    ro.observe(mapPanel);
  }
});
