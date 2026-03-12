"""Core data models for V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.constants import (
    END_MODE_ALL_DEAD,
    INITIAL_INVENTORY,
    INITIAL_STATUS,
    PHASE_DAY,
    ROOM_STATUS_WAITING,
)


@dataclass(slots=True)
class PlayerState:
    player_id: str
    is_human: bool = True
    join_seq: int = 0
    alive: bool = True
    x: int = 4
    y: int = 4
    water: int = INITIAL_STATUS["water"]
    food: int = INITIAL_STATUS["food"]
    exposure: int = INITIAL_STATUS["exposure"]
    inventory: dict[str, int] = field(default_factory=lambda: dict(INITIAL_INVENTORY))
    phase_ended: bool = False
    explored_tiles: set[str] = field(default_factory=set)
    known_characters: set[str] = field(default_factory=set)
    building_memory: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class ActionEnvelope:
    action_id: str
    player_id: str
    day: int
    phase: str
    round: int
    action_type: str
    payload: dict[str, Any]
    join_seq: int
    server_received_at: datetime


@dataclass(slots=True)
class MatchState:
    day: int = 1
    phase: str = PHASE_DAY
    round: int = 1
    round_locked: bool = False
    round_opened_at: datetime | None = None
    phase_base_upkeep_applied: bool = False
    action_queue: list[ActionEnvelope] = field(default_factory=list)
    building_inventory: dict[str, dict[str, int]] = field(default_factory=dict)
    loot_window_state: "LootWindowState | None" = None
    pending_settlement_private_results: dict[str, dict] | None = None
    player_stats: dict[str, "PlayerMatchStats"] = field(default_factory=dict)
    pending_killers: dict[str, str] = field(default_factory=dict)
    death_seq_counter: int = 0
    endgame_summary: dict[str, Any] | None = None
    game_over: bool = False
    game_over_reason: str | None = None


@dataclass(slots=True)
class LootWindowState:
    winner_player_id: str
    loser_player_id: str
    day: int
    phase: str
    round: int
    opened_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class PlayerMatchStats:
    player_id: str
    is_human: bool
    join_seq: int
    days_survived: int = 0
    resources_obtained_total: int = 0
    resources_obtained: dict[str, int] = field(default_factory=dict)
    kills: int = 0
    deaths: int = 0
    death_reason: str | None = None
    death_day: int | None = None
    death_phase: str | None = None
    death_round: int | None = None
    death_seq: int | None = None


@dataclass(slots=True)
class Room:
    room_id: str
    host_player_id: str
    end_mode: str = END_MODE_ALL_DEAD
    status: str = ROOM_STATUS_WAITING
    join_seq_counter: int = 0
    server_seq_counter: int = 0
    players: dict[str, PlayerState] = field(default_factory=dict)
    match_state: MatchState | None = None
