"""Policy interfaces."""

from __future__ import annotations

from typing import Protocol


class Policy(Protocol):
    def choose_action(self, obs: dict, action_mask: list[str]) -> dict:
        """Return action object: {'action_type': str, 'payload': dict}."""

