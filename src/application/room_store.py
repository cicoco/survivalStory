"""In-memory room store."""

from __future__ import annotations

from src.domain.models import Room


class RoomStore:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def add(self, room: Room) -> None:
        if room.room_id in self._rooms:
            raise ValueError(f"room already exists: {room.room_id}")
        self._rooms[room.room_id] = room

    def get(self, room_id: str) -> Room:
        room = self._rooms.get(room_id)
        if room is None:
            raise ValueError(f"room not found: {room_id}")
        return room

    def remove(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)
