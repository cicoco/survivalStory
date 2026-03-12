"""Application configuration loader."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib

from src.infra.constants import (
    DEFAULT_AI_POLICY,
    DEFAULT_APP_CONFIG_PATH,
    DEFAULT_LLM_DISCOVERY_TIMEOUT_MS,
    DEFAULT_LLM_INTENT_TIMEOUT_MS,
    DEFAULT_LOOT_WINDOW_TIMEOUT_SEC,
    DEFAULT_MAX_AI_PLAYERS,
    DEFAULT_NOTIFICATION_HISTORY_LIMIT,
    DEFAULT_ROOM_MAX_PLAYERS,
    DEFAULT_ROUND_ACTION_TIMEOUT_SEC,
    DEFAULT_OPENAI_MODEL,
    ENV_AI_POLICY,
    ENV_APP_CONFIG,
    ENV_LLM_DISCOVERY_TIMEOUT_MS,
    ENV_LLM_INTENT_TIMEOUT_MS,
    ENV_LOOT_WINDOW_TIMEOUT_SEC,
    ENV_MAX_AI_PLAYERS,
    ENV_NOTIFICATION_HISTORY_LIMIT,
    ENV_ROOM_MAX_PLAYERS,
    ENV_ROUND_ACTION_TIMEOUT_SEC,
    ENV_OPENAI_API_KEY,
    ENV_OPENAI_BASE_URL,
    ENV_OPENAI_MODEL,
)


@dataclass(slots=True)
class AppSettings:
    ai_policy: str = DEFAULT_AI_POLICY
    openai_api_key: str = ""
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_base_url: str = ""
    llm_discovery_timeout_ms: int = DEFAULT_LLM_DISCOVERY_TIMEOUT_MS
    llm_intent_timeout_ms: int = DEFAULT_LLM_INTENT_TIMEOUT_MS
    notification_history_limit: int = DEFAULT_NOTIFICATION_HISTORY_LIMIT
    loot_window_timeout_sec: int = DEFAULT_LOOT_WINDOW_TIMEOUT_SEC
    round_action_timeout_sec: int = DEFAULT_ROUND_ACTION_TIMEOUT_SEC
    room_max_players: int = DEFAULT_ROOM_MAX_PLAYERS
    max_ai_players: int = DEFAULT_MAX_AI_PLAYERS


def load_settings(config_path: str | None = None) -> AppSettings:
    path = Path(config_path or os.getenv(ENV_APP_CONFIG, DEFAULT_APP_CONFIG_PATH))
    file_data: dict = {}
    if path.exists():
        file_data = tomllib.loads(path.read_text(encoding="utf-8"))

    ai = file_data.get("ai", {})
    openai = file_data.get("openai", {})
    notification = file_data.get("notification", {})
    gameplay = file_data.get("gameplay", {})

    return AppSettings(
        ai_policy=os.getenv(ENV_AI_POLICY, str(ai.get("policy", DEFAULT_AI_POLICY))).lower(),
        openai_api_key=os.getenv(ENV_OPENAI_API_KEY, str(openai.get("api_key", ""))),
        openai_model=os.getenv(ENV_OPENAI_MODEL, str(openai.get("model", DEFAULT_OPENAI_MODEL))),
        openai_base_url=os.getenv(ENV_OPENAI_BASE_URL, str(openai.get("base_url", ""))),
        llm_discovery_timeout_ms=int(
            os.getenv(
                ENV_LLM_DISCOVERY_TIMEOUT_MS,
                str(openai.get("discovery_timeout_ms", DEFAULT_LLM_DISCOVERY_TIMEOUT_MS)),
            )
        ),
        llm_intent_timeout_ms=int(
            os.getenv(
                ENV_LLM_INTENT_TIMEOUT_MS,
                str(openai.get("intent_timeout_ms", DEFAULT_LLM_INTENT_TIMEOUT_MS)),
            )
        ),
        notification_history_limit=int(
            os.getenv(
                ENV_NOTIFICATION_HISTORY_LIMIT,
                str(notification.get("history_limit", DEFAULT_NOTIFICATION_HISTORY_LIMIT)),
            )
        ),
        loot_window_timeout_sec=int(
            os.getenv(
                ENV_LOOT_WINDOW_TIMEOUT_SEC,
                str(gameplay.get("loot_window_timeout_sec", DEFAULT_LOOT_WINDOW_TIMEOUT_SEC)),
            )
        ),
        round_action_timeout_sec=int(
            os.getenv(
                ENV_ROUND_ACTION_TIMEOUT_SEC,
                str(gameplay.get("round_action_timeout_sec", DEFAULT_ROUND_ACTION_TIMEOUT_SEC)),
            )
        ),
        room_max_players=int(
            os.getenv(
                ENV_ROOM_MAX_PLAYERS,
                str(gameplay.get("room_max_players", DEFAULT_ROOM_MAX_PLAYERS)),
            )
        ),
        max_ai_players=int(
            os.getenv(
                ENV_MAX_AI_PLAYERS,
                str(gameplay.get("max_ai_players", DEFAULT_MAX_AI_PLAYERS)),
            )
        ),
    )
