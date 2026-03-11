from __future__ import annotations

import argparse
import json
import select
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import deque
from queue import Empty, Queue
from typing import Callable
from urllib.parse import parse_qs, urlparse

from game.constants import item_zh_label
from game.room_host import RoomHostServer
from game.settings import OpenAISettings


def _send_json(sock: socket.socket, payload: dict) -> None:
    sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def _recv_one(file_in) -> dict:
    while True:
        line = file_in.readline()
        if not line:
            raise ConnectionError("connection_closed")
        line = line.strip()
        if not line:
            continue
        return json.loads(line)


def _broker_request(broker_host: str, broker_port: int, payload: dict) -> dict:
    s = socket.create_connection((broker_host, broker_port))
    f = s.makefile("r", encoding="utf-8")
    try:
        _send_json(s, payload)
        return _recv_one(f)
    finally:
        f.close()
        s.close()


def _print_rooms(rooms: list[dict]) -> None:
    print("=== 房间列表 ===")
    if not rooms:
        print("(空)")
        return
    for r in rooms:
        status = str(r.get("status", "WAITING")).upper()
        status_cn = "游戏中" if status == "RUNNING" else "等待中"
        human_players = int(r.get("human_players", 0))
        watcher_count = int(r.get("watcher_count", 0))
        print(
            f"- {r.get('room_id')} host={r.get('host_name')} "
            f"{r.get('endpoint_host')}:{r.get('endpoint_port')} "
            f"max={r.get('max_players')} humans={human_players} watchers={watcher_count} "
            f"max_ai={r.get('max_ai')} status={status_cn}"
        )


def _print_state(payload: dict, self_name: str) -> None:
    print("")
    print(f"=== Phase {payload.get('phase_no')} | {payload.get('phase')} | seq={payload.get('action_seq')} ===")
    for p in payload.get("players", []):
        me = " (你)" if p.get("name") == self_name else ""
        status = "alive" if p.get("alive") else "dead"
        print(
            f"- {p.get('name')}{me} {status} "
            f"pos=({p.get('x')},{p.get('y')}) w={p.get('water')} f={p.get('food')} e={p.get('exposure')} "
            f"ended={p.get('phase_ended')}"
        )


def _print_lobby_help() -> None:
    print("")
    print("=== 大厅命令 ===")
    print("- list [page] [page_size] [status]")
    print("- create [max_players] [max_ai]")
    print("- join <room_id>")
    print("- watch <room_id>")
    print("- me                  # 查询当前客户端状态")
    print("- help")
    print("- quit")
    print("示例: list 1 20 WAITING")
    print("示例: create 6 2")
    print("示例: watch ABC123")
    print("")


class _LocalControlState:
    def __init__(
        self,
        queue: Queue[str],
        room_queue: Queue[str],
        is_busy: Callable[[], bool],
        snapshot: Callable[[], dict],
        event_bus: "_LocalEventBus",
        cache_web_templates: bool,
    ) -> None:
        self.queue = queue
        self.room_queue = room_queue
        self.is_busy = is_busy
        self.snapshot = snapshot
        self.event_bus = event_bus
        self.cache_web_templates = cache_web_templates
        self.template_cache: dict[str, str] = {}


class _LocalEventBus:
    def __init__(self, maxlen: int = 512) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._events: deque[tuple[int, dict]] = deque(maxlen=maxlen)
        self._next_id = 1

    def publish(self, event: dict) -> int:
        with self._cond:
            eid = self._next_id
            self._next_id += 1
            self._events.append((eid, event))
            self._cond.notify_all()
            return eid

    def after(self, last_id: int) -> list[tuple[int, dict]]:
        with self._lock:
            return [(eid, e) for eid, e in self._events if eid > last_id]

    def wait_after(self, last_id: int, timeout: float) -> list[tuple[int, dict]]:
        with self._cond:
            items = [(eid, e) for eid, e in self._events if eid > last_id]
            if items:
                return items
            self._cond.wait(timeout=timeout)
            return [(eid, e) for eid, e in self._events if eid > last_id]


