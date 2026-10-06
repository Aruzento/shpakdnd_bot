import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.boss_abilities import engine
from app.mini.boss.boss_abilities.catalog import load_ability_catalog, get_ability
from app.mini.boss.catalog import load_boss_catalog
from app.mini.boss.combat import (
    BossCombatError, BossNotYourTurn, advance_expired_turns,
    force_finish_battle, hit_boss, start_battle,
)
from app.mini.boss.matchups import FACTION_CYCLE, faction_multiplier_percent, modify_damage
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    BossError, close_registration, create_boss_event, get_boss,
    list_participants, register_player, select_battle_hero,
)
from app.mini.catalog import load_hero_catalog
from app.mini.db import connect_mini_db
from app.mini.heroes import set_active_hero, sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds


class MatchupTests(unittest.TestCase):
    def test_all_five_advantages(self):
        for index, faction in enumerate(FACTION_CYCLE):
            with self.subTest(faction=faction):
                self.assertEqual(faction_multiplier_percent(faction, FACTION_CYCLE[(index + 1) % 5]), 200)

    def test_all_five_weaknesses(self):
        for index, faction in enumerate(FACTION_CYCLE):
            with self.subTest(faction=faction):
                self.assertEqual(faction_multiplier_percent(FACTION_CYCLE[(index + 1) % 5], faction), 25)

    def test_same_and_neutral(self):
        for faction in (*FACTION_CYCLE, "neutral"):
            self.assertEqual(faction_multiplier_percent(faction, faction), 100)
            self.assertEqual(faction_multiplier_percent(faction, "neutral"), 100)
            self.assertEqual(faction_multiplier_percent("neutral", faction), 100)

    def test_non_adjacent_factions_have_no_bonus(self):
        for index, faction in enumerate(FACTION_CYCLE):
            self.assertEqual(faction_multiplier_percent(faction, FACTION_CYCLE[(index + 2) % 5]), 100)

    def test_unknown_faction_is_an_explicit_error_even_against_neutral(self):
        for value in ("typo", "", None, [], 1):
            with self.assertRaisesRegex(ValueError, "Unknown faction"):
                faction_multiplier_percent(value, "neutral")

    def test_minimum_damage_and_floor_rounding(self):
        self.assertEqual(modify_damage(3, 25), 1)
        self.assertEqual(modify_damage(7, 25), 1)
        self.assertEqual(modify_damage(7, 80), 5)


