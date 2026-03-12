"""Core data models for V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.constants import INITIAL_INVENTORY, INITIAL_STATUS


@dataclass(slots=True)
class PlayerState:
    player_id: str
    is_human: bool = True
    alive: bool = True
    x: int = 5
    y: int = 5
    water: int = INITIAL_STATUS["water"]
    food: int = INITIAL_STATUS["food"]
    exposure: int = INITIAL_STATUS["exposure"]
    inventory: dict[str, int] = field(default_factory=lambda: dict(INITIAL_INVENTORY))
    phase_ended: bool = False


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
