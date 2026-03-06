from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

@dataclass
class LobbyPlayer:
    player_id: str
    name: str
    is_host: bool = False


@dataclass
class RoomLobby:
    room_id: str
    host_player_id: str
    max_players: int
    started: bool = False
    players: list[LobbyPlayer] = field(default_factory=list)

    def human_names(self) -> list[str]:
        return [p.name for p in self.players]


class RoomManager:
    def __init__(self, max_players: int = 6) -> None:
        if max_players < 1:
            raise ValueError("max_players_must_be_positive")
        self.max_players = max_players
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

    def create_room(self, host_name: str, max_players: int | None = None) -> tuple[RoomLobby, LobbyPlayer]:
        room_max = self.max_players if max_players is None else max_players
        if room_max < 1:
            raise ValueError("max_players_must_be_positive")
        room_id = self._next_room_id()
        host = LobbyPlayer(player_id=self._next_player_id(), name=host_name, is_host=True)
        room = RoomLobby(room_id=room_id, host_player_id=host.player_id, max_players=room_max, players=[host])
        self.rooms[room_id] = room
        return room, host

    def join_room(self, room_id: str, name: str) -> LobbyPlayer:
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError("room_not_found")
        if room.started:
            raise ValueError("room_already_started")
        if len(room.players) >= room.max_players:
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
