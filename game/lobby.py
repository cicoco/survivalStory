from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from game.constants import ROOM_SIZE


@dataclass
class LobbyPlayer:
    player_id: str
    name: str
    is_host: bool = False


@dataclass
class RoomLobby:
    room_id: str
    host_player_id: str
    started: bool = False
    players: list[LobbyPlayer] = field(default_factory=list)

    def human_names(self) -> list[str]:
        return [p.name for p in self.players]


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, RoomLobby] = {}
        self._player_seq = 0

    def _next_player_id(self) -> str:
        self._player_seq += 1
        return f"u{self._player_seq}"

    def _next_room_id(self) -> str:
        while True:
            code = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))
            if code not in self.rooms:
                return code

    def create_room(self, host_name: str) -> tuple[RoomLobby, LobbyPlayer]:
        room_id = self._next_room_id()
        host = LobbyPlayer(player_id=self._next_player_id(), name=host_name, is_host=True)
        room = RoomLobby(room_id=room_id, host_player_id=host.player_id, players=[host])
        self.rooms[room_id] = room
        return room, host

    def join_room(self, room_id: str, name: str) -> LobbyPlayer:
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError("room_not_found")
        if room.started:
            raise ValueError("room_already_started")
        if len(room.players) >= ROOM_SIZE:
            raise ValueError("room_full")
        if any(p.name == name for p in room.players):
            raise ValueError("duplicate_name")
        player = LobbyPlayer(player_id=self._next_player_id(), name=name, is_host=False)
        room.players.append(player)
        return player

    def start_game(self, room_id: str, requester_player_id: str) -> RoomLobby:
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError("room_not_found")
        if room.started:
            raise ValueError("room_already_started")
        if requester_player_id != room.host_player_id:
            raise ValueError("not_host")
        room.started = True
        return room
