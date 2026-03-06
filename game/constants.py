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

# Field semantics for catalog entries:
# - zh: Chinese display name (console/output)
# - aliases: accepted external identifiers (input side only)
# - effect: item effect used by rule engine when USE executes
# - tile_code: map tile marker (building only)
# - storage: initial room inventory by canonical item IDs (building only)

ITEM_CATALOG = {
    "BREAD": {
        "zh": "面包",
        "aliases": ["BREAD", "B", "面包"],
        "effect": {"food": 10, "water": 0},
    },
    "BOTTLED_WATER": {
        "zh": "瓶装水",
        "aliases": ["BOTTLED_WATER", "W", "瓶装水"],
        "effect": {"food": 0, "water": 10},
    },
    "BISCUIT": {
        "zh": "压缩饼干",
        "aliases": ["BISCUIT", "C", "压缩饼干"],
        "effect": {"food": 20, "water": 0},
    },
    "CANNED_FOOD": {
        "zh": "罐头",
        "aliases": ["CANNED_FOOD", "G", "罐头"],
        "effect": {"food": 20, "water": 0},
    },
    "BARREL_WATER": {
        "zh": "桶装水",
        "aliases": ["BARREL_WATER", "T", "桶装水"],
        "effect": {"food": 0, "water": 20},
    },
    "CLEAN_WATER": {
        "zh": "清水",
        "aliases": ["CLEAN_WATER", "Q", "清水"],
        "effect": {"food": 0, "water": 15},
    },
}

ITEM_EFFECTS = {item_id: meta["effect"] for item_id, meta in ITEM_CATALOG.items()}

ITEM_ALIAS_TO_ID = {}
for _item_id, _meta in ITEM_CATALOG.items():
    ITEM_ALIAS_TO_ID[_item_id.upper()] = _item_id
    for _alias in _meta.get("aliases", []):
        ITEM_ALIAS_TO_ID[str(_alias).strip().upper()] = _item_id


def normalize_item_id(token: str) -> str:
    t = token.strip()
    if not t:
        return t
    k = t.upper()
    return ITEM_ALIAS_TO_ID.get(k, ITEM_ALIAS_TO_ID.get(k.replace("-", "_").replace(" ", "_"), t))


def item_zh_label(item_id: str) -> str:
    meta = ITEM_CATALOG.get(item_id)
    if not meta:
        return item_id
    return str(meta["zh"])


BUILDING_CATALOG = {
    "RESIDENTIAL": {
        "zh": "居民楼",
        "tile_code": "J",
        "aliases": ["RESIDENTIAL", "J", "居民楼"],
        "explorable": True,
        "storage": {"BREAD": 3, "BOTTLED_WATER": 3},
    },
    "OFFICE": {
        "zh": "办公楼",
        "tile_code": "B",
        "aliases": ["OFFICE", "B", "办公楼"],
        "explorable": True,
        "storage": {"BISCUIT": 4},
    },
    "SUPERMARKET": {
        "zh": "超市",
        "tile_code": "S",
        "aliases": ["SUPERMARKET", "S", "超市"],
        "explorable": True,
        "storage": {"CANNED_FOOD": 5, "BARREL_WATER": 5},
    },
    "WATER_SOURCE": {
        "zh": "水源",
        "tile_code": "W",
        "aliases": ["WATER_SOURCE", "W", "水源"],
        "explorable": True,
        "storage": {"CLEAN_WATER": 6},
    },
    "PHARMACY": {
        "zh": "药店",
        "tile_code": "M",
        "aliases": ["PHARMACY", "M", "药店"],
        "explorable": False,
        "storage": {},
    },
}

BUILDING_CODE_TO_ID = {}
for _bid, _meta in BUILDING_CATALOG.items():
    BUILDING_CODE_TO_ID[_meta["tile_code"]] = _bid

SAFE_TILES = {meta["tile_code"] for meta in BUILDING_CATALOG.values()}
EXPLORE_TILES = {meta["tile_code"] for meta in BUILDING_CATALOG.values() if bool(meta.get("explorable", False))}
BUILDING_LOOT_TEMPLATE = {meta["tile_code"]: dict(meta.get("storage", {})) for meta in BUILDING_CATALOG.values()}


def building_zh_label(tile_code: str) -> str:
    bid = BUILDING_CODE_TO_ID.get(tile_code)
    if not bid:
        return tile_code
    return str(BUILDING_CATALOG[bid]["zh"])

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