class BossHookTests(unittest.TestCase):
    def boss(self, key, **values):
        result = {
            "ability_key": key, "ability_state_json": "{}", "ability_config_json": "{}",
            "faction": "commoners", "max_hp": 100, "current_hp": 50,
            "status": "fighting", "reward_shields": 3,
        }
        result.update(values)
        return result

    def test_none_hooks(self):
        boss = self.boss("none")
        self.assertEqual(engine.battle_start(boss)["events"], [])
        self.assertEqual(engine.modify_hero_damage(boss, {}, 7)["damage"], 7)
        self.assertEqual(engine.boss_turn(boss, [])["reward_attacks"], 1)
        self.assertEqual(engine.after_boss_turn(boss)["events"], [])
        self.assertFalse(engine.boss_death(boss)["destroyed_reward"])

    def test_critical_proc_and_nonproc(self):
        boss = self.boss("critical_strike")
        self.assertEqual(engine.boss_turn(boss, [], roller=lambda _: True)["reward_attacks"], 2)
        self.assertEqual(engine.boss_turn(boss, [], roller=lambda _: False)["reward_attacks"], 1)

    def test_banishment_counts_only_fifth_turn_and_uses_active_targets(self):
        boss = self.boss("banishment")
        participants = [{"player_id": 1}, {"player_id": 2}, {"player_id": 3, "banished": 1}]
        for turn in range(1, 6):
            result = engine.boss_turn(
                boss, participants, roller=lambda _: True,
                chooser=lambda active: active[-1],
            )
            boss.update(result["boss_changes"])
            self.assertEqual(len(result["participant_changes"]), 1 if turn == 5 else 0)
        self.assertEqual(result["participant_changes"][0]["player_id"], 2)

    def test_banishment_never_removes_last_player(self):
        boss = self.boss("banishment", ability_state_json='{"boss_turns": 4}')
        result = engine.boss_turn(boss, [{"player_id": 1}], roller=lambda _: True)
        self.assertEqual(result["participant_changes"], [])
        self.assertEqual(result["events"][0]["type"], "banishment_no_target")

    def test_shapeshifter_proc_nonproc_and_config(self):
        boss = self.boss("shapeshifter", ability_config_json='{"target_faction": "beasts"}')
        self.assertEqual(engine.battle_start(boss, roller=lambda _: True)["boss_changes"]["faction"], "beasts")
        self.assertNotIn("faction", engine.battle_start(boss, roller=lambda _: False)["boss_changes"])
        boss.update(engine.battle_start(boss, roller=lambda _: True)["boss_changes"])
        self.assertNotIn("faction", engine.boss_turn(boss, [], roller=lambda _: True)["boss_changes"])

    def test_paralysis_chooser_receives_active_participants(self):
        boss = self.boss("paralysis")
        result = engine.boss_turn(
            boss, [{"player_id": 1, "banished": 1}, {"player_id": 2}],
            chooser=lambda active: active[0],
        )
        self.assertEqual(result["participant_changes"], [{"player_id": 2, "forced_skip_turns": 1}])

    def test_rapier_replaces_attack(self):
        boss = self.boss("rapier")
        proc = engine.boss_turn(boss, [], roller=lambda _: True)
        self.assertTrue(proc["ignore_shields"])
        self.assertEqual(proc["reward_attacks"], 1)
        self.assertFalse(engine.boss_turn(boss, [], roller=lambda _: False)["ignore_shields"])

    def test_hydra_caps_healing_and_never_heals_dead_or_failed_boss(self):
        boss = self.boss("hydra_regeneration", current_hp=99)
        self.assertEqual(engine.after_boss_turn(boss)["boss_changes"]["current_hp"], 100)
        boss["current_hp"] = 100
        self.assertEqual(engine.after_boss_turn(boss)["events"][0]["healed_hp"], 0)
        boss["current_hp"] = 0
        self.assertEqual(engine.after_boss_turn(boss)["boss_changes"], {})
        boss.update(current_hp=50, status="failed")
        self.assertEqual(engine.after_boss_turn(boss)["boss_changes"], {})

    def test_magic_shield_nonmagical_and_magical(self):
        boss = self.boss("magic_shield")
        boss.update(engine.battle_start(boss)["boss_changes"])
        for class_tag, active in (("none", True), ("magical", False)):
            result = engine.modify_hero_damage(boss, {"class_tag": class_tag}, 20)
            self.assertEqual(result["damage"], 0)
            boss.update(result["boss_changes"])
            self.assertEqual(json.loads(boss["ability_state_json"])["shield_active"], active)
        self.assertEqual(engine.modify_hero_damage(boss, {"class_tag": "none"}, 20)["damage"], 20)

    def test_magic_shield_accepts_mage_and_legacy_magical_but_blocks_other_tags(self):
        for tag in ("none", "technical", "warrior", "guardian", "sneaky", "healer", "beast"):
            with self.subTest(tag=tag):
                boss = self.boss("magic_shield", ability_state_json='{"shield_active": true}')
                blocked = engine.modify_hero_damage(boss, {"class_tag": tag}, 20)
                self.assertEqual(blocked["damage"], 0)
                self.assertTrue(json.loads(blocked["boss_changes"]["ability_state_json"])["shield_active"])
        for tag in ("mage", "magical"):
            with self.subTest(tag=tag):
                boss = self.boss("magic_shield", ability_state_json='{"shield_active": true}')
                removed = engine.modify_hero_damage(boss, {"class_tag": tag}, 20)
                self.assertEqual(removed["damage"], 0)
                self.assertFalse(json.loads(removed["boss_changes"]["ability_state_json"])["shield_active"])

    def test_mechanism_technical_and_minimum(self):
        boss = self.boss("mechanism")
        self.assertEqual(engine.modify_hero_damage(boss, {"class_tag": "none"}, 11)["damage"], 8)
        self.assertEqual(engine.modify_hero_damage(boss, {"class_tag": "technical"}, 11)["damage"], 11)
        self.assertEqual(engine.modify_hero_damage(boss, {}, 1)["damage"], 1)

    def test_kamikaze_shields(self):
        self.assertFalse(engine.boss_death(self.boss("kamikaze"))["destroyed_reward"])
        self.assertTrue(engine.boss_death(self.boss("kamikaze", reward_shields=0))["destroyed_reward"])

    def test_unknown_ability_and_bad_runtime_json_are_errors(self):
        with self.assertRaisesRegex(ValueError, "Unknown boss"):
            get_ability("typo")
        with self.assertRaisesRegex(ValueError, "object"):
            engine.boss_turn(self.boss("none", ability_state_json="[]"), [])


