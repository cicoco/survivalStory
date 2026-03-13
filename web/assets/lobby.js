const byId = (id) => document.getElementById(id);

let allRooms = [];
let waitingRoomPollTimer = null;
let refreshRoomsInFlight = null;
const IDENTITY_PLAYER_KEY = "survival_identity_player_id";
const IDENTITY_ROOM_KEY = "survival_identity_room_id";

function rand4() {
  if (window.crypto && window.crypto.getRandomValues) {
    const buf = new Uint32Array(1);
    window.crypto.getRandomValues(buf);
    return (buf[0] % 10000).toString().padStart(4, "0");
  }
  return Math.floor(Math.random() * 10000)
    .toString()
    .padStart(4, "0");
}

function ensureIdentitySeeds() {
  let playerId = localStorage.getItem(IDENTITY_PLAYER_KEY);
  let roomId = localStorage.getItem(IDENTITY_ROOM_KEY);
  if (!playerId) {
    playerId = `u${rand4()}`;
    localStorage.setItem(IDENTITY_PLAYER_KEY, playerId);
  }
  if (!roomId) {
    roomId = `room-${rand4()}`;
    localStorage.setItem(IDENTITY_ROOM_KEY, roomId);
  }
  byId("playerId").value = playerId;
  byId("createRoomId").value = roomId;
}

function persistIdentitySeeds() {
  const playerId = byId("playerId").value.trim();
  const roomId = byId("createRoomId").value.trim();
  if (playerId) {
    localStorage.setItem(IDENTITY_PLAYER_KEY, playerId);
  }
  if (roomId) {
    localStorage.setItem(IDENTITY_ROOM_KEY, roomId);
  }
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

function setOutput(title, data) {
  const isErr = title.includes("失败");
  showToast(title, isErr ? "err" : "ok");
  if (data !== undefined) {
    console.log(`[Lobby] ${title}`, data);
  }
}

function currentPlayerId() {
  return byId("playerId").value.trim();
}

function roomById(roomId) {
  return allRooms.find((r) => r.room_id === roomId) || null;
}

function currentRoomForPlayer(playerId) {
  if (!playerId) return null;
  return allRooms.find((r) => r.viewer_in_room === true) || null;
}

function renderControlPanel() {
  const playerId = currentPlayerId();
  const room = currentRoomForPlayer(playerId);

  if (!room) {
    byId("controlTitle").textContent = "控制面板（创建房间）";
    byId("stateCreate").style.display = "block";
    byId("stateHost").style.display = "none";
    byId("stateMember").style.display = "none";
    return;
  }

  const isHost = room.host_player_id === playerId;
  if (isHost) {
    const isClosedLike = room.status === "CLOSED" || room.status === "DISBANDED";
    byId("controlTitle").textContent = "控制面板（房主管理）";
    byId("stateCreate").style.display = "none";
    byId("stateHost").style.display = "block";
    byId("stateMember").style.display = "none";
    byId("hostRoomId").textContent = room.room_id;
    byId("hostRoomStatus").textContent = room.status;
    byId("hostRoomCount").textContent = `${room.player_count}/${room.max_players}`;
    byId("hostStartBtn").disabled = room.status !== "WAITING";
    byId("hostEnterBtn").disabled = isClosedLike;
    byId("hostCloseBtn").textContent = "关闭房间";
    return;
  }

  if (room.status === "CLOSED" || room.status === "DISBANDED") {
    byId("controlTitle").textContent = "控制面板（创建房间）";
    byId("stateCreate").style.display = "block";
    byId("stateHost").style.display = "none";
    byId("stateMember").style.display = "none";
    return;
  }

  byId("controlTitle").textContent = "控制面板（房间状态）";
  byId("stateCreate").style.display = "none";
  byId("stateHost").style.display = "none";
  byId("stateMember").style.display = "block";
  byId("memberRoomId").textContent = room.room_id;
  byId("memberRoomStatus").textContent = room.status;
  byId("memberRoomCount").textContent = `${room.player_count}/${room.max_players}`;
  byId("memberRoomHost").textContent = room.host_player_id;
}

function filteredRooms() {
  const keyword = byId("roomSearch").value.trim().toLowerCase();
  const status = byId("roomStatusFilter").value;
  return allRooms.filter((r) => {
    if (keyword && !r.room_id.toLowerCase().includes(keyword)) return false;
    if (status && r.status !== status) return false;
    return true;
  });
}

function renderRoomList() {
  const host = byId("roomList");
  const playerId = currentPlayerId();
  const activeRoom = currentRoomForPlayer(playerId);
  const activeRoomId = activeRoom?.room_id || "";
  const isPlayerInActiveRoom =
    !!activeRoom &&
    (activeRoom.status === "WAITING" || activeRoom.status === "IN_GAME");
  const rows = filteredRooms();
  if (!rows.length) {
    host.innerHTML = "<div class='hint'>没有匹配的房间。</div>";
    return;
  }
  host.innerHTML = `
    <table class="room-table">
      <thead>
        <tr>
          <th>房间ID</th>
          <th>状态</th>
          <th>人数</th>
          <th>房主</th>
          <th>是否开局</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map((r) => {
            const selected = r.viewer_in_room === true ? "active-row" : "";
            const canJoin = typeof r.can_join === "boolean" ? r.can_join : r.joinable && !isPlayerInActiveRoom;
            const canCleanup = (r.status === "CLOSED" || r.status === "DISBANDED") && r.host_player_id === playerId;
            return `
              <tr class="${selected}">
                <td class="mono">${r.room_id}</td>
                <td>${r.status}</td>
                <td>${r.player_count}/${r.max_players}</td>
                <td class="mono">${r.host_player_id}</td>
                <td>${r.is_in_game ? "是" : "否"}</td>
                <td>
                  <div class="btn-row">
                    <button class="alt join-room-btn" data-room="${r.room_id}" ${canJoin ? "" : "disabled"}>${canJoin ? "加入房间" : "不可加入"}</button>
                    ${canCleanup ? `<button class="warn cleanup-room-btn" data-room="${r.room_id}">关闭房间</button>` : ""}
                  </div>
                </td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;

  host.querySelectorAll(".join-room-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await joinRoom(btn.dataset.room || "");
    });
  });
  host.querySelectorAll(".cleanup-room-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await cleanupRoom(btn.dataset.room || "");
    });
  });
}

