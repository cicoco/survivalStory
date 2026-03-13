"""Phase 2 room/match orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import random
from typing import Any
import zlib

from src.domain.constants import (
    ACTION_ATTACK,
    ACTION_EXPLORE,
    ACTION_GET,
    ACTION_MOVE,
    ACTION_REST,
    ACTION_TAKE,
    ACTION_TOSS,
    ACTION_USE,
    CORE_ACTION_TYPES,
    ACTION_COSTS,
    ATTACK_WIN_DELTA_THRESHOLD,
    DEATH_REASON_LEFT_IN_GAME,
    DEATH_REASON_FATAL_TILE,
    DEATH_REASON_NIGHT_X_FAIL,
    DEATH_REASON_RESOURCE_ZERO,
    END_MODE_ALL_DEAD,
    END_MODE_HOST_LEFT,
    END_MODE_HUMAN_ALL_DEAD,
    ITEM_EFFECTS,
    LOOT_TYPE_GET,
    LOOT_TYPE_TOSS,
    LOOT_TYPES,
    MAX_TAKE_ITEMS_PER_ACTION,
    PHASE_DAY,
    PHASE_NIGHT,
    MAP_MATRIX,
    ROOM_STATUS_CLOSED,
    ROOM_STATUS_DISBANDED,
    ROOM_STATUS_IN_GAME,
    ROOM_STATUS_WAITING,
    SPAWN_ALLOWED_TILES,
    INITIAL_STATUS,
    RESOURCE_TOTAL_DEFAULTS,
)
from src.domain.errors import (
    ERR_ALREADY_SUBMITTED,
    ERR_ATTACK_LOOT_PAYLOAD_INVALID,
    ERR_ATTACK_LOOT_TYPE_INVALID,
    ERR_ATTACK_TARGET_ID_INVALID,
    ERR_ATTACK_TARGET_NOT_DISCOVERED,
    ERR_ATTACK_TARGET_SELF,
    ERR_CANNOT_SETTLE_EMPTY_ROUND,
    ERR_ITEM_BUNDLE_INVALID,
    ERR_ITEM_QTY_INVALID_PREFIX,
    ERR_ITEM_TOTAL_EXCEEDED_PREFIX,
    ERR_ITEM_UNKNOWN_PREFIX,
    ERR_LOOT_WINDOW_ACTION_INVALID,
    ERR_LOOT_WINDOW_NOT_OPEN,
    ERR_LOOT_WINDOW_ONLY_WINNER_CAN_ACT,
    ERR_MATCH_NOT_OVER,
    ERR_MATCH_NOT_STARTED,
    ERR_MOVE_NOT_ADJACENT,
    ERR_MOVE_OUT_OF_BOUNDS,
    ERR_MOVE_PAYLOAD_INVALID,
    ERR_NO_ACTIVE_PLAYERS,
    ERR_PLAYER_CANNOT_ACT,
    ERR_ONLY_HOST_CAN_RESET,
    ERR_ROUND_LOCKED,
    ERR_ROOM_ALREADY_IN_GAME,
    ERR_ROOM_FULL,
    ERR_ROOM_NOT_ACTIVE,
    ERR_ROOM_NOT_IN_GAME,
    ERR_ROOM_NOT_WAITING,
    ERR_ROOM_PLAYER_EXISTS_PREFIX,
    ERR_TAKE_REQUIRES_EXPLORE,
    ERR_UNSUPPORTED_ACTION_PREFIX,
    ERR_UNKNOWN_PLAYER_PREFIX,
)
from src.domain.models import (
    ActionEnvelope,
    LootWindowState,
    MatchState,
    PlayerMatchStats,
    PlayerState,
    Room,
)
from src.application.memory_service import MemoryService
from src.application.player_view_assembler import PlayerViewAssembler
from src.application.round_engine import RoundEngine
from src.engine.map_ops import is_in_bounds, is_safe_tile, tile_at, tile_key
from src.engine.resource_allocator import allocate_resources_iterative_random
from src.engine.rules import (
    apply_item_use,
    apply_status_clamp,
    is_dead_by_resource,
    is_immediate_tile_death,
    resolve_night_x_survival,
)

logger = logging.getLogger("survival_story.api.match_service")


class MatchService:
    def __init__(
        self,
        loot_window_timeout_sec: int = 60,
        round_action_timeout_sec: int = 90,
        max_day_phase_rounds: int = 99,
        max_night_phase_rounds: int = 99,
        room_max_players: int = 6,
        max_ai_players: int = 5,
        recent_positions_maxlen: int = 16,
        local_map_window: int = 5,
    ) -> None:
        # 初始化对局服务配置（时限、房间规模、AI 输入窗口参数等）。
        if room_max_players <= 0:
            raise ValueError("room_max_players must be > 0")
        if max_ai_players < 0:
            raise ValueError("max_ai_players must be >= 0")
        if max_day_phase_rounds <= 0:
            raise ValueError("max_day_phase_rounds must be > 0")
        if max_night_phase_rounds <= 0:
            raise ValueError("max_night_phase_rounds must be > 0")
        if recent_positions_maxlen <= 0:
            raise ValueError("recent_positions_maxlen must be > 0")
        if local_map_window <= 0 or local_map_window % 2 == 0:
            raise ValueError("local_map_window must be a positive odd integer")
        self._loot_window_timeout_sec = loot_window_timeout_sec
        self._round_action_timeout_sec = round_action_timeout_sec
        self._max_day_phase_rounds = max_day_phase_rounds
        self._max_night_phase_rounds = max_night_phase_rounds
        self._room_max_players = room_max_players
        self._max_ai_players = max_ai_players
        self._recent_positions_maxlen = recent_positions_maxlen
        self._local_map_window = local_map_window
        self._memory_service = MemoryService(self)
        self._player_view_assembler = PlayerViewAssembler(self)
        self._round_engine = RoundEngine(self)

    def create_room(self, room_id: str, host_player_id: str, end_mode: str) -> Room:
        # 创建房间并自动将房主加入玩家列表。
        if end_mode not in {END_MODE_ALL_DEAD, END_MODE_HUMAN_ALL_DEAD}:
            raise ValueError(f"unsupported end_mode: {end_mode}")

        room = Room(room_id=room_id, host_player_id=host_player_id, end_mode=end_mode)
        self.join_room(room, host_player_id, is_human=True)
        return room

    def join_room(self, room: Room, player_id: str, is_human: bool = True) -> PlayerState:
        # 等待阶段入房：校验容量/重复后生成玩家状态。
        if room.status != ROOM_STATUS_WAITING:
            raise ValueError(ERR_ROOM_NOT_WAITING)
        if player_id in room.players:
            raise ValueError(f"{ERR_ROOM_PLAYER_EXISTS_PREFIX} {player_id}")
        if len(room.players) >= self._room_max_players:
            raise ValueError(ERR_ROOM_FULL)

        room.join_seq_counter += 1
        player = PlayerState(
            player_id=player_id,
            is_human=is_human,
            join_seq=room.join_seq_counter,
            inventory=self._build_initial_player_inventory(),
        )
        room.players[player_id] = player
        return player

    def start_match(self, room: Room) -> MatchState:
        # 开局入口：补齐 AI、分配出生点、初始化对局状态与地图库存。
        if room.status != ROOM_STATUS_WAITING:
            raise ValueError(ERR_ROOM_NOT_WAITING)
        if room.status == ROOM_STATUS_IN_GAME:
            raise ValueError(ERR_ROOM_ALREADY_IN_GAME)

        self._fill_ai_players(room)
        self._assign_spawn_points(room)
        room.status = ROOM_STATUS_IN_GAME
        player_stats = {
            p.player_id: PlayerMatchStats(
                player_id=p.player_id,
                is_human=p.is_human,
                join_seq=p.join_seq,
            )
            for p in room.players.values()
        }
        room.match_state = MatchState(
            building_inventory=self._build_initial_map_inventory(),
            round_opened_at=datetime.now(UTC),
            player_stats=player_stats,
        )
        for player in room.players.values():
            self._reset_recent_positions(player)
            self._append_recent_position(
                player,
                day=room.match_state.day,
                phase=room.match_state.phase,
                round_no=room.match_state.round,
            )
        return room.match_state

    def submit_action(
        self,
        room: Room,
        player_id: str,
        action_type: str,
        payload: dict | None = None,
        server_received_at: datetime | None = None,
    ) -> ActionEnvelope:
        # 动作提交入口：做规则校验并入队；全部活跃玩家提交后自动锁轮。
        if room.status != ROOM_STATUS_IN_GAME:
            raise ValueError(ERR_ROOM_NOT_IN_GAME)
        match = self._require_match(room)
        player = self._require_player(room, player_id)
        if not player.alive or player.phase_ended:
            raise ValueError(ERR_PLAYER_CANNOT_ACT)
        if match.round_locked:
            raise ValueError(ERR_ROUND_LOCKED)
        if match.loot_window_state is not None:
            raise ValueError(ERR_ROUND_LOCKED)
        if any(a.player_id == player_id for a in match.action_queue):
            raise ValueError(ERR_ALREADY_SUBMITTED)
        self._validate_action(room, player, action_type, payload or {})

        envelope = ActionEnvelope(
            action_id=f"{player_id}-{match.day}-{match.phase}-{match.round}",
            player_id=player_id,
            day=match.day,
            phase=match.phase,
            round=match.round,
            action_type=action_type,
            payload=payload or {},
            join_seq=player.join_seq,
            server_received_at=server_received_at or datetime.now(UTC),
        )
        match.action_queue.append(envelope)
        if match.round_opened_at is None:
            match.round_opened_at = datetime.now(UTC)

        if self._all_active_submitted(room):
            match.round_locked = True
        return envelope

    def settle_round(self, room: Room) -> dict[str, dict]:
        """
        结算当前回合（MatchService 对外入口）。

        说明：
        - 该方法仅负责对外暴露与编排委托。
        - 具体结算规则（FIFO、打断、战利品分支、收口）在 `RoundEngine.settle_round`。
        """
        # 委托给结算内核，MatchService 保留编排角色。
        return self._round_engine.settle_round(room)

    def submit_loot_window_action(
        self,
        room: Room,
        player_id: str,
        action_type: str,
        payload: dict | None = None,
    ) -> dict[str, dict]:
        # 战利品窗口动作入口：仅胜者可执行 GET/TOSS，并续接回合后处理。
        if room.status != ROOM_STATUS_IN_GAME:
            raise ValueError(ERR_ROOM_NOT_IN_GAME)
        match = self._require_match(room)
        lw = match.loot_window_state
        if lw is None:
            raise ValueError(ERR_LOOT_WINDOW_NOT_OPEN)
        if player_id != lw.winner_player_id:
            raise ValueError(ERR_LOOT_WINDOW_ONLY_WINNER_CAN_ACT)
        if action_type not in {ACTION_GET, ACTION_TOSS}:
            raise ValueError(ERR_LOOT_WINDOW_ACTION_INVALID)

        winner = self._require_player(room, lw.winner_player_id)
        loser = self._require_player(room, lw.loser_player_id)
        winner_memory_before_get = self._memory_service.winner_tile_memory_base(winner)
        loser_inventory_before_get = dict(loser.inventory)
        private_results = match.pending_settlement_private_results or {
            p.player_id: {
                "actions": [],
                "events": [],
                "status_before": self._status_dict(p),
                "status_after": self._status_dict(p),
            }
            for p in room.players.values()
        }

        loot_payload = {"type": action_type}
        if action_type == ACTION_GET:
            items = (payload or {}).get("items", {})
            self._validate_item_bundle(
                {"items": items},
                must_exist_in=ITEM_EFFECTS,
                max_total=MAX_TAKE_ITEMS_PER_ACTION,
            )
            loot_payload["items"] = items

        loot_result = self._resolve_loot_window(match, winner, loser, loot_payload)
        winner_before = self._status_dict(winner)
        winner_after = self._status_dict(winner)
        private_results[winner.player_id]["actions"].append(
            {
                "action_type": action_type,
                "cost": dict(ACTION_COSTS[action_type]),
                "before": winner_before,
                "after": winner_after,
                "result": {
                    "result_type": "LOOT_ACTION_RESULT",
                    "choice": action_type,
                    "obtained": loot_result.get("obtained", {}),
                },
            }
        )
        self._attach_loot_resolution_to_attack_results(
            private_results,
            choice=action_type,
            obtained=loot_result.get("obtained", {}),
        )
        private_results[winner.player_id]["events"].append(
            {
                "event_type": "LOOT_WINDOW_RESOLVED",
                "choice": action_type,
                "obtained": loot_result.get("obtained", {}),
            }
        )
        private_results[loser.player_id]["events"].append(
            {
                "event_type": "LOOT_WINDOW_RESOLVED",
                "choice": action_type,
                "obtained": loot_result.get("obtained", {}),
            }
        )

        match.loot_window_state = None
        self._finalize_post_action_phase(room, private_results, all_rest=False)
        if action_type in {ACTION_GET, ACTION_TOSS}:
            self._memory_service.refresh_winner_memory_after_get_if_loser_dead(
                room,
                winner,
                loser,
                winner_memory_before_get=winner_memory_before_get,
                loser_inventory_before_get=loser_inventory_before_get,
                obtained=loot_result.get("obtained", {}),
            )
        self._memory_service.refresh_memories_after_settlement(room, private_results)
        match.pending_settlement_private_results = None
        return private_results

    def resolve_loot_window_timeout_if_needed(self, room: Room) -> dict[str, dict] | None:
        # 战利品窗口超时自动处理：到期后默认执行 TOSS。
        if room.status != ROOM_STATUS_IN_GAME:
            return None
        match = self._require_match(room)
        lw = match.loot_window_state
        if lw is None:
            return None
        if datetime.now(UTC) < lw.expires_at:
            return None
        return self.submit_loot_window_action(room, lw.winner_player_id, ACTION_TOSS, {})

    def resolve_round_timeout_if_needed(self, room: Room) -> list[ActionEnvelope]:
        # 回合超时自动补动作：当前仅为真人补 REST，AI 由调度层单独驱动。
        if room.status != ROOM_STATUS_IN_GAME:
            return []
        match = self._require_match(room)
        if match.game_over or match.round_locked:
            return []
        if match.loot_window_state is not None:
            return []
        opened_at = match.round_opened_at
        if opened_at is None:
            match.round_opened_at = datetime.now(UTC)
            return []
        if datetime.now(UTC) < (opened_at + timedelta(seconds=self._round_action_timeout_sec)):
            return []

        submitted = {a.player_id for a in match.action_queue}
        autos: list[ActionEnvelope] = []
        now = datetime.now(UTC)
        for player in room.players.values():
            if not player.alive or player.phase_ended:
                continue
            if player.player_id in submitted:
                continue
            if not player.is_human:
                continue
            env = self.submit_action(
                room,
                player.player_id,
                ACTION_REST,
                {},
                server_received_at=now,
            )
            autos.append(env)
        return autos

    def get_loot_window_state(self, room: Room) -> LootWindowState | None:
        # 查询当前房间战利品窗口状态。
        if room.match_state is None:
            return None
        match = self._require_match(room)
        return match.loot_window_state

    def get_endgame_summary(self, room: Room) -> dict | None:
        # 读取终局汇总（若未终局则返回 None）。
        if room.match_state is None:
            return None
        match = self._require_match(room)
        if not match.game_over:
            return None
        if match.endgame_summary is None:
            match.endgame_summary = self._build_endgame_summary(room)
        return match.endgame_summary

    def reset_room_for_next_match(self, room: Room, actor_player_id: str) -> dict:
        # 房主触发重置：清理 AI 与对局态，恢复到 WAITING。
        if actor_player_id != room.host_player_id:
            raise ValueError(ERR_ONLY_HOST_CAN_RESET)
        if room.status == ROOM_STATUS_DISBANDED:
            raise ValueError(ERR_ROOM_NOT_ACTIVE)
        match = self._require_match(room)
        if not match.game_over:
            raise ValueError(ERR_MATCH_NOT_OVER)

        remove_ids = [player_id for player_id, p in room.players.items() if not p.is_human]
        for player_id in remove_ids:
            room.players.pop(player_id, None)

        for player in room.players.values():
            player.alive = True
            player.x = 4
            player.y = 4
            player.water = INITIAL_STATUS["water"]
            player.food = INITIAL_STATUS["food"]
            player.exposure = INITIAL_STATUS["exposure"]
            player.inventory = self._build_initial_player_inventory()
            player.phase_ended = False
            player.explored_tiles.clear()
            player.known_characters.clear()
            player.building_memory.clear()
            player.recent_positions.clear()

        room.status = ROOM_STATUS_WAITING
        room.waiting_since = datetime.now(UTC)
        room.match_state = None
        return {"mode": "RESET", "status": room.status, "removed_ai_count": len(remove_ids)}

    def leave_room(self, room: Room, player_id: str) -> dict:
        # 离房处理：按等待态/对局态与是否房主分支执行。
        player = self._require_player(room, player_id)

        if room.status == ROOM_STATUS_WAITING:
            if player_id == room.host_player_id:
                room.players.clear()
                room.status = ROOM_STATUS_DISBANDED
                room.match_state = None
                return {"mode": "DISBANDED", "player_id": player_id}
            room.players.pop(player_id, None)
            return {"mode": "LEFT_WAITING", "player_id": player_id}

        if room.status == ROOM_STATUS_IN_GAME:
            match = self._require_match(room)
            if player_id == room.host_player_id:
                room.status = ROOM_STATUS_CLOSED
                match.game_over = True
                match.game_over_reason = END_MODE_HOST_LEFT
                match.endgame_summary = self._build_endgame_summary(room)
                self._clear_round(match)
                return {"mode": "CLOSED_BY_HOST", "player_id": player_id}

            if player.alive:
                self._kill_player(room, player, reason=DEATH_REASON_LEFT_IN_GAME)
                self._check_game_over(room)
                if match.game_over:
                    match.endgame_summary = self._build_endgame_summary(room)
                    self._clear_round(match)
            return {"mode": "LEFT_IN_GAME_AS_DEATH", "player_id": player_id}

        raise ValueError(ERR_ROOM_NOT_ACTIVE)

    def _finalize_post_action_phase(
        self,
        room: Room,
        private_results: dict[str, dict],
        *,
        all_rest: bool,
    ) -> None:
        # 回合后处理：状态钳制、死亡判定、终局检查、阶段/回合推进与清队列。
        match = self._require_match(room)
        for player in list(room.players.values()):
            if not player.alive:
                continue
            apply_status_clamp(player)
            if is_dead_by_resource(player):
                self._kill_player(room, player, reason=DEATH_REASON_RESOURCE_ZERO)
                private_results[player.player_id]["events"].append(
                    {"event_type": "DEATH", "reason": DEATH_REASON_RESOURCE_ZERO}
                )
                continue
            if match.phase == PHASE_NIGHT and tile_at(player.x, player.y) == "X":
                sample = self._night_x_sample(match, player)
                if not resolve_night_x_survival(player.exposure, sample):
                    self._kill_player(room, player, reason=DEATH_REASON_NIGHT_X_FAIL)
                    private_results[player.player_id]["events"].append(
                        {"event_type": "DEATH", "reason": DEATH_REASON_NIGHT_X_FAIL, "sample": sample}
                    )

        self._check_game_over(room)
        if match.game_over:
            match.endgame_summary = self._build_endgame_summary(room)
            self._clear_round(match)
            for p in room.players.values():
                private_results[p.player_id]["status_after"] = self._status_dict(p)
            return

        if all_rest or self._all_survivors_phase_ended(room):
            self._advance_phase(match, room)
        elif match.round >= self._phase_round_limit(match.phase):
            self._advance_phase(match, room)
        else:
            match.round += 1
            match.round_locked = False
            match.round_opened_at = datetime.now(UTC)

        match.pending_killers.clear()
        match.action_queue.clear()
        for p in room.players.values():
            private_results[p.player_id]["status_after"] = self._status_dict(p)

    def get_player_view(self, room: Room, player_id: str) -> dict:
        # 视图拼装已下沉到 PlayerViewAssembler，MatchService 仅保留委托入口。
        return self._player_view_assembler.build_player_view(room, player_id)

    def get_allowed_actions(self, room: Room, player_id: str) -> list[str]:
        # 计算当前玩家动作掩码（含战利品窗口特例）。
        player = self._require_player(room, player_id)
        match = self._require_match(room)
        if match.loot_window_state is not None:
            if player.player_id == match.loot_window_state.winner_player_id and player.alive:
                return [ACTION_GET, ACTION_TOSS]
            return []
        current_tile = tile_at(player.x, player.y)
        return self._allowed_actions(player, current_tile)

    def _validate_action(self, room: Room, player: PlayerState, action_type: str, payload: dict) -> None:
        # 校验动作及参数合法性，不通过则抛 ValueError。
        if action_type not in CORE_ACTION_TYPES:
            raise ValueError(f"{ERR_UNSUPPORTED_ACTION_PREFIX} {action_type}")

        if action_type == ACTION_MOVE:
            nx = payload.get("x")
            ny = payload.get("y")
            if not isinstance(nx, int) or not isinstance(ny, int):
                raise ValueError(ERR_MOVE_PAYLOAD_INVALID)
            if not is_in_bounds(nx, ny):
                raise ValueError(ERR_MOVE_OUT_OF_BOUNDS)
            dist = abs(nx - player.x) + abs(ny - player.y)
            if dist != 1:
                raise ValueError(ERR_MOVE_NOT_ADJACENT)
            return

        tile_type = tile_at(player.x, player.y)
        if action_type in {ACTION_EXPLORE, ACTION_TAKE, ACTION_ATTACK} and not is_safe_tile(tile_type):
            raise ValueError(f"{action_type} is only allowed on safe building tiles")

        if action_type == ACTION_TAKE:
            self._validate_item_bundle(
                payload,
                must_exist_in=ITEM_EFFECTS,
                max_total=MAX_TAKE_ITEMS_PER_ACTION,
            )
            if tile_key(player.x, player.y) not in player.explored_tiles:
                raise ValueError(ERR_TAKE_REQUIRES_EXPLORE)

        if action_type == ACTION_USE:
            if "items" in payload:
                self._validate_item_bundle(payload, must_exist_in=ITEM_EFFECTS)

        if action_type == ACTION_ATTACK:
            target_id = payload.get("target_id")
            if target_id is None:
                # Backward-compatible no-target ATTACK: only结算体力消耗，不触发对抗。
                return
            if not isinstance(target_id, str) or not target_id:
                raise ValueError(ERR_ATTACK_TARGET_ID_INVALID)
            if target_id == player.player_id:
                raise ValueError(ERR_ATTACK_TARGET_SELF)
            self._require_player(room, target_id)
            if target_id not in player.known_characters:
                raise ValueError(ERR_ATTACK_TARGET_NOT_DISCOVERED)
            loot = payload.get("loot")
            if loot is None:
                return
            if not isinstance(loot, dict):
                raise ValueError(ERR_ATTACK_LOOT_PAYLOAD_INVALID)
            loot_type = loot.get("type", LOOT_TYPE_TOSS)
            if loot_type not in LOOT_TYPES:
                raise ValueError(ERR_ATTACK_LOOT_TYPE_INVALID)
            if loot_type == LOOT_TYPE_GET:
                self._validate_item_bundle(
                    {"items": loot.get("items")},
                    must_exist_in=ITEM_EFFECTS,
                    max_total=MAX_TAKE_ITEMS_PER_ACTION,
                )

    def _validate_item_bundle(
        self,
        payload: dict,
        must_exist_in: dict[str, dict[str, int]],
        max_total: int | None = None,
    ) -> None:
        # 通用物品包校验：类型、数量、总量上限。
        items = payload.get("items")
        if not isinstance(items, dict) or not items:
            raise ValueError(ERR_ITEM_BUNDLE_INVALID)
        total = 0
        for item_id, qty in items.items():
            if item_id not in must_exist_in:
                raise ValueError(f"{ERR_ITEM_UNKNOWN_PREFIX} {item_id}")
            if not isinstance(qty, int) or qty <= 0:
                raise ValueError(f"{ERR_ITEM_QTY_INVALID_PREFIX} {item_id}")
            total += qty
        if max_total is not None and total > max_total:
            raise ValueError(f"{ERR_ITEM_TOTAL_EXCEEDED_PREFIX} {max_total}")

    def _apply_action_effect(
        self,
        room: Room,
        actor: PlayerState,
        action: ActionEnvelope,
    ) -> dict:
        # 执行动作效果并返回可回放结果结构。
        if action.action_type == ACTION_MOVE:
            actor.x = action.payload["x"]
            actor.y = action.payload["y"]
            self._append_recent_position(actor, day=action.day, phase=action.phase, round_no=action.round)
            tile_type = tile_at(actor.x, actor.y)
            if is_immediate_tile_death(tile_type, action.phase):
                self._kill_player(room, actor, reason=DEATH_REASON_FATAL_TILE)
            return {
                "result_type": "MOVE_RESULT",
                "to": {"x": actor.x, "y": actor.y, "tile_type": tile_type},
                "immediate_death": not actor.alive,
            }

        if action.action_type == ACTION_EXPLORE:
            return {
                "result_type": "EXPLORE_RESULT",
                # EXPLORE 快照在回合收口后统一回填，确保前端看到的是最终态。
                "snapshot": {"resources": {}, "characters": []},
            }

        if action.action_type == ACTION_USE:
            gains = {"water": 0, "food": 0}
            for item_id, qty in action.payload.get("items", {}).items():
                apply_item_use(actor, item_id, qty)
                gains["water"] += ITEM_EFFECTS[item_id].get("water", 0) * qty
                gains["food"] += ITEM_EFFECTS[item_id].get("food", 0) * qty
            return {
                "result_type": "USE_RESULT",
                "used_items": dict(action.payload.get("items", {})),
                "gains": gains,
            }

        if action.action_type == ACTION_TAKE:
            got = self._resolve_take(room, actor, action.payload["items"])
            requested = dict(action.payload["items"])
            return {
                "result_type": "TAKE_RESULT",
                "requested": requested,
                "obtained": got,
            }

        if action.action_type == ACTION_REST:
            actor.phase_ended = True
            return {"result_type": "REST_RESULT", "phase_ended": True}

        if action.action_type == ACTION_ATTACK:
            target_id = action.payload.get("target_id")
            if target_id:
                return self._resolve_attack(
                    room,
                    actor,
                    target_id,
                    action.payload.get("loot"),
                )
            return {"result_type": "ATTACK_RESULT", "outcome": "NO_TARGET"}
        return {}

    def _resolve_take(self, room: Room, actor: PlayerState, requested: dict[str, int]) -> dict[str, int]:
        # 处理 TAKE：按库存扣减并更新玩家背包与记忆失真标记。
        match = self._require_match(room)
        key = tile_key(actor.x, actor.y)
        stock = match.building_inventory.setdefault(key, {})
        taken_any = False
        obtained: dict[str, int] = {}
        for item_id, qty in requested.items():
            available = stock.get(item_id, 0)
            got = min(available, qty)
            if got <= 0:
                continue
            stock[item_id] = available - got
            actor.inventory[item_id] = actor.inventory.get(item_id, 0) + got
            taken_any = True
            obtained[item_id] = obtained.get(item_id, 0) + got
        if taken_any:
            self._memory_service.refresh_player_memory(room, actor)
            self._record_obtained(match, actor.player_id, obtained)
        return obtained

    def _winner_tile_memory_base(self, winner: PlayerState) -> dict[str, Any]:
        return self._memory_service.winner_tile_memory_base(winner)

    def _refresh_winner_memory_after_get_if_loser_dead(
        self,
        room: Room,
        winner: PlayerState,
        loser: PlayerState,
        *,
        winner_memory_before_get: dict[str, Any],
        loser_inventory_before_get: dict[str, int],
        obtained: dict[str, int],
    ) -> None:
        self._memory_service.refresh_winner_memory_after_get_if_loser_dead(
            room,
            winner,
            loser,
            winner_memory_before_get=winner_memory_before_get,
            loser_inventory_before_get=loser_inventory_before_get,
            obtained=obtained,
        )

    def _refresh_memories_after_settlement(
        self,
        room: Room,
        private_results: dict[str, dict],
    ) -> None:
        self._memory_service.refresh_memories_after_settlement(room, private_results)

    def _refresh_player_memory(self, room: Room, actor: PlayerState) -> None:
        self._memory_service.refresh_player_memory(room, actor)

    def _memory_last_seen_at(self, room: Room) -> dict[str, Any]:
        return self._memory_service.memory_last_seen_at(room)

    def _kill_player(
        self,
        room: Room,
        player: PlayerState,
        *,
        reason: str,
    ) -> None:
        # 执行死亡落地：掉落背包、修改存活态并登记死亡统计。
        if not player.alive:
            return
        match = self._require_match(room)
        killer_player_id = match.pending_killers.get(player.player_id)
        tile_type = tile_at(player.x, player.y)
        if is_safe_tile(tile_type) and player.inventory:
            key = tile_key(player.x, player.y)
            stock = match.building_inventory.setdefault(key, {})
            for item_id, qty in player.inventory.items():
                if qty <= 0:
                    continue
                stock[item_id] = stock.get(item_id, 0) + qty

        player.inventory.clear()
        player.alive = False
        player.phase_ended = True
        match.pending_killers.pop(player.player_id, None)
        self._record_death(match, player, reason=reason, killer_player_id=killer_player_id)

    def _night_x_sample(self, match: MatchState, player: PlayerState) -> float:
        # 夜晚 X 地块生存判定采样（可复现的伪随机）。
        seed = f"{match.day}:{match.phase}:{match.round}:{player.player_id}"
        return (zlib.crc32(seed.encode("utf-8")) % 10000) / 10000.0

    def _resolve_attack(
        self,
        room: Room,
        attacker: PlayerState,
        target_id: str,
        loot_payload: dict | None,
    ) -> dict:
        # 处理 ATTACK：判定胜负、伤害、战利品窗口与认知刷新。
        match = self._require_match(room)
        target = self._require_player(room, target_id)
        if not target.alive or not attacker.alive:
            attacker.known_characters.add(target.player_id)
            return {
                "result_type": "ATTACK_RESULT",
                "target_player_id": target_id,
                "outcome": "INVALID_TARGET",
                "reason": "TARGET_UNAVAILABLE",
            }
        if target.phase_ended:
            attacker.known_characters.add(target.player_id)
            return {
                "result_type": "ATTACK_RESULT",
                "target_player_id": target_id,
                "outcome": "INVALID_TARGET",
                "reason": "TARGET_PHASE_ENDED",
            }
        if target.x != attacker.x or target.y != attacker.y:
            # FIFO 语义：攻击有效性按动作执行当下位置判定。
            attacker.known_characters.add(target.player_id)
            return {
                "result_type": "ATTACK_RESULT",
                "target_player_id": target_id,
                "outcome": "INVALID_TARGET",
                "reason": "TARGET_LEFT_TILE",
            }

        attacker_score = self._attack_score(match, attacker, target, is_attacker=True)
        defender_score = self._attack_score(match, target, attacker, is_attacker=False)
        delta = attacker_score - defender_score

        winner: PlayerState | None = None
        loser: PlayerState | None = None
        if delta >= ATTACK_WIN_DELTA_THRESHOLD:
            winner, loser = attacker, target
        elif delta <= -ATTACK_WIN_DELTA_THRESHOLD:
            winner, loser = target, attacker

        if winner is None or loser is None:
            # 僵持：无战利品，仅互相建立认知。
            attacker.known_characters.add(target.player_id)
            target.known_characters.add(attacker.player_id)
            return {
                "result_type": "ATTACK_RESULT",
                "target_player_id": target.player_id,
                "outcome": "STALEMATE",
                "delta": delta,
            }

        self._apply_attack_damage(delta, winner, loser)
        winner_stat = match.player_stats.get(winner.player_id)
        if winner_stat is not None:
            winner_stat.combat_wins += 1
        loser_stat = match.player_stats.get(loser.player_id)
        if loser_stat is not None:
            loser_stat.combat_losses += 1
        match.pending_killers[loser.player_id] = winner.player_id
        match.loot_window_state = LootWindowState(
            winner_player_id=winner.player_id,
            loser_player_id=loser.player_id,
            day=match.day,
            phase=match.phase,
            round=match.round,
            opened_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._loot_window_timeout_sec),
        )

        # ATTACK 属于参与动作，双方认知刷新。
        attacker.known_characters.add(target.player_id)
        target.known_characters.add(attacker.player_id)
        return {
            "result_type": "ATTACK_RESULT",
            "target_player_id": target.player_id,
            "outcome": "WIN" if winner.player_id == attacker.player_id else "LOSE",
            "delta": delta,
            "winner": winner.player_id,
            "loser": loser.player_id,
            "loot_window": {
                "opened": True,
                "winner_player_id": winner.player_id,
                "loser_player_id": loser.player_id,
            },
        }

    def _attack_score(
        self,
        match: MatchState,
        actor: PlayerState,
        other: PlayerState,
        is_attacker: bool,
    ) -> int:
        # 对抗评分：资源/暴露/信息优势 + 可复现随机扰动。
        base = 10
        state_mod = self._resource_mod(actor) + self._exposure_mod(actor)
        info_mod = self._info_mod(actor, other)
        rand = self._attack_rand(match, actor.player_id, other.player_id, is_attacker)
        return base + state_mod + info_mod + rand

    def _resource_mod(self, player: PlayerState) -> int:
        # 资源状态修正项（水分+食物）。
        r = (player.water + player.food) / 2
        if r >= 80:
            return 3
        if r >= 60:
            return 2
        if r >= 40:
            return 0
        if r >= 20:
            return -2
        return -4

    def _exposure_mod(self, player: PlayerState) -> int:
        # 暴露值修正项。
        e = player.exposure
        if e < 20:
            return 1
        if e < 60:
            return 0
        if e < 80:
            return -1
        return -2

    def _info_mod(self, actor: PlayerState, other: PlayerState) -> int:
        # 信息优势修正项（是否已识别目标）。
        actor_found_other = other.player_id in actor.known_characters
        if actor_found_other:
            return 2
        return 0

    def _attack_rand(
        self,
        match: MatchState,
        actor_id: str,
        other_id: str,
        is_attacker: bool,
    ) -> int:
        # 对抗随机项（固定种子，保证可复现）。
        role = "A" if is_attacker else "D"
        seed = f"{match.day}:{match.phase}:{match.round}:{actor_id}:{other_id}:{role}"
        v = zlib.crc32(seed.encode("utf-8")) % 3
        return [-1, 0, 1][v]

    def _apply_attack_damage(self, delta: int, winner: PlayerState, loser: PlayerState) -> None:
        # 按胜负差值应用对抗伤害。
        strength = abs(delta)
        if strength >= 6:
            loser.water -= 20
            loser.food -= 20
            loser.exposure += 15
            winner.water -= 2
            winner.food -= 2
            winner.exposure += 1
            return
        if strength >= 4:
            loser.water -= 10
            loser.food -= 10
            loser.exposure += 10
            winner.water -= 1
            winner.food -= 1
            return
        loser.water -= 5
        loser.food -= 5
        loser.exposure += 5

    def _resolve_loot_window(
        self,
        match: MatchState,
        winner: PlayerState,
        loser: PlayerState,
        loot_payload: dict | None,
    ) -> dict:
        # 结算战利品选择（GET/TOSS）并返回实际获得结果。
        if not loser.inventory:
            return {"type": LOOT_TYPE_TOSS, "obtained": {}}

        loot_type = LOOT_TYPE_TOSS
        requested_items: dict[str, int] = {}
        if isinstance(loot_payload, dict):
            loot_type = loot_payload.get("type", LOOT_TYPE_TOSS)
            if loot_type == LOOT_TYPE_GET:
                requested_items = loot_payload.get("items", {})

        if loot_type != LOOT_TYPE_GET:
            return {"type": LOOT_TYPE_TOSS, "obtained": {}}

        total = 0
        for qty in requested_items.values():
            if isinstance(qty, int) and qty > 0:
                total += qty
        if total <= 0:
            return {"type": LOOT_TYPE_GET, "obtained": {}}

        left = MAX_TAKE_ITEMS_PER_ACTION
        obtained: dict[str, int] = {}
        for item_id, qty in requested_items.items():
            if left <= 0:
                break
            if not isinstance(qty, int) or qty <= 0:
                continue
            available = loser.inventory.get(item_id, 0)
            if available <= 0:
                continue
            got = min(available, qty, left)
            if got <= 0:
                continue
            loser.inventory[item_id] = available - got
            winner.inventory[item_id] = winner.inventory.get(item_id, 0) + got
            left -= got
            obtained[item_id] = obtained.get(item_id, 0) + got
        if obtained:
            self._record_obtained(match, winner.player_id, obtained)
        return {"type": LOOT_TYPE_GET, "obtained": obtained}

    def _attach_loot_resolution_to_attack_results(
        self,
        private_results: dict[str, dict],
        *,
        choice: str,
        obtained: dict[str, int],
    ) -> None:
        # 将战利品结论回填到对应 ATTACK 结果，便于前端回放。
        for result in private_results.values():
            actions = result.get("actions", [])
            for action in reversed(actions):
                if action.get("action_type") != ACTION_ATTACK:
                    continue
                action_result = action.get("result")
                if not isinstance(action_result, dict):
                    continue
                if action_result.get("result_type") != "ATTACK_RESULT":
                    continue
                action_result["loot"] = {"type": choice, "obtained": dict(obtained)}
                break

    def _record_obtained(self, match: MatchState, player_id: str, items: dict[str, int]) -> None:
        # 记录玩家本局累计获得资源统计。
        stat = match.player_stats.get(player_id)
        if stat is None:
            return
        for item_id, qty in items.items():
            if qty <= 0:
                continue
            stat.resources_obtained[item_id] = stat.resources_obtained.get(item_id, 0) + qty
            stat.resources_obtained_total += qty

    def _record_death(
        self,
        match: MatchState,
        player: PlayerState,
        *,
        reason: str,
        killer_player_id: str | None,
    ) -> None:
        # 记录死亡明细与击杀归属。
        stat = match.player_stats.get(player.player_id)
        if stat is None:
            return
        match.death_seq_counter += 1
        stat.deaths += 1
        stat.death_reason = reason
        stat.death_day = match.day
        stat.death_phase = match.phase
        stat.death_round = match.round
        stat.death_seq = match.death_seq_counter
        if killer_player_id is None:
            return
        killer = match.player_stats.get(killer_player_id)
        if killer is not None and killer_player_id != player.player_id:
            killer.kills += 1

    def _record_day_survival(self, room: Room) -> None:
        # 记录存活天数（仅对活着的玩家 +1）。
        match = self._require_match(room)
        for player in room.players.values():
            if not player.alive:
                continue
            stat = match.player_stats.get(player.player_id)
            if stat is not None:
                stat.days_survived += 1

    def _build_initial_map_inventory(self) -> dict[str, dict[str, int]]:
        # 基于地图建筑分布生成本局地块资源库存。
        inventory: dict[str, dict[str, int]] = {}
        building_tiles: dict[str, list[str]] = {}
        for y in range(1, 10):
            for x in range(1, 10):
                tile_type = tile_at(x, y)
                if not is_safe_tile(tile_type):
                    continue
                key = tile_key(x, y)
                building_tiles.setdefault(tile_type, []).append(key)
                inventory[key] = {}
        building_counts = {tile_type: len(keys) for tile_type, keys in building_tiles.items()}
        allocation_by_instance = allocate_resources_iterative_random(
            RESOURCE_TOTAL_DEFAULTS,
            building_counts,
        )
        for tile_type, keys in building_tiles.items():
            sorted_keys = sorted(keys)
            for idx, key in enumerate(sorted_keys, start=1):
                inventory[key] = dict(allocation_by_instance.get(f"{tile_type}_{idx}", {}))
        allocated_totals: dict[str, int] = {}
        for tile_inventory in inventory.values():
            for resource_id, qty in tile_inventory.items():
                allocated_totals[resource_id] = allocated_totals.get(resource_id, 0) + qty
        logger.info(
            "资源分配方案: %s; 总共分配: %s",
            inventory,
            allocated_totals,
        )
        return inventory

    def _allowed_actions(self, player: PlayerState, current_tile: str) -> list[str]:
        # 计算基础动作集合（含安全地块附加动作）。
        if not player.alive or player.phase_ended:
            return []
        actions = [ACTION_MOVE, ACTION_USE, ACTION_REST]
        if is_safe_tile(current_tile):
            actions.extend([ACTION_EXPLORE, ACTION_TAKE, ACTION_ATTACK])
        return actions

    def _status_dict(self, player: PlayerState) -> dict:
        # 导出玩家状态快照（用于事件/回放）。
        return {
            "water": player.water,
            "food": player.food,
            "exposure": player.exposure,
            "alive": player.alive,
            "phase_ended": player.phase_ended,
        }

    def _build_initial_player_inventory(self) -> dict[str, int]:
        # 基于随机采样生成玩家初始背包（本例仅含水和面包，且数量极少）。
        inventory: dict[str, int] = {}
        water_qty = random.randint(1, 1)
        bread_qty = random.randint(1, 1)
        if water_qty > 0:
            inventory["bottled_water"] = water_qty
        if bread_qty > 0:
            inventory["bread"] = bread_qty
        return inventory

    def _reset_recent_positions(self, player: PlayerState) -> None:
        # 清空轨迹缓存。
        player.recent_positions.clear()

    def _append_recent_position(self, player: PlayerState, *, day: int, phase: str, round_no: int) -> None:
        # 追加当前位置到有界轨迹缓存。
        row = {"x": player.x, "y": player.y, "day": day, "phase": phase, "round": round_no}
        player.recent_positions.append(row)
        if len(player.recent_positions) > self._recent_positions_maxlen:
            player.recent_positions.pop(0)

    def _tile_memory_view(self, player: PlayerState, x: int, y: int) -> dict[str, Any]:
        return self._memory_service.tile_memory_view(player, x, y)

    def _local_map_summary_view(self, player: PlayerState) -> dict[str, Any]:
        return self._memory_service.local_map_summary_view(player)

    def _loot_window_view(self, room: Room, player: PlayerState) -> dict | None:
        return self._player_view_assembler.loot_window_view(room, player)

    def _attack_target_candidates(self, room: Room, player: PlayerState) -> list[str]:
        return self._player_view_assembler.attack_target_candidates(room, player)

    def _fill_ai_players(self, room: Room) -> None:
        # 开局自动补齐 AI（受总人数与 AI 上限双重约束）。
        ai_count = len([p for p in room.players.values() if not p.is_human])
        ai_slots_left = max(0, self._max_ai_players - ai_count)
        total_slots_left = max(0, self._room_max_players - len(room.players))
        to_add = min(ai_slots_left, total_slots_left)

        ai_idx = 1
        added = 0
        while added < to_add:
            ai_id = f"ai_{ai_idx}"
            ai_idx += 1
            if ai_id in room.players:
                continue
            self.join_room(room, ai_id, is_human=False)
            added += 1

    def _assign_spawn_points(self, room: Room) -> None:
        # # 在可出生地块中为所有玩家分配起点坐标。
        # spawn_candidates: list[tuple[int, int]] = []
        # for y, row in enumerate(MAP_MATRIX, start=1):
        #     for x, tile_type in enumerate(row, start=1):
        #         if tile_type in SPAWN_ALLOWED_TILES:
        #             spawn_candidates.append((x, y))
        # if not spawn_candidates:
        #     raise ValueError("no spawnable building tiles found in map")

        # for player in room.players.values():
        #     player.x, player.y = random.choice(spawn_candidates)
        fixed = (4, 4)
        for player in room.players.values():
            player.x, player.y = fixed

    def _all_active_submitted(self, room: Room) -> bool:
        # 判断当前活跃玩家是否都已提交动作。
        match = self._require_match(room)
        active = [p for p in room.players.values() if p.alive and not p.phase_ended]
        if not active:
            return False
        submitted = {action.player_id for action in match.action_queue}
        return all(player.player_id in submitted for player in active)

    def _all_survivors_phase_ended(self, room: Room) -> bool:
        # 判断所有存活玩家是否都已结束本阶段（如 REST）。
        for player in room.players.values():
            if player.alive and not player.phase_ended:
                return False
        return True

    def _phase_round_limit(self, phase: str) -> int:
        # 获取当前阶段允许的最大回合数。
        if phase == PHASE_DAY:
            return self._max_day_phase_rounds
        return self._max_night_phase_rounds

    def _advance_phase(self, match: MatchState, room: Room) -> None:
        # 推进昼夜阶段并重置阶段内状态。
        if match.phase == PHASE_DAY:
            match.phase = PHASE_NIGHT
        else:
            self._record_day_survival(room)
            match.phase = PHASE_DAY
            match.day += 1
        match.round = 1
        match.round_locked = False
        match.round_opened_at = datetime.now(UTC)
        match.phase_base_upkeep_applied = False
        for player in room.players.values():
            if player.alive:
                player.phase_ended = False

    def _check_game_over(self, room: Room) -> None:
        # 按终局模式检查是否触发 game over。
        match = self._require_match(room)
        alive = [p for p in room.players.values() if p.alive]
        if room.end_mode == END_MODE_ALL_DEAD and not alive:
            match.game_over = True
            match.game_over_reason = END_MODE_ALL_DEAD
            return

        if room.end_mode == END_MODE_HUMAN_ALL_DEAD:
            human_alive = [p for p in alive if p.is_human]
            if not human_alive:
                match.game_over = True
                match.game_over_reason = END_MODE_HUMAN_ALL_DEAD

    def _build_endgame_summary(self, room: Room) -> dict:
        # 构建终局汇总（玩家统计、排行、真人存活信息）。
        match = self._require_match(room)
        player_stats_rows: list[dict] = []
        human_rows: list[dict] = []
        alive_humans: list[str] = []
        for player_id, player in room.players.items():
            stat = match.player_stats.get(player_id)
            if stat is None:
                continue
            row = {
                "player_id": stat.player_id,
                "is_human": stat.is_human,
                "days_survived": stat.days_survived,
                "explored_tiles_count": len(player.explored_tiles),
                "resources_obtained_total": stat.resources_obtained_total,
                "resources_obtained": dict(stat.resources_obtained),
                "death_reason": stat.death_reason if stat.death_reason else ("ALIVE" if player.alive else "UNKNOWN"),
                "combat_wins": stat.combat_wins,
                "combat_losses": stat.combat_losses,
                "kills": stat.kills,
                "deaths": stat.deaths,
            }
            player_stats_rows.append(row)
            if stat.is_human:
                human_rows.append(row)
                if player.alive:
                    alive_humans.append(player_id)

        join_seq_map = {pid: s.join_seq for pid, s in match.player_stats.items()}
        ranking = sorted(
            player_stats_rows,
            key=lambda r: (
                -int(r["days_survived"]),
                -int(r["resources_obtained_total"]),
                int(join_seq_map.get(r["player_id"], 10**9)),
            ),
        )
        ranking_rows = [
            {"rank": idx + 1, "player_id": row["player_id"], "days_survived": row["days_survived"]}
            for idx, row in enumerate(ranking)
        ]

        per_human_days = {row["player_id"]: row["days_survived"] for row in human_rows}
        last_alive_human_player_id: str | None = None
        if len(alive_humans) == 1:
            last_alive_human_player_id = alive_humans[0]
        elif len(alive_humans) == 0 and human_rows:
            sorted_humans = sorted(
                (
                    match.player_stats[row["player_id"]]
                    for row in human_rows
                    if row["player_id"] in match.player_stats
                ),
                key=lambda s: (-(s.days_survived), -(s.death_seq or 0), s.join_seq),
            )
            if sorted_humans:
                last_alive_human_player_id = sorted_humans[0].player_id

        return {
            "room_id": room.room_id,
            "end_mode": room.end_mode,
            "game_over_reason": match.game_over_reason,
            "final_time_state": {"day": match.day, "phase": match.phase, "round": match.round},
            "players": player_stats_rows,
            "human_record": {
                "last_alive_human_player_id": last_alive_human_player_id,
                "alive_human_player_ids": alive_humans,
                "human_survival_days": per_human_days,
                "human_survival_days_max": max(per_human_days.values(), default=0),
                "human_combat_wins_total": sum(int(r["combat_wins"]) for r in human_rows),
                "human_combat_losses_total": sum(int(r["combat_losses"]) for r in human_rows),
                "human_kills_total": sum(int(r["kills"]) for r in human_rows),
                "human_deaths_total": sum(int(r["deaths"]) for r in human_rows),
            },
            "ranking": ranking_rows,
        }

    def _clear_round(self, match: MatchState) -> None:
        # 清空本轮相关运行态（锁、队列、窗口、计时）。
        match.round_locked = False
        match.round_opened_at = None
        match.action_queue.clear()
        match.loot_window_state = None
        match.pending_settlement_private_results = None
        match.pending_killers.clear()

    def _require_match(self, room: Room) -> MatchState:
        # 读取对局状态，不存在则抛错。
        if room.match_state is None:
            raise ValueError(ERR_MATCH_NOT_STARTED)
        return room.match_state

    def _require_player(self, room: Room, player_id: str) -> PlayerState:
        # 读取玩家状态，不存在则抛错。
        if player_id not in room.players:
            raise ValueError(f"{ERR_UNKNOWN_PLAYER_PREFIX} {player_id}")
        return room.players[player_id]
