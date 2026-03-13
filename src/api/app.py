"""FastAPI application entry and dependency wiring."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from src.ai.agent_adapter import AgentAdapter
from src.ai.llm_policy import LLMPolicy
from src.ai.rule_bot import RuleBot
from src.api.constants import (
    ERROR_ACTION_INVALID,
    ERROR_ACTION_NOT_ALLOWED_ON_TILE,
    ERROR_ALREADY_SUBMITTED,
    ERROR_ATTACK_TARGET_NOT_DISCOVERED,
    ERROR_LOOT_WINDOW_ACTION_INVALID,
    ERROR_LOOT_WINDOW_NOT_OPEN,
    ERROR_LOOT_WINDOW_ONLY_WINNER,
    ERROR_MATCH_NOT_OVER,
    ERROR_MOVE_NOT_ADJACENT,
    ERROR_MOVE_OUT_OF_BOUNDS,
    ERROR_ONLY_HOST_CAN_RESET,
    ERROR_PHASE_ENDED,
    ERROR_ROUND_LOCKED,
    ERROR_UNKNOWN_PLAYER,
    SCHEMA_ACTION_REJECTED_V1,
    SCHEMA_GAME_OVER_SUMMARY_V1,
)
from src.api.payload_validation import PayloadValidator
from src.api.router_factory import ApiDeps, build_routers
from src.application.match_service import MatchService
from src.application.notification_service import NotificationService
from src.application.room_store import RoomStore
from src.application.round_scheduler import RoundScheduler
from src.api.ws_hub import WsHub
from src.domain.constants import ROOM_STATUS_IN_GAME, ROOM_STATUS_WAITING
from src.domain.errors import (
    ERR_ACTION_NOT_ALLOWED_ON_TILE_SUFFIX,
    ERR_ALREADY_SUBMITTED,
    ERR_ATTACK_TARGET_NOT_DISCOVERED,
    ERR_LOOT_WINDOW_ACTION_INVALID,
    ERR_LOOT_WINDOW_NOT_OPEN,
    ERR_LOOT_WINDOW_ONLY_WINNER_CAN_ACT,
    ERR_MATCH_NOT_OVER,
    ERR_MOVE_NOT_ADJACENT,
    ERR_MOVE_OUT_OF_BOUNDS,
    ERR_ONLY_HOST_CAN_RESET,
    ERR_PLAYER_CANNOT_ACT,
    ERR_ROUND_LOCKED,
    ERR_UNKNOWN_PLAYER_PREFIX,
)
from src.domain.models import Room
from src.infra.config import load_settings


def create_app() -> FastAPI:
    # 应用主入口：组装配置、服务对象、路由与后台任务。
    app = FastAPI(title="survival-story", version="0.1.0")
    settings = load_settings()
    logger = logging.getLogger("survival_story.api")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(getattr(logging, settings.backend_log_level, logging.WARNING))
    logger.debug("后端调试日志已开启")

    waiting_room_ttl = timedelta(minutes=3)
    waiting_room_gc_interval_sec = 180
    active_room_tick_interval_sec = 1
    root_dir = Path(__file__).resolve().parents[2]
    web_dir = root_dir / "web"
    assets_dir = web_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    store = RoomStore()
    service = MatchService(
        loot_window_timeout_sec=settings.loot_window_timeout_sec,
        round_action_timeout_sec=settings.round_action_timeout_sec,
        max_day_phase_rounds=settings.max_day_phase_rounds,
        max_night_phase_rounds=settings.max_night_phase_rounds,
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

    agent_adapter = AgentAdapter(primary=ai_policy, fallback=RuleBot())
    scheduler = RoundScheduler(
        store=store,
        service=service,
        notify=notify,
        ws_hub=ws_hub,
        payload_validator=payload_validator,
        ai_agent=agent_adapter,
        settings=settings,
        logger=logger,
    )

    def get_room_or_404(room_id: str) -> Room:
        try:
            return store.get(room_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def gc_waiting_rooms_once() -> int:
        now = datetime.now(UTC)
        removed = 0
        for room in list(store.list_all()):
            if room.status != ROOM_STATUS_WAITING:
                continue
            if now - room.waiting_since < waiting_room_ttl:
                continue
            store.remove(room.room_id)
            notify.clear_room_history(room.room_id)
            removed += 1
        return removed

    async def waiting_room_gc_loop() -> None:
        # 等待房间清理任务：定期清理超时未开局房间。
        while True:
            await asyncio.sleep(waiting_room_gc_interval_sec)
            gc_waiting_rooms_once()

    async def active_room_tick_loop() -> None:
        # 对局调度任务：驱动超时处理、AI 补动作与结算推进。
        while True:
            await asyncio.sleep(active_room_tick_interval_sec)
            await scheduler.process_active_rooms_once()

    @app.on_event("startup")
    async def startup_tasks() -> None:
        app.state.waiting_room_gc_task = asyncio.create_task(waiting_room_gc_loop())
        app.state.active_room_tick_task = asyncio.create_task(active_room_tick_loop())

    @app.on_event("shutdown")
    async def shutdown_tasks() -> None:
        gc_task = getattr(app.state, "waiting_room_gc_task", None)
        if gc_task is not None:
            gc_task.cancel()
            with suppress(asyncio.CancelledError):
                await gc_task
        tick_task = getattr(app.state, "active_room_tick_task", None)
        if tick_task is not None:
            tick_task.cancel()
            with suppress(asyncio.CancelledError):
                await tick_task

    def build_rejected_payload(room: Room, player_id: str, exc: ValueError) -> dict:
        reason = str(exc)
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

    def find_player_active_room(player_id: str) -> str | None:
        for room in store.list_all():
            if room.status not in {ROOM_STATUS_WAITING, ROOM_STATUS_IN_GAME}:
                continue
            if player_id in room.players:
                return room.room_id
        return None

    def make_trace_id(room: Room, source: str) -> str:
        match = room.match_state
        day = match.day if match else 0
        phase = match.phase if match else "NA"
        round_no = match.round if match else 0
        return f"{room.room_id}:{day}:{phase}:{round_no}:{source}:{uuid.uuid4().hex[:8]}"

    def build_room_brief(room: Room, *, viewer_active_room_id: str | None = None) -> dict:
        player_count = len(room.players)
        human_count = len([p for p in room.players.values() if p.is_human])
        ai_count = player_count - human_count
        joinable = room.status == ROOM_STATUS_WAITING and player_count < settings.room_max_players
        viewer_in_room = viewer_active_room_id == room.room_id
        can_join = joinable and viewer_active_room_id is None
        return {
            "room_id": room.room_id,
            "host_player_id": room.host_player_id,
            "status": room.status,
            "player_count": player_count,
            "human_count": human_count,
            "ai_count": ai_count,
            "max_players": settings.room_max_players,
            "is_in_game": room.status == ROOM_STATUS_IN_GAME,
            "joinable": joinable,
            "viewer_in_room": viewer_in_room,
            "can_join": can_join,
        }

    deps = ApiDeps(
        service=service,
        store=store,
        notify=notify,
        ws_hub=ws_hub,
        scheduler=scheduler,
        payload_validator=payload_validator,
        settings=settings,
        web_dir=web_dir,
        logger=logger,
        get_room_or_404=get_room_or_404,
        find_player_active_room=find_player_active_room,
        make_trace_id=make_trace_id,
        build_rejected_payload=build_rejected_payload,
        build_game_over_summary_payload=build_game_over_summary_payload,
        build_room_brief=build_room_brief,
    )

    ops_router, gameplay_router, debug_router = build_routers(deps)
    app.include_router(ops_router)
    app.include_router(gameplay_router)
    app.include_router(debug_router)
    return app


app = create_app()
