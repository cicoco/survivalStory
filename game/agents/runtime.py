from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from game.agents.openai_client import OpenAIClient
from game.models import Action, ActionKind, PlayerState, RoomState
from game.settings import OpenAISettings


@dataclass
class AgentConfig:
    agent_id: str
    persona_prompt: str


def load_agent_configs(path: str, agent_ids: list[str]) -> dict[str, AgentConfig]:
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        personas = data.get("personas", [])
    else:
        personas = []
    if not personas:
        personas = [
            "你偏保守，优先规避风险。",
            "你偏均衡，稳健搜集资源。",
            "你偏激进，但仍必须遵守生存铁律。",
        ]
    out = {}
    for i, aid in enumerate(agent_ids):
        out[aid] = AgentConfig(agent_id=aid, persona_prompt=personas[i % len(personas)])
    return out


class AgentRuntime:
    def __init__(self, settings: OpenAISettings, configs: dict[str, AgentConfig]):
        self.settings = settings
        self.configs = configs
        self.system_template = Path(settings.system_prompt_file).read_text(encoding="utf-8")
        self.user_template = Path(settings.user_prompt_file).read_text(encoding="utf-8")
        self.client = OpenAIClient(
            base_url=settings.base_url,
            chat_path=settings.chat_path,
            api_key=settings.api_key,
            model=settings.model,
            timeout_sec=settings.timeout_sec,
        )

    def decide(self, room: RoomState, actor: PlayerState) -> Action:
        cfg = self.configs.get(actor.player_id, AgentConfig(agent_id=actor.player_id, persona_prompt="你保持稳健生存策略。"))
        same_pos_names = [p.name for p in room.players if p.alive and p.player_id != actor.player_id and p.pos() == actor.pos()]
        building_loot = room.building_loot.get(actor.pos(), {})
        ctx = self._build_context(room, actor, cfg.persona_prompt, same_pos_names, building_loot)
        user_prompt = self._render_template(self.user_template, ctx)
        ctx["{{current_situation}}"] = user_prompt
        system_prompt = self._render_system_prompt(self.system_template, ctx)

        try:
            content = self.client.chat(system_prompt, user_prompt)
            return self._parse_action(room, actor, content)
        except Exception as err:
            return Action(actor.player_id, ActionKind.REST, source="AI", reason=f"llm_error:{err}")

    def _parse_action(self, room: RoomState, actor: PlayerState, content: str) -> Action:
        content = content.strip()
        if content.startswith("```"):
            lines = [ln for ln in content.splitlines() if not ln.strip().startswith("```")]
            content = "\n".join(lines).strip()
        data = json.loads(content)
        raw = str(data.get("action", "")).strip()
        reason = str(data.get("reason", "")).strip()
        return self._action_from_text(room, actor, raw, reason)

    def _action_from_text(self, room: RoomState, actor: PlayerState, raw: str, reason: str) -> Action:
        raw_upper = raw.upper()
        normalized = raw.replace("，", ",")

        if raw_upper == "REST" or raw.startswith("休息"):
            return Action(actor.player_id, ActionKind.REST, source="AI", reason=reason)
        if raw_upper == "EXPLORE" or raw.startswith("探索"):
            return Action(actor.player_id, ActionKind.EXPLORE, source="AI", reason=reason)

        if (raw_upper.startswith("MOVE(") and raw_upper.endswith(")")) or (raw.startswith("移动(") and raw.endswith(")")):
            if raw_upper.startswith("MOVE("):
                body = normalized[5:-1]
            else:
                body = normalized[3:-1]
            x_str, y_str = [s.strip() for s in body.split(",", 1)]
            return Action(actor.player_id, ActionKind.MOVE, {"x": int(x_str), "y": int(y_str)}, source="AI", reason=reason)

        if (raw_upper.startswith("USE(") and raw_upper.endswith(")")) or (raw.startswith("使用(") and raw.endswith(")")):
            if raw_upper.startswith("USE("):
                item = normalized[4:-1].strip()
            else:
                item = normalized[3:-1].strip()
            return Action(actor.player_id, ActionKind.USE, {"item": item}, source="AI", reason=reason)

        if (raw_upper.startswith("TAKE(") and raw_upper.endswith(")")) or (raw.startswith("拿取(") and raw.endswith(")")):
            if raw_upper.startswith("TAKE("):
                body = normalized[5:-1]
            else:
                body = normalized[3:-1]
            items = [x.strip() for x in body.split(",") if x.strip()]
            if not items:
                items = [""]
            return Action(actor.player_id, ActionKind.TAKE, {"items": items[:3]}, source="AI", reason=reason)

        if (raw_upper.startswith("ATTACK(") and raw_upper.endswith(")")) or (raw.startswith("攻击(") and raw.endswith(")")):
            if raw_upper.startswith("ATTACK("):
                target_name = normalized[7:-1].strip()
            else:
                target_name = normalized[3:-1].strip()
            target = next((p for p in room.players if p.alive and p.name == target_name), None)
            if not target:
                return Action(actor.player_id, ActionKind.REST, source="AI", reason=f"attack_target_not_found:{target_name}")
            return Action(actor.player_id, ActionKind.ATTACK, {"target_id": target.player_id}, source="AI", reason=reason)

        return Action(actor.player_id, ActionKind.REST, source="AI", reason=f"unknown_action:{raw}")

    def _build_context(
        self,
        room: RoomState,
        actor: PlayerState,
        persona_prompt: str,
        same_pos_names: list[str],
        building_loot: dict[str, int],
    ) -> dict[str, str]:
        phase_cn = "白天" if room.phase.value == "DAY" else "夜晚"
        return {
            "{{room_id}}": room.room_id,
            "{{phase_no}}": str(room.phase_no),
            "{{phase}}": room.phase.value,
            "{{phase_cn}}": phase_cn,
            "{{player_id}}": actor.player_id,
            "{{player_name}}": actor.name,
            "{{current_x}}": str(actor.x),
            "{{current_y}}": str(actor.y),
            "{{water}}": str(actor.water),
            "{{food}}": str(actor.food),
            "{{exposure}}": str(actor.exposure),
            "{{bag_text}}": self._bag_text(actor.bag),
            "{{loot_text}}": self._loot_text(building_loot),
            "{{other_players}}": ",".join(same_pos_names) if same_pos_names else "无",
            "{{persona_prompt}}": persona_prompt,
            "{{current_situation}}": "",
        }

    def _render_template(self, template: str, ctx: dict[str, str]) -> str:
        out = template
        for k, v in ctx.items():
            out = out.replace(k, v)
        return out

    def _render_system_prompt(self, template: str, ctx: dict[str, str]) -> str:
        out = self._render_template(template, ctx)
        if "你必须只输出JSON" not in out:
            out += '\n\n你必须只输出JSON，格式为：{"action":"动作","reason":"原因"}'
        return out

    def _bag_text(self, bag: dict[str, int]) -> str:
        if not bag:
            return "无"
        return ",".join(f"{k}-{v}" for k, v in bag.items() if v > 0) or "无"

    def _loot_text(self, loot: dict[str, int]) -> str:
        if not loot:
            return "无"
        return ",".join(f"{k}-{v}" for k, v in loot.items())
