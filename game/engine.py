from __future__ import annotations

import random
import uuid

from game.agents.runtime import AgentRuntime, load_agent_configs
from game.constants import DEFAULT_ROOM_SIZE, MAP_GRID, MAX_ACTIONS_PER_PHASE, START_EXPOSURE, START_FOOD, START_WATER
from game.memory.service import MemoryService
from game.models import Action, ActionKind, PlayerState, RoomState
from game.rules import apply_action, check_death, init_building_loot, settle_phase
from game.settings import OpenAISettings
from game.store.db import EventStore


def _safe_positions() -> list[tuple[int, int]]:
    out = []
    for y in range(1, 10):
        for x in range(1, 10):
            if MAP_GRID[y - 1][x - 1] in {"J", "B", "S", "W", "M"}:
                out.append((x, y))
    return out


class GameEngine:
    def __init__(self, room: RoomState, agent_runtime: AgentRuntime, store: EventStore, memory: MemoryService, event_callback=None):
        self.room = room
        self.agent_runtime = agent_runtime
        self.store = store
        self.memory = memory
        self.game_id = str(uuid.uuid4())
        self.event_callback = event_callback

    @classmethod
    def create(
        cls,
        human_names: list[str],
        room_id: str = "room-1",
        event_callback=None,
        settings: OpenAISettings | None = None,
        max_players: int = DEFAULT_ROOM_SIZE,
        max_ai: int | None = None,
    ) -> "GameEngine":
        if max_players < len(human_names):
            raise ValueError("max_players_less_than_humans")
        max_ai = max_players if max_ai is None else max_ai
        if max_ai < 0:
            raise ValueError("max_ai_negative")

        positions = _safe_positions()
        random.shuffle(positions)

        players: list[PlayerState] = []
        for idx, name in enumerate(human_names):
            x, y = positions.pop()
            players.append(
                PlayerState(
                    player_id=f"h{idx + 1}",
                    name=name,
                    is_human=True,
                    x=x,
                    y=y,
                    water=START_WATER,
                    food=START_FOOD,
                    exposure=START_EXPOSURE,
                )
            )

        ai_count = min(max_players - len(players), max_ai)
        ai_ids = []
        for i in range(ai_count):
            x, y = positions.pop()
            pid = f"a{i + 1}"
            ai_ids.append(pid)
            players.append(
                PlayerState(
                    player_id=pid,
                    name=f"AI_{i + 1}",
                    is_human=False,
                    x=x,
                    y=y,
                    water=START_WATER,
                    food=START_FOOD,
                    exposure=START_EXPOSURE,
                )
            )

        settings = settings or OpenAISettings.load()
        cfgs = load_agent_configs(settings.agents_file, ai_ids)
        room = RoomState(room_id=room_id, players=players, building_loot=init_building_loot())
        return cls(
            room,
            AgentRuntime(settings=settings, configs=cfgs),
            EventStore("game.db"),
            MemoryService(window_size=20),
            event_callback=event_callback,
        )

    def _emit(self, event: dict) -> None:
        if callable(self.event_callback):
            self.event_callback(event)

    def alive_humans(self) -> list[PlayerState]:
        return [p for p in self.room.players if p.alive and p.is_human]

    def is_phase_done(self) -> bool:
        alive = [p for p in self.room.players if p.alive]
        return all(p.phase_ended for p in alive)

    def start_phase(self) -> None:
        self.room.phase_action_seq = 0
        for p in self.room.players:
            if p.alive:
                p.phase_ended = False
                p.take_locked_in_phase = False
                p.phase_actions_used = 0

    def apply_and_log(self, actor: PlayerState, action: Action) -> tuple[bool, str]:
        action_seq = self.room.phase_action_seq + 1
        result = apply_action(self.room, actor, action)
        if not result["ok"]:
            return False, result["error"]
        self.room.phase_action_seq = action_seq

        for message in result["events"]:
            event = {
                "phase_no": self.room.phase_no,
                "phase": self.room.phase.value,
                "action_seq": action_seq,
                "actor": actor.name,
                "event_type": "action_result",
                "message": f"[#{action_seq}] {message}",
            }
            self.memory.add(event)
            self.store.log(self.game_id, self.room.phase_no, self.room.phase.value, actor.player_id, "action_result", event)
            self._emit(event)

        dead, reason = check_death(actor, self.room.phase)
        if dead and actor.alive:
            actor.alive = False
            actor.phase_ended = True
            event = {
                "phase_no": self.room.phase_no,
                "phase": self.room.phase.value,
                "action_seq": action_seq,
                "actor": actor.name,
                "event_type": "death",
                "reason": reason,
            }
            self.memory.add(event)
            self.store.log(self.game_id, self.room.phase_no, self.room.phase.value, actor.player_id, "death", event)
            self._emit(event)

        if actor.alive and actor.phase_actions_used >= MAX_ACTIONS_PER_PHASE:
            actor.phase_ended = True
            event = {
                "phase_no": self.room.phase_no,
                "phase": self.room.phase.value,
                "action_seq": action_seq,
                "actor": actor.name,
                "event_type": "phase_auto_end",
                "reason": f"max_actions_{MAX_ACTIONS_PER_PHASE}",
            }
            self.memory.add(event)
            self.store.log(self.game_id, self.room.phase_no, self.room.phase.value, actor.player_id, "phase_auto_end", event)
            self._emit(event)

        if not self.alive_humans():
            self.room.finished = True
            self.room.finish_reason = "all_humans_dead"
        return True, ""

    def run_phase(self, human_action_provider) -> None:
        self.start_phase()

        while not self.room.finished and not self.is_phase_done():
            for p in self.room.players:
                if self.room.finished:
                    break
                if not p.alive or p.phase_ended:
                    continue
                if p.is_human:
                    while True:
                        action = human_action_provider(self.room, p)
                        ok, err = self.apply_and_log(p, action)
                        if ok:
                            break
                        print(f"[非法动作] {err}，请重试")
                else:
                    action = self.agent_runtime.decide(self.room, p)
                    ok, _ = self.apply_and_log(p, action)
                    if not ok:
                        fallback = Action(player_id=p.player_id, kind=ActionKind.REST, source="AI", reason="invalid_fallback")
                        self.apply_and_log(p, fallback)

        self.settle_current_phase()

    def settle_current_phase(self) -> None:
        if self.room.finished:
            return
        settle_phase_no = self.room.phase_no
        settle_phase_name = self.room.phase.value
        settle = settle_phase(self.room)
        for msg in settle["events"]:
            event = {
                "phase_no": settle_phase_no,
                "phase": settle_phase_name,
                "event_type": "phase_settle",
                "message": msg,
            }
            self.memory.add(event)
            self.store.log(self.game_id, settle_phase_no, settle_phase_name, None, "phase_settle", event)
            self._emit(event)

    def run_until_finish(self, human_action_provider) -> None:
        while not self.room.finished:
            self.run_phase(human_action_provider)

        survivors = [p.name for p in self.room.players if p.alive]
        summary = ",".join(survivors) if survivors else "none"
        self.store.save_summary(self.game_id, summary, self.room.finish_reason)
        self._emit(
            {
                "event_type": "game_over",
                "finish_reason": self.room.finish_reason,
                "survivors": survivors,
            }
        )