class CombatV2IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "combat.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        sync_hero_catalog(self.db)
        self.world = sync_configured_mini_worlds(self.db)[0]["id"]
        self.players = [
            create_mini_player(self.world, 70001 + n, f"@v2_{n}", f"V2 {n}", self.db)
            for n in range(2)
        ]
        self.now = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
        with connect_mini_db(self.db) as conn:
            self.villager = conn.execute("SELECT id FROM mini_heroes WHERE code = 'Villager'").fetchone()[0]
            self.hero = conn.execute(
                """INSERT INTO mini_heroes (
                    code, name, rarity, race, class_name, attack, passive_key
                ) VALUES ('v2_test', 'V2 Hero', 'rare', 'human', 'test', 10, 'none')"""
            ).lastrowid
            for player in self.players:
                for hero in (self.villager, self.hero):
                    conn.execute(
                        "INSERT INTO mini_player_heroes (player_id, hero_id) VALUES (?, ?)",
                        (player["id"], hero),
                    )
                conn.execute("UPDATE mini_players SET active_hero_id = ? WHERE id = ?", (self.villager, player["id"]))
        self.boss = create_boss_event(self.world, "training_golem", 999, self.db)
        # Synthetic ordinary boss keeps mechanics tests independent of live assignments.
        self.update_boss(min_players=2, max_hp=1000, current_hp=1000, reward_coins=60,
                         faction="commoners", ability_key="none", features_json="[]",
                         ability_config_json="{}")
        for player in self.players:
            register_player(self.boss["id"], player["id"], self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def update_boss(self, **changes):
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_bosses SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE id = ?",
                (*changes.values(), self.boss["id"]),
            )

    def update_hero(self, **changes):
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_heroes SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE id = ?",
                (*changes.values(), self.hero),
            )

    def start(self, ability="none"):
        self.update_boss(ability_key=ability)
        close_registration(self.boss["id"], self.db)
        return start_battle(self.boss["id"], now=self.now, db_path=self.db)

    def hit(self, index, **kwargs):
        return hit_boss(self.boss["id"], self.players[index]["id"], now=self.now, db_path=self.db, **kwargs)

    def round(self):
        self.hit(0)
        return self.hit(1)

    def participant(self, index):
        with connect_mini_db(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute(
                "SELECT * FROM mini_boss_participants WHERE boss_id = ? AND player_id = ?",
                (self.boss["id"], self.players[index]["id"]),
            ).fetchone())

    def events(self):
        with connect_mini_db(self.db) as conn:
            return [json.loads(row[0]) for row in conn.execute(
                "SELECT event_json FROM mini_boss_events WHERE boss_id = ? ORDER BY id",
                (self.boss["id"],),
            )]

    def test_selected_hero_and_active_changes_before_and_after_start(self):
        for player in self.players:
            select_battle_hero(self.boss["id"], player["id"], self.hero, self.db)
        set_active_hero(self.players[0]["id"], self.villager, self.db)
        state = self.start()
        self.assertEqual([p["battle_hero_id"] for p in state["participants"]], [self.hero, self.hero])
        set_active_hero(self.players[0]["id"], self.hero, self.db)
        self.assertEqual(self.hit(0)["damage"], 10)
        set_active_hero(self.players[0]["id"], self.villager, self.db)
        self.hit(1)
        self.assertEqual(self.hit(0)["damage"], 10)

    def test_registration_locks_initial_hero_and_ready_allows_change(self):
        set_active_hero(self.players[0]["id"], self.hero, self.db)
        self.assertEqual(self.participant(0)["hero_id"], self.villager)
        close_registration(self.boss["id"], self.db)
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.assertEqual(self.participant(0)["hero_id"], self.hero)

    def test_selection_rejects_unowned_and_unregistered_and_started(self):
        outsider = create_mini_player(self.world, 70003, "@outsider", "Outsider", self.db)
        with self.assertRaisesRegex(BossError, "зарегистрирован"):
            select_battle_hero(self.boss["id"], outsider["id"], self.hero, self.db)
        with connect_mini_db(self.db) as conn:
            conn.execute("DELETE FROM mini_player_heroes WHERE player_id = ? AND hero_id = ?", (self.players[0]["id"], self.hero))
        with self.assertRaisesRegex(BossError, "коллекции"):
            select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.start()
        with self.assertRaisesRegex(BossError, "начала"):
            select_battle_hero(self.boss["id"], self.players[1]["id"], self.hero, self.db)

    def test_legacy_null_hero_falls_back_once_at_start(self):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET hero_id = NULL WHERE boss_id = ?", (self.boss["id"],))
        set_active_hero(self.players[0]["id"], self.hero, self.db)
        self.start()
        self.assertEqual(self.participant(0)["hero_id"], self.hero)
        set_active_hero(self.players[0]["id"], self.villager, self.db)
        self.assertEqual(self.hit(0)["damage"], 10)

    def test_start_rejects_selected_hero_without_ownership(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        with connect_mini_db(self.db) as conn:
            conn.execute("DELETE FROM mini_player_heroes WHERE player_id = ? AND hero_id = ?", (self.players[0]["id"], self.hero))
        with self.assertRaises(BossCombatError):
            self.start()

    def test_damage_pipeline_stars_passive_faction_mechanism_potion(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_hero(passive_key="strong_start", faction="commoners")
        self.update_boss(faction="beasts")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_player_heroes SET stars = 1 WHERE player_id = ? AND hero_id = ?", (self.players[0]["id"], self.hero))
        self.start("mechanism")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET damage_bonus_percent = 10 WHERE boss_id = ? AND player_id = ?", (self.boss["id"], self.players[0]["id"]))
        result = self.hit(0)
        for field, value in {
            "base_damage": 15, "ability_damage": 30, "faction_multiplier_percent": 200,
            "damage_after_faction": 60, "boss_modifier_percent": 80,
            "damage_before_external_bonus": 48, "damage_bonus_percent": 10, "damage": 53,
        }.items():
            self.assertEqual(result[field], value, field)

    def test_hero_combat_traits_are_snapshotted(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_boss(faction="beasts")
        self.start("mechanism")
        self.update_hero(faction="beasts", class_tag="technical", attack=9999)
        result = self.hit(0)
        self.assertEqual(result["damage"], 16)
        self.assertEqual(result["faction_multiplier_percent"], 200)

    def test_none_matches_previous_balance(self):
        self.start()
        result = self.round()
        self.assertEqual(result["damage"], 3)
        self.assertEqual(result["faction_multiplier_percent"], 100)
        self.assertEqual(result["state"]["boss"]["reward_shields"], 2)
        self.assertEqual(result["reward_event"]["boss_events"], [])

    def test_paralysis_is_automatic_and_distinct_from_timeout(self):
        self.start("paralysis")
        with patch.object(engine, "_choose_participant", side_effect=lambda ps: ps[0]):
            result = self.round()
        self.assertEqual(result["state"]["current"]["player_id"], self.players[1]["id"])
        self.assertEqual(self.participant(0)["forced_skip_turns"], 0)
        self.assertEqual(self.participant(0)["skipped_turns"], 0)
        self.assertEqual(self.participant(0)["hit_count"], 1)
        self.assertEqual(result["forced_skip_events"][0]["type"], "paralysis_skip")
        self.assertEqual([e["type"] for e in self.events()], ["paralysis", "paralysis_skip"])

    def test_paralysis_can_target_later_position_and_is_consumed_once(self):
        self.start("paralysis")
        with patch.object(engine, "_choose_participant", side_effect=lambda ps: ps[-1]):
            self.round()
        self.assertEqual(self.participant(1)["forced_skip_turns"], 1)
        # Switch to none to isolate the one pending skip.
        self.update_boss(ability_key="none")
        result = self.hit(0)
        self.assertEqual(self.participant(1)["forced_skip_turns"], 0)
        self.assertEqual(result["state"]["boss"]["current_round"], 3)
        self.assertEqual(result["state"]["current"]["player_id"], self.players[0]["id"])

    def test_critical_strike_performs_two_sequential_attacks(self):
        self.start("critical_strike")
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.round()
        self.assertEqual(result["state"]["boss"]["reward_shields"], 1)
        self.assertEqual(len(result["reward_event"]["attacks"]), 2)

    def test_critical_strike_stops_at_first_defeat(self):
        self.start("critical_strike")
        self.update_boss(reward_shields=0, reward_percent=10)
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.round()
        self.assertEqual(len(result["reward_event"]["attacks"]), 1)
        self.assertEqual(result["state"]["boss"]["status"], "failed")
        self.assertEqual(result["rewards"]["shards_each"], 6)

    def test_banishment_keeps_registration_statistics_and_rewards(self):
        self.start("banishment")
        with patch.object(engine, "_roll_success", return_value=True), patch.object(engine, "_choose_participant", side_effect=lambda ps: ps[0]):
            for _ in range(5):
                result = self.round()
        self.assertEqual(result["state"]["current"]["player_id"], self.players[1]["id"])
        self.assertEqual(self.participant(0)["banished"], 1)
        self.assertEqual(self.participant(0)["hit_count"], 5)
        self.assertEqual(self.participant(0)["total_damage"], 15)
        self.update_boss(current_hp=1)
        result = self.hit(1)
        self.assertEqual(result["rewards"]["players"], 2)
        self.assertEqual(len(result["state"]["participants"]), 2)

    def test_banishment_cannot_exile_last_active_player(self):
        self.start("banishment")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET banished = 1 WHERE boss_id = ? AND player_id = ?", (self.boss["id"], self.players[1]["id"]))
        self.update_boss(ability_state_json='{"boss_turns": 4}')
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.hit(0)
        self.assertEqual(result["state"]["current"]["player_id"], self.players[0]["id"])
        self.assertEqual(result["reward_event"]["boss_events"][0]["type"], "banishment_no_target")

    def test_shapeshifter_runs_once_and_survives_reinitialization(self):
        with patch.object(engine, "_roll_success", return_value=True) as roller:
            state = self.start("shapeshifter")
            self.round()
            init_boss_db(self.db)
        self.assertEqual(roller.call_count, 1)
        self.assertEqual(state["boss"]["faction"], "dark")
        self.assertEqual(get_boss(self.boss["id"], self.db)["faction"], "dark")

    def test_rapier_ignores_shields_and_uses_existing_failure_rewards(self):
        self.start("rapier")
        self.update_boss(reward_percent=10)
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.round()
        self.assertEqual(result["state"]["boss"]["reward_shields"], 3)
        self.assertEqual(result["state"]["boss"]["status"], "failed")
        self.assertEqual(result["rewards"]["shards_each"], 6)
        self.assertEqual(len(result["reward_event"]["attacks"]), 1)

    def test_rapier_nonproc_uses_normal_shield_attack(self):
        self.start("rapier")
        with patch.object(engine, "_roll_success", return_value=False):
            result = self.round()
        self.assertEqual(result["state"]["boss"]["reward_shields"], 2)
        self.assertEqual(result["state"]["boss"]["reward_percent"], 100)

    def test_hydra_heals_after_boss_turn_but_not_death(self):
        self.start("hydra_regeneration")
        self.update_boss(current_hp=500)
        result = self.round()
        self.assertEqual(result["state"]["boss"]["current_hp"], 594)
        self.update_boss(current_hp=1)
        result = self.hit(0)
        self.assertEqual(result["state"]["boss"]["current_hp"], 0)
        self.assertEqual(result["state"]["boss"]["status"], "defeated")

    def test_kamikaze_unshielded_uses_consolation_economy(self):
        self.start("kamikaze")
        self.update_boss(current_hp=1, reward_shields=0)
        result = self.hit(0)
        self.assertEqual(result["state"]["boss"]["status"], "failed")
        self.assertEqual(result["rewards"]["coins_each"], 0)
        self.assertEqual(result["rewards"]["shards_each"], 6)
        self.assertEqual(result["boss_events"][0]["type"], "kamikaze_destroyed_reward")

    def test_kamikaze_shield_preserves_victory(self):
        self.start("kamikaze")
        self.update_boss(current_hp=1)
        result = self.hit(0)
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertEqual(result["rewards"]["coins_each"], 60)
        self.assertEqual(result["boss_events"][0]["type"], "kamikaze_absorbed")

    def test_magic_shield_blocks_potion_and_magical_removal_attack(self):
        self.update_hero(class_tag="magical")
        select_battle_hero(self.boss["id"], self.players[1]["id"], self.hero, self.db)
        self.start("magic_shield")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET damage_bonus_percent = 10 WHERE boss_id = ?", (self.boss["id"],))
        self.assertEqual(self.hit(0)["damage"], 0)
        self.assertTrue(json.loads(get_boss(self.boss["id"], self.db)["ability_state_json"])["shield_active"])
        self.assertEqual(self.hit(1)["damage"], 0)
        init_boss_db(self.db)
        self.assertFalse(json.loads(get_boss(self.boss["id"], self.db)["ability_state_json"])["shield_active"])
        self.assertEqual(self.hit(0)["damage"], 4)

    def test_mockery_skips_every_hook_and_boss_turn_counter(self):
        for ability in ("paralysis", "critical_strike", "banishment", "shapeshifter",
                        "rapier", "hydra_regeneration", "magic_shield", "mechanism", "kamikaze"):
            with self.subTest(ability=ability):
                if get_boss(self.boss["id"], self.db)["status"] == "announced":
                    self.start()
                self.update_boss(ability_key=ability, current_turn_position=1, current_hp=500,
                                 reward_shields=3, reward_percent=100, ability_state_json='{"boss_turns": 4}')
                with connect_mini_db(self.db) as conn:
                    conn.execute(
                        """INSERT INTO mini_boss_actions (boss_id, player_id, round_number, action_type)
                           VALUES (?, ?, 1, 'boss_skip_queued')""",
                        (self.boss["id"], self.players[0]["id"]),
                    )
                with patch.object(engine, "boss_turn", side_effect=AssertionError("boss hook ran")), patch.object(engine, "after_boss_turn", side_effect=AssertionError("after hook ran")):
                    result = self.round()
                self.assertEqual(result["reward_event"]["type"], "boss_skip")
                self.assertEqual(result["state"]["boss"]["reward_shields"], 3)
                self.assertEqual(json.loads(result["state"]["boss"]["ability_state_json"])["boss_turns"], 4)

    def test_emergency_salvage_runs_once_per_real_critical_attack(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_hero(passive_key="emergency_salvage")
        self.start("critical_strike")
        with patch.object(engine, "_roll_success", return_value=True), patch("app.mini.boss.abilities.engine._roll_success", return_value=True):
            result = self.round()
        self.assertEqual(len(result["reward_event"]["passive_events"]), 2)
        with connect_mini_db(self.db) as conn:
            shards = conn.execute("SELECT shards FROM mini_players WHERE id = ?", (self.players[0]["id"],)).fetchone()[0]
        self.assertEqual(shards, 4)

    def test_admin_finish_still_rewards_all_even_with_kamikaze_and_no_shields(self):
        self.start("kamikaze")
        self.update_boss(reward_shields=0)
        result = force_finish_battle(self.boss["id"], now=self.now, db_path=self.db)
        self.assertEqual(result["state"]["boss"]["battle_result"], "admin_victory")
        self.assertEqual(result["rewards"]["players"], 2)

    def test_stale_turn_token_cannot_replay_attack(self):
        self.start()
        self.hit(0, expected_round=1, expected_position=1)
        with self.assertRaises(BossNotYourTurn):
            self.hit(0, expected_round=1, expected_position=1)
        self.hit(1)
        with self.assertRaises(BossNotYourTurn):
            self.hit(0, expected_round=1, expected_position=1)
        self.assertEqual(self.participant(0)["hit_count"], 1)

    def test_timeout_and_watcher_advance_past_banished_and_paralyzed(self):
        self.start()
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET forced_skip_turns = 1 WHERE boss_id = ? AND player_id = ?", (self.boss["id"], self.players[0]["id"]))
        result = advance_expired_turns(self.boss["id"], now=self.now, db_path=self.db)
        self.assertTrue(result["changed"])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["state"]["current"]["player_id"], self.players[1]["id"])
        result = advance_expired_turns(self.boss["id"], now=self.now + timedelta(hours=4), db_path=self.db)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["state"]["boss"]["current_round"], 2)

    def test_start_clears_runtime_only_on_new_battle(self):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET forced_skip_turns = 2, banished = 1 WHERE boss_id = ?", (self.boss["id"],))
        self.start("magic_shield")
        self.assertEqual(self.participant(0)["forced_skip_turns"], 0)
        self.assertEqual(self.participant(0)["banished"], 0)
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET forced_skip_turns = 1, banished = 1 WHERE boss_id = ? AND player_id = ?", (self.boss["id"], self.players[1]["id"]))
        before = self.participant(1)
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.assertEqual(self.participant(1), before)
        with self.assertRaises(BossCombatError):
            start_battle(self.boss["id"], now=self.now, db_path=self.db)

    def test_complete_selected_hero_lifecycle_and_normal_rewards(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.villager, self.db)
        select_battle_hero(self.boss["id"], self.players[1]["id"], self.hero, self.db)
        set_active_hero(self.players[0]["id"], self.hero, self.db)
        self.update_boss(max_hp=26, current_hp=26)
        state = self.start()
        self.assertEqual([p["battle_hero_id"] for p in state["participants"]], [self.villager, self.hero])
        first = self.hit(0)
        self.assertEqual(first["damage"], 3)
        second = self.hit(1)
        self.assertEqual(second["damage"], 10)
        self.assertEqual(second["state"]["boss"]["current_round"], 2)
        self.hit(0)
        victory = self.hit(1)
        self.assertEqual(victory["state"]["boss"]["status"], "defeated")
        self.assertEqual(victory["rewards"]["players"], 2)
        self.assertEqual(victory["rewards"]["coins_each"], 60)

    def test_legacy_empty_snapshot_never_switches_to_active_hero(self):
        self.start()
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET hero_snapshot_json = '{}' WHERE boss_id = ?", (self.boss["id"],))
        set_active_hero(self.players[0]["id"], self.hero, self.db)
        self.assertEqual(self.hit(0)["damage"], 3)

    def test_concurrent_same_token_only_applies_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.start()
        barrier = Barrier(2)

        def attempt():
            barrier.wait(timeout=5)
            try:
                self.hit(0, expected_round=1, expected_position=1)
                return True
            except BossNotYourTurn:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(attempt) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=20) for f in attempts), [False, True])
        self.assertEqual(self.participant(0)["hit_count"], 1)

    def test_boss_snapshot_retains_traits_without_catalog_refresh(self):
        boss = get_boss(self.boss["id"], self.db)
        self.assertEqual((boss["faction"], boss["ability_key"], json.loads(boss["features_json"])), ("commoners", "none", []))
        self.update_boss(faction="neutral", features_json='["armored"]', ability_key="mechanism")
        init_boss_db(self.db)
        boss = get_boss(self.boss["id"], self.db)
        self.assertEqual((boss["faction"], boss["ability_key"], json.loads(boss["features_json"])), ("neutral", "mechanism", ["armored"]))


