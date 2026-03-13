"""Iterative random resource allocation under building-type constraints."""

from __future__ import annotations

import random
from typing import Mapping

from src.domain.constants import BUILDING_ALLOWED_ITEMS


def allocate_resources_iterative_random(
    total_resources: Mapping[str, int],
    building_counts: Mapping[str, int],
    *,
    allowed_items_by_building: Mapping[str, frozenset[str]] | None = None,
    rng: random.Random | None = None,
) -> dict[str, dict[str, int]]:
    """
    Allocate all resources into building instances with progressive random placement.

    Returns a mapping like {"J_1": {"bread": 2}, "J_2": {}, "S_1": {"canned_food": 1}, ...}.
    """
    allocator_rng = rng or random.Random()
    allowed_map = allowed_items_by_building or BUILDING_ALLOWED_ITEMS

    instances_by_type: dict[str, list[str]] = {}
    allocations: dict[str, dict[str, int]] = {}
    for building_type, count in building_counts.items():
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"building count must be non-negative integer: {building_type}")
        instances = [f"{building_type}_{idx + 1}" for idx in range(count)]
        instances_by_type[building_type] = instances
        for instance_id in instances:
            allocations[instance_id] = {}

    for item_id, total_qty in total_resources.items():
        if not isinstance(total_qty, int) or total_qty < 0:
            raise ValueError(f"resource quantity must be non-negative integer: {item_id}")
        if total_qty == 0:
            continue

        eligible_instances: list[str] = []
        for building_type, instances in instances_by_type.items():
            allowed_items = allowed_map.get(building_type, frozenset())
            if item_id in allowed_items:
                eligible_instances.extend(instances)
        if not eligible_instances:
            raise ValueError(f"no eligible buildings for resource: {item_id}")

        last_picked: str | None = None
        for _ in range(total_qty):
            use_sticky = last_picked is not None and allocator_rng.random() < 0.62
            if use_sticky:
                picked = last_picked
            else:
                weights: list[float] = []
                for instance_id in eligible_instances:
                    same_item_qty = allocations[instance_id].get(item_id, 0)
                    all_item_qty = sum(allocations[instance_id].values())
                    concentration = (same_item_qty + 1) ** 1.30
                    load_bias = (all_item_qty + 1) ** 0.25
                    jitter = allocator_rng.uniform(0.70, 1.45)
                    weights.append(concentration * load_bias * jitter)
                picked = allocator_rng.choices(eligible_instances, weights=weights, k=1)[0]

            allocations[picked][item_id] = allocations[picked].get(item_id, 0) + 1
            last_picked = picked

    # Validation: totals must be exact and no illegal item appears in a building.
    allocated_totals: dict[str, int] = {}
    for instance_id, bag in allocations.items():
        building_type = instance_id.split("_", 1)[0]
        allowed_items = allowed_map.get(building_type, frozenset())
        for item_id, qty in bag.items():
            if item_id not in allowed_items:
                raise ValueError(f"resource {item_id} allocated to illegal building type {building_type}")
            allocated_totals[item_id] = allocated_totals.get(item_id, 0) + qty

    for item_id, total_qty in total_resources.items():
        if allocated_totals.get(item_id, 0) != total_qty:
            raise ValueError(f"resource total mismatch for {item_id}")

    return allocations
