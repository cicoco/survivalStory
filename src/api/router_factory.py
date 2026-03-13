"""Build API routers grouped by responsibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse

from src.api.constants import (
    build_game_started_message,
    EVENT_ACTION_ACCEPTED,
    EVENT_ACTION_REJECTED,
    EVENT_GAME_STARTED,
    EVENT_PLAYER_LEFT,
    EVENT_ROOM_CLOSED,
    EVENT_ROOM_DISBANDED,
)
from src.api.schemas import (
    ActionRequest,
    CleanupRoomRequest,
    CreateRoomRequest,
    JoinRoomRequest,
    LeaveRoomRequest,
    ResetRoomRequest,
)
from src.domain.constants import ROOM_STATUS_CLOSED, ROOM_STATUS_DISBANDED
from src.domain.models import Room
from src.infra.config import AppSettings


@dataclass(slots=True)
class ApiDeps:
    # 各 router 运行时依赖，通过 create_app 注入。
    service: object
    store: object
    notify: object
    ws_hub: object
    scheduler: object
    payload_validator: object
    settings: AppSettings
    web_dir: object
    logger: object
    get_room_or_404: Callable[[str], Room]
    find_player_active_room: Callable[[str], str | None]
    make_trace_id: Callable[[Room, str], str]
    build_rejected_payload: Callable[[Room, str, ValueError], dict]
    build_game_over_summary_payload: Callable[[Room], dict]
    build_room_brief: Callable[..., dict]


def build_routers(deps: ApiDeps) -> tuple[APIRouter, APIRouter, APIRouter]:
    ops_router = APIRouter(tags=["ops"])
    gameplay_router = APIRouter(tags=["gameplay"])
    debug_router = APIRouter(tags=["debug"])

    async def tick_ai_impl(room_id: str) -> dict:
        # 手动调度入口：用于调试/补偿触发 AI 决策与回合推进。
        room = deps.get_room_or_404(room_id)
        trace_id = deps.make_trace_id(room, "tick_ai")
        resolved = await deps.scheduler.resolve_timeouts_and_notify(room, trace_id=trace_id)
        if resolved:
            return {"ok": True, "settled": True, "reason": "loot_window_timeout"}

        await deps.scheduler.auto_submit_ai_actions(room, trace_id=trace_id)
        if deps.service.get_loot_window_state(room) is not None:
            await deps.scheduler.publish_loot_window_started(room, trace_id=trace_id)
            return {"ok": True, "settled": False}

        settled = False
        if room.match_state and room.match_state.round_locked:
            await deps.scheduler.settle_and_notify(room, trace_id=trace_id)
            settled = True
        return {"ok": True, "settled": settled}

    @ops_router.get("/")
    async def root_page() -> RedirectResponse:
        return RedirectResponse(url="/lobby")

    @ops_router.get("/lobby")
    async def lobby_page() -> FileResponse:
        return FileResponse(deps.web_dir / "lobby.html")

    @ops_router.get("/game")
    async def game_page() -> FileResponse:
        return FileResponse(deps.web_dir / "game.html")

    @ops_router.post("/rooms")
    async def create_room(req: CreateRoomRequest) -> dict:
        active_room_id = deps.find_player_active_room(req.host_player_id)
        if active_room_id is not None:
            raise HTTPException(
                status_code=400,
                detail=f"player already in active room: {active_room_id}",
            )
        room = deps.service.create_room(req.room_id, req.host_player_id, req.end_mode)
        deps.store.add(room)
        return {"room_id": room.room_id, "host_player_id": room.host_player_id, "status": room.status}

    @ops_router.get("/rooms")
    async def list_rooms(player_id: str | None = None) -> dict:
        viewer_active_room_id = deps.find_player_active_room(player_id) if player_id else None
        rows = [
            deps.build_room_brief(room, viewer_active_room_id=viewer_active_room_id)
            for room in deps.store.list_all()
        ]
        rows.sort(key=lambda x: x["room_id"])
        return {"items": rows, "count": len(rows)}

    @ops_router.post("/rooms/{room_id}/join")
    async def join_room(room_id: str, req: JoinRoomRequest) -> dict:
        active_room_id = deps.find_player_active_room(req.player_id)
        if active_room_id is not None and active_room_id != room_id:
            raise HTTPException(
                status_code=400,
                detail=f"player already in active room: {active_room_id}",
            )
        room = deps.get_room_or_404(room_id)
        player = deps.service.join_room(room, req.player_id, req.is_human)
        return {"player_id": player.player_id, "join_seq": player.join_seq, "is_human": player.is_human}

    @ops_router.post("/rooms/{room_id}/leave")
    async def leave_room(room_id: str, req: LeaveRoomRequest) -> dict:
        room = deps.get_room_or_404(room_id)
        trace_id = deps.make_trace_id(room, "leave_room")
        outcome = deps.service.leave_room(room, req.player_id)

        if outcome["mode"] == "DISBANDED":
            msgs = deps.notify.publish(
                room,
                EVENT_ROOM_DISBANDED,
                {"room_id": room.room_id, "host_player_id": req.player_id},
                trace_id=trace_id,
            )
            await deps.ws_hub.fanout(room.room_id, msgs)
            deps.store.remove(room_id)
            return {"ok": True, "mode": outcome["mode"], "removed": True}

        if outcome["mode"] == "CLOSED_BY_HOST":
            summary_payload = deps.build_game_over_summary_payload(room)
            deps.payload_validator.validate_game_over_summary(summary_payload)
            msgs = deps.notify.publish(
                room,
                EVENT_ROOM_CLOSED,
                {"room_id": room.room_id, "host_player_id": req.player_id, "summary": summary_payload},
                trace_id=trace_id,
            )
            await deps.ws_hub.fanout(room.room_id, msgs)
            await deps.scheduler.publish_game_over(room, trace_id=trace_id)
            deps.notify.clear_room_history(room.room_id)
            deps.store.remove(room_id)
            return {"ok": True, "mode": outcome["mode"], "removed": True}

        if outcome["mode"] in {"LEFT_WAITING", "LEFT_IN_GAME_AS_DEATH"}:
            msgs = deps.notify.publish(
                room,
                EVENT_PLAYER_LEFT,
                {"room_id": room.room_id, "player_id": req.player_id, "mode": outcome["mode"]},
                trace_id=trace_id,
            )
            await deps.ws_hub.fanout(room.room_id, msgs)
            if room.match_state and room.match_state.game_over:
                await deps.scheduler.publish_game_over(room, trace_id=trace_id)
            return {"ok": True, "mode": outcome["mode"]}

        return {"ok": True, "mode": outcome["mode"]}

    @ops_router.post("/rooms/{room_id}/cleanup")
    async def cleanup_room(room_id: str, req: CleanupRoomRequest) -> dict:
        room = deps.get_room_or_404(room_id)
        if room.host_player_id != req.player_id:
            raise HTTPException(status_code=403, detail="only host can cleanup room")
        if room.status not in {ROOM_STATUS_CLOSED, ROOM_STATUS_DISBANDED}:
            raise HTTPException(status_code=400, detail="room is not cleanup-ready")
        deps.notify.clear_room_history(room.room_id)
        deps.store.remove(room_id)
        return {"ok": True, "mode": "CLEANED", "removed": True}

    @gameplay_router.post("/rooms/{room_id}/start")
    async def start_room(room_id: str) -> dict:
        room = deps.get_room_or_404(room_id)
        trace_id = deps.make_trace_id(room, "start_room")
        match = deps.service.start_match(room)
        started_msgs = deps.notify.publish(
            room,
            EVENT_GAME_STARTED,
            {"message": build_game_started_message(room.room_id, len(room.players))},
            trace_id=trace_id,
        )
        await deps.ws_hub.fanout(room.room_id, started_msgs)
        await deps.scheduler.publish_round_started(room, trace_id=trace_id)
        await deps.scheduler.maybe_resolve_round_timeout(room, trace_id=trace_id)
        await deps.scheduler.auto_submit_ai_actions(room, trace_id=trace_id)

        if room.match_state.round_locked:
            await deps.scheduler.settle_and_notify(room, trace_id=trace_id)
        return {
            "room_id": room.room_id,
            "status": room.status,
            "day": match.day,
            "phase": match.phase,
            "round": match.round,
        }

    @gameplay_router.post("/rooms/{room_id}/actions")
    async def submit_action(room_id: str, req: ActionRequest) -> dict:
        # 玩家动作提交主入口：处理超时补偿、动作校验、结算触发与事件下发。
        deps.logger.debug(
            "玩家提交动作请求: room=%s player=%s action=%s payload=%s",
            room_id,
            req.player_id,
            req.action_type,
            req.payload,
        )
        room = deps.get_room_or_404(room_id)
        trace_id = deps.make_trace_id(room, "submit_action")
        await deps.scheduler.resolve_timeouts_and_notify(room, trace_id=trace_id)

        if deps.service.get_loot_window_state(room) is not None:
            lw_state = deps.service.get_loot_window_state(room)
            try:
                private_results = deps.service.submit_loot_window_action(
                    room, req.player_id, req.action_type, req.payload
                )
            except ValueError as exc:
                rejected_payload = deps.build_rejected_payload(room, req.player_id, exc)
                deps.payload_validator.validate_action_rejected(rejected_payload)
                rejected = deps.notify.publish_private(
                    room,
                    req.player_id,
                    EVENT_ACTION_REJECTED,
                    rejected_payload,
                    trace_id=trace_id,
                )
                await deps.ws_hub.send_to_player(room.room_id, req.player_id, rejected["message"])
                return {"accepted": False, "error": rejected_payload}

            await deps.scheduler.publish_round_settled(room, private_results, trace_id=trace_id)
            if lw_state is not None:
                await deps.scheduler.publish_loot_window_resolved(
                    room,
                    winner_player_id=lw_state.winner_player_id,
                    loser_player_id=lw_state.loser_player_id,
                    private_results=private_results,
                    trace_id=trace_id,
                )
            if room.match_state and room.match_state.game_over:
                await deps.scheduler.publish_game_over(room, trace_id=trace_id)
            elif room.match_state:
                await deps.scheduler.publish_round_started(room, trace_id=trace_id)
            current_match = room.match_state
            return {
                "accepted": True,
                "settled": True,
                "round_locked": bool(current_match and current_match.round_locked),
            }

        try:
            action = deps.service.submit_action(room, req.player_id, req.action_type, req.payload)
        except ValueError as exc:
            rejected_payload = deps.build_rejected_payload(room, req.player_id, exc)
            deps.payload_validator.validate_action_rejected(rejected_payload)
            rejected = deps.notify.publish_private(
                room,
                req.player_id,
                EVENT_ACTION_REJECTED,
                rejected_payload,
                trace_id=trace_id,
            )
            await deps.ws_hub.send_to_player(room.room_id, req.player_id, rejected["message"])
            return {"accepted": False, "error": rejected_payload}

        accepted = deps.notify.publish_private(
            room,
            req.player_id,
            EVENT_ACTION_ACCEPTED,
            {"action_id": action.action_id, "action_type": action.action_type},
            trace_id=trace_id,
        )
        await deps.ws_hub.send_to_player(room.room_id, req.player_id, accepted["message"])
        await deps.scheduler.auto_submit_ai_actions(room, trace_id=trace_id)

        settled = False
        if room.match_state and room.match_state.round_locked:
            await deps.scheduler.settle_and_notify(room, trace_id=trace_id)
            settled = True

        current_match = room.match_state
        return {
            "accepted": True,
            "settled": settled,
            "action_id": action.action_id,
            "round_locked": bool(current_match and current_match.round_locked),
        }

    @gameplay_router.get("/rooms/{room_id}/players/{player_id}/view")
    async def player_view(room_id: str, player_id: str) -> dict:
        room = deps.get_room_or_404(room_id)
        try:
            return deps.service.get_player_view(room, player_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @gameplay_router.get("/rooms/{room_id}/players/{player_id}/history")
    async def player_history(room_id: str, player_id: str, last_seen_seq: int = 0) -> dict:
        deps.get_room_or_404(room_id)
        rows = deps.notify.history(room_id, player_id, last_seen_seq=last_seen_seq)
        return {"items": rows, "count": len(rows)}

    @gameplay_router.get("/rooms/{room_id}/summary")
    async def room_summary(room_id: str) -> dict:
        room = deps.get_room_or_404(room_id)
        try:
            summary_payload = deps.build_game_over_summary_payload(room)
        except ValueError as exc:
            return {"accepted": False, "error": deps.build_rejected_payload(room, room.host_player_id, exc)}
        deps.payload_validator.validate_game_over_summary(summary_payload)
        return summary_payload

    @gameplay_router.post("/rooms/{room_id}/reset")
    async def reset_room(room_id: str, req: ResetRoomRequest) -> dict:
        room = deps.get_room_or_404(room_id)
        trace_id = deps.make_trace_id(room, "reset_room")
        try:
            outcome = deps.service.reset_room_for_next_match(room, req.player_id)
        except ValueError as exc:
            rejected_payload = deps.build_rejected_payload(room, req.player_id, exc)
            deps.payload_validator.validate_action_rejected(rejected_payload)
            rejected = deps.notify.publish_private(
                room,
                req.player_id,
                EVENT_ACTION_REJECTED,
                rejected_payload,
                trace_id=trace_id,
            )
            await deps.ws_hub.send_to_player(room.room_id, req.player_id, rejected["message"])
            return {"accepted": False, "error": rejected_payload}
        deps.notify.clear_room_history(room.room_id)
        return {"accepted": True, "mode": outcome["mode"], "status": outcome["status"]}

    @gameplay_router.websocket("/ws/{room_id}/{player_id}")
    async def room_ws(room_id: str, player_id: str, ws: WebSocket) -> None:
        await ws.accept()
        try:
            room = deps.store.get(room_id)
        except ValueError:
            await ws.close(code=1008, reason="room_not_found")
            return
        if player_id not in room.players:
            await ws.close(code=1008, reason="player_not_in_room")
            return
        await deps.ws_hub.connect(room_id, player_id, ws)
        try:
            while True:
                # Keep alive and allow client ping frames/content.
                text = await ws.receive_text()
                if text == "ping":
                    await ws.send_text("pong")
        except WebSocketDisconnect:
            deps.ws_hub.disconnect(room_id, player_id, ws)

    @debug_router.post("/internal/debug/rooms/{room_id}/tick-ai")
    async def tick_ai_debug(room_id: str) -> dict:
        return await tick_ai_impl(room_id)

    return ops_router, gameplay_router, debug_router
