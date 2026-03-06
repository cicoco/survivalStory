from __future__ import annotations

import argparse
import json
import socket
import threading
import time

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


def _print_rooms(rooms: list[dict]) -> None:
    print("=== 房间列表 ===")
    if not rooms:
        print("(空)")
        return
    for r in rooms:
        print(
            f"- {r.get('room_id')} host={r.get('host_name')} "
            f"{r.get('endpoint_host')}:{r.get('endpoint_port')} "
            f"max={r.get('max_players')} max_ai={r.get('max_ai')}"
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


def _start_heartbeat(broker_host: str, broker_port: int, room_id: str, stop_event: threading.Event) -> threading.Thread:
    def _loop() -> None:
        while not stop_event.is_set():
            try:
                s = socket.create_connection((broker_host, broker_port), timeout=3)
                f = s.makefile("r", encoding="utf-8")
                _send_json(s, {"type": "heartbeat", "room_id": room_id})
                _recv_one(f)
                f.close()
                s.close()
            except Exception:
                pass
            stop_event.wait(5.0)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def _play_room(host: str, port: int, room_id: str, player_name: str) -> None:
    sock = socket.create_connection((host, port))
    fin = sock.makefile("r", encoding="utf-8")
    stop_event = threading.Event()
    game_started = threading.Event()

    _send_json(sock, {"type": "join", "room_id": room_id, "name": player_name})
    print(f"[房间] 尝试加入 {room_id} @ {host}:{port}")

    def _input_loop() -> None:
        while not stop_event.is_set():
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if not text:
                continue
            if text.lower() == "members":
                _send_json(sock, {"type": "members"})
                continue
            if text.lower() == "start":
                _send_json(sock, {"type": "start"})
                continue
            if not game_started.is_set():
                print("游戏未开始，可输入 start（仅房主有效）")
                continue
            _send_json(sock, {"type": "action", "text": text})

    threading.Thread(target=_input_loop, daemon=True).start()

    try:
        for raw in fin:
            line = raw.strip()
            if not line:
                continue
            msg = json.loads(line)
            mtype = msg.get("type")
            if mtype == "error":
                print(f"[错误] {msg.get('message')}")
                continue
            if mtype == "joined":
                print(f"[房间] 已加入，房主={msg.get('host_name')} max={msg.get('max_players')}")
                if msg.get("running"):
                    print("[房间] 游戏已在进行中。")
                    game_started.set()
                continue
            if mtype == "members":
                print(
                    f"[房间成员] room={msg.get('room_id')} host={msg.get('host_name')} "
                    f"max={msg.get('max_players')} max_ai={msg.get('max_ai')}"
                )
                for n in msg.get("members", []):
                    print(f"- {n}")
                continue
            if mtype == "started":
                print(f"[房间] 已开始: {msg.get('room_id')}")
                continue
            if mtype == "event":
                print(f"[事件] {msg.get('message')}")
                continue
            if mtype == "game_started":
                game_started.set()
                print("[系统] 游戏开始，可随时输入动作。")
                _print_state(msg.get("payload", {}), player_name)
                continue
            if mtype == "phase_started":
                print("[系统] 新阶段开始。")
                _print_state(msg.get("payload", {}), player_name)
                continue
            if mtype == "state":
                _print_state(msg.get("payload", {}), player_name)
                continue
            if mtype == "game_over":
                payload = msg.get("payload", {})
                print(f"[游戏结束] 原因: {payload.get('finish_reason')}")
                print(f"[幸存者] {payload.get('survivors')}")
                break
    finally:
        stop_event.set()
        try:
            fin.close()
        finally:
            sock.close()


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
    args = parser.parse_args()

    print(f"连接 broker: {args.broker_host}:{args.broker_port}，玩家名 {args.name}")
    print("大厅命令: list | create [max_players] [max_ai] | join <room_id> | quit")

    hosted_server: RoomHostServer | None = None
    hosted_room_id = ""
    hb_stop = threading.Event()
    heartbeat_thread = None

    while True:
        cmd = input("(lobby)> ").strip()
        if not cmd:
            continue
        parts = cmd.split()
        op = parts[0].lower()
        if op in {"quit", "exit"}:
            break

        if op == "list":
            s = socket.create_connection((args.broker_host, args.broker_port))
            f = s.makefile("r", encoding="utf-8")
            _send_json(s, {"type": "list"})
            msg = _recv_one(f)
            if msg.get("type") == "rooms":
                _print_rooms(msg.get("rooms", []))
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
            # update runtime room_id for join checks
            hosted_server.runtime.room_id = hosted_room_id
            print(f"[创建成功] 房间号: {hosted_room_id}, 地址: {endpoint_host}:{endpoint_port}, max={max_players}, max_ai={max_ai}")
            print("输入 join <room_id>（可直接 join 自己房间）进入对局连接。")

            hb_stop.clear()
            heartbeat_thread = _start_heartbeat(args.broker_host, args.broker_port, hosted_room_id, hb_stop)
            f.close()
            s.close()
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
            _play_room(
                host=room.get("endpoint_host"),
                port=int(room.get("endpoint_port")),
                room_id=room.get("room_id"),
                player_name=args.name,
            )
            continue

        print("未知命令，可用: list | create [max_players] [max_ai] | join <room_id> | quit")

    if hosted_room_id:
        try:
            s = socket.create_connection((args.broker_host, args.broker_port), timeout=2)
            f = s.makefile("r", encoding="utf-8")
            _send_json(s, {"type": "remove", "room_id": hosted_room_id})
            _recv_one(f)
            f.close()
            s.close()
        except Exception:
            pass
    hb_stop.set()
    if hosted_server is not None:
        hosted_server.stop()


if __name__ == "__main__":
    run_client()
