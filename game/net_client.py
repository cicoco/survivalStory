from __future__ import annotations

import argparse
import json
import socket


def send_json(sock: socket.socket, payload: dict) -> None:
    sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def run_client() -> None:
    parser = argparse.ArgumentParser(description="Survival Story remote client")
    parser.add_argument("--host", required=True, help="host server ip/domain")
    parser.add_argument("--port", type=int, default=9009, help="host server port")
    parser.add_argument("--room", required=True, help="room id")
    parser.add_argument("--name", required=True, help="player name")
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port))
    file_in = sock.makefile("r", encoding="utf-8")

    send_json(sock, {"type": "join", "room_id": args.room, "name": args.name})
    print(f"已连接 {args.host}:{args.port}，尝试加入房间 {args.room}，名字 {args.name}")
    print("等待房主开始游戏...")

    try:
        for line in file_in:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            mtype = msg.get("type")
            if mtype == "error":
                print(f"[错误] {msg.get('message')}")
                break
            if mtype == "joined":
                print(f"[系统] 加入成功: {msg.get('room_id')}")
                continue
            if mtype == "event":
                print(f"[事件] {msg.get('message')}")
                continue
            if mtype == "game_over":
                payload = msg.get("payload", {})
                print(f"[游戏结束] 原因: {payload.get('finish_reason')}")
                print(f"[幸存者] {payload.get('survivors')}")
                break
            if mtype == "turn":
                p = msg.get("payload", {})
                print("")
                print(f"=== Phase {p.get('phase_no')} | {p.get('phase')} ===")
                print(f"你: {p.get('name')} 位置=({p.get('x')},{p.get('y')})")
                print(f"状态: 水={p.get('water')} 食={p.get('food')} 曝光={p.get('exposure')}")
                print(f"背包: {p.get('bag')}")
                print(f"同建筑其他角色: {p.get('same_pos_players')}")
                print(f"当前建筑物资: {p.get('building_loot')}")
                print("输入命令: move x y | explore | use 物品名 | take 物品1 物品2 物品3 | rest | attack 角色名")
                text = input("> ").strip()
                send_json(sock, {"type": "action", "text": text})
    finally:
        try:
            file_in.close()
        finally:
            sock.close()


if __name__ == "__main__":
    run_client()
