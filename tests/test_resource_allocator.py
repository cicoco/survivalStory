from __future__ import annotations

import random
import unittest

from src.domain.constants import BUILDING_ALLOWED_ITEMS
from src.engine.resource_allocator import allocate_resources_iterative_random


class ResourceAllocatorTest(unittest.TestCase):
    def test_allocate_resources_totals_and_allowed_items(self) -> None:
        total_resources = {
            "bread": 9,
            "bottled_water": 7,
            "compressed_biscuit": 6,
            "canned_food": 5,
            "barrel_water": 4,
            "clean_water": 3,
        }
        building_counts = {"J": 4, "B": 3, "S": 2, "W": 2, "M": 1}
        allocation = allocate_resources_iterative_random(total_resources, building_counts, rng=random.Random(7))

        allocated_totals = {item_id: 0 for item_id in total_resources}
        for building_id, bag in allocation.items():
            building_type = building_id.split("_", 1)[0]
            allowed = BUILDING_ALLOWED_ITEMS.get(building_type, frozenset())
            for item_id, qty in bag.items():
                self.assertIn(item_id, allowed)
                allocated_totals[item_id] += qty

        self.assertEqual(total_resources, allocated_totals)

    def test_allocate_resources_can_leave_buildings_empty(self) -> None:
        allocation = allocate_resources_iterative_random(
            {"bread": 1},
            {"J": 3, "S": 2},
            rng=random.Random(11),
        )
        empty_count = sum(1 for bag in allocation.values() if not bag)
        self.assertGreaterEqual(empty_count, 1)

    def test_allocate_resources_raises_when_item_has_no_eligible_building(self) -> None:
        with self.assertRaises(ValueError):
            allocate_resources_iterative_random(
                {"clean_water": 2},
                {"J": 2, "B": 1, "S": 1, "M": 1},
                rng=random.Random(1),
            )


if __name__ == "__main__":
    unittest.main()
