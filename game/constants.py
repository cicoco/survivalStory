from __future__ import annotations

MAP_GRID = [
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

SAFE_TILES = {"J", "B", "S", "W", "M"}

ITEM_EFFECTS = {
    "面包": {"food": 10, "water": 0},
    "瓶装水": {"food": 0, "water": 10},
    "压缩饼干": {"food": 20, "water": 0},
    "罐头": {"food": 20, "water": 0},
    "桶装水": {"food": 0, "water": 20},
    "清水": {"food": 0, "water": 15},
}

BUILDING_LOOT_TEMPLATE = {
    "J": {"面包": 3, "瓶装水": 3},
    "B": {"压缩饼干": 4},
    "S": {"罐头": 5, "桶装水": 5},
    "W": {"清水": 6},
    "M": {},
}

ACTION_COSTS = {
    "DAY": {
        "MOVE": {"water": -2, "food": -2, "exposure": 1},
        "EXPLORE": {"water": -2, "food": -2, "exposure": 2},
        "ATTACK": {"water": -4, "food": -4, "exposure": 5},
    },
    "NIGHT": {
        "MOVE": {"water": -2, "food": -2, "exposure": 3},
        "EXPLORE": {"water": -2, "food": -2, "exposure": 4},
        "ATTACK": {"water": -4, "food": -4, "exposure": 5},
    },
}

BASE_PHASE_COST = {"water": -1, "food": -1}

START_WATER = 20
START_FOOD = 20
START_EXPOSURE = 0

DEFAULT_ROOM_SIZE = 6
MAX_ACTIONS_PER_PHASE = 5
