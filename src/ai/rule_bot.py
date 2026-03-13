"""Rule-based fallback AI policy."""

from __future__ import annotations

from typing import Any

from src.ai.policy import Policy
from src.domain.constants import (
    ACTION_ATTACK,
    ACTION_EXPLORE,
    ACTION_MOVE,
    ACTION_REST,
    ACTION_TAKE,
    ACTION_USE,
    PHASE_NIGHT,
)
from src.engine.map_ops import is_in_bounds, is_safe_tile, tile_at


WATER_ITEMS = ("bottled_water", "barrel_water", "clean_water")
FOOD_ITEMS = ("bread", "compressed_biscuit", "canned_food")


class RuleBot(Policy):
    def __init__(
        self,
        *,
        safe_tile_bonus: int = 6,
        unsafe_tile_penalty: int = 12,
        backtrack_penalty: int = 8,
        recent_visit_penalty: int = 3,
        unexplored_bonus: int = 2,
        resource_hint_bonus: int = 3,
        encounter_advantage_bonus: int = 1,
        encounter_risk_penalty: int = 1,
        recent_lookback: int = 5,
        stuck_rest_threshold: int = 2,
    ) -> None:
        # 移动打分权重（可配置），用于在不改主流程的前提下快速调参。
        self._safe_tile_bonus = safe_tile_bonus
        self._unsafe_tile_penalty = unsafe_tile_penalty
        self._backtrack_penalty = backtrack_penalty
        self._recent_visit_penalty = recent_visit_penalty
        self._unexplored_bonus = unexplored_bonus
        self._resource_hint_bonus = resource_hint_bonus
        self._encounter_advantage_bonus = encounter_advantage_bonus
        self._encounter_risk_penalty = encounter_risk_penalty
        self._recent_lookback = max(1, recent_lookback)
        # 连续无收益轮阈值：达到后优先 REST（若可用），用于止损降级。
        self._stuck_rest_threshold = max(1, stuck_rest_threshold)
        self._stuck_streak: dict[tuple[str, str], int] = {}

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
        player_key = self._player_key(obs)
        recent_positions = obs.get("recent_positions", [])
        if not isinstance(recent_positions, list):
            recent_positions = []
        if self._is_no_gain_cycle(recent_positions):
            self._stuck_streak[player_key] = self._stuck_streak.get(player_key, 0) + 1
        else:
            self._stuck_streak[player_key] = 0

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
                    "payload": {"target_id": chars[0]},
                }

        if (
            ACTION_REST in allowed
            and self._stuck_streak.get(player_key, 0) >= self._stuck_rest_threshold
        ):
            self._stuck_streak[player_key] = 0
            return {"action_type": ACTION_REST, "payload": {}}

        if ACTION_MOVE in allowed:
            target = self._pick_move_target(obs, x, y, current_tile)
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

    def _pick_move_target(self, obs: dict, x: int, y: int, current_tile: str) -> tuple[int, int] | None:
        local_map = obs.get("local_map_summary")
        recent = obs.get("recent_positions", [])
        status = obs.get("self_status", {})
        if isinstance(local_map, dict):
            target = self._pick_by_local_map(
                x=x,
                y=y,
                current_tile=current_tile,
                local_map=local_map,
                recent_positions=recent if isinstance(recent, list) else [],
                status=status if isinstance(status, dict) else {},
            )
            if target is not None:
                return target
        return self._pick_safe_adjacent(x, y, current_tile)

    def _pick_by_local_map(
        self,
        *,
        x: int,
        y: int,
        current_tile: str,
        local_map: dict[str, Any],
        recent_positions: list[dict[str, Any]],
        status: dict[str, Any],
    ) -> tuple[int, int] | None:
        lookup: dict[tuple[int, int], dict[str, Any]] = {}
        for row in local_map.get("tiles", []):
            if not isinstance(row, dict):
                continue
            rx = row.get("x")
            ry = row.get("y")
            if isinstance(rx, int) and isinstance(ry, int):
                lookup[(rx, ry)] = row

        last_pos = self._previous_position(recent_positions, x=x, y=y)
        candidates: list[tuple[int, int, int]] = []
        dirs = ((0, -1), (1, 0), (0, 1), (-1, 0))
        for dx, dy in dirs:
            nx, ny = x + dx, y + dy
            if not is_in_bounds(nx, ny):
                continue
            row = lookup.get((nx, ny), {})
            if not bool(row.get("in_bounds", True)):
                continue
            score = self._score_tile(
                nx=nx,
                ny=ny,
                row=row,
                last_pos=last_pos,
                recent_positions=recent_positions,
                status=status,
            )
            candidates.append((score, nx, ny))

        if not candidates:
            return None

        # 优先高分候选；分数相同按方向顺序保持稳定性。
        candidates.sort(key=lambda t: t[0], reverse=True)
        best_score, bx, by = candidates[0]
        if best_score < 0 and is_safe_tile(current_tile):
            return None
        return (bx, by)

    def _score_tile(
        self,
        *,
        nx: int,
        ny: int,
        row: dict[str, Any],
        last_pos: tuple[int, int] | None,
        recent_positions: list[dict[str, Any]],
        status: dict[str, Any],
    ) -> int:
        tile_type = row.get("tile_type")
        if isinstance(tile_type, str):
            is_safe = bool(row.get("is_safe", is_safe_tile(tile_type)))
        else:
            tile_type = tile_at(nx, ny)
            is_safe = is_safe_tile(tile_type)
        score = 0
        score += self._safe_tile_bonus if is_safe else -self._unsafe_tile_penalty

        if last_pos is not None and (nx, ny) == last_pos:
            score -= self._backtrack_penalty
        if self._was_visited_recently(recent_positions, nx=nx, ny=ny, lookback=self._recent_lookback):
            score -= self._recent_visit_penalty

        if not bool(row.get("is_explored", False)):
            score += self._unexplored_bonus

        resources = row.get("known_resources", {})
        if isinstance(resources, dict):
            total = 0
            for qty in resources.values():
                if isinstance(qty, int) and qty > 0:
                    total += qty
            if total > 0:
                score += self._resource_hint_bonus

        chars = row.get("known_characters", [])
        if isinstance(chars, list) and chars:
            water = int(status.get("water", 0))
            food = int(status.get("food", 0))
            score += (
                self._encounter_advantage_bonus
                if water >= 60 and food >= 60
                else -self._encounter_risk_penalty
            )
        return score

    def _player_key(self, obs: dict) -> tuple[str, str]:
        identity = obs.get("identity", {})
        if not isinstance(identity, dict):
            return ("", "")
        room_id = str(identity.get("room_id", ""))
        player_id = str(identity.get("player_id", ""))
        return (room_id, player_id)

    def _is_no_gain_cycle(self, recent_positions: list[dict[str, Any]]) -> bool:
        # 识别 A-B-A-B 型短周期往返，作为“无收益轮”信号。
        if len(recent_positions) < 4:
            return False
        tail = recent_positions[-4:]
        coords: list[tuple[int, int]] = []
        for row in tail:
            if not isinstance(row, dict):
                return False
            x = row.get("x")
            y = row.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                return False
            coords.append((x, y))
        a, b, c, d = coords
        return a == c and b == d and a != b

    def _previous_position(
        self,
        recent_positions: list[dict[str, Any]],
        *,
        x: int,
        y: int,
    ) -> tuple[int, int] | None:
        if len(recent_positions) < 2:
            return None
        last = recent_positions[-1]
        if not isinstance(last, dict):
            return None
        lx = last.get("x")
        ly = last.get("y")
        if lx != x or ly != y:
            return None
        prev = recent_positions[-2]
        if not isinstance(prev, dict):
            return None
        px = prev.get("x")
        py = prev.get("y")
        if isinstance(px, int) and isinstance(py, int):
            return (px, py)
        return None

    def _was_visited_recently(
        self,
        recent_positions: list[dict[str, Any]],
        *,
        nx: int,
        ny: int,
        lookback: int,
    ) -> bool:
        if lookback <= 0:
            return False
        for row in recent_positions[-lookback:]:
            if not isinstance(row, dict):
                continue
            rx = row.get("x")
            ry = row.get("y")
            if rx == nx and ry == ny:
                return True
        return False

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
