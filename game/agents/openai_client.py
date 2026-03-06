from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import request


@dataclass
class OpenAIClient:
    base_url: str
    chat_path: str
    api_key: str
    model: str
    timeout_sec: int = 30

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}{self.chat_path}"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = request.Request(
            url=url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with request.urlopen(req, timeout=self.timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
