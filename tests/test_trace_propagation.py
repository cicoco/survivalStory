from __future__ import annotations

import logging
import unittest

from src.ai.agent_adapter import AgentAdapter
from src.ai.rule_bot import RuleBot
from src.api.payload_validation import PayloadValidator
from src.api.ws_hub import WsHub
from src.application.match_service import MatchService
from src.application.notification_service import NotificationService
from src.application.room_store import RoomStore
from src.application.round_scheduler import RoundScheduler
from src.domain.constants import END_MODE_ALL_DEAD
from src.infra.config import AppSettings


class TracePropagationTest(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_tick_propagates_same_trace_id_for_round(self) -> None:
        store = RoomStore()
        notify = NotificationService()
        service = MatchService(room_max_players=2, max_ai_players=1, round_action_timeout_sec=0)
        scheduler = RoundScheduler(
            store=store,
            service=service,
            notify=notify,
            ws_hub=WsHub(),
            payload_validator=PayloadValidator(),
            ai_agent=AgentAdapter(primary=RuleBot(), fallback=RuleBot()),
            settings=AppSettings(),
            logger=logging.getLogger("tests.trace"),
        )

        room = service.create_room("trace-r1", "host", END_MODE_ALL_DEAD)
        service.start_match(room)
        store.add(room)

        await scheduler.process_active_rooms_once()

        rows = notify.history(room.room_id, "host")
        accepted = [row for row in rows if row["event_type"] == "ACTION_ACCEPTED"]
        settled = [row for row in rows if row["event_type"] == "ROUND_SETTLED"]
        self.assertTrue(accepted)
        self.assertTrue(settled)

        accepted_trace_ids = {row.get("trace_id") for row in accepted}
        settled_trace_ids = {row.get("trace_id") for row in settled}
        self.assertNotIn(None, accepted_trace_ids)
        self.assertNotIn(None, settled_trace_ids)
        self.assertEqual(1, len(accepted_trace_ids))
        self.assertEqual(1, len(settled_trace_ids))
        self.assertEqual(accepted_trace_ids, settled_trace_ids)


if __name__ == "__main__":
    unittest.main()
