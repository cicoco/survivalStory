"""API/event-level constants."""

from __future__ import annotations

from typing import Final

EVENT_GAME_STARTED: Final[str] = "GAME_STARTED"
EVENT_ROUND_STARTED: Final[str] = "ROUND_STARTED"
EVENT_ACTION_ACCEPTED: Final[str] = "ACTION_ACCEPTED"
EVENT_ROUND_SETTLED: Final[str] = "ROUND_SETTLED"
EVENT_ACTION_REJECTED: Final[str] = "ACTION_REJECTED"
EVENT_PLAYER_LEFT: Final[str] = "PLAYER_LEFT"
EVENT_ROOM_DISBANDED: Final[str] = "ROOM_DISBANDED"
EVENT_ROOM_CLOSED: Final[str] = "ROOM_CLOSED"
EVENT_GAME_OVER: Final[str] = "GAME_OVER"

SCHEMA_ACTION_REJECTED_V1: Final[str] = "action_rejected_v1"
SCHEMA_ROUND_SETTLED_PRIVATE_V1: Final[str] = "round_settled_private_v1"
SCHEMA_GAME_OVER_SUMMARY_V1: Final[str] = "game_over_summary_v1"

ERROR_ACTION_INVALID: Final[str] = "ACTION_INVALID"
ERROR_UNKNOWN_PLAYER: Final[str] = "UNKNOWN_PLAYER"
ERROR_PHASE_ENDED: Final[str] = "PHASE_ENDED"
ERROR_ROUND_LOCKED: Final[str] = "ROUND_LOCKED"
ERROR_ALREADY_SUBMITTED: Final[str] = "ALREADY_SUBMITTED"
ERROR_MOVE_OUT_OF_BOUNDS: Final[str] = "MOVE_OUT_OF_BOUNDS"
ERROR_MOVE_NOT_ADJACENT: Final[str] = "MOVE_NOT_ADJACENT"
ERROR_ATTACK_TARGET_NOT_DISCOVERED: Final[str] = "ATTACK_TARGET_NOT_DISCOVERED"
ERROR_ACTION_NOT_ALLOWED_ON_TILE: Final[str] = "ACTION_NOT_ALLOWED_ON_TILE"
ERROR_LOOT_WINDOW_NOT_OPEN: Final[str] = "LOOT_WINDOW_NOT_OPEN"
ERROR_LOOT_WINDOW_ONLY_WINNER: Final[str] = "LOOT_WINDOW_ONLY_WINNER"
ERROR_LOOT_WINDOW_ACTION_INVALID: Final[str] = "LOOT_WINDOW_ACTION_INVALID"
ERROR_MATCH_NOT_OVER: Final[str] = "MATCH_NOT_OVER"
ERROR_ONLY_HOST_CAN_RESET: Final[str] = "ONLY_HOST_CAN_RESET"

ROUND_SETTLED_MESSAGE: Final[str] = "round settled"
ROUND_STARTED_PROMPT_SUFFIX: Final[str] = "choose action"
LOOT_WINDOW_STARTED_MESSAGE: Final[str] = "loot window opened, winner choose GET/TOSS"


def build_game_started_message(room_id: str, player_count: int) -> str:
    return f"room={room_id} players={player_count} started"


def build_round_started_message(day: int, phase: str, round_no: int) -> str:
    return f"day={day} phase={phase} round={round_no} {ROUND_STARTED_PROMPT_SUFFIX}"
