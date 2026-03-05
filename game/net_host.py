from __future__ import annotations

import argparse
import json
import socketserver
import threading
from dataclasses import dataclass, field
from queue import Empty, Queue

from game.cli import parse_command, render_status
from game.engine import GameEngine
from game.lobby import RoomManager
from game.models import Action, ActionKind, PlayerState, RoomState
from game.settings import OpenAISettings


def _send_json(file_obj, payload: dict) -> None:
    file_obj.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    file_obj.flush()


@dataclass
class RemoteClient:
    name: str
    writer: any
    action_queue: Queue[str] = field(default_factory=Queue)


class HostRuntime:
    def __init__(self, bind_host: str = "0.0.0.0", port: int = 9009):
        self.bind_host = bind_host
        self.port = port
        self.lock = threading.Lock()
        self.room_manager = RoomManager()
        self.room = None
        self.host_player = None
        self.remote_clients: dict[str, RemoteClient] = {}

    def create_room(self, host_name: str) -> None:
        self.room, self.host_player = self.room_manager.create_room(host_name)

    def join_remote(self, name: str, writer) -> tuple[bool, str]:
        with self.lock:
            try:
                self.room_manager.join_room(self.room.room_id, name)
            except ValueError as err:
                return False, str(err)
            self.remote_clients[name] = RemoteClient(name=name, writer=writer)
            return True, ""

    def remove_remote(self, name: str) -> None:
        with self.lock:
            self.remote_clients.pop(name, None)

    def push_action(self, name: str, text: str) -> None:
        with self.lock:
            client = self.remote_clients.get(name)
            if client:
                client.action_queue.put(text)

    def send_to(self, name: str, payload: dict) -> None:
        with self.lock:
            client = self.remote_clients.get(name)
            if not client:
                return
            try:
                _send_json(client.writer, payload)
            except Exception:
                pass

    def broadcast(self, payload: dict) -> None:
        with self.lock:
            clients = list(self.remote_clients.values())
        for client in clients:
            try:
                _send_json(client.writer, payload)
            except Exception:
                pass

    def wait_action(self, name: str, timeout: int = 120) -> str | None:
        with self.lock:
            client = self.remote_clients.get(name)
        if not client:
            return None
        try:
            return client.action_queue.get(timeout=timeout)
        except Empty:
            return None

    def members_text(self) -> str:
        members = self.room.players if self.room else []
        lines = [f"房间 {self.room.room_id} 成员:"]
        for idx, p in enumerate(members, start=1):
            role = "房主" if p.is_host else "成员"
            lines.append(f"{idx}. {p.name} ({role})")
        return "\n".join(lines)


class ClientHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        runtime: HostRuntime = self.server.runtime  # type: ignore[attr-defined]
        joined_name = None
        try:
            first = self.rfile.readline().decode("utf-8").strip()
            if not first:
                return
            msg = json.loads(first)
            if msg.get("type") != "join":
                _send_json(self.wfile, {"type": "error", "message": "first_message_must_be_join"})
                return
            room_id = msg.get("room_id", "")
            name = msg.get("name", "")
            if room_id != runtime.room.room_id:
                _send_json(self.wfile, {"type": "error", "message": "room_not_found"})
                return
            if not name:
                _send_json(self.wfile, {"type": "error", "message": "name_required"})
                return
            ok, err = runtime.join_remote(name, self.wfile)
            if not ok:
                _send_json(self.wfile, {"type": "error", "message": err})
                return
            joined_name = name
            _send_json(
                self.wfile,
                {
                    "type": "joined",
                    "room_id": room_id,
                    "name": name,
                    "message": f"joined_room_{room_id}",
                },
            )
            runtime.broadcast({"type": "event", "message": f"{name} 已从远端加入房间"})

            while True:
                line = self.rfile.readline().decode("utf-8")
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("type") == "action":
                    text = str(data.get("text", "")).strip()
                    runtime.push_action(name, text)
        except Exception:
            return
        finally:
            if joined_name:
                runtime.remove_remote(joined_name)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class, runtime: HostRuntime):
        super().__init__(server_address, handler_class)
        self.runtime = runtime


