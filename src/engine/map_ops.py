"""Map helpers."""

from __future__ import annotations

from src.domain.constants import MAP_MATRIX, SAFE_TILES


def is_in_bounds(x: int, y: int) -> bool:
    return 1 <= x <= 9 and 1 <= y <= 9


def tile_at(x: int, y: int) -> str:
    if not is_in_bounds(x, y):
        raise ValueError(f"out of map bounds: ({x},{y})")
    return MAP_MATRIX[y - 1][x - 1]


def is_safe_tile(tile_type: str) -> bool:
    return tile_type in SAFE_TILES


def tile_key(x: int, y: int) -> str:
    return f"{x},{y}"

