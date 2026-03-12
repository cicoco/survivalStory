"""Rule-based fallback AI policy."""

from __future__ import annotations

from src.ai.policy import Policy
from src.domain.constants import (
    ACTION_ATTACK,
    ACTION_EXPLORE,
    ACTION_MOVE,
    ACTION_REST,
    ACTION_TAKE,
    ACTION_TOSS,
    ACTION_USE,
    PHASE_NIGHT,
)
from src.engine.map_ops import is_in_bounds, is_safe_tile, tile_at


WATER_ITEMS = ("bottled_water", "barrel_water", "clean_water")
FOOD_ITEMS = ("bread", "compressed_biscuit", "canned_food")


class RuleBot(Policy):
    def choose_action(self, obs: dict, action_mask: list[str]) -> dict:
        allowed = set(action_mask)
        if not allowed:
            return {"action_type": ACTION_REST, "payload": {}}

        status = obs.get("self_status", {})
        inventory = obs.get("inventory", {})
        phase = obs.get("time_state", {}).get("phase")
        pos = obs.get("position", {})
        x = int(pos.get("x", 0))
        y = int(pos.get("y", 0))
        current_tile = pos.get("tile_type", "X")
        building_snapshot = obs.get("building_snapshot", {})

        if ACTION_USE in allowed:
            water = int(status.get("water", 0))
            food = int(status.get("food", 0))
            if water <= 30:
                item_id = self._first_have(inventory, WATER_ITEMS)
                if item_id:
                    return {"action_type": ACTION_USE, "payload": {"items": {item_id: 1}}}
            if food <= 30:
                item_id = self._first_have(inventory, FOOD_ITEMS)
                if item_id:
                    return {"action_type": ACTION_USE, "payload": {"items": {item_id: 1}}}

        if ACTION_EXPLORE in allowed and obs.get("building_info_state") == "UNEXPLORED":
            return {"action_type": ACTION_EXPLORE, "payload": {}}

        if ACTION_TAKE in allowed:
            resources = building_snapshot.get("resources", {})
            candidate = self._first_positive(resources)
            if candidate:
                return {"action_type": ACTION_TAKE, "payload": {"items": {candidate: 1}}}

        if ACTION_ATTACK in allowed:
            chars = building_snapshot.get("characters", [])
            if chars and int(status.get("water", 0)) >= 60 and int(status.get("food", 0)) >= 60:
                return {
                    "action_type": ACTION_ATTACK,
                    "payload": {"target_id": chars[0], "loot": {"type": ACTION_TOSS}},
                }

        if ACTION_MOVE in allowed:
            target = self._pick_safe_adjacent(x, y, current_tile)
            if target:
                return {"action_type": ACTION_MOVE, "payload": {"x": target[0], "y": target[1]}}

        if phase == PHASE_NIGHT and ACTION_REST in allowed:
            return {"action_type": ACTION_REST, "payload": {}}

        if ACTION_REST in allowed:
            return {"action_type": ACTION_REST, "payload": {}}

        # Last resort; should not happen because action_mask should always contain REST.
        return {"action_type": list(allowed)[0], "payload": {}}

    def _first_have(self, inventory: dict, item_ids: tuple[str, ...]) -> str | None:
        for item_id in item_ids:
            if int(inventory.get(item_id, 0)) > 0:
                return item_id
        return None

    def _first_positive(self, resources: dict) -> str | None:
        for item_id, qty in resources.items():
            if int(qty) > 0:
                return item_id
        return None

    def _pick_safe_adjacent(self, x: int, y: int, current_tile: str) -> tuple[int, int] | None:
        dirs = ((0, -1), (1, 0), (0, 1), (-1, 0))
        for dx, dy in dirs:
            nx, ny = x + dx, y + dy
            if not is_in_bounds(nx, ny):
                continue
            t = tile_at(nx, ny)
            if is_safe_tile(t):
                return (nx, ny)
        # If current tile is already dangerous and no safe option, still try legal movement.
        if not is_safe_tile(current_tile):
            for dx, dy in dirs:
                nx, ny = x + dx, y + dy
                if is_in_bounds(nx, ny):
                    return (nx, ny)
        return None
