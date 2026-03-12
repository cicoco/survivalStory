"""Rule helpers for Phase 1."""

from __future__ import annotations

from src.domain.constants import (
    ACTION_COSTS,
    ITEM_EFFECTS,
    MAX_STATUS,
    PHASE_DAY,
    PHASE_NIGHT,
    TILE_Q,
    TILE_X,
)
from src.domain.models import PlayerState


def clamp(value: int, low: int = 0, high: int = MAX_STATUS) -> int:
    return max(low, min(high, value))


def apply_phase_base_upkeep(player: PlayerState) -> None:
    player.water -= 1
    player.food -= 1


def apply_action_cost(player: PlayerState, action_type: str) -> None:
    if action_type not in ACTION_COSTS:
        raise ValueError(f"unknown action type: {action_type}")

    delta = ACTION_COSTS[action_type]
    player.water += delta["water"]
    player.food += delta["food"]
    player.exposure += delta["exposure"]


def apply_item_use(player: PlayerState, item_id: str, quantity: int = 1) -> None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if item_id not in ITEM_EFFECTS:
        raise ValueError(f"unknown item: {item_id}")

    owned = player.inventory.get(item_id, 0)
    if owned < quantity:
        raise ValueError(f"not enough item: {item_id}")

    player.inventory[item_id] = owned - quantity
    effect = ITEM_EFFECTS[item_id]
    player.water += effect.get("water", 0) * quantity
    player.food += effect.get("food", 0) * quantity


def apply_status_clamp(player: PlayerState) -> None:
    player.water = clamp(player.water)
    player.food = clamp(player.food)
    player.exposure = clamp(player.exposure)


def is_dead_by_resource(player: PlayerState) -> bool:
    return player.water <= 0 or player.food <= 0


def is_immediate_tile_death(tile_type: str, phase: str) -> bool:
    if tile_type == TILE_Q:
        return True
    if tile_type == TILE_X and phase == PHASE_DAY:
        return True
    if tile_type == TILE_X and phase == PHASE_NIGHT:
        return False
    return False


def night_x_survive_probability(exposure: int) -> float:
    prob = 0.97 - 0.008 * exposure
    return max(0.03, min(0.97, prob))


def resolve_night_x_survival(exposure: int, sample: float) -> bool:
    if not 0 <= sample < 1:
        raise ValueError("sample must be in [0, 1)")
    return sample <= night_x_survive_probability(exposure)
