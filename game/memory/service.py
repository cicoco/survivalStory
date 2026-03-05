from __future__ import annotations

from collections import deque


class MemoryService:
    def __init__(self, window_size: int = 20):
        self.window_size = window_size
        self._events = deque(maxlen=window_size)

    def add(self, event: dict) -> None:
        self._events.append(event)

    def recent(self, n: int = 10) -> list[dict]:
        return list(self._events)[-n:]
