from __future__ import annotations

import sys
import types
import unittest

try:
    import jsonschema  # noqa: F401
except ModuleNotFoundError:
    jsonschema_stub = types.ModuleType("jsonschema")
    jsonschema_stub.ValidationError = Exception

    def _validate_stub(*args, **kwargs) -> None:
        return None

    jsonschema_stub.validate = _validate_stub
    sys.modules["jsonschema"] = jsonschema_stub

if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")

    class _OpenAIStub:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

    openai_stub.OpenAI = _OpenAIStub
    sys.modules["openai"] = openai_stub

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")

    def _safe_load_stub(*args, **kwargs):
        _ = args, kwargs
        return {}

    yaml_stub.safe_load = _safe_load_stub
    sys.modules["yaml"] = yaml_stub

from src.ai.llm_policy import LLMPolicy


class LLMPolicyMappingTest(unittest.TestCase):
    def test_get_maps_to_get_with_items(self) -> None:
        policy = object.__new__(LLMPolicy)
        obs = {"position": {"x": 4, "y": 4}}
        out = policy._to_internal_action(
            {
                "action_type": "GET",
                "payload": {"items": [{"item_type": "bread", "qty": 1}]},
            },
            obs,
        )
        self.assertEqual("GET", out["action_type"])
        self.assertEqual({"items": {"bread": 1}}, out["payload"])

    def test_toss_maps_to_toss(self) -> None:
        policy = object.__new__(LLMPolicy)
        out = policy._to_internal_action(
            {
                "action_type": "TOSS",
                "payload": {},
            },
            {"position": {"x": 4, "y": 4}},
        )
        self.assertEqual("TOSS", out["action_type"])
        self.assertEqual({}, out["payload"])

    def test_build_prompt_includes_phase_c_focus_fields(self) -> None:
        policy = object.__new__(LLMPolicy)
        obs = {
            "position": {"x": 4, "y": 4},
            "recent_positions": [{"x": 4, "y": 4, "day": 1, "phase": "DAY", "round": 1}],
            "local_map_summary": {
                "window_size": 5,
                "center": {"x": 4, "y": 4},
                "tiles": [{"x": 4, "y": 4, "in_bounds": True, "tile_type": "M"}],
            },
        }
        prompt = policy._build_prompt(obs, ["MOVE", "REST"], "# Skill\\nDemo")
        self.assertIn("Decision Focus JSON", prompt)
        self.assertIn("recent_positions", prompt)
        self.assertIn("local_map_summary", prompt)


if __name__ == "__main__":
    unittest.main()
