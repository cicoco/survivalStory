from __future__ import annotations

import unittest

from src.api.payload_validation import PayloadValidator


class PayloadValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = PayloadValidator()

    def test_loot_window_started_payload_valid(self) -> None:
        payload = {
            "schema": "loot_window_started_v1",
            "message": "loot window opened, winner choose GET/TOSS",
            "winner_player_id": "p1",
            "loser_player_id": "p2",
            "expires_at": "2026-03-13T10:01:10Z",
        }
        self.validator.validate_loot_window_started(payload)

    def test_loot_window_resolved_payload_valid(self) -> None:
        payload = {
            "schema": "loot_window_resolved_v1",
            "message": "loot window resolved",
            "winner_player_id": "p1",
            "loser_player_id": "p2",
            "choice": "GET",
            "obtained": {"bread": 1},
        }
        self.validator.validate_loot_window_resolved(payload)

    def test_loot_window_resolved_payload_invalid_choice(self) -> None:
        payload = {
            "schema": "loot_window_resolved_v1",
            "message": "loot window resolved",
            "winner_player_id": "p1",
            "loser_player_id": "p2",
            "choice": "INVALID",
            "obtained": {},
        }
        with self.assertRaises(ValueError):
            self.validator.validate_loot_window_resolved(payload)


if __name__ == "__main__":
    unittest.main()
