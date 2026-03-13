from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class RoundSchedulerTest(unittest.IsolatedAsyncioTestCase):
    def _build_scheduler(
        self,
        *,
        service: MatchService,
        store: RoomStore,
        notify: NotificationService,
        ai_agent: AgentAdapter | None = None,
    ) -> RoundScheduler:
        settings = AppSettings()
        logger = logging.getLogger("tests.round_scheduler")
        return RoundScheduler(
            store=store,
            service=service,
            notify=notify,
            ws_hub=WsHub(),
            payload_validator=PayloadValidator(),
            ai_agent=ai_agent or AgentAdapter(primary=RuleBot(), fallback=RuleBot()),
            settings=settings,
            logger=logger,
        )

    async def test_ai_decide_uses_action_mask_not_obs_allowed_actions(self) -> None:
        class SpyAgent:
            def __init__(self) -> None:
                self.called = False
                self.obs: dict | None = None
                self.mask: list[str] | None = None

            def decide(self, obs: dict, action_mask: list[str], deadline_at=None) -> dict:
                _ = deadline_at
                self.called = True
                self.obs = obs
                self.mask = action_mask
                return {"action_type": "REST", "payload": {}}

        spy = SpyAgent()
        store = RoomStore()
        notify = NotificationService()
        service = MatchService(room_max_players=2, max_ai_players=1, round_action_timeout_sec=90)
        scheduler = self._build_scheduler(service=service, store=store, notify=notify, ai_agent=spy)  # type: ignore[arg-type]

        room = service.create_room("sched-mask", "host", END_MODE_ALL_DEAD)
        service.start_match(room)
        store.add(room)

        await scheduler.process_active_rooms_once()

        self.assertTrue(spy.called)
        assert spy.obs is not None
        assert spy.mask is not None
        self.assertNotIn("allowed_actions", spy.obs)
        self.assertGreaterEqual(len(spy.mask), 1)

    async def test_process_active_rooms_once_handles_timeout_and_settles(self) -> None:
        store = RoomStore()
        notify = NotificationService()
        service = MatchService(room_max_players=2, max_ai_players=1, round_action_timeout_sec=0)
        scheduler = self._build_scheduler(service=service, store=store, notify=notify)

        room = service.create_room("sched-r1", "host", END_MODE_ALL_DEAD)
        service.start_match(room)
        store.add(room)

        await scheduler.process_active_rooms_once()

        host_events = [row["event_type"] for row in notify.history(room.room_id, "host")]
        self.assertIn("ACTION_ACCEPTED", host_events)
        self.assertIn("ROUND_SETTLED", host_events)

        host_msgs = notify.history(room.room_id, "host")
        auto_flags = [row.get("payload", {}).get("auto") for row in host_msgs if row["event_type"] == "ACTION_ACCEPTED"]
        self.assertIn(True, auto_flags)

    async def test_resolve_timeouts_and_notify_resolves_loot_timeout(self) -> None:
        store = RoomStore()
        notify = NotificationService()
        service = MatchService(room_max_players=2, max_ai_players=0, loot_window_timeout_sec=0)
        scheduler = self._build_scheduler(service=service, store=store, notify=notify)

        room = service.create_room("sched-r2", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)
        store.add(room)

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

        base_time = datetime.now(UTC)
        service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": target.player_id, "loot": {"type": "GET", "items": {"bread": 1}}},
            server_received_at=base_time,
        )
        service.submit_action(room, target.player_id, "REST", {}, server_received_at=base_time + timedelta(seconds=1))
        service.settle_round(room)
        lw = service.get_loot_window_state(room)
        self.assertIsNotNone(lw)
        assert lw is not None
        lw.expires_at = datetime.now(UTC) - timedelta(seconds=1)

        resolved = await scheduler.resolve_timeouts_and_notify(room)

        self.assertTrue(resolved)
        self.assertIsNone(service.get_loot_window_state(room))

        host_events = [row["event_type"] for row in notify.history(room.room_id, "host")]
        self.assertIn("LOOT_WINDOW_RESOLVED", host_events)
        self.assertIn("ROUND_SETTLED", host_events)


if __name__ == "__main__":
    unittest.main()
