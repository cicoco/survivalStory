from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from src.application.match_service import MatchService
from src.domain.constants import (
    BUILDING_ALLOWED_ITEMS,
    END_MODE_ALL_DEAD,
    END_MODE_HUMAN_ALL_DEAD,
    INFO_STATE_HAS_MEMORY,
    PHASE_DAY,
    PHASE_NIGHT,
    RESOURCE_TOTAL_DEFAULTS,
)
from src.engine.map_ops import tile_at, tile_key


class MatchServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MatchService()

    def test_start_match_auto_fill_ai_to_six(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.join_room(room, "u2", is_human=True)
        self.service.start_match(room)

        self.assertEqual(6, len(room.players))
        ai_count = len([p for p in room.players.values() if not p.is_human])
        self.assertEqual(4, ai_count)

    def test_start_match_inventory_allocation_respects_totals_and_tile_rules(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)
        assert room.match_state is not None

        allocated_totals = {item_id: 0 for item_id in RESOURCE_TOTAL_DEFAULTS}
        for tile_key, bag in room.match_state.building_inventory.items():
            x_str, y_str = tile_key.split(",", 1)
            tile_type = tile_at(int(x_str), int(y_str))
            allowed = BUILDING_ALLOWED_ITEMS.get(tile_type, frozenset())
            for item_id, qty in bag.items():
                self.assertIn(item_id, allowed)
                allocated_totals[item_id] = allocated_totals.get(item_id, 0) + qty

        self.assertEqual(RESOURCE_TOTAL_DEFAULTS, allocated_totals)

    def test_start_match_fill_ai_respects_max_ai_players_not_full_room(self) -> None:
        service = MatchService(room_max_players=6, max_ai_players=2)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        self.assertEqual(4, len(room.players))
        ai_count = len([p for p in room.players.values() if not p.is_human])
        self.assertEqual(2, ai_count)

    def test_initial_inventory_randomized_water_and_bread_in_0_1(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)

        for player in room.players.values():
            water_qty = player.inventory.get("bottled_water", 0)
            bread_qty = player.inventory.get("bread", 0)
            self.assertIn(water_qty, {0, 1})
            self.assertIn(bread_qty, {0, 1})
            other_items = {k: v for k, v in player.inventory.items() if k not in {"bottled_water", "bread"}}
            self.assertEqual({}, other_items)

    def test_start_match_spawns_players_only_in_j_b_s_m(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        allowed = {"J", "B", "S", "M"}
        disallowed = {"X", "Q", "W"}
        for player in room.players.values():
            tile_type = tile_at(player.x, player.y)
            self.assertIn(tile_type, allowed)
            self.assertNotIn(tile_type, disallowed)

    def test_round_lock_when_all_active_submitted(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        for player_id in room.players:
            self.service.submit_action(room, player_id, "USE")
        self.assertTrue(room.match_state.round_locked)

    def test_phase_upkeep_only_once_per_phase(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        target = room.players["host"]
        for player in room.players.values():
            self.service.submit_action(room, player.player_id, "USE")
        self.service.settle_round(room)
        self.assertEqual(99, target.water)
        self.assertEqual(99, target.food)

        for player in room.players.values():
            if player.alive and not player.phase_ended:
                self.service.submit_action(room, player.player_id, "USE")
        self.service.settle_round(room)
        self.assertEqual(99, target.water)
        self.assertEqual(99, target.food)

    def test_all_rest_fast_advance_phase(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)

        for player in room.players.values():
            self.service.submit_action(room, player.player_id, "REST")
        self.service.settle_round(room)

        self.assertEqual(PHASE_NIGHT, room.match_state.phase)
        self.assertEqual(1, room.match_state.round)
        self.assertFalse(room.match_state.phase_base_upkeep_applied)

    def test_auto_advance_phase_when_reaching_phase_round_limit(self) -> None:
        service = MatchService(max_day_phase_rounds=1, max_night_phase_rounds=1)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.start_match(room)

        for player in room.players.values():
            service.submit_action(room, player.player_id, "USE")
        service.settle_round(room)
        self.assertEqual(PHASE_NIGHT, room.match_state.phase)
        self.assertEqual(1, room.match_state.round)

        for player in room.players.values():
            if player.alive and not player.phase_ended:
                service.submit_action(room, player.player_id, "USE")
        service.settle_round(room)
        self.assertEqual(PHASE_DAY, room.match_state.phase)
        self.assertEqual(2, room.match_state.day)
        self.assertEqual(1, room.match_state.round)

    def test_end_mode_all_dead(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)
        for player in room.players.values():
            player.water = 1
            self.service.submit_action(room, player.player_id, "ATTACK")

        self.service.settle_round(room)
        self.assertTrue(room.match_state.game_over)
        self.assertEqual(END_MODE_ALL_DEAD, room.match_state.game_over_reason)

    def test_end_mode_human_all_dead(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_HUMAN_ALL_DEAD)
        self.service.start_match(room)
        for player in room.players.values():
            if player.is_human:
                player.water = 1
                self.service.submit_action(room, player.player_id, "ATTACK")
            else:
                self.service.submit_action(room, player.player_id, "USE")

        self.service.settle_round(room)
        self.assertTrue(room.match_state.game_over)
        self.assertEqual(END_MODE_HUMAN_ALL_DEAD, room.match_state.game_over_reason)

    def test_attack_loot_window_outputs_get_action_and_attack_loot_result(self) -> None:
        room = self.service.create_room("r1", "host", END_MODE_ALL_DEAD)
        self.service.start_match(room)
        attacker = room.players["host"]
        target = room.players["ai_1"]
        attacker.x, attacker.y = 4, 4
        target.x, target.y = 4, 4
        attacker.known_characters.add(target.player_id)
        attacker.water = 100
        attacker.food = 100
        attacker.exposure = 0
        target.water = 10
        target.food = 10
        target.exposure = 90

        self.service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": target.player_id, "loot": {"type": "GET", "items": {"bread": 1}}},
        )
        for player_id in room.players:
            if player_id == attacker.player_id:
                continue
            self.service.submit_action(room, player_id, "REST")

        self.service.settle_round(room)
        loot_window = self.service.get_loot_window_state(room)
        self.assertIsNotNone(loot_window)
        assert loot_window is not None

        private_results = self.service.submit_loot_window_action(
            room,
            loot_window.winner_player_id,
            "GET",
            {"items": {"bread": 1}},
        )
        winner_actions = private_results[loot_window.winner_player_id]["actions"]
        self.assertTrue(any(a["action_type"] == "GET" for a in winner_actions))

        host_actions = private_results["host"]["actions"]
        attack_action = next(a for a in host_actions if a["action_type"] == "ATTACK")
        self.assertEqual("ai_1", attack_action["result"]["target_player_id"])
        self.assertIn("loot", attack_action["result"])
        self.assertEqual("GET", attack_action["result"]["loot"]["type"])

    def test_attack_after_target_rest_in_same_round_is_invalid(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)
        host = room.players["host"]
        attacker = room.players["u2"]
        host.x, host.y = 4, 4
        attacker.x, attacker.y = 4, 4
        attacker.known_characters.add(host.player_id)

        base_time = datetime.now(UTC)
        service.submit_action(room, host.player_id, "REST", {}, server_received_at=base_time)
        service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": host.player_id, "loot": {"type": "TOSS"}},
            server_received_at=base_time + timedelta(seconds=1),
        )

        first_round = service.settle_round(room)
        first_attack = next(a for a in first_round[attacker.player_id]["actions"] if a["action_type"] == "ATTACK")
        self.assertEqual("INVALID_TARGET", first_attack["result"]["outcome"])
        self.assertEqual("TARGET_PHASE_ENDED", first_attack["result"].get("reason"))
        loot_window = service.get_loot_window_state(room)
        self.assertIsNone(loot_window)

        service.submit_action(room, attacker.player_id, "ATTACK", {"target_id": host.player_id, "loot": {"type": "TOSS"}})
        second_round = service.settle_round(room)
        second_attack = next(a for a in second_round[attacker.player_id]["actions"] if a["action_type"] == "ATTACK")
        self.assertEqual("INVALID_TARGET", second_attack["result"]["outcome"])
        self.assertEqual("TARGET_PHASE_ENDED", second_attack["result"].get("reason"))

    def test_attack_after_target_moved_in_same_round_is_invalid(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)
        mover = room.players["host"]
        attacker = room.players["u2"]
        mover.x, mover.y = 4, 4
        attacker.x, attacker.y = 4, 4
        attacker.known_characters.add(mover.player_id)

        base_time = datetime.now(UTC)
        service.submit_action(room, mover.player_id, "MOVE", {"x": 4, "y": 5}, server_received_at=base_time)
        service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": mover.player_id, "loot": {"type": "TOSS"}},
            server_received_at=base_time + timedelta(seconds=1),
        )

        first_round = service.settle_round(room)
        first_attack = next(a for a in first_round[attacker.player_id]["actions"] if a["action_type"] == "ATTACK")
        self.assertEqual("INVALID_TARGET", first_attack["result"]["outcome"])
        self.assertEqual("TARGET_LEFT_TILE", first_attack["result"].get("reason"))

    def test_attack_success_interrupts_later_action_and_applies_interrupt_cost(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        attacker = room.players["u2"]
        target = room.players["host"]
        attacker.x, attacker.y = 4, 4
        target.x, target.y = 4, 4
        attacker.known_characters.add(target.player_id)

        # Force attacker victory deterministically.
        attacker.water, attacker.food, attacker.exposure = 100, 100, 0
        target.water, target.food, target.exposure = 10, 10, 90
        base = datetime.now(UTC)
        service.submit_action(
            room,
            attacker.player_id,
            "ATTACK",
            {"target_id": target.player_id, "loot": {"type": "TOSS"}},
            server_received_at=base,
        )
        service.submit_action(room, target.player_id, "MOVE", {"x": 4, "y": 5}, server_received_at=base + timedelta(seconds=1))

        settled = service.settle_round(room)
        target_move = next(a for a in settled[target.player_id]["actions"] if a["action_type"] == "MOVE")
        self.assertEqual("INTERRUPTED", target_move["result"]["result_type"])
        self.assertEqual("DEFEATED_IN_ATTACK", target_move["result"]["reason"])
        self.assertEqual({"water": 0, "food": 0, "exposure": 0}, target_move["cost"])
        self.assertEqual(target_move["before"], target_move["after"])
        self.assertEqual((4, 4), (target.x, target.y))

    def test_explore_memory_reflects_final_round_snapshot(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        explorer = room.players["host"]
        taker = room.players["u2"]
        explorer.x = taker.x = 4
        explorer.y = taker.y = 4
        key = tile_key(4, 4)
        assert room.match_state is not None
        room.match_state.building_inventory[key] = {"bread": 3}
        taker.explored_tiles.add(key)

        base = datetime.now(UTC)
        service.submit_action(room, explorer.player_id, "EXPLORE", {}, server_received_at=base)
        service.submit_action(
            room,
            taker.player_id,
            "TAKE",
            {"items": {"bread": 1}},
            server_received_at=base + timedelta(seconds=1),
        )

        service.settle_round(room)
        mem = explorer.building_memory[key]
        self.assertEqual(2, mem["resources"].get("bread", 0))
        self.assertIn(taker.player_id, mem["characters"])

    def test_get_then_loser_death_refreshes_winner_memory_by_formula(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        winner = room.players["host"]
        loser = room.players["u2"]
        winner.x = loser.x = 4
        winner.y = loser.y = 4
        key = tile_key(4, 4)
        assert room.match_state is not None
        room.match_state.building_inventory[key] = {"bread": 3}
        winner.explored_tiles.add(key)
        winner.building_memory[key] = {
            "info_state": INFO_STATE_HAS_MEMORY,
            "tile_type": tile_at(4, 4),
            "resources": {"bread": 3},
            "characters": [loser.player_id],
            "updated_at": datetime.now(UTC).isoformat(),
        }

        winner.known_characters.add(loser.player_id)
        winner.water, winner.food, winner.exposure = 100, 100, 0
        loser.water, loser.food, loser.exposure = 1, 1, 90
        loser.inventory = {"bread": 2}

        base = datetime.now(UTC)
        service.submit_action(
            room,
            winner.player_id,
            "ATTACK",
            {"target_id": loser.player_id, "loot": {"type": "GET", "items": {"bread": 1}}},
            server_received_at=base,
        )
        service.submit_action(room, loser.player_id, "REST", {}, server_received_at=base + timedelta(seconds=1))

        service.settle_round(room)
        lw = service.get_loot_window_state(room)
        assert lw is not None
        service.submit_loot_window_action(room, lw.winner_player_id, "GET", {"items": {"bread": 1}})

        self.assertFalse(loser.alive)
        mem = winner.building_memory[key]
        # 规则：新记忆=旧记忆 + (死者背包-拿走)，角色=旧角色-死者。
        self.assertEqual(4, mem["resources"].get("bread", 0))
        self.assertNotIn(loser.player_id, mem["characters"])

    def test_get_without_loser_death_does_not_refresh_winner_memory(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        winner = room.players["host"]
        loser = room.players["u2"]
        winner.x = loser.x = 4
        winner.y = loser.y = 4
        key = tile_key(4, 4)
        assert room.match_state is not None
        room.match_state.building_inventory[key] = {"bread": 3}
        winner.explored_tiles.add(key)
        winner.building_memory[key] = {
            "info_state": INFO_STATE_HAS_MEMORY,
            "tile_type": tile_at(4, 4),
            "resources": {"bread": 3},
            "characters": [loser.player_id],
            "updated_at": datetime.now(UTC).isoformat(),
        }

        winner.known_characters.add(loser.player_id)
        winner.water, winner.food, winner.exposure = 100, 100, 0
        loser.water, loser.food, loser.exposure = 100, 100, 0
        loser.inventory = {"bread": 2}

        base = datetime.now(UTC)
        service.submit_action(
            room,
            winner.player_id,
            "ATTACK",
            {"target_id": loser.player_id, "loot": {"type": "GET", "items": {"bread": 1}}},
            server_received_at=base,
        )
        service.submit_action(room, loser.player_id, "REST", {}, server_received_at=base + timedelta(seconds=1))

        service.settle_round(room)
        lw = service.get_loot_window_state(room)
        assert lw is not None
        service.submit_loot_window_action(room, lw.winner_player_id, "GET", {"items": {"bread": 1}})

        self.assertTrue(loser.alive)
        mem = winner.building_memory[key]
        self.assertEqual(3, mem["resources"].get("bread", 0))
        self.assertIn(loser.player_id, mem["characters"])

    def test_toss_then_loser_death_refreshes_winner_memory_by_formula(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        winner = room.players["host"]
        loser = room.players["u2"]
        winner.x = loser.x = 4
        winner.y = loser.y = 4
        key = tile_key(4, 4)
        assert room.match_state is not None
        room.match_state.building_inventory[key] = {"bread": 3}
        winner.explored_tiles.add(key)
        winner.building_memory[key] = {
            "info_state": INFO_STATE_HAS_MEMORY,
            "tile_type": tile_at(4, 4),
            "resources": {"bread": 3},
            "characters": [loser.player_id],
            "updated_at": datetime.now(UTC).isoformat(),
        }

        winner.known_characters.add(loser.player_id)
        winner.water, winner.food, winner.exposure = 100, 100, 0
        loser.water, loser.food, loser.exposure = 1, 1, 90
        loser.inventory = {"bread": 2}

        base = datetime.now(UTC)
        service.submit_action(
            room,
            winner.player_id,
            "ATTACK",
            {"target_id": loser.player_id, "loot": {"type": "TOSS"}},
            server_received_at=base,
        )
        service.submit_action(room, loser.player_id, "REST", {}, server_received_at=base + timedelta(seconds=1))
        service.settle_round(room)
        lw = service.get_loot_window_state(room)
        assert lw is not None
        service.submit_loot_window_action(room, lw.winner_player_id, "TOSS", {})

        self.assertFalse(loser.alive)
        mem = winner.building_memory[key]
        self.assertEqual(5, mem["resources"].get("bread", 0))
        self.assertNotIn(loser.player_id, mem["characters"])

    def test_toss_without_loser_death_does_not_refresh_winner_memory(self) -> None:
        service = MatchService(room_max_players=2, max_ai_players=0)
        room = service.create_room("r1", "host", END_MODE_ALL_DEAD)
        service.join_room(room, "u2", is_human=True)
        service.start_match(room)

        winner = room.players["host"]
        loser = room.players["u2"]
        winner.x = loser.x = 4
        winner.y = loser.y = 4
        key = tile_key(4, 4)
        assert room.match_state is not None
        room.match_state.building_inventory[key] = {"bread": 3}
        winner.explored_tiles.add(key)
        winner.building_memory[key] = {
            "info_state": INFO_STATE_HAS_MEMORY,
            "tile_type": tile_at(4, 4),
            "resources": {"bread": 3},
            "characters": [loser.player_id],
            "updated_at": datetime.now(UTC).isoformat(),
        }

        winner.known_characters.add(loser.player_id)
        winner.water, winner.food, winner.exposure = 100, 100, 0
        loser.water, loser.food, loser.exposure = 100, 100, 0
        loser.inventory = {"bread": 2}

        base = datetime.now(UTC)
        service.submit_action(
            room,
            winner.player_id,
            "ATTACK",
            {"target_id": loser.player_id, "loot": {"type": "TOSS"}},
            server_received_at=base,
        )
        service.submit_action(room, loser.player_id, "REST", {}, server_received_at=base + timedelta(seconds=1))
        service.settle_round(room)
        lw = service.get_loot_window_state(room)
        assert lw is not None
        service.submit_loot_window_action(room, lw.winner_player_id, "TOSS", {})

        self.assertTrue(loser.alive)
        mem = winner.building_memory[key]
        self.assertEqual(3, mem["resources"].get("bread", 0))
        self.assertIn(loser.player_id, mem["characters"])


if __name__ == "__main__":
    unittest.main()
