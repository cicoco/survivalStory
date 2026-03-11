from __future__ import annotations

import argparse
import json
import random
import secrets
import socketserver
import string
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _send_json(file_obj, payload: dict) -> None:
    file_obj.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    file_obj.flush()


class BrokerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rooms: dict[str, dict] = {}

    def _new_room_id(self) -> str:
        while True:
            room_id = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))
            if room_id not in self.rooms:
                return room_id

    def _new_room_token(self) -> str:
        return secrets.token_urlsafe(18)

    def create_room(self, host_name: str, max_players: int, max_ai: int, endpoint_host: str, endpoint_port: int) -> dict:
        if max_players < 1:
            raise ValueError("invalid_max_players")
        if max_ai < 0:
            raise ValueError("invalid_max_ai")
        if max_ai > max_players:
            raise ValueError("max_ai_exceeds_max_players")
        room_id = self._new_room_id()
        now = time.time()
        room = {
            "room_id": room_id,
            "room_token": self._new_room_token(),
            "host_name": host_name,
            "max_players": max_players,
            "max_ai": max_ai,
            "human_players": 1,
            "watcher_count": 0,
            "endpoint_host": endpoint_host,
            "endpoint_port": endpoint_port,
            "status": "WAITING",
            "created_at": now,
            "last_heartbeat_at": now,
        }
        with self.lock:
            self.rooms[room_id] = room
        return room

    def heartbeat(
        self,
        room_id: str,
        room_token: str,
        status: str | None = None,
        max_players: int | None = None,
        human_players: int | None = None,
        watcher_count: int | None = None,
    ) -> dict:
        with self.lock:
            room = self.rooms.get(room_id)
            if not room:
                raise KeyError("room_not_found")
            if room.get("room_token") != room_token:
                raise PermissionError("unauthorized")
            room["last_heartbeat_at"] = time.time()
            if status in {"WAITING", "RUNNING"}:
                room["status"] = status
            if isinstance(max_players, int) and max_players >= 1:
                room["max_players"] = max_players
            if isinstance(human_players, int) and human_players >= 0:
                room["human_players"] = human_players
            if isinstance(watcher_count, int) and watcher_count >= 0:
                room["watcher_count"] = watcher_count
            return room

    def remove_room(self, room_id: str, room_token: str) -> None:
        with self.lock:
            room = self.rooms.get(room_id)
            if not room:
                raise KeyError("room_not_found")
            if room.get("room_token") != room_token:
                raise PermissionError("unauthorized")
            self.rooms.pop(room_id, None)

    def cleanup_stale(self, ttl_sec: float = 30.0) -> int:
        cutoff = time.time() - ttl_sec
        removed = 0
        with self.lock:
            stale = [rid for rid, r in self.rooms.items() if r.get("last_heartbeat_at", 0) < cutoff]
            for rid in stale:
                self.rooms.pop(rid, None)
                removed += 1
        return removed

    def list_rooms(
        self,
        offset: int = 0,
        limit: int = 20,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        if offset < 0:
            offset = 0
        if limit <= 0:
            limit = 20
        if limit > 200:
            limit = 200
        status_filter = (status or "").strip().upper()
        if status_filter not in {"WAITING", "RUNNING"}:
            status_filter = ""
        with self.lock:
            rooms = list(self.rooms.values())
        rooms.sort(key=lambda x: float(x.get("created_at", 0.0)), reverse=True)
        if status_filter:
            rooms = [r for r in rooms if str(r.get("status", "WAITING")).upper() == status_filter]
        total = len(rooms)
        sliced = rooms[offset : offset + limit]
        return ([
            {
                "room_id": r["room_id"],
                "host_name": r["host_name"],
                "max_players": r["max_players"],
                "max_ai": r["max_ai"],
                "human_players": int(r.get("human_players", 0)),
                "watcher_count": int(r.get("watcher_count", 0)),
                "endpoint_host": r["endpoint_host"],
                "endpoint_port": r["endpoint_port"],
                "status": r.get("status", "WAITING"),
            }
            for r in sliced
        ], total)

    def lookup(self, room_id: str) -> dict | None:
        with self.lock:
            room = self.rooms.get(room_id)
            if not room:
                return None
            return {
                "room_id": room["room_id"],
                "host_name": room["host_name"],
                "max_players": room["max_players"],
                "max_ai": room["max_ai"],
                "human_players": int(room.get("human_players", 0)),
                "watcher_count": int(room.get("watcher_count", 0)),
                "endpoint_host": room["endpoint_host"],
                "endpoint_port": room["endpoint_port"],
                "status": room.get("status", "WAITING"),
            }


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: BrokerState = self.server.state  # type: ignore[attr-defined]
        for raw in self.rfile:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                _send_json(self.wfile, {"type": "error", "message": "invalid_json"})
                continue
            mtype = msg.get("type")

            if mtype == "list":
                page = msg.get("page")
                page_size = msg.get("page_size")
                status = str(msg.get("status", "")).strip().upper()
                page_i = int(page) if isinstance(page, int) and page > 0 else 1
                size_i = int(page_size) if isinstance(page_size, int) and page_size > 0 else 20
                offset = (page_i - 1) * size_i
                rooms, total = state.list_rooms(offset=offset, limit=size_i, status=status or None)
                total_pages = (total + size_i - 1) // size_i if size_i > 0 else 1
                _send_json(
                    self.wfile,
                    {
                        "type": "rooms",
                        "rooms": rooms,
                        "page": page_i,
                        "page_size": size_i,
                        "total": total,
                        "total_pages": total_pages,
                    },
                )
                continue

            if mtype == "create":
                try:
                    room = state.create_room(
                        host_name=str(msg.get("host_name", "")).strip(),
                        max_players=int(msg.get("max_players", 6)),
                        max_ai=int(msg.get("max_ai", 6)),
                        endpoint_host=str(msg.get("endpoint_host", "")).strip(),
                        endpoint_port=int(msg.get("endpoint_port", 0)),
                    )
                except Exception as err:
                    _send_json(self.wfile, {"type": "error", "message": str(err)})
                    continue
                _send_json(self.wfile, {"type": "created", "room": room})
                continue

            if mtype == "lookup":
                room_id = str(msg.get("room_id", "")).strip()
                room = state.lookup(room_id)
                if not room:
                    _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                    continue
                _send_json(self.wfile, {"type": "room", "room": room})
                continue

            if mtype == "heartbeat":
                room_id = str(msg.get("room_id", "")).strip()
                room_token = str(msg.get("room_token", "")).strip()
                status = str(msg.get("status", "")).strip().upper()
                max_players = msg.get("max_players")
                human_players = msg.get("human_players")
                watcher_count = msg.get("watcher_count")
                if not room_token:
                    _send_json(self.wfile, {"type": "error", "message": "room_token_required"})
                    continue
                try:
                    room = state.heartbeat(
                        room_id,
                        room_token=room_token,
                        status=status or None,
                        max_players=int(max_players) if isinstance(max_players, int) else None,
                        human_players=int(human_players) if isinstance(human_players, int) else None,
                        watcher_count=int(watcher_count) if isinstance(watcher_count, int) else None,
                    )
                except KeyError:
                    _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                    continue
                except PermissionError:
                    _send_json(self.wfile, {"type": "error", "message": "unauthorized"})
                    continue
                _send_json(
                    self.wfile,
                    {
                        "type": "heartbeat_ok",
                        "room_id": room_id,
                        "status": room.get("status", "WAITING"),
                        "max_players": room.get("max_players"),
                        "human_players": room.get("human_players", 0),
                        "watcher_count": room.get("watcher_count", 0),
                    },
                )
                continue

            if mtype == "remove":
                room_id = str(msg.get("room_id", "")).strip()
                room_token = str(msg.get("room_token", "")).strip()
                if not room_token:
                    _send_json(self.wfile, {"type": "error", "message": "room_token_required"})
                    continue
                try:
                    state.remove_room(room_id, room_token=room_token)
                except KeyError:
                    _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                    continue
                except PermissionError:
                    _send_json(self.wfile, {"type": "error", "message": "unauthorized"})
                    continue
                _send_json(self.wfile, {"type": "removed", "room_id": room_id})
                continue

            _send_json(self.wfile, {"type": "error", "message": "unknown_message_type"})


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class, state: BrokerState):
        super().__init__(server_address, handler_class)
        self.state = state


