"""FastAPI application entry for Phase 3."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.ai.llm_policy import LLMPolicy
from src.ai.rule_bot import RuleBot
from src.api.constants import (
    build_game_started_message,
    build_round_started_message,
    ERROR_ACTION_INVALID,
    ERROR_ACTION_NOT_ALLOWED_ON_TILE,
    ERROR_ALREADY_SUBMITTED,
    ERROR_ATTACK_TARGET_NOT_DISCOVERED,
    ERROR_MOVE_NOT_ADJACENT,
    ERROR_MOVE_OUT_OF_BOUNDS,
    ERROR_LOOT_WINDOW_ACTION_INVALID,
    ERROR_LOOT_WINDOW_NOT_OPEN,
    ERROR_LOOT_WINDOW_ONLY_WINNER,
    ERROR_MATCH_NOT_OVER,
    ERROR_ONLY_HOST_CAN_RESET,
    ERROR_PHASE_ENDED,
    ERROR_ROUND_LOCKED,
    ERROR_UNKNOWN_PLAYER,
    EVENT_ACTION_ACCEPTED,
    EVENT_ACTION_REJECTED,
    EVENT_GAME_STARTED,
    EVENT_GAME_OVER,
    EVENT_PLAYER_LEFT,
    EVENT_ROOM_CLOSED,
    EVENT_ROOM_DISBANDED,
    EVENT_ROUND_SETTLED,
    EVENT_ROUND_STARTED,
    LOOT_WINDOW_STARTED_MESSAGE,
    ROUND_SETTLED_MESSAGE,
    SCHEMA_ACTION_REJECTED_V1,
    SCHEMA_GAME_OVER_SUMMARY_V1,
    SCHEMA_ROUND_SETTLED_PRIVATE_V1,
)
from src.application.match_service import MatchService
from src.application.notification_service import NotificationService
from src.application.room_store import RoomStore
from src.api.ws_hub import WsHub
from src.api.payload_validation import PayloadValidator
from src.domain.constants import ACTION_REST, ACTION_TOSS
from src.domain.models import Room
from src.domain.errors import (
    ERR_ACTION_NOT_ALLOWED_ON_TILE_SUFFIX,
    ERR_ALREADY_SUBMITTED,
    ERR_ATTACK_TARGET_NOT_DISCOVERED,
    ERR_MOVE_NOT_ADJACENT,
    ERR_MOVE_OUT_OF_BOUNDS,
    ERR_LOOT_WINDOW_ACTION_INVALID,
    ERR_LOOT_WINDOW_NOT_OPEN,
    ERR_LOOT_WINDOW_ONLY_WINNER_CAN_ACT,
    ERR_MATCH_NOT_OVER,
    ERR_ONLY_HOST_CAN_RESET,
    ERR_PLAYER_CANNOT_ACT,
    ERR_ROUND_LOCKED,
    ERR_UNKNOWN_PLAYER_PREFIX,
)
from src.infra.config import load_settings


class CreateRoomRequest(BaseModel):
    room_id: str
    host_player_id: str
    end_mode: str


class JoinRoomRequest(BaseModel):
    player_id: str
    is_human: bool = True


class ActionRequest(BaseModel):
    player_id: str
    action_type: str
    payload: dict = Field(default_factory=dict)


class LeaveRoomRequest(BaseModel):
    player_id: str


class ResetRoomRequest(BaseModel):
    player_id: str


def create_app() -> FastAPI:
    app = FastAPI(title="survival-story", version="0.1.0")
    root_dir = Path(__file__).resolve().parents[2]
    web_dir = root_dir / "web"
    assets_dir = web_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    settings = load_settings()
    store = RoomStore()
    service = MatchService(
        loot_window_timeout_sec=settings.loot_window_timeout_sec,
        round_action_timeout_sec=settings.round_action_timeout_sec,
        room_max_players=settings.room_max_players,
        max_ai_players=settings.max_ai_players,
    )
    notify = NotificationService(history_limit=settings.notification_history_limit)
    ws_hub = WsHub()
    payload_validator = PayloadValidator()
    ai_policy_mode = settings.ai_policy
    if ai_policy_mode == "llm":
        try:
            ai_policy = LLMPolicy(
                model=settings.openai_model,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                discovery_timeout_ms=settings.llm_discovery_timeout_ms,
                intent_timeout_ms=settings.llm_intent_timeout_ms,
            )
        except Exception:
            ai_policy = RuleBot()
    else:
        ai_policy = RuleBot()

    async def auto_submit_ai_actions(room: Room) -> None:
        match = room.match_state
        if match is None or match.game_over:
            return

        # Loot window: if AI is winner, auto choose TOSS as safe default.
        lw = service.get_loot_window_state(room)
        if lw is not None:
            winner = room.players.get(lw.winner_player_id)
            if winner is not None and (not winner.is_human) and winner.alive:
                try:
                    service.submit_loot_window_action(room, winner.player_id, ACTION_TOSS, {})
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
                obs = service.get_player_view(room, ai_player.player_id)
                action = ai_policy.choose_action(obs, obs["allowed_actions"])
                action_type = action.get("action_type", ACTION_REST)
                payload = action.get("payload", {})
                try:
                    a = service.submit_action(room, ai_player.player_id, action_type, payload)
                    acted += 1
                    accepted = notify.publish_private(
                        room,
                        ai_player.player_id,
                        EVENT_ACTION_ACCEPTED,
                        {"action_id": a.action_id, "action_type": a.action_type},
                    )
                    await ws_hub.send_to_player(room.room_id, ai_player.player_id, accepted["message"])
                except ValueError:
                    # Force safe fallback for this turn.
                    if action_type != ACTION_REST:
                        try:
                            a = service.submit_action(room, ai_player.player_id, ACTION_REST, {})
                            acted += 1
                            accepted = notify.publish_private(
                                room,
                                ai_player.player_id,
                                EVENT_ACTION_ACCEPTED,
                                {"action_id": a.action_id, "action_type": a.action_type},
                            )
                            await ws_hub.send_to_player(room.room_id, ai_player.player_id, accepted["message"])
                        except ValueError:
                            continue
            if acted == 0:
                return

    def build_rejected_payload(room: Room, player_id: str, exc: ValueError) -> dict:
        reason = str(exc)
        allowed_actions: list[str] = []
        try:
            allowed_actions = service.get_allowed_actions(room, player_id)
        except Exception:
            allowed_actions = []
        error_code = ERROR_ACTION_INVALID
        if reason.startswith(ERR_UNKNOWN_PLAYER_PREFIX):
            error_code = ERROR_UNKNOWN_PLAYER
        elif reason == ERR_PLAYER_CANNOT_ACT:
            error_code = ERROR_PHASE_ENDED
        elif reason == ERR_ROUND_LOCKED:
            error_code = ERROR_ROUND_LOCKED
        elif reason == ERR_ALREADY_SUBMITTED:
            error_code = ERROR_ALREADY_SUBMITTED
        elif reason == ERR_MOVE_OUT_OF_BOUNDS:
            error_code = ERROR_MOVE_OUT_OF_BOUNDS
        elif reason == ERR_MOVE_NOT_ADJACENT:
            error_code = ERROR_MOVE_NOT_ADJACENT
        elif reason == ERR_ATTACK_TARGET_NOT_DISCOVERED:
            error_code = ERROR_ATTACK_TARGET_NOT_DISCOVERED
        elif reason.endswith(ERR_ACTION_NOT_ALLOWED_ON_TILE_SUFFIX):
            error_code = ERROR_ACTION_NOT_ALLOWED_ON_TILE
        elif reason == ERR_LOOT_WINDOW_NOT_OPEN:
            error_code = ERROR_LOOT_WINDOW_NOT_OPEN
        elif reason == ERR_LOOT_WINDOW_ONLY_WINNER_CAN_ACT:
            error_code = ERROR_LOOT_WINDOW_ONLY_WINNER
        elif reason == ERR_LOOT_WINDOW_ACTION_INVALID:
            error_code = ERROR_LOOT_WINDOW_ACTION_INVALID
        elif reason == ERR_MATCH_NOT_OVER:
            error_code = ERROR_MATCH_NOT_OVER
        elif reason == ERR_ONLY_HOST_CAN_RESET:
            error_code = ERROR_ONLY_HOST_CAN_RESET

        return {
            "schema": SCHEMA_ACTION_REJECTED_V1,
            "error_code": error_code,
            "reason": reason,
            "allowed_actions": allowed_actions,
        }

    def build_private_settlement_payload(private_result: dict) -> dict:
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

    def build_game_over_summary_payload(room: Room) -> dict:
        summary = service.get_endgame_summary(room)
        if summary is None:
            raise ValueError(ERR_MATCH_NOT_OVER)
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

    async def maybe_resolve_loot_window_timeout(room: Room) -> dict[str, dict] | None:
        private_results = service.resolve_loot_window_timeout_if_needed(room)
        if private_results is None:
            return None
        return private_results

    async def maybe_resolve_round_timeout(room: Room) -> list[dict]:
        auto_actions = service.resolve_round_timeout_if_needed(room)
        emitted: list[dict] = []
        for action in auto_actions:
            accepted = notify.publish_private(
                room,
                action.player_id,
                EVENT_ACTION_ACCEPTED,
                {"action_id": action.action_id, "action_type": action.action_type, "auto": True},
            )
            await ws_hub.send_to_player(room.room_id, action.player_id, accepted["message"])
            emitted.append(accepted)
        return emitted

    async def publish_round_settled(room: Room, private_results: dict[str, dict]) -> None:
        private_payload_by_player = {
            player_id: build_private_settlement_payload(result)
            for player_id, result in private_results.items()
        }
        for payload in private_payload_by_player.values():
            payload_validator.validate_round_private(payload)
        settled_msgs = notify.publish(
            room,
            EVENT_ROUND_SETTLED,
            {"message": ROUND_SETTLED_MESSAGE},
            private_payload_by_player=private_payload_by_player,
        )
        await ws_hub.fanout(room.room_id, settled_msgs)

    async def publish_round_started(room: Room) -> None:
        if room.match_state is None:
            return
        next_round_msgs = notify.publish(
            room,
            EVENT_ROUND_STARTED,
            {
                "message": build_round_started_message(
                    room.match_state.day, room.match_state.phase, room.match_state.round
                )
            },
        )
        await ws_hub.fanout(room.room_id, next_round_msgs)

    async def publish_game_over(room: Room) -> None:
        payload = build_game_over_summary_payload(room)
        payload_validator.validate_game_over_summary(payload)
        msgs = notify.publish(room, EVENT_GAME_OVER, payload)
        await ws_hub.fanout(room.room_id, msgs)

    async def publish_loot_window_started(room: Room) -> None:
        lw = service.get_loot_window_state(room)
        if lw is None:
            return
        msgs = notify.publish(
            room,
            EVENT_ROUND_STARTED,
            {
                "message": LOOT_WINDOW_STARTED_MESSAGE,
                "mode": "LOOT_WINDOW",
                "winner_player_id": lw.winner_player_id,
                "loser_player_id": lw.loser_player_id,
                "expires_at": lw.expires_at.isoformat(),
            },
        )
        await ws_hub.fanout(room.room_id, msgs)

    async def settle_and_notify(room: Room) -> None:
        private_results = service.settle_round(room)
        if service.get_loot_window_state(room) is not None:
            await publish_loot_window_started(room)
            return

        await publish_round_settled(room, private_results)
        if room.match_state and room.match_state.game_over:
            await publish_game_over(room)
        elif room.match_state:
            await publish_round_started(room)

    @app.get("/")
    async def root_page() -> RedirectResponse:
        return RedirectResponse(url="/lobby")

    @app.get("/lobby")
    async def lobby_page() -> FileResponse:
        return FileResponse(web_dir / "lobby.html")

    @app.get("/game")
    async def game_page() -> FileResponse:
        return FileResponse(web_dir / "game.html")

    @app.post("/rooms")
    async def create_room(req: CreateRoomRequest) -> dict:
        room = service.create_room(req.room_id, req.host_player_id, req.end_mode)
        store.add(room)
        return {"room_id": room.room_id, "host_player_id": room.host_player_id, "status": room.status}

    @app.post("/rooms/{room_id}/join")
    async def join_room(room_id: str, req: JoinRoomRequest) -> dict:
        room = store.get(room_id)
        player = service.join_room(room, req.player_id, req.is_human)
        return {"player_id": player.player_id, "join_seq": player.join_seq, "is_human": player.is_human}

    @app.post("/rooms/{room_id}/leave")
    async def leave_room(room_id: str, req: LeaveRoomRequest) -> dict:
        room = store.get(room_id)
        outcome = service.leave_room(room, req.player_id)

        if outcome["mode"] == "DISBANDED":
            msgs = notify.publish(
                room,
                EVENT_ROOM_DISBANDED,
                {"room_id": room.room_id, "host_player_id": req.player_id},
            )
            await ws_hub.fanout(room.room_id, msgs)
            store.remove(room_id)
            return {"ok": True, "mode": outcome["mode"], "removed": True}

        if outcome["mode"] == "CLOSED_BY_HOST":
            summary_payload = build_game_over_summary_payload(room)
            payload_validator.validate_game_over_summary(summary_payload)
            msgs = notify.publish(
                room,
                EVENT_ROOM_CLOSED,
                {"room_id": room.room_id, "host_player_id": req.player_id, "summary": summary_payload},
            )
            await ws_hub.fanout(room.room_id, msgs)
            await publish_game_over(room)
            return {"ok": True, "mode": outcome["mode"]}

        if outcome["mode"] in {"LEFT_WAITING", "LEFT_IN_GAME_AS_DEATH"}:
            msgs = notify.publish(
                room,
                EVENT_PLAYER_LEFT,
                {"room_id": room.room_id, "player_id": req.player_id, "mode": outcome["mode"]},
            )
            await ws_hub.fanout(room.room_id, msgs)
            if room.match_state and room.match_state.game_over:
                await publish_game_over(room)
            return {"ok": True, "mode": outcome["mode"]}

        return {"ok": True, "mode": outcome["mode"]}

    @app.post("/rooms/{room_id}/start")
    async def start_room(room_id: str) -> dict:
        room = store.get(room_id)
        match = service.start_match(room)
        started_msgs = notify.publish(
            room,
            EVENT_GAME_STARTED,
            {"message": build_game_started_message(room.room_id, len(room.players))},
        )
        await ws_hub.fanout(room.room_id, started_msgs)
        round_msgs = notify.publish(
            room,
            EVENT_ROUND_STARTED,
            {"message": build_round_started_message(match.day, match.phase, match.round)},
        )
        await ws_hub.fanout(room.room_id, round_msgs)
        await maybe_resolve_round_timeout(room)
        await auto_submit_ai_actions(room)

        if room.match_state.round_locked:
            await settle_and_notify(room)
        return {
            "room_id": room.room_id,
            "status": room.status,
            "day": match.day,
            "phase": match.phase,
            "round": match.round,
        }

    @app.post("/rooms/{room_id}/actions")
    async def submit_action(room_id: str, req: ActionRequest) -> dict:
        room = store.get(room_id)
        if room.match_state is not None:
            await maybe_resolve_round_timeout(room)
        if room.match_state is not None:
            timeout_resolved = await maybe_resolve_loot_window_timeout(room)
            if timeout_resolved is not None:
                await publish_round_settled(room, timeout_resolved)
                if room.match_state and room.match_state.game_over:
                    await publish_game_over(room)
                elif room.match_state:
                    await publish_round_started(room)

        if service.get_loot_window_state(room) is not None:
            try:
                private_results = service.submit_loot_window_action(
                    room, req.player_id, req.action_type, req.payload
                )
            except ValueError as exc:
                rejected_payload = build_rejected_payload(room, req.player_id, exc)
                payload_validator.validate_action_rejected(rejected_payload)
                rejected = notify.publish_private(
                    room,
                    req.player_id,
                    EVENT_ACTION_REJECTED,
                    rejected_payload,
                )
                await ws_hub.send_to_player(room.room_id, req.player_id, rejected["message"])
                return {"accepted": False, "error": rejected_payload}

            await publish_round_settled(room, private_results)
            if room.match_state and room.match_state.game_over:
                await publish_game_over(room)
            elif room.match_state:
                await publish_round_started(room)
            return {
                "accepted": True,
                "settled": True,
                "round_locked": room.match_state.round_locked,
                "mode": "LOOT_WINDOW",
            }

        try:
            action = service.submit_action(room, req.player_id, req.action_type, req.payload)
        except ValueError as exc:
            rejected_payload = build_rejected_payload(room, req.player_id, exc)
            payload_validator.validate_action_rejected(rejected_payload)
            rejected = notify.publish_private(
                room,
                req.player_id,
                EVENT_ACTION_REJECTED,
                rejected_payload,
            )
            await ws_hub.send_to_player(room.room_id, req.player_id, rejected["message"])
            return {"accepted": False, "error": rejected_payload}
        accepted = notify.publish_private(
            room,
            req.player_id,
            EVENT_ACTION_ACCEPTED,
            {"action_id": action.action_id, "action_type": action.action_type},
        )
        await ws_hub.send_to_player(room.room_id, req.player_id, accepted["message"])
        await auto_submit_ai_actions(room)

        match = room.match_state
        settled = False
        if match and match.round_locked:
            await settle_and_notify(room)
            settled = True

        return {
            "accepted": True,
            "settled": settled,
            "action_id": action.action_id,
            "round_locked": room.match_state.round_locked,
        }

    @app.post("/rooms/{room_id}/tick-ai")
    async def tick_ai(room_id: str) -> dict:
        room = store.get(room_id)
        if room.match_state is not None:
            await maybe_resolve_round_timeout(room)
        if room.match_state is not None:
            timeout_resolved = await maybe_resolve_loot_window_timeout(room)
            if timeout_resolved is not None:
                await publish_round_settled(room, timeout_resolved)
                if room.match_state and room.match_state.game_over:
                    await publish_game_over(room)
                elif room.match_state:
                    await publish_round_started(room)
                return {"ok": True, "settled": True, "reason": "loot_window_timeout"}

        await auto_submit_ai_actions(room)
        if service.get_loot_window_state(room) is not None:
            await publish_loot_window_started(room)
            return {"ok": True, "settled": False, "mode": "LOOT_WINDOW"}
        settled = False
        if room.match_state and room.match_state.round_locked:
            await settle_and_notify(room)
            settled = True
        return {"ok": True, "settled": settled}

    @app.get("/rooms/{room_id}/players/{player_id}/view")
    async def player_view(room_id: str, player_id: str) -> dict:
        room = store.get(room_id)
        return service.get_player_view(room, player_id)

    @app.get("/rooms/{room_id}/players/{player_id}/history")
    async def player_history(room_id: str, player_id: str, last_seen_seq: int = 0) -> dict:
        store.get(room_id)
        rows = notify.history(room_id, player_id, last_seen_seq=last_seen_seq)
        return {"items": rows, "count": len(rows)}

    @app.get("/rooms/{room_id}/summary")
    async def room_summary(room_id: str) -> dict:
        room = store.get(room_id)
        try:
            summary_payload = build_game_over_summary_payload(room)
        except ValueError as exc:
            return {"accepted": False, "error": build_rejected_payload(room, room.host_player_id, exc)}
        payload_validator.validate_game_over_summary(summary_payload)
        return summary_payload

    @app.post("/rooms/{room_id}/reset")
    async def reset_room(room_id: str, req: ResetRoomRequest) -> dict:
        room = store.get(room_id)
        try:
            outcome = service.reset_room_for_next_match(room, req.player_id)
        except ValueError as exc:
            rejected_payload = build_rejected_payload(room, req.player_id, exc)
            payload_validator.validate_action_rejected(rejected_payload)
            rejected = notify.publish_private(
                room,
                req.player_id,
                EVENT_ACTION_REJECTED,
                rejected_payload,
            )
            await ws_hub.send_to_player(room.room_id, req.player_id, rejected["message"])
            return {"accepted": False, "error": rejected_payload}
        notify.clear_room_history(room.room_id)
        return {"accepted": True, "mode": outcome["mode"], "status": outcome["status"]}

    @app.websocket("/ws/{room_id}/{player_id}")
    async def room_ws(room_id: str, player_id: str, ws: WebSocket) -> None:
        store.get(room_id)
        await ws_hub.connect(room_id, player_id, ws)
        try:
            while True:
                # Keep alive and allow client ping frames/content.
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_hub.disconnect(room_id, player_id, ws)

    return app


app = create_app()
