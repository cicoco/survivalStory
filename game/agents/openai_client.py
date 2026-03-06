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
    log_io: bool = False
    log_max_chars: int = 1200

    def _clip(self, text: str) -> str:
        if len(text) <= self.log_max_chars:
            return text
        return text[: self.log_max_chars] + "...(truncated)"

    def chat(self, system_prompt: str, user_prompt: str, trace_tag: str = "AI") -> str:
        url = f"{self.base_url}{self.chat_path}"
        if self.log_io:
            print(f"[AI][REQ][{trace_tag}] model={self.model} url={url}")
            print(f"[AI][REQ][{trace_tag}] system={self._clip(system_prompt)}")
            print(f"[AI][REQ][{trace_tag}] user={self._clip(user_prompt)}")
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
        if self.log_io:
            print(f"[AI][RESP][{trace_tag}] raw={self._clip(raw)}")
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        if self.log_io:
            print(f"[AI][RESP][{trace_tag}] content={self._clip(content)}")
        return content
