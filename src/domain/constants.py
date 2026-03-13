"""Domain constants for V1 rules."""

from __future__ import annotations

from typing import Final

# 阶段与房间生命周期
PHASE_DAY: Final[str] = "DAY"
PHASE_NIGHT: Final[str] = "NIGHT"
ROOM_STATUS_WAITING: Final[str] = "WAITING"
ROOM_STATUS_IN_GAME: Final[str] = "IN_GAME"
ROOM_STATUS_DISBANDED: Final[str] = "DISBANDED"
ROOM_STATUS_CLOSED: Final[str] = "CLOSED"
END_MODE_ALL_DEAD: Final[str] = "ALL_DEAD"
END_MODE_HUMAN_ALL_DEAD: Final[str] = "HUMAN_ALL_DEAD"
END_MODE_HOST_LEFT: Final[str] = "HOST_LEFT"
MAX_ROOM_PLAYERS: Final[int] = 6
MAX_TAKE_ITEMS_PER_ACTION: Final[int] = 3
ATTACK_WIN_DELTA_THRESHOLD: Final[int] = 1

# 玩家动作类型
ACTION_MOVE: Final[str] = "MOVE"
ACTION_EXPLORE: Final[str] = "EXPLORE"
ACTION_USE: Final[str] = "USE"
ACTION_TAKE: Final[str] = "TAKE"
ACTION_REST: Final[str] = "REST"
ACTION_ATTACK: Final[str] = "ATTACK"
ACTION_GET: Final[str] = "GET"
ACTION_TOSS: Final[str] = "TOSS"

CORE_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        ACTION_MOVE,
        ACTION_EXPLORE,
        ACTION_USE,
        ACTION_TAKE,
        ACTION_REST,
        ACTION_ATTACK,
    }
)
FOLLOW_UP_ACTION_TYPES: Final[frozenset[str]] = frozenset({ACTION_GET, ACTION_TOSS})
ALL_ACTION_TYPES: Final[frozenset[str]] = CORE_ACTION_TYPES | FOLLOW_UP_ACTION_TYPES

# 战利品窗口动作类型
LOOT_TYPE_GET: Final[str] = "GET"
LOOT_TYPE_TOSS: Final[str] = "TOSS"
LOOT_TYPES: Final[frozenset[str]] = frozenset({LOOT_TYPE_GET, LOOT_TYPE_TOSS})

# 信息可见性状态：仅保留未探索与已有记忆两态。
INFO_STATE_UNEXPLORED: Final[str] = "UNEXPLORED"
INFO_STATE_HAS_MEMORY: Final[str] = "HAS_MEMORY"

# 地图与地块类型
TILE_Q: Final[str] = "Q"
TILE_X: Final[str] = "X"
SAFE_TILES: Final[set[str]] = {"J", "B", "S", "W", "M"}
SPAWN_ALLOWED_TILES: Final[frozenset[str]] = frozenset({"J", "B", "S", "M"})

MAP_MATRIX: Final[list[list[str]]] = [
    ["Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q"],
    ["Q", "X", "W", "X", "X", "J", "X", "J", "Q"],
    ["Q", "J", "X", "B", "B", "X", "B", "X", "Q"],
    ["Q", "X", "S", "M", "X", "W", "X", "J", "Q"],
    ["Q", "W", "X", "J", "X", "X", "W", "X", "Q"],
    ["Q", "B", "X", "X", "X", "W", "J", "J", "Q"],
    ["Q", "X", "J", "X", "X", "M", "S", "W", "Q"],
    ["Q", "X", "X", "X", "J", "B", "X", "B", "Q"],
    ["Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q", "Q"],
]

# 角色初始背包与状态
INITIAL_INVENTORY: Final[dict[str, int]] = {
    "bottled_water": 1,
    "bread": 1,
}

INITIAL_STATUS: Final[dict[str, int]] = {
    "water": 100,
    "food": 100,
    "exposure": 0,
}

MAX_STATUS: Final[int] = 100

# 建筑资源池规则：每类建筑允许生成的物资类型
BUILDING_ALLOWED_ITEMS: Final[dict[str, frozenset[str]]] = {
    "J": frozenset({"bread", "bottled_water"}),
    "B": frozenset({"compressed_biscuit", "barrel_water"}),
    "S": frozenset({"canned_food", "barrel_water", "bread", "bottled_water"}),
    "W": frozenset({"clean_water"}),
    "M": frozenset({"bottled_water"}),
}

# Default global resource supply (total units) for one match.
RESOURCE_TOTAL_DEFAULTS: Final[dict[str, int]] = {
    "bread": 27,
    "bottled_water": 27,
    "compressed_biscuit": 24,
    "canned_food": 10,
    "barrel_water": 10,
    "clean_water": 36,
}

ITEM_EFFECTS: Final[dict[str, dict[str, int]]] = {
    "bread": {"food": 10},
    "bottled_water": {"water": 10},
    "compressed_biscuit": {"food": 20},
    "canned_food": {"food": 20},
    "barrel_water": {"water": 20},
    "clean_water": {"water": 15},
}

# 各动作的额外状态消耗/变化（基础维持消耗在结算流程中另算）
ACTION_COSTS: Final[dict[str, dict[str, int]]] = {
    "MOVE": {"water": -2, "food": -1, "exposure": 2},
    "EXPLORE": {"water": -1, "food": -1, "exposure": 1},
    "USE": {"water": 0, "food": 0, "exposure": 0},
    "TAKE": {"water": -1, "food": 0, "exposure": 1},
    "REST": {"water": 0, "food": 0, "exposure": -3},
    "ATTACK": {"water": -2, "food": -2, "exposure": 3},
    "GET": {"water": 0, "food": 0, "exposure": 0},
    "TOSS": {"water": 0, "food": 0, "exposure": 0},
}

# 死亡原因枚举：用于结算记录与终局统计
DEATH_REASON_RESOURCE_ZERO: Final[str] = "RESOURCE_ZERO"
DEATH_REASON_NIGHT_X_FAIL: Final[str] = "NIGHT_X_FAIL"
DEATH_REASON_LEFT_IN_GAME: Final[str] = "LEFT_IN_GAME"
DEATH_REASON_FATAL_TILE: Final[str] = "FATAL_TILE"
