from __future__ import annotations

import json
import socketserver
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Queue

from game.cli import parse_command
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
        msg = event.get("message")
        if msg:
            self.broadcast({"type": "event", "message": msg})
        self.broadcast({"type": "state", "payload": self.snapshot()})
        if event.get("event_type") == "game_over":
            self.broadcast({"type": "game_over", "payload": event})

    def _run_loop(self) -> None:
        assert self.engine is not None
        engine = self.engine
        self.broadcast({"type": "game_started", "payload": self.snapshot()})
        ai_interval = max(0.05, self.ai_interval_ms / 1000.0)

        while not engine.room.finished:
            engine.start_phase()
            self.broadcast({"type": "phase_started", "payload": self.snapshot()})
            ai_next_ts = 0.0
            last_human_activity = {p.name: time.time() for p in engine.room.players if p.is_human and p.alive}

            while not engine.room.finished and not engine.is_phase_done():
                incoming = self.next_action(timeout=0.1)
                if incoming:
                    player_name, text, ts = incoming
                    actor = next((p for p in engine.room.players if p.name == player_name and p.alive and not p.phase_ended), None)
                    if actor is None:
                        self.send_to(player_name, {"type": "event", "message": "当前无法行动"})
                        continue
                    action = parse_command(engine.room, actor, text)
                    if action is None:
                        self.send_to(player_name, {"type": "event", "message": "无效命令"})
                        continue
                    ok, err = engine.apply_and_log(actor, action)
                    if not ok:
                        self.send_to(player_name, {"type": "event", "message": f"非法动作: {err}"})
                    last_human_activity[player_name] = ts
                    continue

                now = time.time()
                connected = self.connected_names()
                for p in engine.room.players:
                    if engine.room.finished:
                        break
                    if (not p.alive) or (not p.is_human) or p.phase_ended:
                        continue
                    last_ts = last_human_activity.get(p.name, now)
                    disconnected = p.name not in connected
                    timed_out = (now - last_ts) >= self.human_timeout_sec
                    if disconnected or timed_out:
                        engine.apply_and_log(
                            p,
                            Action(player_id=p.player_id, kind=ActionKind.REST, source="SYSTEM", reason="human_timeout_or_disconnect"),
                        )
                        self.send_to(p.name, {"type": "event", "message": "已自动执行 REST（超时或离线）"})
                        last_human_activity[p.name] = now

                if now >= ai_next_ts:
                    for p in engine.room.players:
                        if engine.room.finished:
                            break
                        if p.alive and (not p.is_human) and (not p.phase_ended):
                            a = engine.agent_runtime.decide(engine.room, p)
                            ok, _ = engine.apply_and_log(p, a)
                            if not ok:
                                engine.apply_and_log(
                                    p,
                                    Action(player_id=p.player_id, kind=ActionKind.REST, source="AI", reason="invalid_fallback"),
                                )
                    ai_next_ts = now + ai_interval

            engine.settle_current_phase()

        self.running = False


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        runtime: RoomHostRuntime = self.server.runtime  # type: ignore[attr-defined]
        name = ""
        try:
            for raw in self.rfile:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                msg = json.loads(line)
                mtype = msg.get("type")
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
                    members = sorted(runtime.joined_names)
                    _send_json(
                        self.wfile,
                        {
                            "type": "members",
                            "room_id": runtime.room_id,
                            "host_name": runtime.host_name,
                            "max_players": runtime.max_players,
                            "max_ai": runtime.max_ai,
                            "members": members,
                        },
                    )
                    continue

                if mtype == "action":
                    text = str(msg.get("text", "")).strip()
                    runtime.push_action(name, text)
                    continue

                _send_json(self.wfile, {"type": "error", "message": "unknown_message_type"})
        finally:
            if name:
                runtime.detach(name)


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