class _LocalControlHandler(BaseHTTPRequestHandler):
    server_version = "SurvivalLocalControl/1.0"
    _host_html_cache: str | None = None
    _game_html_cache: str | None = None

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send_json({"ok": True}, status=204)

    def do_GET(self) -> None:
        state: _LocalControlState = self.server.control_state  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query or "")
        if path.startswith("/assets/"):
            self._serve_asset(path)
            return
        if path == "/health":
            self._send_json({"ok": True, "busy": state.is_busy()})
            return
        if path == "/me":
            snap = state.snapshot()
            self._send_json({"ok": True, **snap})
            return
        if path == "/room":
            snap = state.snapshot()
            self._send_json({"ok": True, **snap})
            return
        if path == "/events":
            last_id = 0
            try:
                raw = qs.get("since", ["0"])[0]
                last_id = int(raw)
            except Exception:
                last_id = 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            init_event = {"type": "state", "snapshot": state.snapshot()}
            try:
                payload = json.dumps(init_event, ensure_ascii=False)
                self.wfile.write(f"id: {last_id}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    items = state.event_bus.wait_after(last_id, timeout=12.0)
                    if not items:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    for eid, event in items:
                        payload = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"id: {eid}\ndata: {payload}\n\n".encode("utf-8"))
                        last_id = eid
                    self.wfile.flush()
            except Exception:
                return
            return
        if path == "/host":
            body = self._load_template("host_server.html").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/game":
            body = self._load_template("game_client.html").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"ok": False, "message": "not_found"}, status=404)

    def _serve_asset(self, request_path: str) -> None:
        web_root = (Path(__file__).resolve().parent / "web").resolve()
        rel = request_path.lstrip("/")
        target = (web_root / rel).resolve()
        try:
            target.relative_to(web_root)
        except ValueError:
            self._send_json({"ok": False, "message": "not_found"}, status=404)
            return
        if (not target.exists()) or (not target.is_file()):
            self._send_json({"ok": False, "message": "not_found"}, status=404)
            return
        content_type = "application/octet-stream"
        suffix = target.suffix.lower()
        if suffix == ".png":
            content_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            content_type = "image/jpeg"
        elif suffix == ".webp":
            content_type = "image/webp"
        elif suffix == ".svg":
            content_type = "image/svg+xml"
        elif suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        state: _LocalControlState = self.server.control_state  # type: ignore[attr-defined]
        if self.path not in {"/join", "/create", "/start", "/leave", "/action", "/members"}:
            self._send_json({"ok": False, "message": "not_found"}, status=404)
            return
        if self.path in {"/join", "/create"} and state.is_busy():
            self._send_json({"ok": False, "message": "client_busy_in_room"}, status=409)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json({"ok": False, "message": "invalid_json"}, status=400)
            return
        if self.path == "/join":
            room_id = str(payload.get("room_id", "")).strip().upper()
            if not room_id:
                self._send_json({"ok": False, "message": "room_id_required"}, status=400)
                return
            state.queue.put(f"join {room_id}")
            self._send_json({"ok": True, "message": "join_enqueued", "room_id": room_id}, status=200)
            return
        if self.path == "/start":
            if not state.is_busy():
                self._send_json({"ok": False, "message": "not_in_room"}, status=409)
                return
            state.room_queue.put("start")
            self._send_json({"ok": True, "message": "start_enqueued"}, status=200)
            return
        if self.path == "/leave":
            if not state.is_busy():
                self._send_json({"ok": False, "message": "not_in_room"}, status=409)
                return
            state.room_queue.put("leave")
            self._send_json({"ok": True, "message": "leave_enqueued"}, status=200)
            return
        if self.path == "/members":
            if not state.is_busy():
                self._send_json({"ok": False, "message": "not_in_room"}, status=409)
                return
            state.room_queue.put("members")
            self._send_json({"ok": True, "message": "members_enqueued"}, status=200)
            return
        if self.path == "/action":
            if not state.is_busy():
                self._send_json({"ok": False, "message": "not_in_room"}, status=409)
                return
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json({"ok": False, "message": "action_text_required"}, status=400)
                return
            state.room_queue.put(f"action {text}")
            self._send_json({"ok": True, "message": "action_enqueued"}, status=200)
            return

        try:
            max_players = int(payload.get("max_players", 6))
            max_ai = int(payload.get("max_ai", max_players))
        except Exception:
            self._send_json({"ok": False, "message": "invalid_number"}, status=400)
            return
        if max_players < 1:
            self._send_json({"ok": False, "message": "invalid_max_players"}, status=400)
            return
        if max_ai < 0 or max_ai > max_players:
            self._send_json({"ok": False, "message": "invalid_max_ai"}, status=400)
            return
        state.queue.put(f"create {max_players} {max_ai}")
        self._send_json(
            {
                "ok": True,
                "message": "create_enqueued",
                "max_players": max_players,
                "max_ai": max_ai,
            },
            status=200,
        )

    def log_message(self, fmt: str, *args) -> None:
        return

    def _load_template(self, filename: str) -> str:
        state: _LocalControlState = self.server.control_state  # type: ignore[attr-defined]
        if state.cache_web_templates and filename in state.template_cache:
            return state.template_cache[filename]
        template = Path(__file__).resolve().parent / "web" / filename
        text = template.read_text(encoding="utf-8")
        if state.cache_web_templates:
            state.template_cache[filename] = text
        return text


class _LocalControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_class, control_state: _LocalControlState):
        super().__init__(server_address, handler_class)
        self.control_state = control_state


def _start_local_control_api(
    bind: str,
    port: int,
    queue: Queue[str],
    room_queue: Queue[str],
    is_busy: Callable[[], bool],
    snapshot: Callable[[], dict],
    event_bus: _LocalEventBus,
    cache_web_templates: bool,
):
    control_state = _LocalControlState(
        queue=queue,
        room_queue=room_queue,
        is_busy=is_busy,
        snapshot=snapshot,
        event_bus=event_bus,
        cache_web_templates=cache_web_templates,
    )
    server = _LocalControlServer((bind, port), _LocalControlHandler, control_state)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _print_room_help() -> None:
    print("")
    print("=== 房间命令 ===")
    print("- start                # 仅房主可用")
    print("- members              # 查看成员")
    print("- MOVE x y")
    print("- EXPLORE")
    print("- USE 物资标识")
    print("- TAKE item1 item2 item3")
    print("- REST")
    print("- ATTACK player_name")
    print("- 物资别名/编码: B/BREAD W/BOTTLED_WATER C/BISCUIT G/CANNED_FOOD T/BARREL_WATER Q/CLEAN_WATER")
    print("- help")
    print("- leave                # 离开当前房间/对局并返回大厅")
    print("")


