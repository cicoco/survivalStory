from __future__ import annotations

import unittest

from src.application.match_service import MatchService
from src.domain.constants import END_MODE_ALL_DEAD


class EndgameSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MatchService()

    def test_endgame_summary_contains_players_and_ranking(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)
        for player in room.players.values():
            player.water = 1
            self.service.submit_action(room, player.player_id, "ATTACK")

        self.service.settle_round(room)
        summary = self.service.get_endgame_summary(room)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual("r1", summary["room_id"])
        self.assertEqual(6, len(summary["players"]))
        self.assertEqual(6, len(summary["ranking"]))

    def test_reset_after_game_over(self) -> None:
        room = self.service.create_room("r2", "host", END_MODE_ALL_DEAD)
        self.service.join_room(room, "u2", is_human=True)
        self.service.start_match(room)
        for player in room.players.values():
            player.water = 1
            self.service.submit_action(room, player.player_id, "ATTACK")

        self.service.settle_round(room)
        outcome = self.service.reset_room_for_next_match(room, "host")

        self.assertEqual("RESET", outcome["mode"])
        self.assertEqual("WAITING", room.status)
        self.assertIsNone(room.match_state)
        self.assertTrue(all(p.alive for p in room.players.values()))
        self.assertEqual({"host", "u2"}, set(room.players.keys()))
        self.assertTrue(all(p.is_human for p in room.players.values()))
        self.assertEqual(4, outcome["removed_ai_count"])

        self.service.start_match(room)
        self.assertEqual(6, len(room.players))


if __name__ == "__main__":
    unittest.main()
