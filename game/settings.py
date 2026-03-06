from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OpenAISettings:
    base_url: str
    chat_path: str
    api_key: str
    model: str
    timeout_sec: int
    log_io: bool
    log_max_chars: int
    system_prompt_file: str
    user_prompt_file: str
    agents_file: str

    @classmethod
    def load(cls, config_path: str = "config/openai.json") -> "OpenAISettings":
        data = {}
        p = Path(config_path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))

        base_url = os.getenv("SURVIVAL_OPENAI_BASE_URL", data.get("base_url", "")).strip()
        chat_path = os.getenv("SURVIVAL_OPENAI_CHAT_PATH", data.get("chat_path", "/chat/completions")).strip()
        api_key = os.getenv("SURVIVAL_OPENAI_API_KEY", data.get("api_key", "")).strip()
        model = os.getenv("SURVIVAL_OPENAI_MODEL", data.get("model", "gpt-4o-mini")).strip()
        timeout_sec = int(os.getenv("SURVIVAL_OPENAI_TIMEOUT_SEC", str(data.get("timeout_sec", 30))))
        log_io_raw = os.getenv("SURVIVAL_OPENAI_LOG_IO", str(data.get("log_io", False))).strip().lower()
        log_io = log_io_raw in {"1", "true", "yes", "on"}
        log_max_chars = int(os.getenv("SURVIVAL_OPENAI_LOG_MAX_CHARS", str(data.get("log_max_chars", 1200))))
        system_prompt_file = os.getenv(
            "SURVIVAL_AI_SYSTEM_PROMPT_FILE",
            data.get("system_prompt_file", data.get("prompt_file", "config/prompts/system_prompt.md")),
        ).strip()
        user_prompt_file = os.getenv(
            "SURVIVAL_AI_USER_PROMPT_FILE",
            data.get("user_prompt_file", "config/prompts/user_prompt.md"),
        ).strip()
        agents_file = os.getenv("SURVIVAL_AI_AGENTS_FILE", data.get("agents_file", "config/agents.json")).strip()

        if not base_url or not api_key:
            raise ValueError("missing_openai_config: base_url/api_key required")
        if not chat_path.startswith("/"):
            chat_path = "/" + chat_path

        return cls(
            base_url=base_url.rstrip("/"),
            chat_path=chat_path,
            api_key=api_key,
            model=model,
            timeout_sec=timeout_sec,
            log_io=log_io,
            log_max_chars=log_max_chars,
            system_prompt_file=system_prompt_file,
            user_prompt_file=user_prompt_file,
            agents_file=agents_file,
        )
