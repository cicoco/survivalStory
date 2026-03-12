from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from src.domain.models import ActionEnvelope
from src.engine.round_order import sort_action_queue


class RoundOrderTest(unittest.TestCase):
    def test_sort_by_server_time_then_join_seq(self) -> None:
        t = datetime(2026, 3, 12, 10, 0, 0)
        actions = [
            ActionEnvelope(
                action_id="a3",
                player_id="p3",
                day=1,
                phase="DAY",
                round=1,
                action_type="REST",
                payload={},
                join_seq=3,
                server_received_at=t + timedelta(seconds=2),
            ),
            ActionEnvelope(
                action_id="a2",
                player_id="p2",
                day=1,
                phase="DAY",
                round=1,
                action_type="REST",
                payload={},
                join_seq=2,
                server_received_at=t,
            ),
            ActionEnvelope(
                action_id="a1",
                player_id="p1",
                day=1,
                phase="DAY",
                round=1,
                action_type="REST",
                payload={},
                join_seq=1,
                server_received_at=t,
            ),
        ]

        sorted_actions = sort_action_queue(actions)
        self.assertEqual(["a1", "a2", "a3"], [a.action_id for a in sorted_actions])


if __name__ == "__main__":
    unittest.main()
