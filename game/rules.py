from __future__ import annotations

import random
from copy import deepcopy

from game.constants import ACTION_COSTS, BASE_PHASE_COST, BUILDING_LOOT_TEMPLATE, MAP_GRID, SAFE_TILES
from game.models import Action, ActionKind, Phase, PlayerState, RoomState


def tile_at(x: int, y: int) -> str:
    return MAP_GRID[y - 1][x - 1]


def in_bounds(x: int, y: int) -> bool:
    return 1 <= x <= 9 and 1 <= y <= 9


def init_building_loot() -> dict[tuple[int, int], dict[str, int]]:
    loot = {}
    for y in range(1, 10):
        for x in range(1, 10):
            t = tile_at(x, y)
            if t in BUILDING_LOOT_TEMPLATE:
                loot[(x, y)] = deepcopy(BUILDING_LOOT_TEMPLATE[t])
    return loot


def apply_delta(player: PlayerState, water: int = 0, food: int = 0, exposure: int = 0) -> None:
    player.water += water
    player.food += food
    player.exposure = max(0, player.exposure + exposure)


def check_death(player: PlayerState, phase: Phase) -> tuple[bool, str]:
    if not player.alive:
        return True, "already_dead"
    tile = tile_at(player.x, player.y)
    if phase == Phase.DAY and tile in {"Q", "X"}:
        return True, f"day_on_{tile}"
    if phase == Phase.NIGHT and tile in {"Q", "X"}:
        survival = max(0, min(100, 100 - (player.exposure / 10.0) * 5))
        if random.random() * 100 > survival:
            return True, f"night_{tile}_roll_fail"
    if player.water <= 0:
        return True, "water_depleted"
    if player.food <= 0:
        return True, "food_depleted"
    return False, ""


def validate_action(room: RoomState, actor: PlayerState, action: Action) -> tuple[bool, str]:
    if not actor.alive:
        return False, "dead_player"
    if actor.phase_ended:
        return False, "phase_already_ended"

    if action.kind == ActionKind.MOVE:
        tx = int(action.payload.get("x", 0))
        ty = int(action.payload.get("y", 0))
        if not in_bounds(tx, ty):
            return False, "out_of_bounds"
        if abs(tx - actor.x) + abs(ty - actor.y) != 1:
            return False, "move_not_adjacent"
        if tile_at(tx, ty) not in SAFE_TILES:
            return False, "move_not_safe"
        return True, ""

    if action.kind == ActionKind.EXPLORE:
        if tile_at(actor.x, actor.y) not in {"J", "B", "S", "W"}:
            return False, "explore_not_allowed_here"
        return True, ""

    if action.kind == ActionKind.USE:
        item = action.payload.get("item")
        if not item:
            return False, "item_required"
        if actor.bag.get(item, 0) <= 0:
            return False, "item_not_in_bag"
        return True, ""

    if action.kind == ActionKind.TAKE:
        if actor.take_locked_in_phase:
            return False, "take_locked_by_attack"
        if actor.pos() not in actor.explored_positions:
            return False, "must_explore_first"
        items = action.payload.get("items", [])
        if not isinstance(items, list) or len(items) == 0 or len(items) > 3:
            return False, "take_items_invalid"
        return True, ""

    if action.kind == ActionKind.ATTACK:
        target_id = action.payload.get("target_id")
        if not target_id:
            return False, "target_required"
        target = next((p for p in room.players if p.player_id == target_id and p.alive), None)
        if not target:
            return False, "target_not_found"
        if target.pos() != actor.pos():
            return False, "target_not_same_building"
        return True, ""

    if action.kind == ActionKind.REST:
        return True, ""

    return False, "unknown_action"