def _start_heartbeat(
    broker_host: str,
    broker_port: int,
    room_id: str,
    room_token: str,
    stop_event: threading.Event,
    status_provider: Callable[[], str],
    room_stats_provider: Callable[[], dict],
) -> threading.Thread:
    heartbeat_interval_sec = 12.0

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                s = socket.create_connection((broker_host, broker_port), timeout=3)
                f = s.makefile("r", encoding="utf-8")
                stats = room_stats_provider()
                payload = {
                    "type": "heartbeat",
                    "room_id": room_id,
                    "room_token": room_token,
                    "status": status_provider(),
                }
                payload.update(stats)
                _send_json(s, payload)
                _recv_one(f)
                f.close()
                s.close()
            except Exception:
                pass
            stop_event.wait(heartbeat_interval_sec)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def _print_watch_event(seq: int, event: dict) -> None:
    et = event.get("event_type", "unknown")
    phase_no = event.get("phase_no", "-")
    phase = event.get("phase", "-")
    if et == "round_action":
        a = event.get("action", {})
        payload = a.get("payload", {})
        if isinstance(payload, dict):
            payload = dict(payload)
            if "item" in payload and isinstance(payload["item"], str):
                payload["item"] = item_zh_label(payload["item"])
            if "items" in payload and isinstance(payload["items"], list):
                payload["items"] = [item_zh_label(x) if isinstance(x, str) else x for x in payload["items"]]
        print(
            f"[watch#{seq}] phase={phase_no}/{phase} actor={event.get('actor')} "
            f"is_human={event.get('is_human')} seq={event.get('action_seq')} "
            f"action={a.get('kind')} payload={payload}"
        )
        return
    if et == "round_settled":
        print(
            f"[watch#{seq}] phase={phase_no}/{phase} ROUND_SETTLED "
            f"seq={event.get('action_seq_start')}..{event.get('action_seq_end')} "
            f"actions={event.get('processed_actions')}"
        )
        return
    if et in {"game_started", "phase_started", "phase_settle", "game_over"}:
        print(f"[watch#{seq}] phase={phase_no}/{phase} {et}: {event.get('message', '')}")
        if et == "game_over":
            print(f"           finish_reason={event.get('finish_reason')} survivors={event.get('survivors')}")
        return
    if et == "phase_settled_summary":
        print(
            f"[watch#{seq}] PHASE_SETTLED settled={event.get('settled_phase_no')}/{event.get('settled_phase')} "
            f"players={len(event.get('players', []))}"
        )
        for p in event.get("players", []):
            print(
                f"           {p.get('name')} alive={p.get('alive')} "
                f"pos=({p.get('x')},{p.get('y')}) w={p.get('water')} f={p.get('food')} e={p.get('exposure')}"
            )
        return
    print(f"[watch#{seq}] {event}")


def _watch_room(broker_host: str, broker_port: int, room_id: str) -> None:
    lookup = _broker_request(broker_host, broker_port, {"type": "lookup", "room_id": room_id})
    if lookup.get("type") != "room":
        print(f"[错误] {lookup.get('message', 'room_not_found')}")
        return
    room = lookup.get("room", {})
    host = room.get("endpoint_host")
    port = int(room.get("endpoint_port"))
    sock = socket.create_connection((host, port))
    fin = sock.makefile("r", encoding="utf-8")
    _send_json(sock, {"type": "watch_join", "room_id": room_id})
    joined = _recv_one(fin)
    if joined.get("type") != "watch_joined":
        print(f"[错误] {joined.get('message', 'watch_join_failed')}")
        fin.close()
        sock.close()
        return
    print(f"[观察] room={room_id} host={host}:{port}")
    print("按 Ctrl+C 退出观察。")
    try:
        for raw in fin:
            line = raw.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("type") == "watch_event":
                _print_watch_event(int(msg.get("seq", 0)), msg.get("event", {}))
            elif msg.get("type") == "error":
                print(f"[错误] {msg.get('message')}")
                break
    finally:
        fin.close()
        sock.close()


