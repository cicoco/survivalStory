"""Round scheduling and event publishing orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import uuid

from src.ai.agent_adapter import AgentAdapter
from src.api.constants import (
    EVENT_ACTION_ACCEPTED,
    EVENT_GAME_OVER,
    EVENT_LOOT_WINDOW_RESOLVED,
    EVENT_LOOT_WINDOW_STARTED,
    EVENT_ROUND_SETTLED,
    EVENT_ROUND_STARTED,
    LOOT_WINDOW_RESOLVED_MESSAGE,
    LOOT_WINDOW_STARTED_MESSAGE,
    ROUND_SETTLED_MESSAGE,
    SCHEMA_GAME_OVER_SUMMARY_V1,
    SCHEMA_LOOT_WINDOW_RESOLVED_V1,
    SCHEMA_LOOT_WINDOW_STARTED_V1,
    SCHEMA_ROUND_SETTLED_PRIVATE_V1,
    build_round_started_message,
)
from src.api.payload_validation import PayloadValidator
from src.api.ws_hub import WsHub
from src.application.match_service import MatchService
from src.application.notification_service import NotificationService
from src.application.room_store import RoomStore
from src.domain.constants import (
    ACTION_GET,
    ACTION_REST,
    ACTION_TOSS,
    MAX_TAKE_ITEMS_PER_ACTION,
    ROOM_STATUS_IN_GAME,
)
from src.domain.models import Room
from src.infra.config import AppSettings


class RoundScheduler:
    """回合调度编排器：承接超时、AI 决策触发、结算与事件发布。"""
    def __init__(
        self,
        *,
        store: RoomStore,
        service: MatchService,
        notify: NotificationService,
        ws_hub: WsHub,
        payload_validator: PayloadValidator,
        ai_agent: AgentAdapter,
        settings: AppSettings,
        logger: logging.Logger,
    ) -> None:
        self._store = store
        self._service = service
        self._notify = notify
        self._ws_hub = ws_hub
        self._payload_validator = payload_validator
        self._ai_agent = ai_agent
        self._settings = settings
        self._logger = logger

    def _build_private_settlement_payload(self, private_result: dict) -> dict:
        actions = private_result.get("actions", [])
        events = private_result.get("events", [])
        return {
            "schema": SCHEMA_ROUND_SETTLED_PRIVATE_V1,
            "action_count": len(actions),
            "event_count": len(events),
            "actions": actions,
            "events": events,
            "status_before": private_result.get("status_before", {}),
            "status_after": private_result.get("status_after", {}),
        }

    def _build_game_over_summary_payload(self, room: Room) -> dict:
        summary = self._service.get_endgame_summary(room)
        if summary is None:
            raise ValueError("match not over")
        return {
            "schema": SCHEMA_GAME_OVER_SUMMARY_V1,
            "room_id": summary["room_id"],
            "end_mode": summary["end_mode"],
            "game_over_reason": summary["game_over_reason"],
            "final_time_state": summary["final_time_state"],
            "players": summary["players"],
            "human_record": summary["human_record"],
            "ranking": summary["ranking"],
        }

    def _build_round_timer_payload(self, room: Room) -> dict | None:
        match = room.match_state
        if match is None or match.round_opened_at is None:
            return None
        opened_at = match.round_opened_at
        deadline_at = opened_at + timedelta(seconds=self._settings.round_action_timeout_sec)
        remaining_sec = max(0, int((deadline_at - datetime.now(UTC)).total_seconds()))
        return {
            "opened_at": opened_at.isoformat(),
            "deadline_at": deadline_at.isoformat(),
            "timeout_sec": self._settings.round_action_timeout_sec,
            "remaining_sec": remaining_sec,
        }

    def _extract_loot_window_resolution(self, private_results: dict[str, dict]) -> tuple[str, dict]:
        for result in private_results.values():
            for event in result.get("events", []):
                if event.get("event_type") == "LOOT_WINDOW_RESOLVED":
                    return str(event.get("choice", "TOSS")), dict(event.get("obtained", {}))
        return "TOSS", {}

    def _next_trace_id(self, room: Room, source: str) -> str:
        match = room.match_state
        day = match.day if match else 0
        phase = match.phase if match else "NA"
        round_no = match.round if match else 0
        suffix = uuid.uuid4().hex[:8]
        return f"{room.room_id}:{day}:{phase}:{round_no}:{source}:{suffix}"

    def _ensure_trace_id(self, room: Room, trace_id: str | None, source: str) -> str:
        if trace_id:
            return trace_id
        return self._next_trace_id(room, source)

    def _log(self, event: str, room: Room, trace_id: str, **extra: object) -> None:
        match = room.match_state
        day = match.day if match else 0
        phase = match.phase if match else "NA"
        round_no = match.round if match else 0
        details = " ".join(f"{k}={v}" for k, v in extra.items())
        if details:
            self._logger.info(
                "scheduler event=%s room=%s day=%s phase=%s round=%s trace_id=%s %s",
                event,
                room.room_id,
                day,
                phase,
                round_no,
                trace_id,
                details,
            )
            return
        self._logger.info(
            "scheduler event=%s room=%s day=%s phase=%s round=%s trace_id=%s",
            event,
            room.room_id,
            day,
            phase,
            round_no,
            trace_id,
        )

    def _deadline_from_obs(self, obs: dict) -> datetime | None:
        timer = obs.get("round_timer", {})
        if not isinstance(timer, dict):
            return None
        text = timer.get("deadline_at")
        if not isinstance(text, str) or not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    async def auto_submit_ai_actions(self, room: Room, *, trace_id: str | None = None) -> None:
        # 为尚未提交动作的 AI 自动决策并提交；失败时回退 REST。
        match = room.match_state
        if match is None or match.game_over:
            return
        trace = self._ensure_trace_id(room, trace_id, "auto_ai")

        def pick_ai_loot_items(loser_inventory: dict[str, int]) -> dict[str, int]:
            if not loser_inventory:
                return {}
            picked: dict[str, int] = {}
            total = 0
            for item_id, qty in sorted(loser_inventory.items(), key=lambda row: (-int(row[1]), row[0])):
                if total >= MAX_TAKE_ITEMS_PER_ACTION:
                    break
                available = int(qty)
                if available <= 0:
                    continue
                take = min(available, MAX_TAKE_ITEMS_PER_ACTION - total)
                if take <= 0:
                    continue
                picked[item_id] = take
                total += take
            return picked

        lw = self._service.get_loot_window_state(room)
        if lw is not None:
            winner = room.players.get(lw.winner_player_id)
            loser = room.players.get(lw.loser_player_id)
            if winner is not None and loser is not None and (not winner.is_human) and winner.alive:
                loot_items = pick_ai_loot_items(loser.inventory)
                try:
                    if loot_items:
                        self._logger.debug(
                            "AI战利品处理: room=%s winner=%s action=GET items=%s day=%s phase=%s round=%s",
                            room.room_id,
                            winner.player_id,
                            loot_items,
                            match.day,
                            match.phase,
                            match.round,
                        )
                        self._service.submit_loot_window_action(room, winner.player_id, ACTION_GET, {"items": loot_items})
                        self._log("AI_LOOT_GET", room, trace, player_id=winner.player_id, items=loot_items)
                    else:
                        self._logger.debug(
                            "AI战利品处理: room=%s winner=%s action=TOSS day=%s phase=%s round=%s",
                            room.room_id,
                            winner.player_id,
                            match.day,
                            match.phase,
                            match.round,
                        )
                        self._service.submit_loot_window_action(room, winner.player_id, ACTION_TOSS, {})
                        self._log("AI_LOOT_TOSS", room, trace, player_id=winner.player_id)
                except ValueError:
                    pass
            return

        while not match.round_locked and not match.game_over:
            submitted = {a.player_id for a in match.action_queue}
            ai_to_act = [
                p
                for p in room.players.values()
                if (not p.is_human) and p.alive and (not p.phase_ended) and p.player_id not in submitted
            ]
            if not ai_to_act:
                return

            acted = 0
            for ai_player in ai_to_act:
                obs = self._service.get_player_view(room, ai_player.player_id)
                action_mask = self._service.get_allowed_actions(room, ai_player.player_id)
                action = self._ai_agent.decide(
                    obs,
                    action_mask,
                    deadline_at=self._deadline_from_obs(obs),
                )
                action_type = action.get("action_type", ACTION_REST)
                payload = action.get("payload", {})
                try:
                    a = self._service.submit_action(room, ai_player.player_id, action_type, payload)
                    self._logger.debug(
                        "AI自动提交动作: room=%s player=%s action=%s payload=%s day=%s phase=%s round=%s",
                        room.room_id,
                        ai_player.player_id,
                        action_type,
                        payload,
                        match.day,
                        match.phase,
                        match.round,
                    )
                    acted += 1
                    accepted = self._notify.publish_private(
                        room,
                        ai_player.player_id,
                        EVENT_ACTION_ACCEPTED,
                        {"action_id": a.action_id, "action_type": a.action_type},
                        trace_id=trace,
                    )
                    await self._ws_hub.send_to_player(room.room_id, ai_player.player_id, accepted["message"])
                    self._log("AI_ACTION_ACCEPTED", room, trace, player_id=ai_player.player_id, action=action_type)
                except ValueError:
                    if action_type != ACTION_REST:
                        try:
                            a = self._service.submit_action(room, ai_player.player_id, ACTION_REST, {})
                            self._logger.debug(
                                "AI动作回退REST: room=%s player=%s day=%s phase=%s round=%s",
                                room.room_id,
                                ai_player.player_id,
                                match.day,
                                match.phase,
                                match.round,
                            )
                            acted += 1
                            accepted = self._notify.publish_private(
                                room,
                                ai_player.player_id,
                                EVENT_ACTION_ACCEPTED,
                                {"action_id": a.action_id, "action_type": a.action_type},
                                trace_id=trace,
                            )
                            await self._ws_hub.send_to_player(room.room_id, ai_player.player_id, accepted["message"])
                            self._log("AI_ACTION_FALLBACK_REST", room, trace, player_id=ai_player.player_id)
                        except ValueError:
                            continue
            if acted == 0:
                return

    async def maybe_resolve_loot_window_timeout(
        self,
        room: Room,
        *,
        trace_id: str | None = None,
    ) -> dict[str, dict] | None:
        _ = self._ensure_trace_id(room, trace_id, "loot_timeout")
        return self._service.resolve_loot_window_timeout_if_needed(room)

    async def maybe_resolve_round_timeout(
        self,
        room: Room,
        *,
        trace_id: str | None = None,
    ) -> list[dict]:
        # 核心动作超时处理：仅对真人自动补 REST，并发送私有 accepted 事件。
        trace = self._ensure_trace_id(room, trace_id, "round_timeout")
        auto_actions = self._service.resolve_round_timeout_if_needed(room)
        emitted: list[dict] = []
        for action in auto_actions:
            accepted = self._notify.publish_private(
                room,
                action.player_id,
                EVENT_ACTION_ACCEPTED,
                {"action_id": action.action_id, "action_type": action.action_type, "auto": True},
                trace_id=trace,
            )
            await self._ws_hub.send_to_player(room.room_id, action.player_id, accepted["message"])
            emitted.append(accepted)
            self._log("HUMAN_TIMEOUT_AUTO_REST", room, trace, player_id=action.player_id)
        return emitted

    async def publish_round_settled(
        self,
        room: Room,
        private_results: dict[str, dict],
        *,
        trace_id: str | None = None,
    ) -> None:
        trace = self._ensure_trace_id(room, trace_id, "round_settled")
        private_payload_by_player = {
            player_id: self._build_private_settlement_payload(result)
            for player_id, result in private_results.items()
        }
        for payload in private_payload_by_player.values():
            self._payload_validator.validate_round_private(payload)
        settled_msgs = self._notify.publish(
            room,
            EVENT_ROUND_SETTLED,
            {"message": ROUND_SETTLED_MESSAGE},
            private_payload_by_player=private_payload_by_player,
            trace_id=trace,
        )
        await self._ws_hub.fanout(room.room_id, settled_msgs)
        self._log("ROUND_SETTLED", room, trace)

    async def publish_round_started(self, room: Room, *, trace_id: str | None = None) -> None:
        if room.match_state is None:
            return
        trace = self._ensure_trace_id(room, trace_id, "round_started")
        payload = {
            "message": build_round_started_message(
                room.match_state.day, room.match_state.phase, room.match_state.round
            )
        }
        timer_payload = self._build_round_timer_payload(room)
        if timer_payload is not None:
            payload["round_timer"] = timer_payload
        next_round_msgs = self._notify.publish(room, EVENT_ROUND_STARTED, payload, trace_id=trace)
        await self._ws_hub.fanout(room.room_id, next_round_msgs)
        self._log("ROUND_STARTED", room, trace)

    async def publish_game_over(self, room: Room, *, trace_id: str | None = None) -> None:
        trace = self._ensure_trace_id(room, trace_id, "game_over")
        payload = self._build_game_over_summary_payload(room)
        self._payload_validator.validate_game_over_summary(payload)
        msgs = self._notify.publish(room, EVENT_GAME_OVER, payload, trace_id=trace)
        await self._ws_hub.fanout(room.room_id, msgs)
        self._log("GAME_OVER", room, trace)
        if room.status == ROOM_STATUS_IN_GAME and room.match_state and room.match_state.game_over:
            self._service.reset_room_for_next_match(room, room.host_player_id)
            self._notify.clear_room_history(room.room_id)

    async def publish_loot_window_started(self, room: Room, *, trace_id: str | None = None) -> None:
        lw = self._service.get_loot_window_state(room)
        if lw is None:
            return
        trace = self._ensure_trace_id(room, trace_id, "loot_started")
        payload = {
            "schema": SCHEMA_LOOT_WINDOW_STARTED_V1,
            "message": LOOT_WINDOW_STARTED_MESSAGE,
            "winner_player_id": lw.winner_player_id,
            "loser_player_id": lw.loser_player_id,
            "expires_at": lw.expires_at.isoformat(),
        }
        self._payload_validator.validate_loot_window_started(payload)
        msgs = self._notify.publish(room, EVENT_LOOT_WINDOW_STARTED, payload, trace_id=trace)
        await self._ws_hub.fanout(room.room_id, msgs)
        self._log("LOOT_WINDOW_STARTED", room, trace, winner=lw.winner_player_id, loser=lw.loser_player_id)

    async def publish_loot_window_resolved(
        self,
        room: Room,
        *,
        winner_player_id: str,
        loser_player_id: str,
        private_results: dict[str, dict],
        trace_id: str | None = None,
    ) -> None:
        trace = self._ensure_trace_id(room, trace_id, "loot_resolved")
        choice, obtained = self._extract_loot_window_resolution(private_results)
        payload = {
            "schema": SCHEMA_LOOT_WINDOW_RESOLVED_V1,
            "message": LOOT_WINDOW_RESOLVED_MESSAGE,
            "winner_player_id": winner_player_id,
            "loser_player_id": loser_player_id,
            "choice": choice,
            "obtained": obtained,
        }
        self._payload_validator.validate_loot_window_resolved(payload)
        msgs = self._notify.publish(room, EVENT_LOOT_WINDOW_RESOLVED, payload, trace_id=trace)
        await self._ws_hub.fanout(room.room_id, msgs)
        self._log("LOOT_WINDOW_RESOLVED", room, trace, winner=winner_player_id, loser=loser_player_id, choice=choice)

    async def settle_and_notify(self, room: Room, *, trace_id: str | None = None) -> None:
        # 结算主流程：先 settle，再按状态发布 loot/round/game_over 事件。
        trace = self._ensure_trace_id(room, trace_id, "settle")
        private_results = self._service.settle_round(room)
        if self._service.get_loot_window_state(room) is not None:
            await self.publish_loot_window_started(room, trace_id=trace)
            return

        await self.publish_round_settled(room, private_results, trace_id=trace)
        if room.match_state and room.match_state.game_over:
            await self.publish_game_over(room, trace_id=trace)
        elif room.match_state:
            await self.publish_round_started(room, trace_id=trace)

    async def resolve_timeouts_and_notify(self, room: Room, *, trace_id: str | None = None) -> bool:
        trace = self._ensure_trace_id(room, trace_id, "resolve_timeouts")
        if room.match_state is not None:
            await self.maybe_resolve_round_timeout(room, trace_id=trace)
        if room.match_state is not None:
            lw_before_timeout = self._service.get_loot_window_state(room)
            timeout_resolved = await self.maybe_resolve_loot_window_timeout(room, trace_id=trace)
            if timeout_resolved is not None:
                winner_player_id = lw_before_timeout.winner_player_id if lw_before_timeout else ""
                loser_player_id = lw_before_timeout.loser_player_id if lw_before_timeout else ""
                await self.publish_round_settled(room, timeout_resolved, trace_id=trace)
                if winner_player_id and loser_player_id:
                    await self.publish_loot_window_resolved(
                        room,
                        winner_player_id=winner_player_id,
                        loser_player_id=loser_player_id,
                        private_results=timeout_resolved,
                        trace_id=trace,
                    )
                if room.match_state and room.match_state.game_over:
                    await self.publish_game_over(room, trace_id=trace)
                elif room.match_state:
                    await self.publish_round_started(room, trace_id=trace)
                return True
        return False

    async def process_active_rooms_once(self) -> None:
        # 后台 tick 单次执行：超时处理 -> AI 自动补动作 -> 条件满足则结算。
        for room in list(self._store.list_all()):
            if room.status != ROOM_STATUS_IN_GAME:
                continue
            if room.match_state is None:
                continue

            trace = self._next_trace_id(room, "tick")
            # self._log("TICK_BEGIN", room, trace)
            resolved = await self.resolve_timeouts_and_notify(room, trace_id=trace)
            if resolved:
                continue

            await self.auto_submit_ai_actions(room, trace_id=trace)
            if room.match_state and room.match_state.round_locked and room.match_state.action_queue:
                await self.settle_and_notify(room, trace_id=trace)
