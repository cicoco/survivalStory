"""Round action ordering utilities."""

from __future__ import annotations

from src.domain.models import ActionEnvelope


def sort_action_queue(actions: list[ActionEnvelope]) -> list[ActionEnvelope]:
    return sorted(actions, key=lambda x: (x.server_received_at, x.join_seq))
