"""Player-facing view assembly service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from src.domain.constants import ROOM_STATUS_CLOSED, ROOM_STATUS_IN_GAME
from src.domain.errors import ERR_ROOM_NOT_ACTIVE
from src.domain.models import PlayerState, Room
from src.engine.map_ops import tile_at, tile_key

if TYPE_CHECKING:
    from src.application.match_service import MatchService


class PlayerViewAssembler:
    """Read-model assembler for player/game page view payloads."""

    def __init__(self, service: MatchService) -> None:
        self._service = service

    def build_player_view(self, room: Room, player_id: str) -> dict:
        """
        组装玩家视角 `view` 响应（游戏页主读模型）。

        职责：
        - 聚合身份、时间、倒计时、位置、自身状态、背包、局部地图、动作掩码等字段。
        - 返回“当前建筑快照记忆”和“可攻击目标/战利品窗口”读模型。

        约束：
        - 仅允许房间状态为 `IN_GAME/CLOSED`（关闭态允许历史查看）。

        Returns:
            dict: 前端 `/view` 所需完整观测结构。
        """
        if room.status not in {ROOM_STATUS_IN_GAME, ROOM_STATUS_CLOSED}:
            raise ValueError(ERR_ROOM_NOT_ACTIVE)

        player = self._service._require_player(room, player_id)
        match = self._service._require_match(room)
        current_tile = tile_at(player.x, player.y)
        building_info_state, building_snapshot = self._current_tile_memory_snapshot(player)

        round_opened_at = match.round_opened_at
        round_deadline_at = None
        round_remaining_sec = None
        if round_opened_at is not None:
            round_deadline_at = round_opened_at + timedelta(seconds=self._service._round_action_timeout_sec)
            round_remaining_sec = max(0, int((round_deadline_at - datetime.now(UTC)).total_seconds()))

        return {
            "identity": {"player_id": player.player_id, "room_id": room.room_id},
            "time_state": {"day": match.day, "phase": match.phase, "round": match.round},
            "round_timer": {
                "timeout_sec": self._service._round_action_timeout_sec,
                "opened_at": round_opened_at.isoformat() if round_opened_at is not None else None,
                "deadline_at": round_deadline_at.isoformat() if round_deadline_at is not None else None,
                "remaining_sec": round_remaining_sec,
            },
            "position": {"x": player.x, "y": player.y, "tile_type": current_tile},
            "building_info_state": building_info_state,
            "building_snapshot": building_snapshot,
            "self_status": {
                "water": player.water,
                "food": player.food,
                "exposure": player.exposure,
                "alive": player.alive,
                "phase_ended": player.phase_ended,
            },
            "inventory": dict(player.inventory),
            "recent_positions": list(player.recent_positions),
            "local_map_summary": self._service._memory_service.local_map_summary_view(player),
            "action_mask": self._service.get_allowed_actions(room, player_id),
            "attack_targets": self.attack_target_candidates(room, player),
            "loot_window": self.loot_window_view(room, player),
        }

    def loot_window_view(self, room: Room, player: PlayerState) -> dict | None:
        """
        构建玩家视角战利品窗口读模型。

        规则：
        - 仅当 loot window 打开时返回对象，否则返回 `None`。
        - 仅胜者可见败者当前背包（用于 GET 选择），其他人只拿到窗口元数据。
        """
        match = self._service._require_match(room)
        lw = match.loot_window_state
        if lw is None:
            return None
        loser_inventory: dict[str, int] = {}
        can_choose = player.player_id == lw.winner_player_id and player.alive
        if can_choose:
            loser = room.players.get(lw.loser_player_id)
            if loser is not None:
                loser_inventory = dict(loser.inventory)
        return {
            "is_open": True,
            "winner_player_id": lw.winner_player_id,
            "loser_player_id": lw.loser_player_id,
            "expires_at": lw.expires_at.isoformat(),
            "can_choose": can_choose,
            "loser_inventory": loser_inventory,
        }

    def attack_target_candidates(self, room: Room, player: PlayerState) -> list[str]:
        """
        基于玩家“当前建筑记忆”计算可攻击目标列表。

        过滤规则：
        - 玩家自身、非法 id、当前已 phase_ended 的目标都会被过滤。
        - 数据来源是记忆快照（而非实时强制扫描），与“先探索再攻击”语义保持一致。
        """
        if not player.alive or player.phase_ended:
            return []
        key = tile_key(player.x, player.y)
        memory = player.building_memory.get(key)
        if memory is None:
            return []
        chars = memory.get("characters", [])
        if not isinstance(chars, list):
            return []
        rested_player_ids = {p.player_id for p in room.players.values() if p.alive and p.phase_ended}
        candidates: list[str] = []
        for pid in chars:
            if not isinstance(pid, str) or pid == player.player_id:
                continue
            if pid in rested_player_ids:
                continue
            candidates.append(pid)
        return candidates

    def _current_tile_memory_snapshot(self, player: PlayerState) -> tuple[str, dict]:
        """
        提取玩家当前位置的建筑记忆快照（供 `/view` 顶层字段使用）。

        Returns:
            tuple[str, dict]:
            - `building_info_state`
            - `building_snapshot`（resources/characters/snapshot_last_seen_at）
        """
        key = tile_key(player.x, player.y)
        memory = player.building_memory.get(key)
        if memory is None:
            return (
                "UNEXPLORED",
                {"resources": {}, "characters": [], "snapshot_last_seen_at": None},
            )
        return (
            str(memory.get("info_state", "HAS_MEMORY")),
            {
                "resources": memory.get("resources", {}),
                "characters": memory.get("characters", []),
                "snapshot_last_seen_at": memory.get("last_seen_at"),
            },
        )