def apply_action(room: RoomState, actor: PlayerState, action: Action) -> dict:
    result = {"ok": True, "events": [], "error": ""}
    is_valid, error = validate_action(room, actor, action)
    if not is_valid:
        result["ok"] = False
        result["error"] = error
        return result

    phase_costs = ACTION_COSTS[room.phase.value]

    if action.kind == ActionKind.MOVE:
        actor.x = int(action.payload["x"])
        actor.y = int(action.payload["y"])
        c = phase_costs["MOVE"]
        apply_delta(actor, water=c["water"], food=c["food"], exposure=c["exposure"])
        result["events"].append(f"{actor.name} 移动到 ({actor.x},{actor.y})")

    elif action.kind == ActionKind.EXPLORE:
        c = phase_costs["EXPLORE"]
        apply_delta(actor, water=c["water"], food=c["food"], exposure=c["exposure"])
        actor.explored_positions.add(actor.pos())
        result["events"].append(f"{actor.name} 完成探索")

    elif action.kind == ActionKind.USE:
        from game.constants import ITEM_EFFECTS

        item = action.payload["item"]
        actor.bag[item] = actor.bag.get(item, 0) - 1
        eff = ITEM_EFFECTS[item]
        apply_delta(actor, water=eff["water"], food=eff["food"])
        result["events"].append(f"{actor.name} 使用 {item}")

    elif action.kind == ActionKind.REST:
        apply_delta(actor, exposure=-10)
        actor.phase_ended = True
        result["events"].append(f"{actor.name} 休息并结束本阶段")

    elif action.kind == ActionKind.TAKE:
        items = action.payload["items"]
        room_loot = room.building_loot.get(actor.pos(), {})
        taken = []
        for item in items:
            if not item:
                continue
            if room_loot.get(item, 0) > 0:
                room_loot[item] -= 1
                actor.bag[item] = actor.bag.get(item, 0) + 1
                taken.append(item)
        result["events"].append(f"{actor.name} 拿取: {','.join(taken) if taken else '无'}")

    elif action.kind == ActionKind.ATTACK:
        c = phase_costs["ATTACK"]
        apply_delta(actor, water=c["water"], food=c["food"], exposure=c["exposure"])
        target_id = action.payload["target_id"]
        target = next(p for p in room.players if p.player_id == target_id)
        actor_power = actor.water + actor.food
        target_power = target.water + target.food
        if actor_power > target_power:
            target.take_locked_in_phase = True
            apply_delta(target, water=-10, food=-10)
            result["events"].append(f"{actor.name} 攻击成功，{target.name} 本阶段无法拿取")
        else:
            actor.take_locked_in_phase = True
            apply_delta(actor, water=-10, food=-10, exposure=10)
            result["events"].append(f"{actor.name} 攻击失败，本阶段无法拿取")

    actor.phase_actions_used += 1
    return result


def settle_phase(room: RoomState) -> dict:
    events = []
    for p in room.players:
        if p.alive:
            apply_delta(p, water=BASE_PHASE_COST["water"], food=BASE_PHASE_COST["food"])
    events.append(f"阶段固定消耗: 水{BASE_PHASE_COST['water']} 食{BASE_PHASE_COST['food']}")

    deaths = []
    for p in room.players:
        dead, reason = check_death(p, room.phase)
        if dead and p.alive:
            p.alive = False
            p.phase_ended = True
            deaths.append((p.name, reason))
    for name, reason in deaths:
        events.append(f"{name} 死亡: {reason}")

    for p in room.players:
        if p.alive:
            p.survival_phases += 1
            p.phase_ended = False
            p.take_locked_in_phase = False
            p.phase_actions_used = 0

    alive_humans = [p for p in room.players if p.alive and p.is_human]
    if not alive_humans:
        room.finished = True
        room.finish_reason = "all_humans_dead"
        events.append("终局触发: 真人玩家全部死亡")

    if not room.finished:
        room.phase = Phase.NIGHT if room.phase == Phase.DAY else Phase.DAY
        room.phase_no += 1
    return {"events": events, "deaths": deaths}