class CombatV2CatalogTests(unittest.TestCase):
    def test_current_hero_and_boss_traits_are_valid(self):
        from app.mini.boss.matchups import FACTIONS
        heroes = load_hero_catalog()["heroes"]
        self.assertEqual(len({hero["code"] for hero in heroes}), len(heroes))
        for hero in heroes:
            with self.subTest(hero=hero["code"]):
                self.assertIn(hero["faction"], FACTIONS)
                self.assertIn(hero["damage_type"], {"slashing", "piercing", "bludgeoning", "magic"})
                self.assertIn(hero["attack_range"], {"melee", "ranged"})
                self.assertIsInstance(hero["class_tag"], str)
                self.assertTrue(hero["class_tag"])
                self.assertIsInstance(hero["special_trait"], str)
                self.assertTrue(hero["special_trait"])
        expected = {
            "training_golem": ("monsters", "magic_shield"),
            "graveyard_warden": ("dark", "banishment"),
            "swamp_hydra": ("monsters", "hydra_regeneration"),
            "iron_juggernaut": ("warriors", "none"),
            "crimson_vampire": ("dark", "shapeshifter"),
            "wild_berserker": ("beasts", "critical_strike"),
            "fallen_sun_champion": ("dark", "rapier"),
        }
        bosses = load_boss_catalog()["bosses"]
        self.assertEqual({boss["code"] for boss in bosses}, set(expected))
        for boss in bosses:
            with self.subTest(boss=boss["code"]):
                self.assertEqual((boss["faction"], boss["ability_key"]), expected[boss["code"]])
                self.assertIsInstance(boss["features"], list)
                self.assertTrue(boss["ability_text"])

    def test_hero_validation_rejects_missing_and_invalid_traits_and_passive(self):
        data = load_hero_catalog()
        for field, value in (
            ("faction", "unknown"), ("damage_type", "fire"), ("attack_range", "near"),
            ("class_tag", []), ("special_trait", None), ("passive_key", "typo"),
        ):
            broken = copy.deepcopy(data)
            broken["heroes"][0][field] = value
            with self.subTest(field=field), patch("app.mini.catalog._read_json", return_value=broken):
                with self.assertRaisesRegex(ValueError, field):
                    load_hero_catalog()
        for field in ("faction", "damage_type", "class_tag", "attack_range", "special_trait"):
            broken = copy.deepcopy(data)
            del broken["heroes"][0][field]
            with patch("app.mini.catalog._read_json", return_value=broken):
                from app.mini.combat.tags import LEGACY_HERO_TRAITS
                self.assertEqual(load_hero_catalog()["heroes"][0][field], LEGACY_HERO_TRAITS[field])

    def test_open_string_tags_are_valid(self):
        data = load_hero_catalog()
        data["heroes"][0].update(class_tag="new_class", special_trait="new_trait")
        with patch("app.mini.catalog._read_json", return_value=data):
            self.assertEqual(load_hero_catalog()["heroes"][0]["class_tag"], "new_class")

    def test_boss_validation_rejects_unknown_multiple_missing_and_bad_features(self):
        data = load_boss_catalog()
        for field, value in (
            ("faction", "unknown"), ("ability_key", "unknown"),
            ("ability_key", ["none", "mechanism"]), ("features", {}),
            ("features", [123]), ("ability_config", {"target_faction": "typo"}),
        ):
            broken = copy.deepcopy(data)
            broken["bosses"][0][field] = value
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "bosses.json"
                path.write_text(json.dumps(broken), encoding="utf-8")
                with patch("app.mini.boss.catalog.BOSSES_JSON", path), self.assertRaises(ValueError):
                    load_boss_catalog()

    def test_bad_json_is_a_clear_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text("{broken", encoding="utf-8")
            with patch("app.mini.boss.boss_abilities.catalog.ABILITIES_JSON", path), self.assertRaisesRegex(ValueError, "JSON"):
                load_ability_catalog()
            with patch("app.mini.catalog.HEROES_PATH", path), self.assertRaisesRegex(ValueError, "JSON"):
                load_hero_catalog()
            with patch("app.mini.boss.catalog.BOSSES_JSON", path), self.assertRaisesRegex(ValueError, "JSON"):
                load_boss_catalog()

    def test_create_boss_copies_faction_ability_config_and_features(self):
        from app.mini.boss.catalog import get_boss_template
        template = get_boss_template("training_golem")
        template.update(faction="beasts", ability_key="shapeshifter", ability_text="Test",
                        ability_config={"target_faction": "warriors"}, features=["flying"])
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "snapshot.db"
            init_mini_db(db)
            init_boss_db(db)
            world = sync_configured_mini_worlds(db)[0]["id"]
            with patch("app.mini.boss.service.get_boss_template", return_value=template):
                boss = create_boss_event(world, "training_golem", 999, db)
            self.assertEqual(boss["faction"], "beasts")
            self.assertEqual(boss["ability_key"], "shapeshifter")
            self.assertEqual(boss["ability_text"], "Test")
            self.assertEqual(json.loads(boss["features_json"]), ["flying"])
            self.assertEqual(json.loads(boss["ability_config_json"]), {"target_faction": "warriors"})

    def test_sync_hero_traits_updates_all_five_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "sync.db"
            init_mini_db(db)
            sync_hero_catalog(db)
            data = load_hero_catalog()
            data["heroes"][0].update(faction="dark", damage_type="magic", class_tag="magical", attack_range="ranged", special_trait="flying")
            with patch("app.mini.heroes.load_hero_catalog", return_value=data):
                sync_hero_catalog(db)
            with connect_mini_db(db) as conn:
                row = conn.execute(
                    "SELECT faction, damage_type, class_tag, attack_range, special_trait FROM mini_heroes WHERE code = ?",
                    (data["heroes"][0]["code"],),
                ).fetchone()
            self.assertEqual(row, ("dark", "magic", "magical", "ranged", "flying"))
