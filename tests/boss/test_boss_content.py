import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.catalog import load_boss_catalog
from app.mini.boss.combat import hit_boss, start_battle
from app.mini.boss.public import format_public_boss
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    close_registration, create_boss_event, get_boss, register_player, select_battle_hero,
)
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds
from tests.topic_fixtures import isolated_topics


class LiveBossContentTests(unittest.TestCase):
    """Use real catalog abilities, independently of the synthetic combat fixtures."""

    def setUp(self):
        self.enterContext(isolated_topics())
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "boss-content.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        sync_hero_catalog(self.db)
        self.world = sync_configured_mini_worlds(self.db)[0]["id"]
        self.players = [
            create_mini_player(self.world, 81001 + n, f"@content_{n}", f"Content {n}", self.db)
            for n in range(2)
        ]
        self.now = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
        with connect_mini_db(self.db) as conn:
            self.heroes = dict(conn.execute(
                "SELECT code, id FROM mini_heroes WHERE code IN ('Villager', 'ApprenticeMage')"
            ))
            for player in self.players:
                for hero_id in self.heroes.values():
                    conn.execute(
                        "INSERT INTO mini_player_heroes (player_id, hero_id) VALUES (?, ?)",
                        (player["id"], hero_id),
                    )
                conn.execute(
                    "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
                    (self.heroes["Villager"], player["id"]),
                )

    def tearDown(self):
        self.temp.cleanup()

    def create(self, code):
        self.boss = create_boss_event(self.world, code, 999, self.db)
        # Only lower the signup threshold for two test players; keep live traits.
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_bosses SET min_players = 2 WHERE id = ?", (self.boss["id"],))
        for player in self.players:
            register_player(self.boss["id"], player["id"], self.db)
        select_battle_hero(
            self.boss["id"], self.players[1]["id"], self.heroes["ApprenticeMage"], self.db,
        )
        close_registration(self.boss["id"], self.db)
        return start_battle(self.boss["id"], now=self.now, db_path=self.db)

    def hit(self, index):
        # Hero passive randomness is irrelevant to these boss-content scenarios.
        with patch("app.mini.boss.abilities.engine._roll_success", return_value=False):
            return hit_boss(
                self.boss["id"], self.players[index]["id"], now=self.now, db_path=self.db,
            )

    def test_all_catalog_templates_snapshot_real_traits_configs_hp_and_rewards(self):
        for template in load_boss_catalog()["bosses"]:
            with self.subTest(boss=template["code"]):
                boss = create_boss_event(self.world, template["code"], 999, self.db)
                for key in ("faction", "ability_key", "ability_text", "max_hp", "min_players", "reward_coins", "reward_shields"):
                    self.assertEqual(boss[key], template[key], key)
                self.assertEqual(boss["current_hp"], template["max_hp"])
                self.assertEqual(json.loads(boss["features_json"]), template["features"])
                self.assertEqual(json.loads(boss["ability_config_json"]), template.get("ability_config", {}))
                self.assertEqual(json.loads(boss["reward_items_json"]), template.get("reward_items", []))
                with connect_mini_db(self.db) as conn:
                    conn.execute("UPDATE mini_bosses SET status = 'cancelled' WHERE id = ?", (boss["id"],))

    def test_actual_mage_breaks_training_golem_shield_without_hp_damage(self):
        state = self.create("training_golem")
        hp = state["boss"]["current_hp"]
        self.assertEqual(state["boss"]["ability_key"], "magic_shield")
        blocked = self.hit(0)
        self.assertEqual(blocked["damage"], 0)
        self.assertEqual(blocked["boss_events"][0]["type"], "magic_shield_blocked")
        with connect_mini_db(self.db) as conn:
            raw = conn.execute(
                "SELECT hero_snapshot_json FROM mini_boss_participants WHERE boss_id = ? AND player_id = ?",
                (self.boss["id"], self.players[1]["id"]),
            ).fetchone()[0]
            self.assertEqual(json.loads(raw)["class_tag"], "mage")
            # The battle loadout still identifies the mage after a live catalog edit.
            conn.execute("UPDATE mini_heroes SET class_tag = 'none' WHERE id = ?", (self.heroes["ApprenticeMage"],))
        removed = self.hit(1)
        self.assertEqual(removed["damage"], 0)
        self.assertEqual(removed["state"]["boss"]["current_hp"], hp)
        self.assertFalse(json.loads(removed["state"]["boss"]["ability_state_json"])["shield_active"])
        self.assertEqual(removed["boss_events"][0]["type"], "magic_shield_removed")
        ordinary = self.hit(0)
        # New armored feature applies AFTER the existing final damage of 3.
        self.assertEqual(ordinary["hero_final_damage"], 3)
        self.assertEqual(ordinary["damage"], 2)
        self.assertEqual(ordinary["state"]["boss"]["current_hp"], hp - 2)

    def test_actual_vampire_changes_to_beasts_once_on_proc(self):
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=True) as roller:
            state = self.create("crimson_vampire")
            self.assertEqual(state["boss"]["faction"], "beasts")
            self.assertEqual(state["boss_events"][0]["type"], "shapeshifter")
            self.hit(0)
            self.hit(1)
            init_boss_db(self.db)
        self.assertEqual(roller.call_count, 1)
        self.assertEqual(get_boss(self.boss["id"], self.db)["faction"], "beasts")

    def test_actual_vampire_stays_dark_after_nonproc_and_next_turn(self):
        with patch("app.mini.boss.boss_abilities.engine._roll_success", return_value=False) as roller:
            state = self.create("crimson_vampire")
            self.assertEqual(state["boss"]["faction"], "dark")
            self.assertEqual(state["boss_events"][0]["type"], "shapeshifter_unchanged")
            self.hit(0)
            result = self.hit(1)
        self.assertEqual(roller.call_count, 1)
        self.assertEqual(result["state"]["boss"]["faction"], "dark")

    def test_actual_hydra_heals_ten_percent_after_reward_attack(self):
        state = self.create("swamp_hydra")
        self.assertEqual(state["boss"]["ability_key"], "hydra_regeneration")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_bosses SET current_hp = 100 WHERE id = ?", (self.boss["id"],))
        first = self.hit(0)
        second = self.hit(1)
        heal = state["boss"]["max_hp"] // 10
        self.assertEqual(second["state"]["boss"]["current_hp"], 100 - first["damage"] - second["damage"] + heal)
        events = second["reward_event"]["boss_events"]
        self.assertEqual(events[0], {"type": "hydra_regeneration", "healed_hp": heal})
        self.assertEqual(second["state"]["boss"]["reward_shields"], state["boss"]["reward_shields"] - 1)

    def test_running_boss_keeps_ability_faction_state_when_catalog_changes(self):
        self.create("training_golem")
        before = get_boss(self.boss["id"], self.db)
        future = load_boss_catalog()
        for boss in future["bosses"]:
            if boss["code"] == "training_golem":
                boss.update(faction="commoners", ability_key="none", features=[])
        future_path = self.db.parent / "future-bosses.json"
        future_path.write_text(json.dumps(future, ensure_ascii=False), encoding="utf-8")
        with patch("app.mini.boss.catalog.BOSSES_JSON", future_path):
            init_boss_db(self.db)
            self.assertEqual(get_boss(self.boss["id"], self.db), before)
            result = self.hit(0)
        self.assertEqual(result["damage"], 0)
        self.assertEqual(result["state"]["boss"]["ability_key"], "magic_shield")
        self.assertEqual(result["state"]["boss"]["faction"], "monsters")

    def test_poisonous_feature_has_russian_display_before_battle(self):
        boss = next(b for b in load_boss_catalog()["bosses"] if b["code"] == "swamp_hydra")
        snapshot = create_boss_event(self.world, boss["code"], 999, self.db)
        self.assertIn("‼️ Особенности: Ядовитый", format_public_boss(snapshot, []))
