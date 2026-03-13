"""AI agent decision adapter with safe fallback."""

from __future__ import annotations

from datetime import UTC, datetime

from src.ai.policy import Policy
from src.domain.constants import ACTION_REST


class AgentAdapter:
    def __init__(self, *, primary: Policy, fallback: Policy) -> None:
        self._primary = primary
        self._fallback = fallback

    def decide(self, obs: dict, action_mask: list[str], deadline_at: datetime | None = None) -> dict:
        if not action_mask:
            return {"action_type": ACTION_REST, "payload": {}}

        if deadline_at is not None and datetime.now(UTC) >= deadline_at:
            return self._normalize(self._fallback.choose_action(obs, action_mask))

        try:
            return self._normalize(self._primary.choose_action(obs, action_mask))
        except Exception:
            return self._normalize(self._fallback.choose_action(obs, action_mask))

    def _normalize(self, action: dict | None) -> dict:
        if not isinstance(action, dict):
            return {"action_type": ACTION_REST, "payload": {}}
        action_type = action.get("action_type", ACTION_REST)
        payload = action.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        return {"action_type": action_type, "payload": payload}
