"""Skill template routing for LLM policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(slots=True)
class SkillTemplate:
    skill_id: str
    name: str
    short_desc: str
    priority: int
    path: str
    body: str


class SkillRouter:
    def __init__(self, skills_dir: str = "docs/v1/skills") -> None:
        self.skills_dir = Path(skills_dir)
        self._skills = self._load_skills()

    def choose(self, obs: dict, action_mask: list[str]) -> SkillTemplate | None:
        if not self._skills:
            return None
        phase = obs.get("time_state", {}).get("phase", "DAY")
        status = obs.get("self_status", {})
        water = int(status.get("water", 0))
        food = int(status.get("food", 0))
        exposure = int(status.get("exposure", 0))

        emergency = water <= 20 or food <= 20 or exposure >= 85
        has_loot_window = "GET" in action_mask or "TOSS" in action_mask

        if has_loot_window:
            return self._find_by_id("loot_window_decision")
        if emergency:
            return self._find_by_id("emergency_survival")
        if phase == "NIGHT":
            return self._find_by_id("night_survival_control")
        return self._find_by_id("day_explore_collect")

    def _find_by_id(self, skill_id: str) -> SkillTemplate | None:
        for s in self._skills:
            if s.skill_id == skill_id:
                return s
        return self._skills[0] if self._skills else None

    def _load_skills(self) -> list[SkillTemplate]:
        index_path = self.skills_dir / "index.yaml"
        if not index_path.exists():
            return []
        data = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        rows = data.get("skills", [])
        loaded: list[SkillTemplate] = []
        for row in rows:
            if not row.get("enabled", False):
                continue
            rel_path = row.get("path")
            if not rel_path:
                continue
            file_path = self.skills_dir / rel_path
            if not file_path.exists():
                continue
            body = file_path.read_text(encoding="utf-8")
            loaded.append(
                SkillTemplate(
                    skill_id=str(row.get("skill_id", "")),
                    name=str(row.get("name", "")),
                    short_desc=str(row.get("short_desc", "")),
                    priority=int(row.get("priority", 0)),
                    path=str(file_path),
                    body=body,
                )
            )
        loaded.sort(key=lambda x: x.priority, reverse=True)
        return loaded

