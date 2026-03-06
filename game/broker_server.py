from __future__ import annotations

import argparse
import json
import random
import socketserver
import string
import threading
import time


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
            "host_name": host_name,
            "max_players": max_players,
            "max_ai": max_ai,
            "endpoint_host": endpoint_host,
            "endpoint_port": endpoint_port,
            "created_at": now,
            "last_heartbeat_at": now,
        }
        with self.lock:
            self.rooms[room_id] = room
        return room

    def heartbeat(self, room_id: str) -> dict | None:
        with self.lock:
            room = self.rooms.get(room_id)
            if not room:
                return None
            room["last_heartbeat_at"] = time.time()
            return room

    def remove_room(self, room_id: str) -> None:
        with self.lock:
            self.rooms.pop(room_id, None)

    def list_rooms(self) -> list[dict]:
        cutoff = time.time() - 30.0
        with self.lock:
            stale = [rid for rid, r in self.rooms.items() if r.get("last_heartbeat_at", 0) < cutoff]
            for rid in stale:
                self.rooms.pop(rid, None)
            rooms = list(self.rooms.values())
        return [
            {
                "room_id": r["room_id"],
                "host_name": r["host_name"],
                "max_players": r["max_players"],
                "max_ai": r["max_ai"],
                "endpoint_host": r["endpoint_host"],
                "endpoint_port": r["endpoint_port"],
            }
            for r in rooms
        ]

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
                "endpoint_host": room["endpoint_host"],
                "endpoint_port": room["endpoint_port"],
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
                _send_json(self.wfile, {"type": "rooms", "rooms": state.list_rooms()})
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
                room = state.heartbeat(room_id)
                if not room:
                    _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                    continue
                _send_json(self.wfile, {"type": "heartbeat_ok", "room_id": room_id})
                continue

            if mtype == "remove":
                room_id = str(msg.get("room_id", "")).strip()
                state.remove_room(room_id)
                _send_json(self.wfile, {"type": "removed", "room_id": room_id})
                continue

            _send_json(self.wfile, {"type": "error", "message": "unknown_message_type"})


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class, state: BrokerState):
        super().__init__(server_address, handler_class)
        self.state = state


def run_broker_server() -> None:
    parser = argparse.ArgumentParser(description="Survival Story broker server")
    parser.add_argument("--bind", default="0.0.0.0", help="bind address")
    parser.add_argument("--port", type=int, default=9010, help="listen port")
    args = parser.parse_args()

    state = BrokerState()
    server = ThreadedTCPServer((args.bind, args.port), Handler, state)
    print(f"Broker listening on {args.bind}:{args.port}")
    try:
        server.serve_forever()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run_broker_server()
