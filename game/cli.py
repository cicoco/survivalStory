from __future__ import annotations

from game.constants import ITEM_EFFECTS, MAP_GRID, building_zh_label, item_zh_label, normalize_item_id
from game.engine import GameEngine
from game.lobby import RoomManager
from game.models import Action, ActionKind, PlayerState, RoomState

def _normalize_item_token(token: str) -> str:
    return normalize_item_id(token.strip())


def _format_item_dict(items: dict[str, int]) -> str:
    if not items:
        return "{}"
    parts = []
    for item_id, cnt in items.items():
        if cnt <= 0:
            continue
        parts.append(f"{item_zh_label(item_id)}:{cnt}")
    return "{" + ", ".join(parts) + "}" if parts else "{}"


def _known_loot_text(actor: PlayerState) -> str:
    loot = actor.known_building_loot.get(actor.pos())
    if loot is None:
        return "未知(先EXPLORE后可见)"
    return _format_item_dict(loot)


def render_status(room: RoomState, actor: PlayerState) -> None:
    tile = MAP_GRID[actor.y - 1][actor.x - 1]
    same_pos = [p.name for p in room.players if p.alive and p.player_id != actor.player_id and p.pos() == actor.pos()]
    print("")
    print(f"=== Phase {room.phase_no} | {room.phase.value} ===")
    print(f"你: {actor.name} {'(存活)' if actor.alive else '(死亡)'} 位置=({actor.x},{actor.y}) 区域={building_zh_label(tile)}({tile})")
    print(f"状态: 水={actor.water} 食={actor.food} 曝光={actor.exposure} 本阶段动作数={actor.phase_actions_used}")
    print(f"背包: {_format_item_dict(actor.bag)}")
    print(f"同建筑其他角色: {same_pos if same_pos else '无'}")
    print(f"当前建筑物资: {_known_loot_text(actor)}")
    print("命令: MOVE x y | EXPLORE | USE 物资标识 | TAKE 物资1 物资2 物资3 | REST | ATTACK 玩家名 | STATUS | HELP")
    print("物资别名/编码: B/BREAD W/BOTTLED_WATER C/BISCUIT G/CANNED_FOOD T/BARREL_WATER Q/CLEAN_WATER")


def parse_command(room: RoomState, actor: PlayerState, text: str) -> Action | None:
    normalized = (
        text.strip()
        .replace("（", " ")
        .replace("）", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("，", " ")
        .replace(",", " ")
    )
    parts = [p for p in normalized.split() if p]
    if not parts:
        return None
    cmd = parts[0].lower()

    if cmd == "status":
        render_status(room, actor)
        return None
    if cmd == "help":
        print("示例: MOVE 2 3 / EXPLORE / USE W / USE BOTTLED_WATER / TAKE B W / REST / ATTACK AI_1")
        print("物资别名/编码: B/BREAD W/BOTTLED_WATER C/BISCUIT G/CANNED_FOOD T/BARREL_WATER Q/CLEAN_WATER")
        return None
    if cmd == "move" and len(parts) >= 3:
        try:
            x = int(parts[1])
            y = int(parts[2])
        except ValueError:
            print("MOVE 需要数字坐标，例如: MOVE 2 3")
            return None
        return Action(actor.player_id, ActionKind.MOVE, {"x": x, "y": y}, source="HUMAN")
    if cmd == "explore":
        return Action(actor.player_id, ActionKind.EXPLORE, source="HUMAN")
    if cmd == "use" and len(parts) >= 2:
        item = _normalize_item_token(" ".join(parts[1:]))
        if item not in ITEM_EFFECTS:
            print("未知物资，输入 HELP 查看支持的别名/编码。")
            return None
        return Action(actor.player_id, ActionKind.USE, {"item": item}, source="HUMAN")
    if cmd == "take":
        items = [_normalize_item_token(x) for x in parts[1:4]]
        if not items:
            print("TAKE 至少需要一个物资。")
            return None
        return Action(actor.player_id, ActionKind.TAKE, {"items": items}, source="HUMAN")
    if cmd == "rest":
        return Action(actor.player_id, ActionKind.REST, source="HUMAN")
    if cmd == "attack" and len(parts) >= 2:
        target_name = " ".join(parts[1:])
        target = next((p for p in room.players if p.alive and p.name == target_name), None)
        if not target:
            print("目标不存在或已死亡。")
            return None
        return Action(actor.player_id, ActionKind.ATTACK, {"target_id": target.player_id}, source="HUMAN")

    print("无法识别的命令，输入 HELP 查看示例。")
    return None


def run_lobby(owner_name: str) -> tuple[str, list[str]]:
    manager = RoomManager()
    room, host = manager.create_room(owner_name)
    print("")
    print(f"已创建房间: {room.room_id}")
    print(f"房主: {owner_name}")
    print("Lobby命令: join 玩家名 | members | start | help")

    while True:
        raw = input("(lobby)> ").strip()
        if not raw:
            continue
        parts = raw.split()
        cmd = parts[0].lower()

        if cmd == "help":
            print("join 玩家名: 模拟新真人加入房间")
            print("members: 查看房间成员")
            print("start: 房主开始游戏（不足6人会自动补AI）")
            continue
        if cmd == "members":
            print(f"房间 {room.room_id} 成员:")
            for idx, p in enumerate(room.players, start=1):
                role = "房主" if p.is_host else "成员"
                print(f"{idx}. {p.name} ({role})")
            print(f"当前真人: {len(room.players)}")
            continue
        if cmd == "join":
            if len(parts) < 2:
                print("用法: join 玩家名")
                continue
            name = " ".join(parts[1:])
            try:
                player = manager.join_room(room.room_id, name)
                print(f"{player.name} 已加入房间")
            except ValueError as err:
                print(f"加入失败: {err}")
            continue
        if cmd == "start":
            try:
                manager.start_game(room.room_id, host.player_id)
                print("房主已开始游戏")
                return room.room_id, room.human_names()
            except ValueError as err:
                print(f"开始失败: {err}")
            continue

        print("未知命令，输入 help 查看可用命令")


def run_cli_game() -> None:
    print("=== 末日废墟生存战（控制台版）===")
    owner_name = input("输入房主名字（默认 玩家1）: ").strip() or "玩家1"
    room_id, human_names = run_lobby(owner_name)
    engine = GameEngine.create(human_names, room_id=room_id)
    print("输入 help 查看命令。规则重点：休息后你本阶段结束，阶段末固定消耗只扣一次。")

    def human_action_provider(room: RoomState, actor: PlayerState) -> Action:
        while True:
            print(f"\n[真人回合] 当前操作者: {actor.name}")
            render_status(room, actor)
            text = input("> ").strip()
            action = parse_command(room, actor, text)
            if action is not None:
                return action

    engine.run_until_finish(human_action_provider)

    print("")
    print("=== 游戏结束 ===")
    print(f"结束原因: {engine.room.finish_reason}")
    ranking = sorted(engine.room.players, key=lambda p: p.survival_phases, reverse=True)
    for idx, p in enumerate(ranking, start=1):
        status = "存活" if p.alive else "死亡"
        print(f"{idx}. {p.name} | {status} | 存活阶段数={p.survival_phases}")
    print("对局日志已写入 game.db")
