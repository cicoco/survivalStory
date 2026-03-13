from __future__ import annotations

import unittest

from src.application.match_service import MatchService
from src.domain.constants import END_MODE_ALL_DEAD


class AIInputViewTest(unittest.TestCase):
    def test_player_view_contains_phase_a_fields(self) -> None:
        service = MatchService(room_max_players=1, max_ai_players=0)
        room = service.create_room("view-r1", "host", END_MODE_ALL_DEAD)
        service.start_match(room)

        view = service.get_player_view(room, "host")

        self.assertIn("recent_positions", view)
        self.assertIn("local_map_summary", view)
        self.assertIn("action_mask", view)
        self.assertNotIn("allowed_actions", view)

        local = view["local_map_summary"]
        self.assertEqual(5, local["window_size"])
        self.assertEqual(25, len(local["tiles"]))

    def test_recent_positions_updates_after_move(self) -> None:
        service = MatchService(room_max_players=1, max_ai_players=0, recent_positions_maxlen=4)
        room = service.create_room("view-r2", "host", END_MODE_ALL_DEAD)
        match = service.start_match(room)
        player = room.players["host"]

        player.x = 4
        player.y = 4
        player.recent_positions = [{"x": 4, "y": 4, "day": match.day, "phase": match.phase, "round": match.round}]

        service.submit_action(room, "host", "MOVE", {"x": 4, "y": 3})
        service.settle_round(room)

        self.assertGreaterEqual(len(player.recent_positions), 2)
        last = player.recent_positions[-1]
        self.assertEqual(4, last["x"])
        self.assertEqual(3, last["y"])
        self.assertLessEqual(len(player.recent_positions), 4)


if __name__ == "__main__":
    unittest.main()