def _play_room(
    host: str,
    port: int,
    room_id: str,
    player_name: str,
    room_api_queue: Queue[str] | None = None,
    state_lock: threading.Lock | None = None,
    client_state: dict | None = None,
    enable_console: bool = True,
    snapshot_provider: Callable[[], dict] | None = None,
    event_sink: Callable[[dict], None] | None = None,
) -> str:
    sock = socket.create_connection((host, port))
    fin = sock.makefile("r", encoding="utf-8")
    stop_event = threading.Event()
    game_started = threading.Event()
    submit_state = {"submitted": False}
    leave_requested = {"value": False}
    exit_reason = "disconnected"
    joined_ok = {"value": False}

    _send_json(sock, {"type": "join", "room_id": room_id, "name": player_name})
    print(f"[房间] 尝试加入 {room_id} @ {host}:{port}")
    _print_room_help()

    def _update_state(**kwargs) -> None:
        if state_lock is None or client_state is None:
            return
        with state_lock:
            client_state.update(kwargs)
        if event_sink is not None and snapshot_provider is not None:
            event_sink({"type": "state", "snapshot": snapshot_provider()})

    def _sync_self_payload(payload: dict | None) -> None:
        if not isinstance(payload, dict):
            return
        phase_no = payload.get("phase_no")
        phase = payload.get("phase")
        action_seq = payload.get("action_seq")
        players = payload.get("players", [])
        view = payload.get("view", {})
        if not isinstance(players, list):
            players = []
        actor = None
        for p in players:
            if isinstance(p, dict) and p.get("name") == player_name:
                actor = p
                break
        if actor is None and players and isinstance(players[0], dict):
            # self_snapshot payload may only contain one player
            actor = players[0]
        updates = {}
        if isinstance(phase_no, int):
            updates["phase_no"] = phase_no
        if isinstance(phase, str):
            updates["phase"] = phase
        if isinstance(action_seq, int):
            updates["action_seq"] = action_seq
        if isinstance(actor, dict):
            for k_src, k_dst in [
                ("x", "player_x"),
                ("y", "player_y"),
                ("water", "player_water"),
                ("food", "player_food"),
                ("exposure", "player_exposure"),
                ("alive", "player_alive"),
                ("phase_ended", "player_phase_ended"),
            ]:
                if k_src in actor:
                    updates[k_dst] = actor.get(k_src)
        if isinstance(view, dict):
            if "bag_text" in view:
                updates["bag_text"] = str(view.get("bag_text", ""))
            if "visible_loot_text" in view:
                updates["visible_loot_text"] = str(view.get("visible_loot_text", ""))
        if updates:
            _update_state(**updates)

    # Clear stale room-api commands before entering a fresh room session.
    if room_api_queue is not None:
        try:
            while True:
                room_api_queue.get_nowait()
        except Empty:
            pass

    def _input_loop() -> None:
        prompt_shown = False
        while not stop_event.is_set():
            if not prompt_shown:
                sys.stdout.write("(room)> ")
                sys.stdout.flush()
                prompt_shown = True
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.2)
            except Exception:
                continue
            if not r:
                continue
            line = sys.stdin.readline()
            prompt_shown = False
            if line == "":
                break
            text = line.strip()
            if not text:
                continue
            if text.lower() in {"help", "h", "?"}:
                _print_room_help()
                continue
            if text.lower() in {"leave", "back"}:
                if leave_requested["value"]:
                    print("[房间] 已发送离开请求，等待服务端确认。")
                    continue
                _send_json(sock, {"type": "leave"})
                leave_requested["value"] = True
                print("[房间] 已请求离开，等待服务端确认...")
                continue
            if text.lower() == "members":
                _send_json(sock, {"type": "members"})
                continue
            if text.lower() == "start":
                _send_json(sock, {"type": "start"})
                continue
            if not game_started.is_set():
                if leave_requested["value"]:
                    print("[房间] 正在离开中，请稍候。")
                    continue
                print("[提示] 游戏未开始，可输入 start（仅房主有效）或 members 查看成员。")
                continue
            if leave_requested["value"]:
                print("[房间] 正在离开中，请稍候。")
                continue
            if submit_state["submitted"]:
                print("[等待] 你本轮已提交动作，请等待所有人完成并结算。")
                continue
            _send_json(sock, {"type": "action", "text": text})
            submit_state["submitted"] = True
            print("[已提交] 等待其他玩家提交，本轮结算后可继续操作。")

    if enable_console:
        threading.Thread(target=_input_loop, daemon=True).start()

    def _room_api_loop() -> None:
        if room_api_queue is None:
            return
        while not stop_event.is_set():
            try:
                cmd = room_api_queue.get(timeout=0.2).strip().lower()
            except Empty:
                continue
            if cmd == "start":
                _send_json(sock, {"type": "start"})
                print("[本地控制] 已发送 start 请求")
            elif cmd == "members":
                _send_json(sock, {"type": "members"})
            elif cmd == "leave":
                if leave_requested["value"]:
                    continue
                leave_requested["value"] = True
                _send_json(sock, {"type": "leave"})
                print("[本地控制] 已发送 leave 请求，等待服务端确认...")
            elif cmd.startswith("action "):
                text = cmd[7:].strip()
                if not text:
                    continue
                _send_json(sock, {"type": "action", "text": text})
                print(f"[本地控制] 已发送 action: {text}")
            elif cmd:
                print(f"[本地控制] 未知房间命令: {cmd}")

    threading.Thread(target=_room_api_loop, daemon=True).start()

    try:
        for raw in fin:
            line = raw.strip()
            if not line:
                continue
            msg = json.loads(line)
            mtype = msg.get("type")
            if event_sink is not None:
                event_sink({"type": "room_message", "message": msg})
            if mtype == "error":
                print(f"[错误] {msg.get('message')}")
                if not joined_ok["value"]:
                    exit_reason = str(msg.get("message", "join_failed"))
                    _update_state(in_room=False, room_id="", room_status="LOBBY")
                    break
                continue
            if mtype == "joined":
                joined_ok["value"] = True
                print(f"[房间] 已加入，房主={msg.get('host_name')} max={msg.get('max_players')}")
                is_host = str(msg.get("host_name", "")) == player_name
                if msg.get("running"):
                    print("[房间] 游戏已在进行中。")
                    game_started.set()
                    _update_state(
                        in_room=True,
                        room_id=room_id,
                        room_status="RUNNING",
                        room_max_players=int(msg.get("max_players", 0) or 0),
                        room_max_ai=int(msg.get("max_ai", 0) or 0),
                        room_member_count=max(1, int(client_state.get("room_member_count", 0) if client_state else 0)),
                        is_host=is_host,
                    )
                else:
                    print("[提示] 等待房主 start 开始游戏。")
                    _update_state(
                        in_room=True,
                        room_id=room_id,
                        room_status="WAITING",
                        room_max_players=int(msg.get("max_players", 0) or 0),
                        room_max_ai=int(msg.get("max_ai", 0) or 0),
                        room_member_count=max(1, int(client_state.get("room_member_count", 0) if client_state else 0)),
                        is_host=is_host,
                    )
                continue
            if mtype == "members":
                members = msg.get("members", [])
                print(f"[房间成员] 共 {len(members)} 人")
                for n in members:
                    print(f"- {n}")
                _update_state(room_member_count=len(members))
                continue
            if mtype == "members_update":
                p = msg.get("payload", {})
                members = p.get("members", [])
                print(f"[成员更新] 当前共 {len(members)} 人")
                for n in members:
                    print(f"- {n}")
                _update_state(room_member_count=len(members))
                continue
            if mtype == "started":
                print(f"[房间] 已开始: {msg.get('room_id')}")
                continue
            if mtype == "event":
                print(f"[事件] {msg.get('message')}")
                continue
            if mtype == "action_ack":
                status = msg.get("status")
                reason = msg.get("reason", "")
                if status == "accepted":
                    submit_state["submitted"] = True
                    print(f"[动作确认] 已接收，等待结算 ({reason})")
                elif status == "auto":
                    submit_state["submitted"] = True
                    print(f"[动作确认] 系统已自动处理 ({reason})")
                else:
                    submit_state["submitted"] = False
                    print(f"[动作拒绝] {reason}")
                continue
            if mtype == "action_prompt":
                if isinstance(msg.get("view"), dict):
                    _update_state(
                        bag_text=str(msg["view"].get("bag_text", "")),
                        visible_loot_text=str(msg["view"].get("visible_loot_text", "")),
                    )
                print("[操作提示]")
                print(msg.get("message", ""))
                continue
            if mtype == "game_started":
                game_started.set()
                submit_state["submitted"] = False
                _update_state(in_room=True, room_id=room_id, room_status="RUNNING")
                _sync_self_payload(msg.get("payload"))
                print(f"[系统] {msg.get('message', '游戏开始，可随时输入动作。')}")
                if msg.get("operation_hint"):
                    print(f"[操作提示] {msg.get('operation_hint')}")
                print("[提示] 本轮每人只能提交 1 次，提交后进入等待。")
                _print_state(msg.get("payload", {}), player_name)
                continue
            if mtype == "phase_started":
                submit_state["submitted"] = False
                _update_state(in_room=True, room_id=room_id, room_status="RUNNING")
                _sync_self_payload(msg.get("payload"))
                print(f"[系统] {msg.get('message', '新阶段开始。')}")
                if msg.get("operation_hint"):
                    print(f"[操作提示] {msg.get('operation_hint')}")
                print("[提示] 请输入一个动作，提交后等待全员结算。")
                _print_state(msg.get("payload", {}), player_name)
                continue
            if mtype == "round_settled":
                submit_state["submitted"] = False
                print(
                    f"[回合结算] {msg.get('message')} "
                    f"phase={msg.get('phase')} "
                    f"seq={msg.get('action_seq_start')}..{msg.get('action_seq_end')} "
                    f"actions={msg.get('processed_actions')}"
                )
                payload = msg.get("payload")
                if isinstance(payload, dict):
                    _sync_self_payload(payload)
                    _print_state(payload, player_name)
                continue
            if mtype == "state":
                _sync_self_payload(msg.get("payload"))
                _print_state(msg.get("payload", {}), player_name)
                continue
            if mtype == "game_over":
                payload = msg.get("payload", {})
                print(f"[游戏结束] 原因: {payload.get('finish_reason')}")
                print(f"[幸存者] {payload.get('survivors')}")
                exit_reason = "game_over"
                _update_state(
                    in_room=False,
                    room_id="",
                    room_status="LOBBY",
                    room_max_players=0,
                    room_max_ai=0,
                    room_member_count=0,
                    is_host=False,
                    phase_no=0,
                    phase="",
                    action_seq=0,
                    player_x=0,
                    player_y=0,
                    player_water=0,
                    player_food=0,
                    player_exposure=0,
                    player_alive=False,
                    player_phase_ended=False,
                )
                break
            if mtype == "left":
                print(f"[房间] {msg.get('message', '已离开房间。')}")
                exit_reason = "left"
                _update_state(
                    in_room=False,
                    room_id="",
                    room_status="LOBBY",
                    room_max_players=0,
                    room_max_ai=0,
                    room_member_count=0,
                    is_host=False,
                    phase_no=0,
                    phase="",
                    action_seq=0,
                    player_x=0,
                    player_y=0,
                    player_water=0,
                    player_food=0,
                    player_exposure=0,
                    player_alive=False,
                    player_phase_ended=False,
                )
                break
            if mtype == "room_closed":
                print(f"[房间关闭] {msg.get('message', '房间已关闭。')}")
                exit_reason = str(msg.get("room_close_reason", "room_closed"))
                _update_state(
                    in_room=False,
                    room_id="",
                    room_status="LOBBY",
                    room_max_players=0,
                    room_max_ai=0,
                    room_member_count=0,
                    is_host=False,
                    phase_no=0,
                    phase="",
                    action_seq=0,
                    player_x=0,
                    player_y=0,
                    player_water=0,
                    player_food=0,
                    player_exposure=0,
                    player_alive=False,
                    player_phase_ended=False,
                )
                break
    finally:
        stop_event.set()
        try:
            fin.close()
        finally:
            sock.close()
    _update_state(
        in_room=False,
        room_id="",
        room_status="LOBBY",
        room_max_players=0,
        room_max_ai=0,
        room_member_count=0,
        is_host=False,
        phase_no=0,
        phase="",
        action_seq=0,
        player_x=0,
        player_y=0,
        player_water=0,
        player_food=0,
        player_exposure=0,
        player_alive=False,
        player_phase_ended=False,
    )
    return exit_reason


