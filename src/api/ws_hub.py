"""WebSocket hub for room notifications."""

from __future__ import annotations

from collections import defaultdict

from fastapi import WebSocket


class WsHub:
    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], set[WebSocket]] = defaultdict(set)

    async def connect(self, room_id: str, player_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[(room_id, player_id)].add(ws)

    def disconnect(self, room_id: str, player_id: str, ws: WebSocket) -> None:
        key = (room_id, player_id)
        if key not in self._connections:
            return
        self._connections[key].discard(ws)
        if not self._connections[key]:
            del self._connections[key]

    async def send_to_player(self, room_id: str, player_id: str, message: dict) -> None:
        sockets = list(self._connections.get((room_id, player_id), set()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(room_id, player_id, ws)

    async def fanout(self, room_id: str, messages: list[dict]) -> None:
        for row in messages:
            await self.send_to_player(room_id, row["player_id"], row["message"])