async function refreshRooms() {
  if (refreshRoomsInFlight) {
    return refreshRoomsInFlight;
  }
  refreshRoomsInFlight = (async () => {
    try {
      const playerId = currentPlayerId();
      const payload = await callJson(`/rooms?player_id=${encodeURIComponent(playerId)}`, { method: "GET" });
      allRooms = payload.items || [];
      renderControlPanel();
      renderRoomList();
      syncWaitingRoomPolling();
    } catch (err) {
      setOutput("刷新房间失败", { error: String(err) });
    }
  })();
  try {
    await refreshRoomsInFlight;
  } finally {
    refreshRoomsInFlight = null;
  }
}

function isInWaitingRoomForPoll() {
  const playerId = currentPlayerId();
  const room = currentRoomForPlayer(playerId);
  if (!room) return false;
  return room.status === "WAITING";
}

function syncWaitingRoomPolling() {
  const shouldPoll = isInWaitingRoomForPoll();
  if (shouldPoll && !waitingRoomPollTimer) {
    waitingRoomPollTimer = setInterval(() => {
      if (document.hidden) return;
      refreshRooms();
    }, 3000);
    return;
  }
  if (!shouldPoll && waitingRoomPollTimer) {
    clearInterval(waitingRoomPollTimer);
    waitingRoomPollTimer = null;
  }
}

async function joinRoom(roomId) {
  const playerId = currentPlayerId();
  if (!roomId || !playerId) return;
  try {
    const payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/join`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId, is_human: true }),
    });
    setOutput("加入房间成功", payload);
    await refreshRooms();
  } catch (err) {
    setOutput("加入房间失败", { error: String(err) });
  }
}

async function cleanupRoom(roomId) {
  const playerId = currentPlayerId();
  if (!roomId || !playerId) return;
  try {
    const payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/cleanup`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId }),
    });
    setOutput("清理房间成功", payload);
    await refreshRooms();
  } catch (err) {
    setOutput("清理房间失败", { error: String(err) });
  }
}

