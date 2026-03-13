from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from src.ai.agent_adapter import AgentAdapter


class _PrimaryOk:
    def choose_action(self, obs: dict, action_mask: list[str]) -> dict:
        _ = obs, action_mask
        return {"action_type": "USE", "payload": {"items": {"bread": 1}}}


class _PrimaryCrash:
    def choose_action(self, obs: dict, action_mask: list[str]) -> dict:
        _ = obs, action_mask
        raise RuntimeError("boom")


class _Fallback:
    def choose_action(self, obs: dict, action_mask: list[str]) -> dict:
        _ = obs, action_mask
        return {"action_type": "REST", "payload": {}}


class AgentAdapterTest(unittest.TestCase):
    def test_primary_success(self) -> None:
        adapter = AgentAdapter(primary=_PrimaryOk(), fallback=_Fallback())
        out = adapter.decide({"x": 1}, ["USE", "REST"], deadline_at=datetime.now(UTC) + timedelta(seconds=1))
        self.assertEqual("USE", out["action_type"])
        self.assertEqual({"items": {"bread": 1}}, out["payload"])

    def test_primary_failure_fallback(self) -> None:
        adapter = AgentAdapter(primary=_PrimaryCrash(), fallback=_Fallback())
        out = adapter.decide({"x": 1}, ["REST"], deadline_at=datetime.now(UTC) + timedelta(seconds=1))
        self.assertEqual("REST", out["action_type"])
        self.assertEqual({}, out["payload"])

    def test_deadline_expired_fallback(self) -> None:
        adapter = AgentAdapter(primary=_PrimaryOk(), fallback=_Fallback())
        out = adapter.decide({"x": 1}, ["USE", "REST"], deadline_at=datetime.now(UTC) - timedelta(seconds=1))
        self.assertEqual("REST", out["action_type"])


if __name__ == "__main__":
    unittest.main()
