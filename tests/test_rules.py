from __future__ import annotations

import unittest

from src.domain.models import PlayerState
from src.engine.rules import (
    apply_action_cost,
    apply_item_use,
    apply_status_clamp,
    is_dead_by_resource,
    is_immediate_tile_death,
    night_x_survive_probability,
)


class RulesTest(unittest.TestCase):
    def test_initial_player_state(self) -> None:
        p = PlayerState(player_id="p1")
        self.assertEqual(100, p.water)
        self.assertEqual(100, p.food)
        self.assertEqual(0, p.exposure)
        self.assertEqual(1, p.inventory["bottled_water"])
        self.assertEqual(1, p.inventory["bread"])

    def test_move_action_cost(self) -> None:
        p = PlayerState(player_id="p1")
        apply_action_cost(p, "MOVE")
        self.assertEqual(98, p.water)
        self.assertEqual(99, p.food)
        self.assertEqual(2, p.exposure)

    def test_use_item_effect(self) -> None:
        p = PlayerState(player_id="p1", water=80, food=80, exposure=0)
        apply_item_use(p, "bottled_water")
        self.assertEqual(90, p.water)
        self.assertEqual(80, p.food)
        self.assertEqual(0, p.inventory["bottled_water"])

    def test_status_clamp_and_resource_death(self) -> None:
        p = PlayerState(player_id="p1", water=-5, food=120, exposure=103)
        apply_status_clamp(p)
        self.assertEqual(0, p.water)
        self.assertEqual(100, p.food)
        self.assertEqual(100, p.exposure)
        self.assertTrue(is_dead_by_resource(p))

    def test_night_x_probability_at_e20(self) -> None:
        self.assertAlmostEqual(0.81, night_x_survive_probability(20))

    def test_night_x_probability_clamp(self) -> None:
        self.assertAlmostEqual(0.97, night_x_survive_probability(-999))
        self.assertAlmostEqual(0.03, night_x_survive_probability(999))

    def test_tile_immediate_death(self) -> None:
        self.assertTrue(is_immediate_tile_death("Q", "DAY"))
        self.assertTrue(is_immediate_tile_death("Q", "NIGHT"))
        self.assertTrue(is_immediate_tile_death("X", "DAY"))
        self.assertFalse(is_immediate_tile_death("X", "NIGHT"))
        self.assertFalse(is_immediate_tile_death("J", "DAY"))


if __name__ == "__main__":
    unittest.main()
