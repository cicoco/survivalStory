from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from src.api.app import create_app


class EventSequenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup: dict[str, str | None] = {}
        self._set_env("MAX_AI_PLAYERS", "0")
        self._set_env("AI_POLICY", "rule")
        self._set_env("ROUND_ACTION_TIMEOUT_SEC", "90")
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_env(self, key: str, value: str) -> None:
        if key not in self._env_backup:
            self._env_backup[key] = os.environ.get(key)
        os.environ[key] = value

    def _create_and_start_room(self, room_id: str, host_player_id: str) -> None:
        created = self.client.post(
            "/rooms",
            json={"room_id": room_id, "host_player_id": host_player_id, "end_mode": "ALL_DEAD"},
        )
        self.assertEqual(200, created.status_code)

        started = self.client.post(f"/rooms/{room_id}/start")
        self.assertEqual(200, started.status_code)

    def _history_events(self, room_id: str, player_id: str) -> list[str]:
        resp = self.client.get(f"/rooms/{room_id}/players/{player_id}/history")
        self.assertEqual(200, resp.status_code)
        payload = resp.json()
        return [row["event_type"] for row in payload["items"]]

    def test_start_emits_game_started_then_round_started(self) -> None:
        room_id = "ev-seq-start"
        host = "host-seq-start"
        self._create_and_start_room(room_id, host)

        events = self._history_events(room_id, host)
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual("GAME_STARTED", events[0])
        self.assertEqual("ROUND_STARTED", events[1])

    def test_submit_action_emits_accepted_before_round_settled(self) -> None:
        room_id = "ev-seq-action"
        host = "host-seq-action"
        self._create_and_start_room(room_id, host)

        submitted = self.client.post(
            f"/rooms/{room_id}/actions",
            json={"player_id": host, "action_type": "REST", "payload": {}},
        )
        self.assertEqual(200, submitted.status_code)
        self.assertTrue(submitted.json().get("accepted"))

        events = self._history_events(room_id, host)
        accepted_idx = events.index("ACTION_ACCEPTED")
        settled_idx = events.index("ROUND_SETTLED")
        self.assertLess(accepted_idx, settled_idx)

    def test_view_on_waiting_room_returns_400_not_500(self) -> None:
        room_id = "ev-seq-view-waiting"
        host = "host-seq-view-waiting"
        created = self.client.post(
            "/rooms",
            json={"room_id": room_id, "host_player_id": host, "end_mode": "ALL_DEAD"},
        )
        self.assertEqual(200, created.status_code)

        view_resp = self.client.get(f"/rooms/{room_id}/players/{host}/view")
        self.assertEqual(400, view_resp.status_code)
        self.assertEqual("room is not active", view_resp.json().get("detail"))


if __name__ == "__main__":
    unittest.main()
