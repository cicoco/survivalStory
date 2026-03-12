"""Notification and history service."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import uuid

from src.domain.models import Room


class NotificationService:
    def __init__(self, history_limit: int = 100) -> None:
        self._history_limit = history_limit
        self._history_by_room_player: dict[tuple[str, str], deque[dict]] = {}

    def publish(
        self,
        room: Room,
        event_type: str,
        payload: dict,
        *,
        private_payload_by_player: dict[str, dict] | None = None,
    ) -> list[dict]:
        match = room.match_state
        day = match.day if match else 0
        phase = match.phase if match else "UNKNOWN"
        round_no = match.round if match else 0

        room.server_seq_counter += 1
        base = {
            "message_id": str(uuid.uuid4()),
            "server_seq": room.server_seq_counter,
            "day": day,
            "phase": phase,
            "round": round_no,
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }

        emitted: list[dict] = []
        for player_id in room.players:
            message = dict(base)
            if private_payload_by_player and player_id in private_payload_by_player:
                message["private_payload"] = private_payload_by_player[player_id]
            self._append_history(room.room_id, player_id, message)
            emitted.append({"player_id": player_id, "message": message})
        return emitted

    def publish_private(
        self,
        room: Room,
        target_player_id: str,
        event_type: str,
        payload: dict,
    ) -> dict:
        match = room.match_state
        day = match.day if match else 0
        phase = match.phase if match else "UNKNOWN"
        round_no = match.round if match else 0

        room.server_seq_counter += 1
        message = {
            "message_id": str(uuid.uuid4()),
            "server_seq": room.server_seq_counter,
            "day": day,
            "phase": phase,
            "round": round_no,
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        self._append_history(room.room_id, target_player_id, message)
        return {"player_id": target_player_id, "message": message}

    def history(self, room_id: str, player_id: str, last_seen_seq: int = 0) -> list[dict]:
        key = (room_id, player_id)
        rows = list(self._history_by_room_player.get(key, deque()))
        if last_seen_seq <= 0:
            return rows
        return [row for row in rows if row["server_seq"] > last_seen_seq]

    def clear_room_history(self, room_id: str) -> None:
        keys = [key for key in self._history_by_room_player if key[0] == room_id]
        for key in keys:
            self._history_by_room_player.pop(key, None)

    def _append_history(self, room_id: str, player_id: str, message: dict) -> None:
        key = (room_id, player_id)
        if key not in self._history_by_room_player:
            self._history_by_room_player[key] = deque(maxlen=self._history_limit)
        self._history_by_room_player[key].append(message)