def _teardown_hosted_room(
    broker_host: str,
    broker_port: int,
    hosted_room_id: str,
    hosted_room_token: str,
    hosted_server: RoomHostServer | None,
    hb_stop: threading.Event,
) -> tuple[str, str, RoomHostServer | None]:
    if hosted_room_id and hosted_room_token:
        try:
            s = socket.create_connection((broker_host, broker_port), timeout=2)
            f = s.makefile("r", encoding="utf-8")
            _send_json(s, {"type": "remove", "room_id": hosted_room_id, "room_token": hosted_room_token})
            _recv_one(f)
            f.close()
            s.close()
        except Exception:
            pass
    hb_stop.set()
    if hosted_server is not None:
        hosted_server.stop()
    return "", "", None


def _poll_lobby_command(api_queue: Queue[str]) -> tuple[str, str]:
    prompt_shown = False
    while True:
        try:
            cmd = api_queue.get_nowait().strip()
            if cmd:
                if prompt_shown:
                    print("")
                return cmd, "api"
        except Empty:
            pass
        if not prompt_shown:
            sys.stdout.write("(lobby)> ")
            sys.stdout.flush()
            prompt_shown = True
        r, _, _ = select.select([sys.stdin], [], [], 0.25)
        if not r:
            continue
        line = sys.stdin.readline()
        if line == "":
            return "quit", "stdin"
        return line.strip(), "stdin"


