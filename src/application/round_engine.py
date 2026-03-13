"""Round settlement engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.constants import ACTION_ATTACK, ACTION_COSTS, ACTION_REST, ROOM_STATUS_IN_GAME
from src.domain.errors import ERR_CANNOT_SETTLE_EMPTY_ROUND, ERR_NO_ACTIVE_PLAYERS, ERR_ROOM_NOT_IN_GAME
from src.domain.models import Room
from src.engine.round_order import sort_action_queue
from src.engine.rules import apply_action_cost, apply_phase_base_upkeep

if TYPE_CHECKING:
    from src.application.match_service import MatchService


class RoundEngine:
    """Core round settlement flow extracted from MatchService."""

    def __init__(self, service: MatchService) -> None:
        self._service = service

    def settle_round(self, room: Room) -> dict[str, dict]:
        """
        结算当前回合（核心规则入口，按 FIFO 顺序执行）。

        执行阶段：
        1) 前置校验
        - 房间必须处于 `IN_GAME`
        - 当前回合动作队列不能为空
        - 必须存在至少一名“活着且未 phase_ended”的活跃玩家

        2) 初始化私有结算结果 `private_results`
        - 为房间内每名玩家创建一份私有结果槽位：
          `actions/events/status_before/status_after`
        - `status_before` 在回合执行前采样

        3) 阶段基础消耗（每 phase 仅一次）
        - 对活跃玩家施加 `BASE_UPKEEP`
        - 写入事件 `BASE_UPKEEP`

        4) FIFO 动作执行
        - 按 `sort_action_queue` 排序后的顺序逐条执行
        - 若玩家已死亡：跳过该动作
        - 若玩家被前序攻击打断且当前动作非 ATTACK：
          记录 `INTERRUPTED/DEFEATED_IN_ATTACK`，动作成本记 0
        - 否则：扣动作成本 -> 执行动作效果 -> 记录 before/after/result
        - 若 ATTACK 结果为 `WIN`：将 loser 标记到 `interrupted_players`
          以影响其后续非攻击动作

        5) 战利品窗口分支
        - 若本轮产生 `loot_window_state`：
          - 保存 `pending_settlement_private_results`
          - 锁轮并清空动作队列
          - 立即返回（回合收口延迟到 GET/TOSS）

        6) 无战利品窗口时的回合收口
        - 调用 `_finalize_post_action_phase` 推进 round/phase/day、结算死亡等
        - 调用 `_refresh_memories_after_settlement` 执行“回合末记忆刷新”策略

        Args:
            room: 当前房间对象（原地修改）。

        Returns:
            dict[str, dict]: 每个玩家一份私有结算结果（用于历史回放/私有推送）。

        Raises:
            ValueError:
            - `ERR_ROOM_NOT_IN_GAME`
            - `ERR_CANNOT_SETTLE_EMPTY_ROUND`
            - `ERR_NO_ACTIVE_PLAYERS`

        Side Effects:
            - 修改玩家状态（位置/资源/生死/phase_ended/背包等）
            - 修改对局状态（round/phase/day/round_locked/action_queue/loot window）
            - 可能写入 pending settlement（等待 loot 决策）
        """
        if room.status != ROOM_STATUS_IN_GAME:
            raise ValueError(ERR_ROOM_NOT_IN_GAME)
        match = self._service._require_match(room)
        if not match.action_queue:
            raise ValueError(ERR_CANNOT_SETTLE_EMPTY_ROUND)

        active_players = [p for p in room.players.values() if p.alive and not p.phase_ended]
        if not active_players:
            raise ValueError(ERR_NO_ACTIVE_PLAYERS)
        private_results: dict[str, dict] = {
            p.player_id: {
                "actions": [],
                "events": [],
                "status_before": self._service._status_dict(p),
                "status_after": None,
            }
            for p in room.players.values()
        }

        if not match.phase_base_upkeep_applied:
            for player in active_players:
                apply_phase_base_upkeep(player)
                private_results[player.player_id]["events"].append(
                    {"event_type": "BASE_UPKEEP", "delta": {"water": -1, "food": -1, "exposure": 0}}
                )
            match.phase_base_upkeep_applied = True

        sorted_actions = sort_action_queue(match.action_queue)
        all_rest = len(sorted_actions) == len(active_players) and all(
            action.action_type == ACTION_REST for action in sorted_actions
        )
        interrupted_players: set[str] = set()

        for action in sorted_actions:
            actor = room.players[action.player_id]
            if not actor.alive:
                continue
            if action.action_type != ACTION_ATTACK and actor.player_id in interrupted_players:
                before = self._service._status_dict(actor)
                private_results[actor.player_id]["actions"].append(
                    {
                        "action_type": action.action_type,
                        "cost": {"water": 0, "food": 0, "exposure": 0},
                        "before": before,
                        "after": before,
                        "result": {"result_type": "INTERRUPTED", "reason": "DEFEATED_IN_ATTACK"},
                    }
                )
                continue
            before = self._service._status_dict(actor)
            apply_action_cost(actor, action.action_type)
            effect = self._service._apply_action_effect(room, actor, action)
            after = self._service._status_dict(actor)
            private_results[actor.player_id]["actions"].append(
                {
                    "action_type": action.action_type,
                    "cost": dict(ACTION_COSTS[action.action_type]),
                    "before": before,
                    "after": after,
                    "result": effect,
                }
            )
            if (
                action.action_type == ACTION_ATTACK
                and isinstance(effect, dict)
                and effect.get("result_type") == "ATTACK_RESULT"
                and effect.get("outcome") == "WIN"
            ):
                loser_id = str(effect.get("loser") or "")
                if loser_id:
                    interrupted_players.add(loser_id)

        if match.loot_window_state is not None:
            match.pending_settlement_private_results = private_results
            match.round_locked = True
            match.action_queue.clear()
            return private_results

        self._service._finalize_post_action_phase(room, private_results, all_rest=all_rest)
        self._service._refresh_memories_after_settlement(room, private_results)
        return private_results