function openGame(roomId) {
  const playerId = currentPlayerId();
  if (!roomId || !playerId) return;
  location.href = `/game?room_id=${encodeURIComponent(roomId)}&player_id=${encodeURIComponent(playerId)}`;
}

function ensureToastWrap() {
  let wrap = document.querySelector(".toast-wrap");
  if (wrap) return wrap;
  wrap = document.createElement("div");
  wrap.className = "toast-wrap";
  document.body.appendChild(wrap);
  return wrap;
}

function showToast(text, type = "ok") {
  const wrap = ensureToastWrap();
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = text;
  wrap.appendChild(node);
  setTimeout(() => {
    node.remove();
  }, 2200);
}

document.addEventListener("DOMContentLoaded", () => {
  ensureIdentitySeeds();

  byId("refreshRoomsBtn").addEventListener("click", () => refreshRooms());
  byId("roomSearch").addEventListener("input", () => renderRoomList());
  byId("roomStatusFilter").addEventListener("change", () => renderRoomList());

  byId("playerId").addEventListener("change", () => {
    persistIdentitySeeds();
    refreshRooms();
  });
  byId("createRoomId").addEventListener("change", () => {
    persistIdentitySeeds();
  });

  byId("clearStateBtn").addEventListener("click", () => {
    setOutput("已移除本地房间状态缓存，仅保留服务端状态", { player_id: currentPlayerId() });
    refreshRooms();
  });

  byId("createBtn").addEventListener("click", async () => {
    const roomId = byId("createRoomId").value.trim();
    const playerId = currentPlayerId();
    const endMode = byId("createEndMode").value;
    if (!roomId || !playerId) return;
    const existed = roomById(roomId);
    if (existed) {
      if (existed.host_player_id === playerId) {
        renderControlPanel();
        renderRoomList();
        setOutput("房间已存在，已切换到房主管理", existed);
      } else {
        setOutput("创建房间失败：房间ID已被占用", existed);
      }
      return;
    }
    try {
      const payload = await callJson("/rooms", {
        method: "POST",
        body: JSON.stringify({ room_id: roomId, host_player_id: playerId, end_mode: endMode }),
      });
      persistIdentitySeeds();
      setOutput("创建房间成功", payload);
      await refreshRooms();
    } catch (err) {
      setOutput("创建房间失败", { error: String(err) });
    }
  });

  byId("hostStartBtn").addEventListener("click", async () => {
    const roomId = currentRoomForPlayer(currentPlayerId())?.room_id || "";
    if (!roomId) return;
    try {
      const payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/start`, { method: "POST" });
      setOutput("开始对局成功", payload);
      await refreshRooms();
    } catch (err) {
      setOutput("开始对局失败", { error: String(err) });
    }
  });

  byId("hostCloseBtn").addEventListener("click", async () => {
    const playerId = currentPlayerId();
    const roomId = currentRoomForPlayer(playerId)?.room_id || "";
    if (!roomId) return;
    try {
      const room = roomById(roomId);
      let payload;
      if (room && (room.status === "CLOSED" || room.status === "DISBANDED")) {
        payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/cleanup`, {
          method: "POST",
          body: JSON.stringify({ player_id: playerId }),
        });
      } else {
        payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/leave`, {
          method: "POST",
          body: JSON.stringify({ player_id: playerId }),
        });
      }
      setOutput("关闭房间完成", payload);
      await refreshRooms();
    } catch (err) {
      setOutput("关闭房间失败", { error: String(err) });
    }
  });

  byId("hostEnterBtn").addEventListener("click", () => {
    openGame(currentRoomForPlayer(currentPlayerId())?.room_id || "");
  });

  byId("memberLeaveBtn").addEventListener("click", async () => {
    const playerId = currentPlayerId();
    const roomId = currentRoomForPlayer(playerId)?.room_id || "";
    if (!roomId) return;
    try {
      const payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/leave`, {
        method: "POST",
        body: JSON.stringify({ player_id: playerId }),
      });
      setOutput("退出房间完成", payload);
      await refreshRooms();
    } catch (err) {
      setOutput("退出房间失败", { error: String(err) });
    }
  });

  byId("memberEnterBtn").addEventListener("click", () => {
    openGame(currentRoomForPlayer(currentPlayerId())?.room_id || "");
  });

  refreshRooms();
});