_LOBBY_HTML_CACHE: str | None = None


def _lobby_html(use_cache: bool = True) -> str:
    global _LOBBY_HTML_CACHE
    if use_cache and _LOBBY_HTML_CACHE is not None:
        return _LOBBY_HTML_CACHE
    template = Path(__file__).resolve().parent / "web" / "lobby.html"
    text = template.read_text(encoding="utf-8")
    if use_cache:
        _LOBBY_HTML_CACHE = text
    return text


class LobbyHandler(BaseHTTPRequestHandler):
    server_version = "SurvivalBrokerLobby/1.0"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        state: BrokerState = self.server.state  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query or "")
        if path == "/":
            use_cache = not bool(getattr(self.server, "dev_web", False))
            html_body = _lobby_html(use_cache=use_cache).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_body)))
            self.end_headers()
            self.wfile.write(html_body)
            return
        if path == "/api/rooms":
            try:
                page = int((qs.get("page") or ["1"])[0])
            except Exception:
                page = 1
            try:
                page_size = int((qs.get("page_size") or ["20"])[0])
            except Exception:
                page_size = 20
            status = str((qs.get("status") or [""])[0]).strip().upper()
            if page <= 0:
                page = 1
            if page_size <= 0:
                page_size = 20
            offset = (page - 1) * page_size
            rooms, total = state.list_rooms(offset=offset, limit=page_size, status=status or None)
            total_pages = (total + page_size - 1) // page_size if page_size > 0 else 1
            self._send_json(
                {
                    "rooms": rooms,
                    "ts": time.time(),
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages,
                }
            )
            return
        self._send_json({"type": "error", "message": "not_found"}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        return


class ThreadedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_class, state: BrokerState, dev_web: bool = False):
        super().__init__(server_address, handler_class)
        self.state = state
        self.dev_web = dev_web


