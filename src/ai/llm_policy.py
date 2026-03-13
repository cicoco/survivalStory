"""LLM policy using OpenAI with strict schema validation and RuleBot fallback."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from jsonschema import ValidationError, validate
from openai import OpenAI

from src.ai.policy import Policy
from src.ai.rule_bot import RuleBot
from src.ai.skill_router import SkillRouter
from src.domain.constants import (
    ACTION_ATTACK,
    ACTION_EXPLORE,
    ACTION_GET,
    ACTION_MOVE,
    ACTION_REST,
    ACTION_TAKE,
    ACTION_TOSS,
    ACTION_USE,
)
from src.infra.constants import DEFAULT_OPENAI_MODEL


class LLMPolicy(Policy):
    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        api_key: str = "",
        base_url: str = "",
        discovery_timeout_ms: int = 600,
        intent_timeout_ms: int = 1200,
        schema_path: str = "docs/v1/skills/schemas/choose_action_v1.json",
    ) -> None:
        self._fallback = RuleBot()
        self._model = model
        self._budget_ms = discovery_timeout_ms + intent_timeout_ms
        self._schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        self._router = SkillRouter()
        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    def choose_action(self, obs: dict, action_mask: list[str]) -> dict:
        if not action_mask:
            return {"action_type": ACTION_REST, "payload": {}}

        start = time.monotonic()
        skill = self._router.choose(obs, action_mask)
        skill_block = skill.body if skill else "# Skill\nUse conservative survival policy."

        prompt = self._build_prompt(obs, action_mask, skill_block)
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an AI policy in a survival game. "
                            "Return only one JSON object, no markdown."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                timeout=max(1, self._budget_ms // 1000),
            )
            raw = resp.choices[0].message.content or "{}"
            obj = json.loads(raw)
            validate(obj, self._schema)
            internal = self._to_internal_action(obj, obs)
            if internal["action_type"] not in set(action_mask):
                raise ValueError("action outside action_mask")
            return internal
        except (ValidationError, ValueError, KeyError, json.JSONDecodeError, Exception):
            # Any failure path must fallback to RuleBot per V1 constraints.
            return self._fallback.choose_action(obs, action_mask)
        finally:
            _ = start

    def _build_prompt(self, obs: dict, action_mask: list[str], skill_block: str) -> str:
        decision_focus = self._build_decision_focus(obs)
        # Fixed 4-section assembly as required by V1.
        return (
            "System Rules:\n"
            "- Follow game constraints and action_mask strictly.\n"
            "- Use recent_positions and local_map_summary as primary movement context.\n"
            "- Return schema-compliant JSON only.\n\n"
            f"Skill Template:\n{skill_block}\n\n"
            f"State JSON:\n{json.dumps(obs, ensure_ascii=False)}\n\n"
            f"Decision Focus JSON:\n{json.dumps(decision_focus, ensure_ascii=False)}\n\n"
            "Constraint(action_mask/schema):\n"
            f"- action_mask={json.dumps(action_mask, ensure_ascii=False)}\n"
            "- schema=choose_action_v1\n"
        )

    def _build_decision_focus(self, obs: dict) -> dict[str, Any]:
        # 强化关键输入：让 LLM 明确消费最近轨迹与局部地图摘要。
        recent_positions = obs.get("recent_positions", [])
        if not isinstance(recent_positions, list):
            recent_positions = []
        local_map_summary = obs.get("local_map_summary", {})
        if not isinstance(local_map_summary, dict):
            local_map_summary = {}
        return {
            "recent_positions": recent_positions,
            "local_map_summary": local_map_summary,
        }

    def _to_internal_action(self, schema_action: dict[str, Any], obs: dict) -> dict:
        action_type = str(schema_action["action_type"])
        payload = schema_action.get("payload", {})

        if action_type == "MOVE":
            move_dir = payload.get("move_dir")
            pos = obs.get("position", {})
            x = int(pos.get("x", 0))
            y = int(pos.get("y", 0))
            if move_dir == "UP":
                return {"action_type": ACTION_MOVE, "payload": {"x": x, "y": y - 1}}
            if move_dir == "DOWN":
                return {"action_type": ACTION_MOVE, "payload": {"x": x, "y": y + 1}}
            if move_dir == "LEFT":
                return {"action_type": ACTION_MOVE, "payload": {"x": x - 1, "y": y}}
            if move_dir == "RIGHT":
                return {"action_type": ACTION_MOVE, "payload": {"x": x + 1, "y": y}}
            raise ValueError("invalid move_dir")

        if action_type in {ACTION_USE, ACTION_TAKE, ACTION_GET}:
            items_arr = payload.get("items", [])
            items_map: dict[str, int] = {}
            for row in items_arr:
                item_type = row["item_type"]
                qty = int(row["qty"])
                items_map[item_type] = items_map.get(item_type, 0) + qty
            if action_type == ACTION_GET:
                return {"action_type": ACTION_GET, "payload": {"items": items_map}}
            return {"action_type": action_type, "payload": {"items": items_map}}

        if action_type == ACTION_ATTACK:
            target = payload.get("target_player_id")
            return {"action_type": ACTION_ATTACK, "payload": {"target_id": target}}

        if action_type in {ACTION_EXPLORE, ACTION_REST, ACTION_TOSS}:
            if action_type == ACTION_TOSS:
                return {"action_type": ACTION_TOSS, "payload": {}}
            return {"action_type": action_type, "payload": {}}

        raise ValueError(f"unsupported action_type: {action_type}")
