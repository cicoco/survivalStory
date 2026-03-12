const byId = (id) => document.getElementById(id);

function saveSession(roomId, playerId) {
  const raw = localStorage.getItem("survival_sessions");
  const rows = raw ? JSON.parse(raw) : [];
  const next = [{ roomId, playerId }, ...rows.filter((r) => !(r.roomId === roomId && r.playerId === playerId))];
  localStorage.setItem("survival_sessions", JSON.stringify(next.slice(0, 8)));
}

function renderSessions() {
  const host = byId("recentSessions");
  const raw = localStorage.getItem("survival_sessions");
  const rows = raw ? JSON.parse(raw) : [];
  if (!rows.length) {
    host.innerHTML = "<div class='hint'>No recent sessions.</div>";
    return;
  }
  host.innerHTML = rows
    .map(
      (row) => `
      <div class="feed-item">
        <div class="meta mono">${row.roomId} / ${row.playerId}</div>
        <div class="btn-row">
          <button class="alt" data-room="${row.roomId}" data-player="${row.playerId}">Enter Game</button>
        </div>
      </div>
    `,
    )
    .join("");
  host.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const roomId = btn.dataset.room;
      const playerId = btn.dataset.player;
      location.href = `/game?room_id=${encodeURIComponent(roomId)}&player_id=${encodeURIComponent(playerId)}`;
    });
  });
}

async function callJson(url, options) {
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
  byId("outputTitle").textContent = title;
  byId("outputBody").textContent = JSON.stringify(data, null, 2);
}

document.addEventListener("DOMContentLoaded", () => {
  renderSessions();

  byId("createBtn").addEventListener("click", async () => {
    const roomId = byId("createRoomId").value.trim();
    const hostId = byId("createHostId").value.trim();
    const endMode = byId("createEndMode").value;
    if (!roomId || !hostId) return;
    try {
      const payload = await callJson("/rooms", {
        method: "POST",
        body: JSON.stringify({ room_id: roomId, host_player_id: hostId, end_mode: endMode }),
      });
      saveSession(roomId, hostId);
      renderSessions();
      setOutput("Room Created", payload);
    } catch (err) {
      setOutput("Create Failed", { error: String(err) });
    }
  });

  byId("joinBtn").addEventListener("click", async () => {
    const roomId = byId("joinRoomId").value.trim();
    const playerId = byId("joinPlayerId").value.trim();
    const isHuman = byId("joinIsHuman").checked;
    if (!roomId || !playerId) return;
    try {
      const payload = await callJson(`/rooms/${encodeURIComponent(roomId)}/join`, {
        method: "POST",
        body: JSON.stringify({ player_id: playerId, is_human: isHuman }),
      });
      saveSession(roomId, playerId);
      renderSessions();
      setOutput("Joined", payload);
    } catch (err) {
      setOutput("Join Failed", { error: String(err) });
    }
  });

  byId("enterBtn").addEventListener("click", () => {
    const roomId = byId("enterRoomId").value.trim();
    const playerId = byId("enterPlayerId").value.trim();
    if (!roomId || !playerId) return;
    saveSession(roomId, playerId);
    location.href = `/game?room_id=${encodeURIComponent(roomId)}&player_id=${encodeURIComponent(playerId)}`;
  });
});