def _remote_status_payload(room: RoomState, actor: PlayerState) -> dict:
    same_pos = [p.name for p in room.players if p.alive and p.player_id != actor.player_id and p.pos() == actor.pos()]
    return {
        "phase_no": room.phase_no,
        "phase": room.phase.value,
        "name": actor.name,
        "x": actor.x,
        "y": actor.y,
        "water": actor.water,
        "food": actor.food,
        "exposure": actor.exposure,
        "bag": actor.bag,
        "same_pos_players": same_pos,
        "building_loot": room.building_loot.get(actor.pos(), {}),
    }


def run_host_server() -> None:
    parser = argparse.ArgumentParser(description="Survival Story host server")
    parser.add_argument("--name", required=True, help="host player name")
    parser.add_argument("--bind", default="0.0.0.0", help="bind address, default 0.0.0.0")
    parser.add_argument("--port", type=int, default=9009, help="listen port, default 9009")
    parser.add_argument("--config", default="config/openai.json", help="OpenAI config file path")
    args = parser.parse_args()

    print("=== 末日废墟生存战（房主服务端）===")
    host_name = args.name
    bind = args.bind
    port = args.port

    runtime = HostRuntime(bind, port)
    runtime.create_room(host_name)

    server = ThreadedTCPServer((bind, port), ClientHandler, runtime)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print("")
    print(f"房间已创建: {runtime.room.room_id}")
    print(f"服务端监听: {bind}:{port}")
    print("远端玩家加入命令示例:")
    print(f"uv run survival-client --host <房主IP> --port {port} --room {runtime.room.room_id} --name 玩家B")
    print("Lobby命令: members | start | help")

    while True:
        raw = input("(host-lobby)> ").strip().lower()
        if raw == "help":
            print("members: 查看当前成员")
            print("start: 房主开始游戏")
            continue
        if raw == "members":
            print(runtime.members_text())
            continue
        if raw == "start":
            try:
                runtime.room_manager.start_game(runtime.room.room_id, runtime.host_player.player_id)
                break
            except ValueError as err:
                print(f"开始失败: {err}")
                continue
        print("未知命令，输入 help 查看")

    print("游戏开始。")

    try:
        settings = OpenAISettings.load(args.config)
    except Exception as err:
        print(f"OpenAI配置错误: {err}")
        print("请检查 config/openai.json 或环境变量 SURVIVAL_OPENAI_*")
        server.shutdown()
        server.server_close()
        return

    def emit_event(event: dict) -> None:
        if event.get("event_type") == "action_result":
            msg = event.get("message", "")
            print(f"[事件] {msg}")
            runtime.broadcast({"type": "event", "message": msg})
        elif event.get("event_type") == "phase_settle":
            msg = event.get("message", "")
            print(f"[结算] {msg}")
            runtime.broadcast({"type": "event", "message": f"[结算] {msg}"})
        elif event.get("event_type") == "game_over":
            runtime.broadcast({"type": "game_over", "payload": event})

    engine = GameEngine.create(
        runtime.room.human_names(),
        room_id=runtime.room.room_id,
        event_callback=emit_event,
        settings=settings,
    )

    def human_action_provider(room: RoomState, actor: PlayerState) -> Action:
        if actor.name == host_name:
            while True:
                print(f"\n[房主回合] {actor.name}")
                render_status(room, actor)
                text = input("> ").strip()
                action = parse_command(room, actor, text)
                if action is not None:
                    return action

        while True:
            runtime.send_to(actor.name, {"type": "turn", "payload": _remote_status_payload(room, actor)})
            text = runtime.wait_action(actor.name, timeout=120)
            if text is None:
                runtime.send_to(actor.name, {"type": "event", "message": "超时，已自动执行 rest"})
                return Action(actor.player_id, ActionKind.REST, source="HUMAN", reason="remote_timeout")
            action = parse_command(room, actor, text)
            if action is not None:
                return action
            runtime.send_to(actor.name, {"type": "event", "message": "非法或无效命令，请重新输入"})

    try:
        engine.run_until_finish(human_action_provider)
    finally:
        ranking = sorted(engine.room.players, key=lambda p: p.survival_phases, reverse=True)
        print("")
        print("=== 游戏结束 ===")
        print(f"结束原因: {engine.room.finish_reason}")
        for idx, p in enumerate(ranking, start=1):
            status = "存活" if p.alive else "死亡"
            print(f"{idx}. {p.name} | {status} | 存活阶段数={p.survival_phases}")
        server.shutdown()
        server.server_close()
