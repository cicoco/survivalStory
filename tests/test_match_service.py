from __future__ import annotations

import unittest

from src.application.match_service import MatchService
from src.domain.constants import END_MODE_ALL_DEAD, END_MODE_HUMAN_ALL_DEAD, PHASE_NIGHT


class MatchServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MatchService()

    def test_start_match_auto_fill_ai_to_six(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.join_room(room, "u2", is_human=True)
        self.service.start_match(room)

        self.assertEqual(6, len(room.players))
        ai_count = len([p for p in room.players.values() if not p.is_human])
        self.assertEqual(4, ai_count)

    def test_start_match_fill_ai_respects_max_ai_players_not_full_room(self) -> None:
        service = MatchService(room_max_players=6, max_ai_players=2)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        self.assertEqual(4, len(room.players))
        ai_count = len([p for p in room.players.values() if not p.is_human])
        self.assertEqual(2, ai_count)

    def test_round_lock_when_all_active_submitted(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        for player_id in room.players:
            self.service.submit_action(room, player_id, "USE")
        self.assertTrue(room.match_state.round_locked)

    def test_phase_upkeep_only_once_per_phase(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        target = room.players["host"]
        for player in room.players.values():
            self.service.submit_action(room, player.player_id, "USE")
        self.service.settle_round(room)
        self.assertEqual(99, target.water)
        self.assertEqual(99, target.food)

        for player in room.players.values():
            if player.alive and not player.phase_ended:
                self.service.submit_action(room, player.player_id, "USE")
        self.service.settle_round(room)
        self.assertEqual(99, target.water)
        self.assertEqual(99, target.food)

    def test_all_rest_fast_advance_phase(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        for player in room.players.values():
            self.service.submit_action(room, player.player_id, "REST")
        self.service.settle_round(room)

        self.assertEqual(PHASE_NIGHT, room.match_state.phase)
        self.assertEqual(1, room.match_state.round)
        self.assertFalse(room.match_state.phase_base_upkeep_applied)

    def test_end_mode_all_dead(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)
        for player in room.players.values():
            player.water = 1
            self.service.submit_action(room, player.player_id, "ATTACK")

        self.service.settle_round(room)
        self.assertTrue(room.match_state.game_over)
        self.assertEqual(END_MODE_ALL_DEAD, room.match_state.game_over_reason)

    def test_end_mode_human_all_dead(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_HUMAN_ALL_DEAD)
        self.service.start_match(room)
        for player in room.players.values():
            if player.is_human:
                player.water = 1
                self.service.submit_action(room, player.player_id, "ATTACK")
            else:
                self.service.submit_action(room, player.player_id, "USE")

        self.service.settle_round(room)
        self.assertTrue(room.match_state.game_over)
        self.assertEqual(END_MODE_HUMAN_ALL_DEAD, room.match_state.game_over_reason)

    def test_attack_loot_window_outputs_get_action_and_attack_loot_result(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)
        attacker = room.players["host"]
        target = room.players["ai_1"]
        attacker.known_characters.add(target.player_id)
        attacker.water = 100
        attacker.food = 100
        attacker.exposure = 0
        target.water = 10
        target.food = 10
        target.exposure = 90

        self.service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": target.player_id, "loot": {"type": "GET", "items": {"bread": 1}}},
        )
        for player_id in room.players:
            if player_id == attacker.player_id:
                continue
            self.service.submit_action(room, player_id, "REST")

        self.service.settle_round(room)
        loot_window = self.service.get_loot_window_state(room)
        self.assertIsNotNone(loot_window)
        assert loot_window is not None

        private_results = self.service.submit_loot_window_action(
            room,
            loot_window.winner_player_id,
            "GET",
            {"items": {"bread": 1}},
        )
        winner_actions = private_results[loot_window.winner_player_id]["actions"]
        self.assertTrue(any(a["action_type"] == "GET" for a in winner_actions))

        host_actions = private_results["host"]["actions"]
        attack_action = next(a for a in host_actions if a["action_type"] == "ATTACK")
        self.assertEqual("ai_1", attack_action["result"]["target_player_id"])
        self.assertIn("loot", attack_action["result"])
        self.assertEqual("GET", attack_action["result"]["loot"]["type"])


if __name__ == "__main__":
    unittest.main()
