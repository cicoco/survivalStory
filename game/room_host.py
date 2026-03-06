from __future__ import annotations

import json
import socketserver
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue

from game.cli import parse_command
from game.constants import item_zh_label
from game.engine import GameEngine
from game.models import Action, ActionKind
from game.settings import OpenAISettings


def _send_json(file_obj, payload: dict) -> None:
    file_obj.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    file_obj.flush()


@dataclass
class RoomHostRuntime:
    room_id: str
    host_name: str
    max_players: int
    max_ai: int
    settings: OpenAISettings
    ai_interval_ms: int = 700
    human_timeout_sec: int = 120
    lock: threading.Lock = field(default_factory=threading.Lock)
    connected: dict[str, any] = field(default_factory=dict)
    joined_names: set[str] = field(default_factory=set)
    actions: Queue[tuple[str, str, float]] = field(default_factory=Queue)
    engine: GameEngine | None = None
    running: bool = False
    human_prompt_template: str = ""
    watchers: dict[int, any] = field(default_factory=dict)
    next_watcher_id: int = 1
    watch_seq: int = 0
    room_closed: bool = False
    room_close_reason: str = ""

    def __post_init__(self) -> None:
        template_path = Path(self.settings.user_prompt_file)
        if template_path.exists():
            self.human_prompt_template = template_path.read_text(encoding="utf-8").strip()
        else:
            self.human_prompt_template = (
                "当前坐标:({{current_x}},{{current_y}})\n"
                "当前时段:{{phase_cn}}\n"
                "当前阶段:进行中\n"
                "当前角色阶段状态:可行动\n"
                "当前状态:水分-{{water}}，饱食-{{food}}，曝光-{{exposure}}\n"
                "背包物品:{{bag_text}}\n"
                "当前建筑剩余物资:{{loot_text}}\n"
                "当前建筑内其他玩家:{{other_players}}\n"
                "请基于以上信息输出一个动作。"
            )

    def join(self, name: str, writer) -> None:
        with self.lock:
            if self.running and name not in self.joined_names:
                raise ValueError("room_already_started")
            if (name not in self.joined_names) and (len(self.joined_names) >= self.max_players):
                raise ValueError("room_full")
            self.joined_names.add(name)
            self.connected[name] = writer

    def detach(self, name: str) -> None:
        with self.lock:
            self.connected.pop(name, None)
            if not self.running:
                # Before game start, leaving the room should remove membership entirely.
                self.joined_names.discard(name)
        self.broadcast({"type": "event", "message": f"{name} 已离线"})
        self.broadcast_members()

    def _mark_player_dead(self, name: str, reason: str) -> None:
        if self.engine is None:
            return
        actor = next((p for p in self.engine.room.players if p.name == name), None)
        if actor is None:
            return
        actor.alive = False
        actor.phase_ended = True
        self.broadcast_watch_event(
            {
                "event_type": "player_left_dead",
                "phase_no": self.engine.room.phase_no,
                "phase": self.engine.room.phase.value,
                "actor": name,
                "reason": reason,
            }
        )
        if not self.engine.alive_humans():
            self.engine.room.finished = True
            self.engine.room.finish_reason = "all_humans_dead"

    def explicit_leave(self, name: str) -> dict:
        # Returns a payload for leaving client:
        # {"type":"left"|"room_closed", "message": "...", "room_close_reason"?: "..."}
        with self.lock:
            is_host = name == self.host_name
        if is_host and not self.running:
            with self.lock:
                self.connected.pop(name, None)
                self.joined_names.clear()
                self.room_closed = True
                self.room_close_reason = "host_left_before_start"
            self.broadcast(
                {
                    "type": "room_closed",
                    "room_close_reason": "host_left_before_start",
                    "message": "房主离开，房间已解散。",
                }
            )
            self.broadcast_watch_event(
                {
                    "event_type": "game_over",
                    "finish_reason": "host_left_before_start",
                    "survivors": [],
                    "message": "房主离开，房间已解散。",
                }
            )
            return {
                "type": "room_closed",
                "room_close_reason": "host_left_before_start",
                "message": "你已离开，房间已解散。",
            }

        if is_host and self.running:
            if self.engine is not None and not self.engine.room.finished:
                self.engine.room.finished = True
                self.engine.room.finish_reason = "host_left_room_closed"
            with self.lock:
                self.connected.pop(name, None)
                self.room_closed = True
                self.room_close_reason = "host_left_room_closed"
            self.broadcast(
                {
                    "type": "room_closed",
                    "room_close_reason": "host_left_room_closed",
                    "message": "房主离开，房间已关闭，本局结束。",
                }
            )
            survivors = []
            if self.engine is not None:
                survivors = [p.name for p in self.engine.room.players if p.alive]
            self.broadcast_watch_event(
                {
                    "event_type": "game_over",
                    "finish_reason": "host_left_room_closed",
                    "survivors": survivors,
                    "message": "房主离开，房间已关闭，本局结束。",
                }
            )
            return {
                "type": "room_closed",
                "room_close_reason": "host_left_room_closed",
                "message": "你已离开，房间已关闭，本局结束。",
            }

        # non-host leave
        with self.lock:
            self.connected.pop(name, None)
            self.joined_names.discard(name)
        if self.running:
            self._mark_player_dead(name, reason="left_after_start")
            self.broadcast({"type": "event", "message": f"{name} 已退出对局（判定死亡）"})
            self.broadcast_members()
            return {"type": "left", "message": "你已退出对局，本局中判定为死亡。"}
        self.broadcast({"type": "event", "message": f"{name} 退出了房间"})
        self.broadcast_members()
        return {"type": "left", "message": "你已退出房间。"}

    def send_to(self, name: str, payload: dict) -> None:
        with self.lock:
            w = self.connected.get(name)
        if not w:
            return
        try:
            _send_json(w, payload)
        except Exception:
            pass

    def broadcast(self, payload: dict) -> None:
        with self.lock:
            writers = list(self.connected.values())
        for w in writers:
            try:
                _send_json(w, payload)
            except Exception:
                pass

    def attach_watcher(self, writer) -> int:
        with self.lock:
            wid = self.next_watcher_id
            self.next_watcher_id += 1
            self.watchers[wid] = writer
            return wid

    def detach_watcher(self, watcher_id: int) -> None:
        with self.lock:
            self.watchers.pop(watcher_id, None)

    def broadcast_watch_event(self, event: dict) -> None:
        with self.lock:
            self.watch_seq += 1
            seq = self.watch_seq
            watchers = list(self.watchers.values())
        payload = {
            "type": "watch_event",
            "room_id": self.room_id,
            "seq": seq,
            "ts": time.time(),
            "event": event,
        }
        for w in watchers:
            try:
                _send_json(w, payload)
            except Exception:
                pass

    def connected_names(self) -> set[str]:
        with self.lock:
            return set(self.connected.keys())

    def push_action(self, player_name: str, text: str) -> None:
        self.actions.put((player_name, text, time.time()))

    def next_action(self, timeout: float = 0.1) -> tuple[str, str, float] | None:
        try:
            return self.actions.get(timeout=timeout)
        except Empty:
            return None

    def snapshot(self) -> dict:
        room = self.engine.room if self.engine else None
        if room is None:
            return {}
        return {
            "room_id": room.room_id,
            "phase_no": room.phase_no,
            "phase": room.phase.value,
            "action_seq": room.phase_action_seq,
            "players": [
                {
                    "name": p.name,
                    "alive": p.alive,
                    "x": p.x,
                    "y": p.y,
                    "water": p.water,
                    "food": p.food,
                    "exposure": p.exposure,
                    "phase_ended": p.phase_ended,
                }
                for p in room.players
            ],
        }

    def self_snapshot(self, player_name: str) -> dict:
        room = self.engine.room if self.engine else None
        if room is None:
            return {}
        actor = next((p for p in room.players if p.name == player_name), None)
        if actor is None:
            return {
                "room_id": room.room_id,
                "phase_no": room.phase_no,
                "phase": room.phase.value,
                "action_seq": room.phase_action_seq,
                "players": [],
                "view": {},
            }
        return {
            "room_id": room.room_id,
            "phase_no": room.phase_no,
            "phase": room.phase.value,
            "action_seq": room.phase_action_seq,
            "players": [
                {
                    "name": actor.name,
                    "alive": actor.alive,
                    "x": actor.x,
                    "y": actor.y,
                    "water": actor.water,
                    "food": actor.food,
                    "exposure": actor.exposure,
                    "phase_ended": actor.phase_ended,
                }
            ],
            "view": self._player_view(player_name),
        }

    def connected_human_names(self) -> list[str]:
        if self.engine is None:
            return []
        humans = {p.name for p in self.engine.room.players if p.is_human}
        connected = self.connected_names()
        return sorted(name for name in connected if name in humans)

    def members_payload(self) -> dict:
        with self.lock:
            members = sorted(self.joined_names)
        return {
            "members": members,
        }

    def broadcast_members(self) -> None:
        self.broadcast({"type": "members_update", "payload": self.members_payload()})

    def operation_hint(self) -> str:
        return (
            "命令: MOVE x y | EXPLORE | USE 物资标识 | TAKE 物资1 物资2 物资3 | "
            "REST | ATTACK 玩家名 | MEMBERS ; "
            "物资别名/编码: B/BREAD W/BOTTLED_WATER C/BISCUIT G/CANNED_FOOD T/BARREL_WATER Q/CLEAN_WATER"
        )

    def _bag_text(self, bag: dict[str, int]) -> str:
        if not bag:
            return "无"
        return ",".join(f"{item_zh_label(k)}-{v}" for k, v in bag.items() if v > 0) or "无"

    def _loot_text(self, loot: dict[str, int] | None) -> str:
        if loot is None:
            return "未知(先EXPLORE后可见)"
        if not loot:
            return "无"
        return ",".join(f"{item_zh_label(k)}-{v}" for k, v in loot.items() if v > 0) or "无"

    def _render_human_prompt(self, player_name: str) -> str:
        if self.engine is None:
            return ""
        room = self.engine.room
        actor = next((p for p in room.players if p.name == player_name), None)
        if actor is None:
            return ""
        phase_cn = "白天" if room.phase.value == "DAY" else "夜晚"
        same_pos_names = [
            p.name for p in room.players if p.alive and p.name != actor.name and p.pos() == actor.pos()
        ]
        loot = actor.known_building_loot.get(actor.pos())
        status = "可行动" if (actor.alive and not actor.phase_ended) else "已结束"
        context = {
            "{{current_x}}": str(actor.x),
            "{{current_y}}": str(actor.y),
            "{{phase_cn}}": phase_cn,
            "{{water}}": str(actor.water),
            "{{food}}": str(actor.food),
            "{{exposure}}": str(actor.exposure),
            "{{bag_text}}": self._bag_text(actor.bag),
            "{{loot_text}}": self._loot_text(loot),
            "{{other_players}}": ",".join(same_pos_names) if same_pos_names else "无",
            "{{phase_status}}": status,
        }
        prompt = self.human_prompt_template
        for k, v in context.items():
            prompt = prompt.replace(k, v)
        code_hint = "物资别名/编码: B/BREAD W/BOTTLED_WATER C/BISCUIT G/CANNED_FOOD T/BARREL_WATER Q/CLEAN_WATER"
        return f"当前阶段 第{room.phase_no}天，{phase_cn}\n{prompt}\n{code_hint}"

    def _player_view(self, player_name: str) -> dict:
        if self.engine is None:
            return {}
        room = self.engine.room
        actor = next((p for p in room.players if p.name == player_name), None)
        if actor is None:
            return {}
        loot = actor.known_building_loot.get(actor.pos())
        bag_data = {k: int(v) for k, v in actor.bag.items() if int(v) > 0}
        loot_data = None if loot is None else {k: int(v) for k, v in loot.items() if int(v) > 0}
        return {
            "bag": bag_data,
            "bag_text": self._bag_text(actor.bag),
            "visible_loot": loot_data,
            "visible_loot_text": self._loot_text(loot),
        }

    def send_action_prompt(self, player_name: str) -> None:
        prompt = self._render_human_prompt(player_name)
        if not prompt:
            return
        self.send_to(
            player_name,
            {
                "type": "action_prompt",
                "phase_no": self.engine.room.phase_no if self.engine else 0,
                "phase": self.engine.room.phase.value if self.engine else "",
                "message": prompt,
                "view": self._player_view(player_name),
            },
        )

    def start(self, requester_name: str) -> None:
        if requester_name != self.host_name:
            raise ValueError("not_host")
        if self.running:
            raise ValueError("room_already_running")
        human_names = list(self.joined_names)
        if self.host_name not in human_names:
            human_names.append(self.host_name)
        self.engine = GameEngine.create(
            human_names=human_names,
            room_id=self.room_id,
            settings=self.settings,
            max_players=self.max_players,
            max_ai=self.max_ai,
            event_callback=self._emit_event,
        )
        self.running = True
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _emit_event(self, event: dict) -> None:
        # Privacy for round-synchronized play:
        # do not broadcast per-action engine events/state (which can expose other players' operations).
        # Public progression is notified via room-level messages + round_settled snapshots.
        et = str(event.get("event_type", ""))
        if et in {"phase_settle", "game_over"}:
            self.broadcast_watch_event(event)
        if event.get("event_type") == "game_over":
            self.broadcast({"type": "game_over", "payload": event})

    def _run_loop(self) -> None:
        assert self.engine is not None
        engine = self.engine
        for name in self.connected_human_names():
            self.send_to(
                name,
                {
                    "type": "game_started",
                    "payload": self.self_snapshot(name),
                    "message": "游戏开始了",
                    "operation_hint": self.operation_hint(),
                },
            )
        self.broadcast_watch_event(
            {
                "event_type": "game_started",
                "phase_no": engine.room.phase_no,
                "phase": engine.room.phase.value,
                "message": "游戏开始了",
            }
        )
        self.broadcast({"type": "event", "message": "游戏开始了，请根据当前阶段信息输入动作。"})
        ai_interval = max(0.05, self.ai_interval_ms / 1000.0)

        while not engine.room.finished:
            engine.start_phase()
            for name in self.connected_human_names():
                self.send_to(
                    name,
                    {
                        "type": "phase_started",
                        "payload": self.self_snapshot(name),
                        "message": f"阶段开始: {engine.room.phase.value}",
                        "operation_hint": self.operation_hint(),
                    },
                )
            self.broadcast_watch_event(
                {
                    "event_type": "phase_started",
                    "phase_no": engine.room.phase_no,
                    "phase": engine.room.phase.value,
                    "message": f"阶段开始: {engine.room.phase.value}",
                }
            )
            self.broadcast({"type": "event", "message": f"当前阶段 {engine.room.phase.value}，请提交动作。"})

            while not engine.room.finished and not engine.is_phase_done():
                # Round-based synchronized submit/settle:
                # each alive player submits at most one action per round.
                active_players = [p for p in engine.room.players if p.alive and not p.phase_ended]
                if not active_players:
                    break

                round_start_seq = engine.room.phase_action_seq
                pending_humans = {p.name: p for p in active_players if p.is_human}
                human_deadline = {name: time.time() + self.human_timeout_sec for name in pending_humans}
                submitted: dict[str, tuple[Action, float]] = {}
                staged_actions: list[tuple[float, Action, str]] = []

                for name in pending_humans:
                    self.send_action_prompt(name)

                # AI prepares one action for this round.
                for p in active_players:
                    if not p.is_human:
                        a = engine.agent_runtime.decide(engine.room, p)
                        staged_actions.append((time.time(), a, p.name))

                while pending_humans and not engine.room.finished:
                    incoming = self.next_action(timeout=0.1)
                    now = time.time()
                    if incoming:
                        player_name, text, ts = incoming
                        actor = pending_humans.get(player_name)
                        if actor is None:
                            self.send_to(
                                player_name,
                                {"type": "action_ack", "status": "rejected", "reason": "not_pending_or_cannot_act"},
                            )
                            continue
                        if player_name in submitted:
                            self.send_to(
                                player_name,
                                {"type": "action_ack", "status": "rejected", "reason": "already_submitted_this_round"},
                            )
                            continue
                        action = parse_command(engine.room, actor, text)
                        if action is None:
                            self.send_to(
                                player_name,
                                {"type": "action_ack", "status": "rejected", "reason": "invalid_command"},
                            )
                            continue
                        submitted[player_name] = (action, ts)
                        staged_actions.append((ts, action, player_name))
                        self.send_to(
                            player_name,
                            {"type": "action_ack", "status": "accepted", "reason": "submitted_waiting_others"},
                        )
                        pending_humans.pop(player_name, None)
                        continue

                    # timeout/disconnect => auto REST
                    connected = self.connected_names()
                    for name in list(pending_humans.keys()):
                        actor = pending_humans[name]
                        if (now >= human_deadline[name]) or (name not in connected):
                            auto = Action(
                                player_id=actor.player_id,
                                kind=ActionKind.REST,
                                source="SYSTEM",
                                reason="human_timeout_or_disconnect",
                            )
                            staged_actions.append((now, auto, name))
                            self.send_to(
                                name,
                                {"type": "action_ack", "status": "auto", "reason": "timeout_or_disconnect_auto_rest"},
                            )
                            pending_humans.pop(name, None)

                # Settle this round in submit order.
                staged_actions.sort(key=lambda x: x[0])
                for _, action, name in staged_actions:
                    actor = next((p for p in engine.room.players if p.name == name and p.alive and not p.phase_ended), None)
                    if actor is None:
                        continue
                    ok, err = engine.apply_and_log(actor, action)
                    if not ok:
                        self.send_to(
                            name,
                            {"type": "action_ack", "status": "rejected", "reason": f"invalid_action:{err}"},
                        )
                        # invalid action in settle stage falls back to REST to avoid blocking round.
                        engine.apply_and_log(
                            actor,
                            Action(player_id=actor.player_id, kind=ActionKind.REST, source="SYSTEM", reason="invalid_fallback_rest"),
                        )
                        self.broadcast_watch_event(
                            {
                                "event_type": "round_action",
                                "phase_no": engine.room.phase_no,
                                "phase": engine.room.phase.value,
                                "actor": actor.name,
                                "is_human": actor.is_human,
                                "action_seq": engine.room.phase_action_seq,
                                "action": {
                                    "kind": "REST",
                                    "payload": {},
                                    "source": "SYSTEM",
                                    "reason": "invalid_fallback_rest",
                                },
                            }
                        )
                    else:
                        self.broadcast_watch_event(
                            {
                                "event_type": "round_action",
                                "phase_no": engine.room.phase_no,
                                "phase": engine.room.phase.value,
                                "actor": actor.name,
                                "is_human": actor.is_human,
                                "action_seq": engine.room.phase_action_seq,
                                "action": {
                                    "kind": action.kind.value,
                                    "payload": action.payload,
                                    "source": action.source,
                                    "reason": action.reason,
                                },
                            }
                        )

                for name in self.connected_human_names():
                    self.send_to(
                        name,
                        {
                            "type": "round_settled",
                            "phase_no": engine.room.phase_no,
                            "phase": engine.room.phase.value,
                            "action_seq_start": round_start_seq + 1 if staged_actions else round_start_seq,
                            "action_seq_end": engine.room.phase_action_seq,
                            "processed_actions": len(staged_actions),
                            "message": "本轮结算完成",
                            "payload": self.self_snapshot(name),
                        },
                    )
                # After round settlement, players who have explored their current building
                # can refresh what they know about that building's remaining loot.
                for p in engine.room.players:
                    if not p.alive:
                        continue
                    pos = p.pos()
                    if pos in p.explored_positions:
                        p.known_building_loot[pos] = deepcopy(engine.room.building_loot.get(pos, {}))
                self.broadcast_watch_event(
                    {
                        "event_type": "round_settled",
                        "phase_no": engine.room.phase_no,
                        "phase": engine.room.phase.value,
                        "action_seq_start": round_start_seq + 1 if staged_actions else round_start_seq,
                        "action_seq_end": engine.room.phase_action_seq,
                        "processed_actions": len(staged_actions),
                        "message": "本轮结算完成",
                    }
                )

            settled_phase_no = engine.room.phase_no
            settled_phase = engine.room.phase.value
            engine.settle_current_phase()
            self.broadcast_watch_event(
                {
                    "event_type": "phase_settled_summary",
                    "settled_phase_no": settled_phase_no,
                    "settled_phase": settled_phase,
                    "players": [
                        {
                            "name": p.name,
                            "is_human": p.is_human,
                            "alive": p.alive,
                            "x": p.x,
                            "y": p.y,
                            "water": p.water,
                            "food": p.food,
                            "exposure": p.exposure,
                            "phase_ended": p.phase_ended,
                        }
                        for p in engine.room.players
                    ],
                }
            )

        self.running = False


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        runtime: RoomHostRuntime = self.server.runtime  # type: ignore[attr-defined]
        name = ""
        watcher_id = 0
        explicit_left = False
        try:
            for raw in self.rfile:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                msg = json.loads(line)
                mtype = msg.get("type")
                if mtype == "watch_join":
                    room_id = str(msg.get("room_id", "")).strip()
                    if room_id != runtime.room_id:
                        _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                        continue
                    watcher_id = runtime.attach_watcher(self.wfile)
                    _send_json(
                        self.wfile,
                        {"type": "watch_joined", "room_id": runtime.room_id, "watcher_id": watcher_id},
                    )
                    continue
                if mtype == "join":
                    room_id = str(msg.get("room_id", "")).strip()
                    pname = str(msg.get("name", "")).strip()
                    if room_id != runtime.room_id:
                        _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                        continue
                    try:
                        runtime.join(pname, self.wfile)
                    except ValueError as err:
                        _send_json(self.wfile, {"type": "error", "message": str(err)})
                        continue
                    name = pname
                    _send_json(
                        self.wfile,
                        {
                            "type": "joined",
                            "room_id": runtime.room_id,
                            "name": name,
                            "host_name": runtime.host_name,
                            "max_players": runtime.max_players,
                            "max_ai": runtime.max_ai,
                            "running": runtime.running,
                        },
                    )
                    runtime.broadcast({"type": "event", "message": f"{name} 加入了房间"})
                    runtime.broadcast_members()
                    continue

                if not name:
                    _send_json(self.wfile, {"type": "error", "message": "join_first"})
                    continue

                if mtype == "start":
                    try:
                        runtime.start(name)
                        _send_json(self.wfile, {"type": "started", "room_id": runtime.room_id})
                    except ValueError as err:
                        _send_json(self.wfile, {"type": "error", "message": str(err)})
                    continue

                if mtype == "members":
                    _send_json(
                        self.wfile,
                        {
                            "type": "members",
                            **runtime.members_payload(),
                        },
                    )
                    continue

                if mtype == "action":
                    text = str(msg.get("text", "")).strip()
                    runtime.push_action(name, text)
                    continue

                if mtype == "leave":
                    payload = runtime.explicit_leave(name)
                    _send_json(self.wfile, payload)
                    explicit_left = True
                    break

                _send_json(self.wfile, {"type": "error", "message": "unknown_message_type"})
        finally:
            if name and not explicit_left:
                runtime.detach(name)
            if watcher_id:
                runtime.detach_watcher(watcher_id)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class, runtime: RoomHostRuntime):
        super().__init__(server_address, handler_class)
        self.runtime = runtime


class RoomHostServer:
    def __init__(
        self,
        room_id: str,
        host_name: str,
        max_players: int,
        max_ai: int,
        settings: OpenAISettings,
        bind: str = "0.0.0.0",
        port: int = 0,
        ai_interval_ms: int = 700,
        human_timeout_sec: int = 120,
    ) -> None:
        self.runtime = RoomHostRuntime(
            room_id=room_id,
            host_name=host_name,
            max_players=max_players,
            max_ai=max_ai,
            settings=settings,
            ai_interval_ms=ai_interval_ms,
            human_timeout_sec=human_timeout_sec,
        )
        self.server = ThreadedTCPServer((bind, port), Handler, self.runtime)
        self.thread: threading.Thread | None = None

    @property
    def listen_host(self) -> str:
        return self.server.server_address[0]

    @property
    def listen_port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> None:
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
