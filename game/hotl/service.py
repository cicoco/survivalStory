from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReviewDecision:
    approved: bool
    override_action: dict | None = None
    reason: str = ""


class HOTLService:
    """
    V1 默认不开启人工审核。
    仅预留接口，后续可接入审核面板或控制台命令。
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def should_review(self, event_type: str, payload: dict) -> bool:
        if not self.enabled:
            return False
        return False

    def review(self, event_type: str, payload: dict) -> ReviewDecision:
        return ReviewDecision(approved=True, reason="auto_pass_v1")
