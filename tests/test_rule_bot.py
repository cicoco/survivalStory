from __future__ import annotations

import unittest

from src.ai.rule_bot import RuleBot


class RuleBotTest(unittest.TestCase):
    def _base_obs(self) -> dict:
        return {
            "self_status": {"water": 80, "food": 80, "exposure": 10},
            "inventory": {},
            "time_state": {"phase": "DAY"},
            "position": {"x": 4, "y": 4, "tile_type": "M"},
            "building_snapshot": {"resources": {}, "characters": []},
            "building_info_state": "HAS_MEMORY",
            "recent_positions": [],
        }

    def _make_local(self, rows: list[dict]) -> dict:
        return {"window_size": 5, "center": {"x": 4, "y": 4}, "tiles": rows}

    def test_move_avoids_immediate_backtrack(self) -> None:
        bot = RuleBot()
        obs = self._base_obs()
        obs["recent_positions"] = [
            {"x": 4, "y": 3, "day": 1, "phase": "DAY", "round": 1},
            {"x": 4, "y": 4, "day": 1, "phase": "DAY", "round": 1},
        ]
        obs["local_map_summary"] = self._make_local(
            [
                {
                    "x": 4,
                    "y": 3,
                    "in_bounds": True,
                    "tile_type": "B",
                    "is_safe": True,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                },
                {
                    "x": 5,
                    "y": 4,
                    "in_bounds": True,
                    "tile_type": "J",
                    "is_safe": True,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                },
                {
                    "x": 4,
                    "y": 5,
                    "in_bounds": True,
                    "tile_type": "X",
                    "is_safe": False,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                },
                {
                    "x": 3,
                    "y": 4,
                    "in_bounds": True,
                    "tile_type": "X",
                    "is_safe": False,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                },
            ]
        )

        action = bot.choose_action(obs, ["MOVE", "REST"])
        self.assertEqual("MOVE", action["action_type"])
        self.assertEqual({"x": 5, "y": 4}, action["payload"])

    def test_move_prefers_resource_hint_in_local_map(self) -> None:
        bot = RuleBot()
        obs = self._base_obs()
        obs["local_map_summary"] = self._make_local(
            [
                {
                    "x": 4,
                    "y": 3,
                    "in_bounds": True,
                    "tile_type": "B",
                    "is_safe": True,
                    "is_explored": True,
                    "known_resources": {},
                    "known_characters": [],
                },
                {
                    "x": 5,
                    "y": 4,
                    "in_bounds": True,
                    "tile_type": "J",
                    "is_safe": True,
                    "is_explored": True,
                    "known_resources": {"bread": 2},
                    "known_characters": [],
                },
            ]
        )

        action = bot.choose_action(obs, ["MOVE"])
        self.assertEqual("MOVE", action["action_type"])
        self.assertEqual({"x": 5, "y": 4}, action["payload"])

    def test_move_falls_back_without_local_map(self) -> None:
        bot = RuleBot()
        obs = self._base_obs()

        action = bot.choose_action(obs, ["MOVE"])
        self.assertEqual("MOVE", action["action_type"])
        self.assertEqual({"x": 4, "y": 3}, action["payload"])

    def test_configurable_weights_change_move_choice(self) -> None:
        obs = self._base_obs()
        obs["local_map_summary"] = self._make_local(
            [
                {
                    "x": 4,
                    "y": 3,
                    "in_bounds": True,
                    "tile_type": "B",
                    "is_safe": True,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                },
                {
                    "x": 5,
                    "y": 4,
                    "in_bounds": True,
                    "tile_type": "J",
                    "is_safe": True,
                    "is_explored": True,
                    "known_resources": {"bread": 2},
                    "known_characters": [],
                },
            ]
        )

        default_bot = RuleBot()
        default_action = default_bot.choose_action(obs, ["MOVE"])
        self.assertEqual({"x": 5, "y": 4}, default_action["payload"])

        tuned_bot = RuleBot(resource_hint_bonus=0, unexplored_bonus=4)
        tuned_action = tuned_bot.choose_action(obs, ["MOVE"])
        self.assertEqual({"x": 4, "y": 3}, tuned_action["payload"])

    def test_stuck_cycle_prefers_rest_when_allowed(self) -> None:
        bot = RuleBot(stuck_rest_threshold=1)
        obs = self._base_obs()
        obs["identity"] = {"room_id": "r1", "player_id": "ai_1"}
        obs["recent_positions"] = [
            {"x": 4, "y": 4, "day": 1, "phase": "DAY", "round": 1},
            {"x": 4, "y": 3, "day": 1, "phase": "DAY", "round": 1},
            {"x": 4, "y": 4, "day": 1, "phase": "DAY", "round": 2},
            {"x": 4, "y": 3, "day": 1, "phase": "DAY", "round": 2},
        ]
        obs["position"] = {"x": 4, "y": 3, "tile_type": "B"}
        obs["local_map_summary"] = self._make_local(
            [
                {
                    "x": 4,
                    "y": 4,
                    "in_bounds": True,
                    "tile_type": "B",
                    "is_safe": True,
                    "is_explored": True,
                    "known_resources": {},
                    "known_characters": [],
                }
            ]
        )

        action = bot.choose_action(obs, ["MOVE", "REST"])
        self.assertEqual("REST", action["action_type"])

    def test_stuck_cycle_turns_when_rest_not_allowed(self) -> None:
        bot = RuleBot(stuck_rest_threshold=1)
        obs = self._base_obs()
        obs["identity"] = {"room_id": "r2", "player_id": "ai_2"}
        obs["recent_positions"] = [
            {"x": 4, "y": 4, "day": 1, "phase": "DAY", "round": 1},
            {"x": 4, "y": 3, "day": 1, "phase": "DAY", "round": 1},
            {"x": 4, "y": 4, "day": 1, "phase": "DAY", "round": 2},
            {"x": 4, "y": 3, "day": 1, "phase": "DAY", "round": 2},
        ]
        obs["position"] = {"x": 4, "y": 3, "tile_type": "B"}
        obs["local_map_summary"] = self._make_local(
            [
                {
                    "x": 4,
                    "y": 4,
                    "in_bounds": True,
                    "tile_type": "B",
                    "is_safe": True,
                    "is_explored": True,
                    "known_resources": {},
                    "known_characters": [],
                },
                {
                    "x": 5,
                    "y": 3,
                    "in_bounds": True,
                    "tile_type": "J",
                    "is_safe": True,
                    "is_explored": False,
                    "known_resources": {},
                    "known_characters": [],
                },
            ]
        )

        action = bot.choose_action(obs, ["MOVE"])
        self.assertEqual("MOVE", action["action_type"])
        self.assertEqual({"x": 5, "y": 3}, action["payload"])


if __name__ == "__main__":
    unittest.main()