def run_client() -> None:
    parser = argparse.ArgumentParser(description="Survival Story unified player client")
    parser.add_argument("--broker-host", required=True, help="broker host")
    parser.add_argument("--broker-port", type=int, default=9010, help="broker port")
    parser.add_argument("--name", required=True, help="player name")
    parser.add_argument("--public-host", default="", help="your public host/ip for room registration")
    parser.add_argument("--room-bind", default="0.0.0.0", help="local room-host bind address")
    parser.add_argument("--room-port", type=int, default=0, help="local room-host port, 0 for auto")
    parser.add_argument("--config", default="config/openai.json", help="OpenAI config path for hosting room")
    parser.add_argument("--ai-interval-ms", type=int, default=700, help="AI action interval in hosted room")
    parser.add_argument("--human-timeout-sec", type=int, default=120, help="human timeout in hosted room")
    parser.add_argument("--control-bind", default="127.0.0.1", help="local control api bind")
    parser.add_argument("--control-port", type=int, default=17890, help="local control api port, <=0 to disable")
    parser.add_argument("--dev-web", action="store_true", help="disable local web template cache for development")
    parser.add_argument("--console", action="store_true", help="enable interactive console mode")
    args = parser.parse_args()

    print(f"连接 broker: {args.broker_host}:{args.broker_port}，玩家名 {args.name}")
    if args.console:
        _print_lobby_help()
    else:
        print("已启用网页优先模式（无终端交互输入）。")

    hosted_server: RoomHostServer | None = None
    hosted_room_id = ""
    hosted_room_token = ""
    hb_stop = threading.Event()
    heartbeat_thread = None
    in_room_lock = threading.Lock()
    client_state = {
        "name": args.name,
        "room_id": "",
        "room_status": "LOBBY",
        "in_room": False,
        "room_max_players": 0,
        "room_max_ai": 0,
        "room_member_count": 0,
        "is_host": False,
        "phase_no": 0,
        "phase": "",
        "action_seq": 0,
        "player_x": 0,
        "player_y": 0,
        "player_water": 0,
        "player_food": 0,
        "player_exposure": 0,
        "player_alive": False,
        "player_phase_ended": False,
        "bag_text": "未知",
        "visible_loot_text": "未知",
    }
    event_bus = _LocalEventBus()
    api_queue: Queue[str] = Queue()
    room_api_queue: Queue[str] = Queue()
    control_server = None

    def _is_busy() -> bool:
        with in_room_lock:
            return bool(client_state["in_room"])

    def _snapshot() -> dict:
        with in_room_lock:
            snap = dict(client_state)
        status_cn = {"LOBBY": "大厅中", "WAITING": "等待中", "RUNNING": "游戏中"}.get(snap.get("room_status"), "未知")
        return {
            "name": snap.get("name", ""),
            "room_id": snap.get("room_id", ""),
            "room_status": snap.get("room_status", "LOBBY"),
            "room_status_cn": status_cn,
            "in_room": bool(snap.get("in_room", False)),
            "room_max_players": int(snap.get("room_max_players", 0) or 0),
            "room_max_ai": int(snap.get("room_max_ai", 0) or 0),
            "room_member_count": int(snap.get("room_member_count", 0) or 0),
            "is_host": bool(snap.get("is_host", False)),
            "phase_no": int(snap.get("phase_no", 0) or 0),
            "phase": snap.get("phase", ""),
            "action_seq": int(snap.get("action_seq", 0) or 0),
            "player_x": int(snap.get("player_x", 0) or 0),
            "player_y": int(snap.get("player_y", 0) or 0),
            "player_water": int(snap.get("player_water", 0) or 0),
            "player_food": int(snap.get("player_food", 0) or 0),
            "player_exposure": int(snap.get("player_exposure", 0) or 0),
            "player_alive": bool(snap.get("player_alive", False)),
            "player_phase_ended": bool(snap.get("player_phase_ended", False)),
            "bag_text": snap.get("bag_text", "未知"),
            "visible_loot_text": snap.get("visible_loot_text", "未知"),
        }

    if args.control_port > 0:
        control_server = _start_local_control_api(
            args.control_bind,
            args.control_port,
            api_queue,
            room_api_queue,
            _is_busy,
            _snapshot,
            event_bus,
            cache_web_templates=(not args.dev_web),
        )
        event_bus.publish({"type": "state", "snapshot": _snapshot()})
        print(
            f"本地控制API: http://{args.control_bind}:{args.control_port} "
            "(GET /me, GET /room, GET /events, GET /host, GET /game, POST /join, POST /create, POST /start, POST /leave, POST /action, POST /members)"
        )
        if args.dev_web:
            print("本地网页模板缓存: 关闭（dev-web 模式，刷新页面即可生效）")

    while True:
        if args.console:
            cmd, source = _poll_lobby_command(api_queue)
        else:
            try:
                cmd = api_queue.get(timeout=0.5).strip()
            except Empty:
                continue
            source = "api"
        if not cmd:
            continue
        if source == "api":
            print(f"\n[本地控制] 执行命令: {cmd}")
        parts = cmd.split()
        op = parts[0].lower()
        if op in {"help", "h", "?"}:
            _print_lobby_help()
            continue
        if op in {"quit", "exit"}:
            break
        if op == "me":
            snap = _snapshot()
            print(
                f"[我的状态] name={snap['name']} room={snap['room_id'] or '-'} "
                f"status={snap['room_status_cn']}({snap['room_status']}) "
                f"members={snap['room_member_count']} max={snap['room_max_players']} ai_max={snap['room_max_ai']}"
            )
            continue

        if op == "list":
            page = 1
            page_size = 20
            status = ""
            if len(parts) >= 2 and parts[1].isdigit():
                page = max(1, int(parts[1]))
            if len(parts) >= 3 and parts[2].isdigit():
                page_size = max(1, int(parts[2]))
            if len(parts) >= 4:
                status = parts[3].strip().upper()
            s = socket.create_connection((args.broker_host, args.broker_port))
            f = s.makefile("r", encoding="utf-8")
            payload = {"type": "list", "page": page, "page_size": page_size}
            if status in {"WAITING", "RUNNING"}:
                payload["status"] = status
            _send_json(s, payload)
            msg = _recv_one(f)
            if msg.get("type") == "rooms":
                _print_rooms(msg.get("rooms", []))
                print(
                    f"[分页] page={msg.get('page', page)} size={msg.get('page_size', page_size)} "
                    f"total={msg.get('total', 0)} total_pages={msg.get('total_pages', 0)}"
                )
            else:
                print(f"[错误] {msg.get('message')}")
            f.close()
            s.close()
            continue

        if op == "create":
            max_players = 6
            max_ai = 6
            if len(parts) >= 2 and parts[1].isdigit():
                max_players = int(parts[1])
                max_ai = max_players
            if len(parts) >= 3 and parts[2].isdigit():
                max_ai = int(parts[2])
            if hosted_server is not None:
                print("[错误] 你已经托管了一个房间。")
                continue
            if max_ai < 0 or max_ai > max_players:
                print("[错误] max_ai 必须满足 0 <= max_ai <= max_players")
                continue

            settings = OpenAISettings.load(args.config)
            # Pre-create broker room id/endpoints first
            s = socket.create_connection((args.broker_host, args.broker_port))
            f = s.makefile("r", encoding="utf-8")

            # Start local room host first to get actual listen port
            # room_id is filled after broker create, recreate server afterwards if needed.
            tmp_room_id = "PENDING"
            hosted_server = RoomHostServer(
                room_id=tmp_room_id,
                host_name=args.name,
                max_players=max_players,
                max_ai=max_ai,
                settings=settings,
                bind=args.room_bind,
                port=args.room_port,
                ai_interval_ms=args.ai_interval_ms,
                human_timeout_sec=args.human_timeout_sec,
            )
            hosted_server.start()
            endpoint_host = args.public_host.strip() or socket.gethostbyname(socket.gethostname())
            endpoint_port = hosted_server.listen_port

            _send_json(
                s,
                {
                    "type": "create",
                    "host_name": args.name,
                    "max_players": max_players,
                    "max_ai": max_ai,
                    "endpoint_host": endpoint_host,
                    "endpoint_port": endpoint_port,
                },
            )
            msg = _recv_one(f)
            if msg.get("type") != "created":
                print(f"[错误] {msg.get('message')}")
                hosted_server.stop()
                hosted_server = None
                f.close()
                s.close()
                continue

            room = msg.get("room", {})
            hosted_room_id = room.get("room_id")
            hosted_room_token = str(room.get("room_token", ""))
            if not hosted_room_token:
                print("[错误] broker 未返回 room_token，创建失败。")
                hosted_server.stop()
                hosted_server = None
                hosted_room_id = ""
                f.close()
                s.close()
                continue
            # update runtime room_id for join checks
            hosted_server.runtime.room_id = hosted_room_id
            print(f"[创建成功] 房间号: {hosted_room_id}, 地址: {endpoint_host}:{endpoint_port}, max={max_players}, max_ai={max_ai}")
            print("已自动加入自己创建的房间。等待其他玩家加入后可输入 start 开局。")

            hb_stop.clear()
            heartbeat_thread = _start_heartbeat(
                args.broker_host,
                args.broker_port,
                hosted_room_id,
                hosted_room_token,
                hb_stop,
                status_provider=lambda: "RUNNING" if (hosted_server and hosted_server.runtime.running) else "WAITING",
                room_stats_provider=lambda: {
                    "max_players": hosted_server.runtime.max_players if hosted_server else max_players,
                    "human_players": len(hosted_server.runtime.joined_names) if hosted_server else 0,
                    "watcher_count": len(hosted_server.runtime.watchers) if hosted_server else 0,
                },
            )
            f.close()
            s.close()

            # Auto-join local hosted room for creator.
            with in_room_lock:
                client_state.update(
                    {
                        "in_room": True,
                        "room_id": hosted_room_id,
                        "room_status": "WAITING",
                        "room_max_players": max_players,
                        "room_max_ai": max_ai,
                        "room_member_count": 1,
                        "is_host": True,
                        "phase_no": 0,
                        "phase": "",
                        "action_seq": 0,
                        "player_x": 0,
                        "player_y": 0,
                        "player_water": 0,
                        "player_food": 0,
                        "player_exposure": 0,
                        "player_alive": True,
                        "player_phase_ended": False,
                        "bag_text": "未知",
                        "visible_loot_text": "未知",
                    }
                )
            event_bus.publish({"type": "state", "snapshot": _snapshot()})
            _play_room(
                host="127.0.0.1",
                port=endpoint_port,
                room_id=hosted_room_id,
                player_name=args.name,
                room_api_queue=room_api_queue,
                state_lock=in_room_lock,
                client_state=client_state,
                enable_console=args.console,
                snapshot_provider=_snapshot,
                event_sink=event_bus.publish,
            )
            with in_room_lock:
                client_state.update(
                    {
                        "in_room": False,
                        "room_id": "",
                        "room_status": "LOBBY",
                        "room_max_players": 0,
                        "room_max_ai": 0,
                        "room_member_count": 0,
                        "is_host": False,
                        "phase_no": 0,
                        "phase": "",
                        "action_seq": 0,
                        "player_x": 0,
                        "player_y": 0,
                        "player_water": 0,
                        "player_food": 0,
                        "player_exposure": 0,
                        "player_alive": False,
                        "player_phase_ended": False,
                        "bag_text": "未知",
                        "visible_loot_text": "未知",
                    }
                )
            event_bus.publish({"type": "state", "snapshot": _snapshot()})
            hosted_room_id, hosted_room_token, hosted_server = _teardown_hosted_room(
                args.broker_host,
                args.broker_port,
                hosted_room_id,
                hosted_room_token,
                hosted_server,
                hb_stop,
            )
            continue

        if op == "join" and len(parts) >= 2:
            room_id = parts[1]
            s = socket.create_connection((args.broker_host, args.broker_port))
            f = s.makefile("r", encoding="utf-8")
            _send_json(s, {"type": "lookup", "room_id": room_id})
            msg = _recv_one(f)
            f.close()
            s.close()
            if msg.get("type") != "room":
                print(f"[错误] {msg.get('message')}")
                continue
            room = msg.get("room", {})
            if str(room.get("status", "WAITING")).upper() == "RUNNING":
                print("[错误] 房间已开局，不能加入。")
                continue
            with in_room_lock:
                client_state.update(
                    {
                        "in_room": True,
                        "room_id": room.get("room_id"),
                        "room_status": "RUNNING" if str(room.get("status", "WAITING")).upper() == "RUNNING" else "WAITING",
                        "room_max_players": int(room.get("max_players", 0) or 0),
                        "room_max_ai": int(room.get("max_ai", 0) or 0),
                        "room_member_count": 0,
                        "is_host": False,
                        "phase_no": 0,
                        "phase": "",
                        "action_seq": 0,
                        "player_x": 0,
                        "player_y": 0,
                        "player_water": 0,
                        "player_food": 0,
                        "player_exposure": 0,
                        "player_alive": True,
                        "player_phase_ended": False,
                        "bag_text": "未知",
                        "visible_loot_text": "未知",
                    }
                )
            event_bus.publish({"type": "state", "snapshot": _snapshot()})
            _play_room(
                host=room.get("endpoint_host"),
                port=int(room.get("endpoint_port")),
                room_id=room.get("room_id"),
                player_name=args.name,
                room_api_queue=room_api_queue,
                state_lock=in_room_lock,
                client_state=client_state,
                enable_console=args.console,
                snapshot_provider=_snapshot,
                event_sink=event_bus.publish,
            )
            with in_room_lock:
                client_state.update(
                    {
                        "in_room": False,
                        "room_id": "",
                        "room_status": "LOBBY",
                        "room_max_players": 0,
                        "room_max_ai": 0,
                        "room_member_count": 0,
                        "is_host": False,
                        "phase_no": 0,
                        "phase": "",
                        "action_seq": 0,
                        "player_x": 0,
                        "player_y": 0,
                        "player_water": 0,
                        "player_food": 0,
                        "player_exposure": 0,
                        "player_alive": False,
                        "player_phase_ended": False,
                        "bag_text": "未知",
                        "visible_loot_text": "未知",
                    }
                )
            event_bus.publish({"type": "state", "snapshot": _snapshot()})
            continue

        if op == "watch" and len(parts) >= 2:
            room_id = parts[1]
            try:
                _watch_room(args.broker_host, args.broker_port, room_id)
            except KeyboardInterrupt:
                print("")
                print("[观察] 已退出")
            continue

        print("[错误] 未知命令。输入 help 查看可用命令。")

    hosted_room_id, hosted_room_token, hosted_server = _teardown_hosted_room(
        args.broker_host,
        args.broker_port,
        hosted_room_id,
        hosted_room_token,
        hosted_server,
        hb_stop,
    )
    if control_server is not None:
        control_server.shutdown()
        control_server.server_close()


if __name__ == "__main__":
    run_client()
