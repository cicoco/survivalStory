from __future__ import annotations

import unittest

from src.application.match_service import MatchService
from src.domain.constants import END_MODE_ALL_DEAD


class TimeoutPathsTest(unittest.TestCase):
    def test_round_timeout_auto_rest_for_human_only(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=1, round_action_timeout_sec=0)
        room = service.create_room("timeout-human", "host", END_MODE_ALL_DEAD)
        service.start_match(room)

        auto_actions = service.resolve_round_timeout_if_needed(room)
        auto_player_ids = {action.player_id for action in auto_actions}
        self.assertIn("host", auto_player_ids)
        self.assertNotIn("ai_1", auto_player_ids)
        self.assertTrue(all(action.action_type == "REST" for action in auto_actions))

    def test_loot_window_timeout_defaults_to_toss(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0, loot_window_timeout_sec=0)
        room = service.create_room("timeout-loot", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        attacker = room.players["host"]
        target = room.players["u2"]
        attacker.x, attacker.y = 4, 4
        target.x, target.y = 4, 4
        attacker.known_characters.add(target.player_id)
        attacker.water = 100
        attacker.food = 100
        attacker.exposure = 0
        target.water = 10
        target.food = 10
        target.exposure = 90

        service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": target.player_id, "loot": {"type": "GET", "items": {"bread": 1}}},
        )
        service.submit_action(room, target.player_id, "REST")
        service.settle_round(room)

        self.assertIsNotNone(service.get_loot_window_state(room))
        private_results = service.resolve_loot_window_timeout_if_needed(room)
        self.assertIsNotNone(private_results)
        self.assertIsNone(service.get_loot_window_state(room))

        assert private_results is not None
        loot_events = [
            event
            for result in private_results.values()
            for event in result.get("events", [])
            if event.get("event_type") == "LOOT_WINDOW_RESOLVED"
        ]
        self.assertTrue(loot_events)
        self.assertTrue(all(event.get("choice") == "TOSS" for event in loot_events))


if __name__ == "__main__":
    unittest.main()