def run_broker_server() -> None:
    parser = argparse.ArgumentParser(description="Survival Story broker server")
    parser.add_argument("--bind", default="0.0.0.0", help="bind address")
    parser.add_argument("--port", type=int, default=9010, help="listen port")
    parser.add_argument("--web-bind", default="0.0.0.0", help="lobby web bind")
    parser.add_argument("--web-port", type=int, default=9011, help="lobby web port, <=0 to disable")
    parser.add_argument("--dev-web", action="store_true", help="disable lobby web template cache for development")
    args = parser.parse_args()

    state = BrokerState()
    tcp_server = ThreadedTCPServer((args.bind, args.port), Handler, state)
    stop_gc = threading.Event()
    gc_thread = threading.Thread(
        target=lambda: _run_gc_loop(state, stop_gc, ttl_sec=54.0, interval_sec=10.0),
        daemon=True,
    )
    gc_thread.start()
    web_server = None
    web_thread = None
    if args.web_port > 0:
        web_server = ThreadedHTTPServer((args.web_bind, args.web_port), LobbyHandler, state, dev_web=args.dev_web)
        web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
        web_thread.start()
        print(f"Broker lobby web on http://{args.web_bind}:{args.web_port}")
        if args.dev_web:
            print("Broker 网页模板缓存: 关闭（dev-web 模式，刷新页面即可生效）")
    print(f"Broker TCP listening on {args.bind}:{args.port}")
    try:
        tcp_server.serve_forever()
    finally:
        stop_gc.set()
        tcp_server.shutdown()
        tcp_server.server_close()
        if web_server is not None:
            web_server.shutdown()
            web_server.server_close()


def _run_gc_loop(state: BrokerState, stop_event: threading.Event, ttl_sec: float, interval_sec: float) -> None:
    while not stop_event.is_set():
        try:
            state.cleanup_stale(ttl_sec=ttl_sec)
        except Exception:
            pass
        stop_event.wait(interval_sec)


if __name__ == "__main__":
    run_broker_server()
