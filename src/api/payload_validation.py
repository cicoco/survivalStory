"""Outgoing payload schema validation."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import ValidationError, validate


class PayloadValidator:
    def __init__(
        self,
        action_rejected_schema_path: str = "docs/v1/schemas/action_rejected_v1.json",
        round_private_schema_path: str = "docs/v1/schemas/round_settled_private_v1.json",
        game_over_summary_schema_path: str = "docs/v1/schemas/game_over_summary_v1.json",
        loot_window_started_schema_path: str = "docs/v1/schemas/loot_window_started_v1.json",
        loot_window_resolved_schema_path: str = "docs/v1/schemas/loot_window_resolved_v1.json",
    ) -> None:
        self._action_rejected_schema = json.loads(
            Path(action_rejected_schema_path).read_text(encoding="utf-8")
        )
        self._round_private_schema = json.loads(
            Path(round_private_schema_path).read_text(encoding="utf-8")
        )
        self._game_over_summary_schema = json.loads(
            Path(game_over_summary_schema_path).read_text(encoding="utf-8")
        )
        self._loot_window_started_schema = json.loads(
            Path(loot_window_started_schema_path).read_text(encoding="utf-8")
        )
        self._loot_window_resolved_schema = json.loads(
            Path(loot_window_resolved_schema_path).read_text(encoding="utf-8")
        )

    def validate_action_rejected(self, payload: dict) -> None:
        self._validate(payload, self._action_rejected_schema)

    def validate_round_private(self, payload: dict) -> None:
        self._validate(payload, self._round_private_schema)

    def validate_game_over_summary(self, payload: dict) -> None:
        self._validate(payload, self._game_over_summary_schema)

    def validate_loot_window_started(self, payload: dict) -> None:
        self._validate(payload, self._loot_window_started_schema)

    def validate_loot_window_resolved(self, payload: dict) -> None:
        self._validate(payload, self._loot_window_resolved_schema)

    def _validate(self, payload: dict, schema: dict) -> None:
        try:
            validate(payload, schema)
        except ValidationError as exc:
            raise ValueError(f"outgoing payload schema validation failed: {exc.message}") from exc
