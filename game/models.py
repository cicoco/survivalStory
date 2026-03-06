from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    DAY = "DAY"
    NIGHT = "NIGHT"


class ActionKind(str, Enum):
    MOVE = "MOVE"
    EXPLORE = "EXPLORE"
    USE = "USE"
    REST = "REST"
    TAKE = "TAKE"
    ATTACK = "ATTACK"


@dataclass
class Action:
    player_id: str
    kind: ActionKind
    payload: dict = field(default_factory=dict)
    source: str = "HUMAN"
    reason: str = ""


@dataclass
class PlayerState:
    player_id: str
    name: str
    is_human: bool
    alive: bool = True
    x: int = 2
    y: int = 2
    water: int = 20
    food: int = 20
    exposure: int = 0
    bag: dict[str, int] = field(default_factory=dict)
    phase_ended: bool = False
    take_locked_in_phase: bool = False
    phase_actions_used: int = 0
    explored_positions: set[tuple[int, int]] = field(default_factory=set)
    known_building_loot: dict[tuple[int, int], dict[str, int]] = field(default_factory=dict)
    survival_phases: int = 0

    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)


@dataclass
class RoomState:
    room_id: str
    phase: Phase = Phase.DAY
    phase_no: int = 1
    phase_action_seq: int = 0
    finished: bool = False
    finish_reason: str = ""
    players: list[PlayerState] = field(default_factory=list)
    building_loot: dict[tuple[int, int], dict[str, int]] = field(default_factory=dict)
