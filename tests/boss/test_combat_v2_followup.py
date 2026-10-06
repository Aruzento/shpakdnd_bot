import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from tests.boss import test_combat_v2 as fixtures
from app.mini.boss.combat import start_battle
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import get_boss
from app.mini.catalog import load_hero_catalog
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.boss.notices import combat_event_lines
from app.mini.boss.service import select_battle_hero

class FrozenLoadoutTests(unittest.TestCase):
    setUp = fixtures.CombatV2IntegrationTests.setUp
    tearDown = fixtures.CombatV2IntegrationTests.tearDown
    update_boss = fixtures.CombatV2IntegrationTests.update_boss
    update_hero = fixtures.CombatV2IntegrationTests.update_hero
    start = fixtures.CombatV2IntegrationTests.start
    hit = fixtures.CombatV2IntegrationTests.hit
    round = fixtures.CombatV2IntegrationTests.round
    participant = fixtures.CombatV2IntegrationTests.participant
    events = fixtures.CombatV2IntegrationTests.events

    def test_banished_salvage_stops_but_past_contribution_retains_rewards(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_hero(passive_key="emergency_salvage")
        self.start()
        self.hit(0)
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET banished = 1 WHERE boss_id = ? AND player_id = ?",
                         (self.boss["id"], self.players[0]["id"]))
        with patch("app.mini.boss.abilities.engine._roll_success", return_value=True) as roller:
            result = self.hit(1)
        self.assertEqual(result["reward_event"]["passive_events"], [])
        roller.assert_not_called()
        with connect_mini_db(self.db) as conn:
            shards = conn.execute("SELECT shards FROM mini_players WHERE id = ?", (self.players[0]["id"],)).fetchone()[0]
        self.assertEqual(shards, 0)
        self.update_boss(current_hp=1)
        victory = self.hit(1)
        self.assertEqual(victory["rewards"]["players"], 2)

    def test_passive_does_not_change_mid_battle_on_attack(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_hero(passive_key="strong_start")
        self.start()
        self.update_hero(passive_key="none")
        result = self.hit(0)
        self.assertEqual(result["damage"], 20)
        self.assertEqual(result["passive_key"], "strong_start")

    def test_passive_does_not_change_mid_battle_on_boss_attack(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_hero(passive_key="emergency_salvage")
        self.start()
        self.update_hero(passive_key="none")
        with patch("app.mini.boss.abilities.engine._roll_success", return_value=True):
            result = self.round()
        self.assertEqual(result["reward_event"]["passive_events"][0]["shards"], 2)

    def test_passive_does_not_change_mid_battle_on_kill(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_hero(passive_key="execution_protocol")
        self.start()
        self.update_hero(passive_key="none")
        self.update_boss(current_hp=1)
        with patch("app.mini.boss.abilities.engine._roll_success", return_value=True):
            result = self.hit(0)
        self.assertEqual(result["bonus_shards"], 30)

    def test_empty_legacy_snapshot_uses_placeholders_even_after_live_traits_changed(self):
        select_battle_hero(self.boss["id"], self.players[0]["id"], self.hero, self.db)
        self.update_boss(faction="beasts")
        self.start("mechanism")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET hero_snapshot_json = '{}' WHERE boss_id = ? AND player_id = ?",
                         (self.boss["id"], self.players[0]["id"]))
        self.update_hero(faction="dark", class_tag="technical", damage_type="magic", attack_range="ranged", special_trait="demon")
        init_boss_db(self.db)
        snapshot = json.loads(self.participant(0)["hero_snapshot_json"])
        self.assertEqual(
            [snapshot[k] for k in ("faction", "class_tag", "damage_type", "attack_range", "special_trait")],
            ["commoners", "none", "slashing", "melee", "none"],
        )
        result = self.hit(0)
        self.assertEqual(result["damage"], 16)
        self.assertEqual(result["faction_multiplier_percent"], 200)

    def test_partial_v2_snapshot_captures_passive_before_catalog_sync_once(self):
        self.start()
        original = json.loads(self.participant(0)["hero_snapshot_json"])
        del original["passive_key"]
        del original["passive_text"]
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_boss_participants SET hero_snapshot_json = ? WHERE boss_id = ? AND player_id = ?",
                         (json.dumps(original), self.boss["id"], self.players[0]["id"]))
        catalog = load_hero_catalog()
        for hero in catalog["heroes"]:
            if hero["code"] == "Villager":
                hero.update(passive_key="strong_start", faction="dark", class_tag="magical")
        with patch("app.mini.heroes.load_hero_catalog", return_value=catalog):
            sync_hero_catalog(self.db)
        snapshot = self.participant(0)["hero_snapshot_json"]
        self.assertEqual(json.loads(snapshot)["passive_key"], "none")
        init_boss_db(self.db)
        self.assertEqual(self.participant(0)["hero_snapshot_json"], snapshot)
        self.assertEqual(self.hit(0)["damage"], 3)

    def test_regeneration_event_has_no_fabricated_player(self):
        self.start("hydra_regeneration")
        self.update_boss(current_hp=500)
        result = self.round()
        with connect_mini_db(self.db) as conn:
            row = conn.execute("SELECT target_player_id, event_json FROM mini_boss_events WHERE event_type = 'hydra_regeneration'").fetchone()
            old_count = conn.execute("SELECT COUNT(*) FROM mini_boss_actions WHERE action_type LIKE 'boss_ability_%'").fetchone()[0]
        self.assertIsNone(row[0])
        self.assertEqual(json.loads(row[1])["actor_kind"], "boss")
        self.assertNotIn("player_id", json.loads(row[1]))
        self.assertEqual(old_count, 0)
        self.assertTrue(any("Гидра восстановила 100 HP" in line for line in combat_event_lines(result)))

    def test_old_events_are_copied_idempotently_with_targets_from_payload(self):
        self.start()
        with connect_mini_db(self.db) as conn:
            for event in ({"type": "hydra_regeneration", "healed_hp": 10},
                          {"type": "paralysis", "player_id": self.players[1]["id"]}):
                conn.execute(
                    """INSERT INTO mini_boss_actions (boss_id, player_id, round_number, action_type, event_json)
                       VALUES (?, ?, 1, ?, ?)""",
                    (self.boss["id"], self.players[0]["id"], "boss_ability_" + event["type"], json.dumps(event)),
                )
        init_boss_db(self.db)
        init_boss_db(self.db)
        with connect_mini_db(self.db) as conn:
            rows = conn.execute("SELECT event_type, target_player_id FROM mini_boss_events ORDER BY id").fetchall()
            old_count = conn.execute("SELECT COUNT(*) FROM mini_boss_actions WHERE action_type LIKE 'boss_ability_%'").fetchone()[0]
        self.assertEqual(rows, [("hydra_regeneration", None), ("paralysis", self.players[1]["id"])])
        self.assertEqual(old_count, 2)

    def test_paralysis_notice_identifies_target_and_forced_skip(self):
        self.start("paralysis")
        with patch("app.mini.boss.boss_abilities.engine._choose_participant", side_effect=lambda ps: ps[0]):
            result = self.round()
        lines = combat_event_lines(result)
        self.assertTrue(any("Босс парализовал @v2_0" in line for line in lines))
        self.assertTrue(any("из-за паралича" in line for line in lines))
        self.assertFalse(any("таймер" in line for line in lines))
