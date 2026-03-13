"""Player memory read/write service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.constants import ACTION_EXPLORE, INFO_STATE_HAS_MEMORY, INFO_STATE_UNEXPLORED
from src.domain.models import PlayerState, Room
from src.engine.map_ops import is_in_bounds, is_safe_tile, tile_at, tile_key

if TYPE_CHECKING:
    from src.application.match_service import MatchService


class MemoryService:
    """Memory-domain logic extracted from MatchService."""

    def __init__(self, service: MatchService) -> None:
        self._service = service

    def winner_tile_memory_base(self, winner: PlayerState) -> dict[str, Any]:
        """
        读取胜者当前建筑的记忆基线（GET/TOSS 公式刷新输入）。

        用途：
        - 在 loot 选择前冻结一份“旧记忆”，作为后续
          `旧资源 + (败者背包 - 实际拿走)` 的计算基准。
        """
        key = tile_key(winner.x, winner.y)
        memory = winner.building_memory.get(key) or {}
        return {
            "resources": dict(memory.get("resources", {})) if isinstance(memory.get("resources"), dict) else {},
            "characters": list(memory.get("characters", [])) if isinstance(memory.get("characters"), list) else [],
            "tile_type": tile_at(winner.x, winner.y),
        }

    def refresh_winner_memory_after_get_if_loser_dead(
        self,
        room: Room,
        winner: PlayerState,
        loser: PlayerState,
        *,
        winner_memory_before_get: dict[str, Any],
        loser_inventory_before_get: dict[str, int],
        obtained: dict[str, int],
    ) -> None:
        """
        在 GET/TOSS 完成后按规则刷新胜者记忆（仅败者死亡时生效）。

        规则：
        - 败者未死亡：不刷新胜者该建筑记忆。
        - 败者死亡：
          1) players = 旧记忆 players - 败者
          2) resources = 旧记忆 resources + (败者背包总量 - 本次获得量)
          3) last_seen_at 使用局内时间位点（day/phase/round）
        """
        if not winner.alive or loser.alive:
            return
        base_resources = (
            dict(winner_memory_before_get.get("resources", {}))
            if isinstance(winner_memory_before_get.get("resources"), dict)
            else {}
        )
        base_characters = (
            list(winner_memory_before_get.get("characters", []))
            if isinstance(winner_memory_before_get.get("characters"), list)
            else []
        )
        dropped: dict[str, int] = {}
        for item_id, qty in loser_inventory_before_get.items():
            remain = int(qty) - int(obtained.get(item_id, 0))
            if remain > 0:
                dropped[item_id] = remain
        merged = dict(base_resources)
        for item_id, qty in dropped.items():
            merged[item_id] = int(merged.get(item_id, 0)) + int(qty)
        chars = [pid for pid in base_characters if pid != loser.player_id]
        key = tile_key(winner.x, winner.y)
        winner.explored_tiles.add(key)
        winner.building_memory[key] = {
            "info_state": INFO_STATE_HAS_MEMORY,
            "tile_type": winner_memory_before_get.get("tile_type") or tile_at(winner.x, winner.y),
            "resources": merged,
            "characters": chars,
            "last_seen_at": self.memory_last_seen_at(room),
        }

    def refresh_memories_after_settlement(self, room: Room, private_results: dict[str, dict]) -> None:
        """
        执行“回合收口后”记忆刷新（当前仅处理 EXPLORE 最终快照）。

        触发点：
        - 普通结算收口后调用。
        - loot window 完成并收口后再次调用。

        语义：
        - 只处理结果类型为 `EXPLORE_RESULT` 的动作。
        - 先刷新玩家当前位置记忆到“本轮最终态”，再把该快照回填到
          `action.result.snapshot`，保证前端回放一致。
        """
        for player_id, result in private_results.items():
            actions = result.get("actions", [])
            explore_actions = [
                action
                for action in actions
                if isinstance(action, dict)
                and action.get("action_type") == ACTION_EXPLORE
                and isinstance(action.get("result"), dict)
                and action["result"].get("result_type") == "EXPLORE_RESULT"
            ]
            if not explore_actions:
                continue
            player = room.players.get(player_id)
            if player is None or not player.alive:
                continue
            self.refresh_player_memory(room, player)
            key = tile_key(player.x, player.y)
            memory = player.building_memory.get(key, {})
            snapshot = {
                "resources": dict(memory.get("resources", {}))
                if isinstance(memory.get("resources"), dict)
                else {},
                "characters": list(memory.get("characters", []))
                if isinstance(memory.get("characters"), list)
                else [],
            }
            for action in explore_actions:
                action["result"]["snapshot"] = snapshot

    def refresh_player_memory(self, room: Room, actor: PlayerState) -> None:
        """
        刷新玩家当前位置建筑记忆（A/B 同步快照）。

        写入内容：
        - `resources`: 建筑当前库存（B）
        - `characters`: 同格且存活的其他玩家（A）
        - `info_state`: HAS_MEMORY
        - `last_seen_at`: 局内时间位点
        """
        match = self._service._require_match(room)
        key = tile_key(actor.x, actor.y)
        tile_type = tile_at(actor.x, actor.y)
        resources = dict(match.building_inventory.get(key, {}))
        characters = [
            p.player_id
            for p in room.players.values()
            if p.alive and p.x == actor.x and p.y == actor.y and p.player_id != actor.player_id
        ]
        actor.explored_tiles.add(key)
        actor.known_characters.update(characters)
        actor.building_memory[key] = {
            "info_state": INFO_STATE_HAS_MEMORY,
            "tile_type": tile_type,
            "resources": resources,
            "characters": characters,
            "last_seen_at": self.memory_last_seen_at(room),
        }

    def memory_last_seen_at(self, room: Room) -> dict[str, Any]:
        """
        生成记忆时间锚点（局内时间，而非 wall-clock 时间戳）。
        """
        match = self._service._require_match(room)
        return {"day": match.day, "phase": match.phase, "round": match.round}

    def tile_memory_view(self, player: PlayerState, x: int, y: int) -> dict[str, Any]:
        """
        读取玩家对单格地块的记忆视图（无记忆时返回 UNEXPLORED 默认结构）。
        """
        key = tile_key(x, y)
        memory = player.building_memory.get(key)
        if memory is None:
            return {
                "info_state": INFO_STATE_UNEXPLORED,
                "is_explored": False,
                "known_resources": {},
                "known_characters": [],
                "last_seen_at": None,
            }
        return {
            "info_state": memory.get("info_state", INFO_STATE_HAS_MEMORY),
            "is_explored": key in player.explored_tiles,
            "known_resources": dict(memory.get("resources", {})),
            "known_characters": list(memory.get("characters", [])),
            "last_seen_at": memory.get("last_seen_at"),
        }

    def local_map_summary_view(self, player: PlayerState) -> dict[str, Any]:
        """
        构建玩家中心局部地图摘要（含每格记忆状态）。

        输出用于前端与 AI：
        - 地块基础信息（坐标/类型/是否安全）
        - 记忆信息（info_state / known_resources / known_characters / last_seen_at）
        """
        radius = self._service._local_map_window // 2
        tiles: list[dict[str, Any]] = []
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                x, y = player.x + dx, player.y + dy
                in_bounds = is_in_bounds(x, y)
                row: dict[str, Any] = {
                    "x": x,
                    "y": y,
                    "dx": dx,
                    "dy": dy,
                    "in_bounds": in_bounds,
                    "tile_type": None,
                    "is_safe": False,
                    "info_state": INFO_STATE_UNEXPLORED,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                    "last_seen_at": None,
                }
                if in_bounds:
                    tile_type = tile_at(x, y)
                    memory = self.tile_memory_view(player, x, y)
                    row.update(
                        {
                            "tile_type": tile_type,
                            "is_safe": is_safe_tile(tile_type),
                            "info_state": memory["info_state"],
                            "is_explored": bool(memory["is_explored"]),
                            "known_resources": memory["known_resources"],
                            "known_characters": memory["known_characters"],
                            "last_seen_at": memory["last_seen_at"],
                        }
                    )
                tiles.append(row)
        return {
            "window_size": self._service._local_map_window,
            "center": {"x": player.x, "y": player.y},
            "tiles": tiles,
        }
