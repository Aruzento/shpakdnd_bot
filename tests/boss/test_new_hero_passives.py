import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.admin_grants import grant_mini_hero
from app.mini.boss import combat
from app.mini.boss.abilities import engine
from app.mini.boss.catalog import load_boss_catalog
from app.mini.boss.notices import combat_event_lines, timeout_event_lines
from app.mini.boss.public import format_public_turn
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import close_registration, create_boss_event, register_player
from app.mini.catalog import load_hero_catalog, validate_content
from app.mini.content_safety import validate_combat_content
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.presentation import hero_trait_lines
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds

HERO_CODES = {
    "rune_spark": "winged_rune_fox", "unstable_shell": "cursed_bombardier_construct",
    "infernal_guard": "abyssal_gatekeeper", "holy_relic": "wandering_relic_hunter",
    "battle_echo": "renkai", "blood_frenzy": "gromm_bloody_thunder",
    "emergency_salvage": "panic_dungeon_engineer", "mockery": "Lasar", "none": "Villager",
}


class NewHeroEffectUnitTests(unittest.TestCase):
    def attack(self, key, number=1, **kwargs):
        return engine.resolve_attack(key, base_damage=100, hit_number=number,
                                     boss_hp_before=1000, boss_max_hp=1000, **kwargs)

    def test_rune_spark_uses_twenty_percent_only_with_active_magic_shield(self):
        with patch.object(engine, "_roll_success", return_value=True) as roll:
            result = self.attack("rune_spark", boss_ability_key="magic_shield", boss_state={"shield_active": True})
        roll.assert_called_once_with(20.0)
        self.assertTrue(result["remove_magic_shield"])
        self.assertEqual(len(result["events"]), 1)
        for ability, state in (("none", {"shield_active": True}), ("magic_shield", {"shield_active": False})):
            with patch.object(engine, "_roll_success") as roll:
                result = self.attack("rune_spark", boss_ability_key=ability, boss_state=state)
                self.assertFalse(result["remove_magic_shield"])
                self.assertEqual(result["events"], [])
                roll.assert_not_called()

    def test_guard_cadence_counts_only_own_hit_number_and_keeps_other_state(self):
        for key, every in (("unstable_shell", 4), ("infernal_guard", 3)):
            for number in range(1, every * 2 + 1):
                with self.subTest(key=key, number=number):
                    result = engine.resolve_after_attack(key, hit_number=number, actual_hp_damage=0, state={"future": 7})
                    self.assertEqual(result["state"].get("reward_guard_charges", 0), int(number % every == 0))
                    self.assertEqual(result["state"]["future"], 7)

    def test_echo_stores_half_actual_damage_and_ignores_zero_damage(self):
        for actual, expected in ((1, 1), (3, 1), (101, 50), (0, 0)):
            result = engine.resolve_after_attack("battle_echo", hit_number=3, actual_hp_damage=actual, state={})
            self.assertEqual(result["state"].get("pending_echo_damage", 0), expected)
        result = engine.resolve_after_attack("battle_echo", hit_number=2, actual_hp_damage=100, state={})
        self.assertEqual(result["state"], {})

    def test_echo_turn_start_consumes_only_pending_damage(self):
        source = {"pending_echo_damage": 123, "reward_guard_charges": 2}
        result = engine.resolve_turn_start(state=source)
        self.assertEqual(result, {"damage": 123, "state": {"reward_guard_charges": 2}})
        self.assertEqual(source["pending_echo_damage"], 123)
        self.assertEqual(engine.resolve_turn_start(state=result["state"])["damage"], 0)

    def test_frenzy_cycles_without_recursion(self):
        for number, expected in enumerate((100, 115, 130, 145, 100, 115, 130, 145), 1):
            result = self.attack("blood_frenzy", number)
            self.assertEqual(result["damage"], expected)
            self.assertEqual(result["extra_attacks"], int(number % 4 == 0))

    def test_holy_relic_uses_fifteen_percent_and_three_shards(self):
        with patch.object(engine, "_roll_success", return_value=True) as roll:
            result = engine.resolve_victory("holy_relic")
        roll.assert_called_once_with(15.0)
        self.assertEqual(result["bonus_shards"], 3)
        self.assertEqual(result["events"][0]["shards"], 3)
        self.assertEqual(engine.resolve_victory("holy_relic", roller=lambda _: False)["bonus_shards"], 0)

    def test_current_content_is_valid_without_new_heroes_or_tags(self):
        catalog = load_hero_catalog()
        heroes = {h["code"]: h for h in catalog["heroes"]}
        for code in HERO_CODES.values():
            self.assertIn(code, heroes)
        self.assertEqual(heroes["wandering_relic_hunter"]["damage_type"], "magic")
        self.assertEqual(heroes["wandering_relic_hunter"]["special_trait"], "holy")
        self.assertEqual(heroes["gromm_bloody_thunder"]["special_trait"], "demon")
        self.assertIn("Следопыт", hero_trait_lines(heroes["wandering_relic_hunter"])[0])
        self.assertFalse(heroes["panic_dungeon_engineer"]["active"])
        validate_content()
        validate_combat_content()


class NewHeroPassiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        sync_hero_catalog(self.db)
        self.world = sync_configured_mini_worlds(self.db)[0]["id"]
        self.players = [create_mini_player(self.world, 980001+i, f"@new_{i}", f"Игрок {i}", self.db) for i in range(2)]
        self.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)

    def update(self, table, **fields):
        with connect_mini_db(self.db) as conn:
            conn.execute(f"UPDATE {table} SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
                         (*fields.values(), self.boss["id"]))

    def participant_update(self, index, **fields):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET " + ", ".join(f"{k} = ?" for k in fields)
                         + " WHERE boss_id = ? AND player_id = ?",
                         (*fields.values(), self.boss["id"], self.players[index]["id"]))

    def participant(self, index):
        with connect_mini_db(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM mini_boss_participants WHERE boss_id = ? AND player_id = ?",
                                    (self.boss["id"], self.players[index]["id"])).fetchone())

    def runtime(self, index):
        return json.loads(self.participant(index)["hero_state_json"])

    def seed_runtime(self, index, **state):
        self.participant_update(index, hero_state_json=json.dumps(state))

    def shards(self, index):
        with connect_mini_db(self.db) as conn:
            return conn.execute("SELECT shards FROM mini_players WHERE id = ?", (self.players[index]["id"],)).fetchone()[0]

    def events(self, kind):
        with connect_mini_db(self.db) as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT event_json FROM mini_boss_events WHERE boss_id = ? AND event_type = ? ORDER BY id", (self.boss["id"], kind))]

    def start(self, passive="none", second="none", ability="none", *, attack=100, faction="commoners", class_tag="none", hp=100000):
        for index, key in enumerate((passive, second)):
            hero = grant_mini_hero(self.players[index]["id"], HERO_CODES[key], self.db)
            with connect_mini_db(self.db) as conn:
                conn.execute("UPDATE mini_heroes SET attack = ?, passive_key = ?, faction = ?, class_tag = ? WHERE id = ?",
                             (attack, key, faction, class_tag, hero["id"]))
        # grant sync may restore the first live hero; set both deterministic fixtures after grants.
        with connect_mini_db(self.db) as conn:
            for index, key in enumerate((passive, second)):
                conn.execute("UPDATE mini_heroes SET attack = ?, passive_key = ?, faction = ?, class_tag = ?, special_trait = 'none' WHERE code = ?",
                             (attack, key, faction, class_tag, HERO_CODES[key]))
        self.boss = create_boss_event(self.world, "training_golem", 999, self.db)
        self.update("mini_bosses", min_players=2, max_hp=hp, current_hp=hp, ability_key=ability,
                    faction="commoners", features_json="[]", reward_items_json="[]", reward_coins=60,
                    reward_shields=100, reward_shields_max=100, ability_config_json="{}")
        for player in self.players:
            register_player(self.boss["id"], player["id"], self.db)
        close_registration(self.boss["id"], self.db)
        return combat.start_battle(self.boss["id"], now=self.now, db_path=self.db)

    def hit(self, index, **kwargs):
        return combat.hit_boss(self.boss["id"], self.players[index]["id"], now=self.now, db_path=self.db, **kwargs)

    def advance(self, now=None):
        return combat.advance_expired_turns(self.boss["id"], now=now or self.now, db_path=self.db)

    def round(self):
        self.hit(0)
        return self.hit(1)

    def test_rune_proc_removes_shield_once_with_zero_hp_damage_and_no_duplicate_event(self):
        self.start("rune_spark", ability="magic_shield", class_tag="mage")
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.hit(0)
        self.assertEqual(result["damage"], 0)
        self.assertEqual(result["state"]["boss"]["current_hp"], 100000)
        self.assertEqual([e["type"] for e in result["passive_events"]], ["chance_remove_magic_shield"])
        self.assertEqual(result["boss_events"], [])
        self.assertFalse(json.loads(result["state"]["boss"]["ability_state_json"])["shield_active"])
        self.hit(1)
        with patch.object(engine, "_roll_success") as roll:
            self.assertEqual(self.hit(0)["damage"], 100)
            roll.assert_not_called()

    def test_rune_nonproc_retains_normal_mage_removal(self):
        self.start("rune_spark", ability="magic_shield", class_tag="mage")
        with patch.object(engine, "_roll_success", return_value=False):
            result = self.hit(0)
        self.assertEqual(result["damage"], 0)
        self.assertEqual([e["type"] for e in result["boss_events"]], ["magic_shield_removed"])
        self.assertEqual(result["passive_events"], [])

    def test_rune_nonmage_nonproc_remains_blocked(self):
        self.start("rune_spark", ability="magic_shield")
        with patch.object(engine, "_roll_success", return_value=False):
            result = self.hit(0)
        self.assertEqual(result["damage"], 0)
        self.assertTrue(json.loads(result["state"]["boss"]["ability_state_json"])["shield_active"])

    def test_rune_has_no_effect_without_magic_shield(self):
        self.start("rune_spark")
        with patch.object(engine, "_roll_success") as roll:
            self.assertEqual(self.hit(0)["damage"], 100)
            roll.assert_not_called()

    def assert_guard_cadence(self, passive, every):
        self.start(passive)
        for _ in range(every - 1):
            self.round()
            self.assertEqual(self.runtime(0).get("reward_guard_charges", 0), 0)
        result = self.hit(0)
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 1)
        init_boss_db(self.db)
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 1)
        before = result["state"]["boss"]
        result = self.hit(1)
        self.assertEqual(result["reward_event"]["type"], "reward_guard")
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 0)
        self.assertEqual(result["state"]["boss"]["reward_shields"], before["reward_shields"])
        self.assertEqual(result["state"]["boss"]["reward_percent"], before["reward_percent"])
        self.assertIn("@new_0 защитил награду", " ".join(combat_event_lines(result)))

    def test_unstable_shell_creates_persistent_guard_every_fourth_own_hit(self):
        self.assert_guard_cadence("unstable_shell", 4)

    def test_infernal_guard_creates_persistent_guard_every_third_own_hit(self):
        self.assert_guard_cadence("infernal_guard", 3)

    def test_mockery_skips_salvage_and_does_not_consume_guard(self):
        self.start("emergency_salvage", "mockery")
        self.seed_runtime(0, reward_guard_charges=1)
        self.participant_update(1, hit_count=2)
        with patch.object(engine, "_roll_success") as roll:
            result = self.round()
            roll.assert_not_called()
        self.assertEqual(result["reward_event"]["type"], "boss_skip")
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 1)
        self.assertEqual(self.shards(0), 0)

    def test_emergency_salvage_rolls_on_guarded_real_attack(self):
        self.start("emergency_salvage")
        self.seed_runtime(0, reward_guard_charges=1)
        with patch.object(engine, "_roll_success", return_value=True) as roll:
            result = self.round()
        roll.assert_called_once_with(10.0)
        self.assertEqual(self.shards(0), 2)
        self.assertEqual(result["reward_event"]["type"], "reward_guard")

    def test_rapier_can_be_absorbed_without_shield_or_reward_loss(self):
        self.start("infernal_guard", ability="rapier")
        self.seed_runtime(0, reward_guard_charges=1)
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=True):
            result = self.round()
        self.assertEqual(result["reward_event"]["type"], "reward_guard")
        self.assertEqual(result["state"]["boss"]["reward_shields"], 100)
        self.assertEqual(result["state"]["boss"]["reward_percent"], 100)
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 0)

    def test_critical_strike_one_charge_absorbs_only_first_impact(self):
        self.start("unstable_shell", ability="critical_strike")
        self.seed_runtime(0, reward_guard_charges=1)
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=True):
            result = self.round()
        self.assertEqual([e["type"] for e in result["reward_event"]["attacks"]], ["reward_guard", "shield"])
        self.assertEqual(result["state"]["boss"]["reward_shields"], 99)
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 0)

    def test_multiple_guard_sources_consumed_in_queue_order_one_per_impact(self):
        self.start("unstable_shell", "infernal_guard", ability="critical_strike")
        self.seed_runtime(0, reward_guard_charges=1)
        self.seed_runtime(1, reward_guard_charges=2)
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=True):
            result = self.round()
        attacks = result["reward_event"]["attacks"]
        self.assertEqual([e["guard_event"]["player_id"] for e in attacks], [p["id"] for p in self.players])
        self.assertEqual([self.runtime(i)["reward_guard_charges"] for i in range(2)], [0, 1])
        self.assertEqual(result["state"]["boss"]["reward_shields"], 100)

    def test_two_stacked_charges_process_critical_attacks_sequentially(self):
        self.start("infernal_guard", ability="critical_strike")
        self.seed_runtime(0, reward_guard_charges=2)
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=True):
            result = self.round()
        self.assertEqual([e["type"] for e in result["reward_event"]["attacks"]], ["reward_guard", "reward_guard"])
        self.assertEqual(self.runtime(0)["reward_guard_charges"], 0)

    def test_holy_relic_normal_victory_once_even_after_restart_and_refinalization(self):
        self.start("holy_relic", hp=100)
        with patch.object(engine, "_roll_success", return_value=True) as roll:
            result = self.hit(0)
            init_boss_db(self.db)
            self.advance()
            self.hit(0)
            with connect_mini_db(self.db) as conn:
                conn.row_factory = sqlite3.Row
                boss = conn.execute("SELECT * FROM mini_bosses WHERE id = ?", (self.boss["id"],)).fetchone()
                combat._finish_victory(conn, boss, self.now)
        roll.assert_called_once_with(15.0)
        self.assertEqual(self.shards(0), 3)
        self.assertEqual(len(self.events("chance_shards")), 1)
        self.assertIn("Освящённая находка: +3 осколка", " ".join(combat_event_lines(result)))

    def test_holy_relic_nonproc_gives_no_bonus_shards(self):
        self.start("holy_relic", hp=100)
        with patch.object(engine, "_roll_success", return_value=False) as roll:
            self.hit(0)
        roll.assert_called_once_with(15.0)
        self.assertEqual(self.shards(0), 0)

    def test_holy_relic_independent_rolls_for_each_participant(self):
        self.start("holy_relic", "holy_relic", hp=200)
        with patch.object(engine, "_roll_success", side_effect=[True, False]) as roll:
            self.round()
        self.assertEqual(roll.call_count, 2)
        self.assertEqual([self.shards(i) for i in range(2)], [3, 0])

    def test_holy_relic_no_roll_on_failure(self):
        self.start("holy_relic")
        self.update("mini_bosses", reward_shields=0, reward_percent=10)
        with patch.object(engine, "_roll_success") as roll:
            result = self.round()
            roll.assert_not_called()
        self.assertEqual(result["state"]["boss"]["status"], "failed")
        self.assertEqual(self.shards(0), 6)

    def test_holy_relic_no_roll_on_admin_force_finish(self):
        self.start("holy_relic")
        self.hit(0)
        with patch.object(engine, "_roll_success") as roll:
            combat.force_finish_battle(self.boss["id"], now=self.now, db_path=self.db)
            roll.assert_not_called()
        self.assertEqual(self.shards(0), 0)

    def test_holy_relic_phantom_without_hit_has_no_roll(self):
        self.start("holy_relic", hp=100)
        self.participant_update(0, phantom_reward=1)
        self.update("mini_bosses", current_turn_position=2)
        with patch.object(engine, "_roll_success") as roll:
            self.hit(1)
            roll.assert_not_called()
        self.assertEqual(self.shards(0), 0)
        self.assertEqual(self.participant(0)["reward_granted"], 1)

    def test_holy_relic_without_hit_or_phantom_has_no_roll(self):
        self.start("holy_relic", hp=100)
        self.update("mini_bosses", current_turn_position=2)
        with patch.object(engine, "_roll_success") as roll:
            result = self.hit(1)
            roll.assert_not_called()
        self.assertEqual(result["rewards"]["missed_players"], 1)
        self.assertEqual(self.shards(0), 0)

    def test_critical_guarded_attacks_each_roll_salvage_once(self):
        self.start("emergency_salvage", ability="critical_strike")
        self.seed_runtime(0, reward_guard_charges=1)
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=True), patch.object(
            engine, "_roll_success", return_value=True
        ) as roll:
            result = self.round()
        self.assertEqual(roll.call_count, 2)
        self.assertEqual(self.shards(0), 4)
        self.assertEqual([a["type"] for a in result["reward_event"]["attacks"]], ["reward_guard", "shield"])

    def test_echo_repeats_on_sixth_hit_without_echo_increasing_hit_count(self):
        self.start("battle_echo")
        for _ in range(6):
            self.round()
        self.assertEqual(self.participant(0)["hit_count"], 6)
        self.assertEqual(self.participant(0)["total_damage"], 700)
        self.assertEqual(len(self.events("battle_echo")), 2)
        self.assertNotIn("pending_echo_damage", self.runtime(0))

    def test_holy_relic_banished_after_hit_is_eligible(self):
        self.start("holy_relic", hp=200)
        self.hit(0)
        self.participant_update(0, banished=1)
        with patch.object(engine, "_roll_success", return_value=True) as roll:
            self.hit(1)
        roll.assert_called_once_with(15.0)
        self.assertEqual(self.shards(0), 3)

    def test_echo_generated_on_third_hit_fires_before_next_manual_hit(self):
        self.start("battle_echo", attack=101)
        self.round()
        self.round()
        result = self.hit(0)
        self.assertEqual(self.runtime(0)["pending_echo_damage"], 50)
        before = self.participant(0)
        init_boss_db(self.db)
        result = self.hit(1)
        after = self.participant(0)
        self.assertEqual(after["hit_count"], before["hit_count"])
        self.assertEqual(after["total_damage"], before["total_damage"] + 50)
        self.assertNotIn("pending_echo_damage", self.runtime(0))
        self.assertEqual(result["hero_events"][0]["damage"], 50)
        self.assertIn("Эхо боя Ренкай наносит 50", " ".join(combat_event_lines(result)))
        self.assertEqual(len(self.events("battle_echo")), 1)

    def test_echo_uses_actual_hp_loss_on_overkill_not_calculated_damage(self):
        self.start("battle_echo", hp=21)
        self.participant_update(0, hit_count=2)
        self.hit(0)
        self.assertEqual(self.runtime(0)["pending_echo_damage"], 10)

    def test_echo_zero_damage_shield_hit_does_not_create_echo(self):
        self.start("battle_echo", ability="magic_shield")
        self.participant_update(0, hit_count=2)
        self.hit(0)
        self.assertNotIn("pending_echo_damage", self.runtime(0))

    def test_echo_skips_all_modifiers_passives_and_kill_hooks(self):
        self.start("battle_echo", ability="mechanism", faction="beasts")
        self.seed_runtime(0, pending_echo_damage=37)
        self.participant_update(0, damage_bonus_percent=999)
        with patch.object(engine, "resolve_attack", side_effect=AssertionError("No attack passive")), patch(
            "app.mini.boss.runtime.faction_multiplier_percent", side_effect=AssertionError("No faction recalculation")
        ), patch("app.mini.boss.boss_abilities.engine.modify_hero_damage", side_effect=AssertionError("No boss modifier")), patch(
            "app.mini.boss.combat.resolve_kill", side_effect=AssertionError("No kill hook")
        ):
            result = self.advance()
        self.assertEqual(result["state"]["boss"]["current_hp"], 100000 - 37)
        self.assertEqual(self.participant(0)["hit_count"], 0)
        self.assertEqual(self.participant(0)["total_damage"], 37)

    def test_echo_recovery_and_repeated_refresh_apply_once_and_preserve_notice(self):
        self.start("battle_echo")
        self.seed_runtime(0, pending_echo_damage=45)
        init_boss_db(self.db)
        result = self.advance()
        again = self.advance()
        self.assertTrue(result["changed"])
        self.assertFalse(again["changed"])
        self.assertEqual(len(self.events("battle_echo")), 1)
        self.assertEqual(self.participant(0)["total_damage"], 45)
        card = format_public_turn(again["state"]["boss"], again["state"]["participants"])
        self.assertIn("Эхо боя Ренкай наносит 45", card)
        self.assertIn("Эхо боя", " ".join(timeout_event_lines(result)))

    def test_echo_paralysis_preserves_pending_until_actionable_turn(self):
        self.start("battle_echo")
        self.seed_runtime(0, pending_echo_damage=45)
        self.participant_update(0, forced_skip_turns=1)
        skipped = self.advance()
        self.assertEqual(self.runtime(0)["pending_echo_damage"], 45)
        self.assertEqual(skipped["hero_events"], [])
        result = self.hit(1)
        self.assertNotIn("pending_echo_damage", self.runtime(0))
        self.assertEqual(result["hero_events"][0]["damage"], 45)

    def test_echo_banishment_never_consumes_pending_damage(self):
        self.start("battle_echo")
        self.seed_runtime(0, pending_echo_damage=45)
        self.participant_update(0, banished=1)
        self.advance()
        self.hit(1)
        self.assertEqual(self.runtime(0)["pending_echo_damage"], 45)
        self.assertEqual(self.events("battle_echo"), [])

    def test_echo_timeout_does_not_apply_already_consumed_damage_again(self):
        self.start("battle_echo")
        self.seed_runtime(0, pending_echo_damage=45)
        self.advance()
        self.advance(self.now + timedelta(hours=4))
        self.assertEqual(len(self.events("battle_echo")), 1)
        self.assertEqual(self.participant(0)["total_damage"], 45)
        self.assertEqual(self.participant(0)["skipped_turns"], 1)

    def test_echo_can_finish_normal_victory_without_pressing_hit(self):
        self.start("battle_echo", "holy_relic", hp=1000)
        self.participant_update(1, hit_count=1)
        self.seed_runtime(0, pending_echo_damage=1500)
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.advance()
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertEqual(result["rewards"]["players"], 1)
        self.assertEqual(self.participant(0)["total_damage"], 1000)
        self.assertEqual(self.participant(0)["hit_count"], 0)
        self.assertEqual(self.shards(1), 3)

    def test_echo_recovery_victory_does_not_duplicate_relic_public_event(self):
        self.start("battle_echo", "holy_relic")
        self.participant_update(1, hit_count=1)
        self.seed_runtime(0, pending_echo_damage=100000)
        with patch.object(engine, "_roll_success", return_value=True):
            result = self.hit(0)
        self.assertFalse(result["applied"])
        lines = combat_event_lines(result)
        self.assertEqual(sum("Освящённая находка" in line for line in lines), 1)
        self.assertEqual(sum("Эхо боя Ренкай" in line for line in lines), 1)

    def test_victory_relic_and_ordinary_hit_rollback_together_on_failure(self):
        self.start("holy_relic", hp=100)
        with patch.object(engine, "_roll_success", return_value=True), patch(
            "app.mini.boss.rewards.log_hero_event", side_effect=RuntimeError("Transaction failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "Transaction failed"):
                self.hit(0)
        self.assertEqual(self.shards(0), 0)
        self.assertEqual(self.participant(0)["hit_count"], 0)
        self.assertEqual(self.participant(0)["reward_granted"], 0)
        state = combat.get_combat_state(self.boss["id"], db_path=self.db)
        self.assertEqual(state["boss"]["current_hp"], 100)
        with patch.object(engine, "_roll_success", return_value=True):
            self.hit(0)
        self.assertEqual(self.shards(0), 3)
        self.assertEqual(len(self.events("chance_shards")), 1)

    def test_echo_kill_from_turn_transition_propagates_rewards(self):
        self.start("battle_echo", hp=1000)
        self.hit(0)
        self.seed_runtime(0, pending_echo_damage=1000)
        result = self.hit(1)
        self.assertTrue(result["battle_ended"])
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertEqual(result["rewards"]["players"], 2)

    def test_frenzy_manual_damage_cycle_and_extra_hit_count(self):
        self.start("blood_frenzy")
        for number, expected in enumerate((100, 115, 130, 145, 100), 1):
            result = self.hit(0)
            self.assertEqual(result["ability_damage"], expected)
            self.assertEqual(result["damage"], expected)
            self.assertEqual(result["extra_damage"], 100 if number == 4 else 0)
            self.assertEqual(self.participant(0)["hit_count"], number)
            self.hit(1)
        self.assertEqual(self.participant(0)["total_damage"], 690)
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mini_boss_actions WHERE action_type = 'extra_attack'").fetchone()[0], 1)

    def test_frenzy_extra_pipeline_uses_faction_mechanism_and_potion_after_snapshot(self):
        self.start("blood_frenzy", ability="mechanism", faction="dark")
        self.participant_update(0, hit_count=3, damage_bonus_percent=10)
        result = self.hit(0)
        self.assertEqual(result["ability_damage"], 145)
        self.assertEqual(result["damage_after_faction"], 290)
        self.assertEqual(result["damage_before_external_bonus"], 232)
        self.assertEqual(result["damage"], 256)
        self.assertEqual(result["extra_damage"], 176)
        self.assertEqual(self.participant(0)["hit_count"], 4)
        self.assertEqual(self.participant(0)["total_damage"], 432)

    def test_frenzy_extra_does_not_call_attack_or_kill_passive_again(self):
        self.start("blood_frenzy", hp=240)
        self.participant_update(0, hit_count=3)
        with patch("app.mini.boss.calculations.resolve_attack", wraps=engine.resolve_attack) as attack, patch(
            "app.mini.boss.combat.resolve_kill", side_effect=AssertionError("No extra kill hook")
        ):
            result = self.hit(0)
        self.assertEqual(attack.call_count, 1)
        self.assertEqual(result["extra_damage"], 95)
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertEqual(self.participant(0)["hit_count"], 4)

    def test_frenzy_main_kill_does_not_execute_extra(self):
        self.start("blood_frenzy", hp=140)
        self.participant_update(0, hit_count=3)
        result = self.hit(0)
        self.assertEqual(result["extra_damage"], 0)
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        self.assertFalse(any(e["type"] == "extra_attack" for e in result["passive_events"]))

    def test_frenzy_extra_normal_mage_modifier_can_damage_after_primary_removes_shield(self):
        self.start("blood_frenzy", ability="magic_shield", class_tag="mage")
        self.participant_update(0, hit_count=3)
        result = self.hit(0)
        self.assertEqual(result["damage"], 0)
        self.assertEqual(result["extra_damage"], 100)
        self.assertEqual(self.participant(0)["hit_count"], 4)

    def test_runtime_migration_preserves_all_existing_fighting_values(self):
        self.start("battle_echo")
        self.round()
        with connect_mini_db(self.db) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("ALTER TABLE mini_boss_participants DROP COLUMN hero_state_json")
            before_boss = dict(conn.execute("SELECT * FROM mini_bosses WHERE id = ?", (self.boss["id"],)).fetchone())
            before_participants = [dict(r) for r in conn.execute("SELECT * FROM mini_boss_participants ORDER BY queue_position")]
        init_boss_db(self.db)
        self.assertEqual(combat.get_combat_state(self.boss["id"], db_path=self.db)["boss"], before_boss)
        for index, before in enumerate(before_participants):
            after = self.participant(index)
            self.assertEqual(after.pop("hero_state_json"), "{}")
            self.assertEqual(after, before)
        self.seed_runtime(0, pending_echo_damage=70, reward_guard_charges=2)
        init_boss_db(self.db)
        sync_hero_catalog(self.db)
        self.assertEqual(self.runtime(0), {"pending_echo_damage": 70, "reward_guard_charges": 2})

    def test_new_battle_clears_prestart_runtime_state(self):
        self.start("infernal_guard")
        self.seed_runtime(0, reward_guard_charges=2, pending_echo_damage=99)
        self.update("mini_bosses", status="ready")
        combat.start_battle(self.boss["id"], now=self.now, db_path=self.db)
        self.assertEqual(self.runtime(0), {})
