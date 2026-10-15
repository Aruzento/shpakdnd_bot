import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.admin_grants import grant_mini_hero, grant_mini_hero_all
from app.mini.boss.combat import hit_boss, start_battle
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    BossRegistrationClosed, close_registration, create_boss_event,
    get_boss, list_participants, register_player, select_battle_hero,
)
from app.mini.catalog import load_hero_catalog
from app.mini.db import connect_mini_db
from app.mini.gacha import _choose_hero_code, _rarity_weight_units, perform_gacha_pull
from app.mini.hero_upgrades import upgrade_cost, upgrade_hero
from app.mini.heroes import get_player_heroes, set_active_hero, sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds
from tests.topic_fixtures import isolated_topics

ENGINEER = "panic_dungeon_engineer"


class CompensationReleaseTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.world = sync_configured_mini_worlds(self.db)[0]["id"]
        self.players = [
            create_mini_player(self.world, 770001 + i, f"@release_{i}", f"Игрок {i}", self.db)
            for i in range(2)
        ]
        sync_hero_catalog(self.db)

    def owned_engineer(self, player_id):
        return next(hero for hero in get_player_heroes(player_id, self.db) if hero["code"] == ENGINEER)

    def registered_boss(self):
        boss = create_boss_event(self.world, "training_golem", 999, self.db)
        while len(self.players) < boss["min_players"]:
            i = len(self.players)
            self.players.append(create_mini_player(
                self.world, 770001 + i, f"@release_{i}", f"Игрок {i}", self.db
            ))
        for player in self.players:
            grant_mini_hero(player["id"], "Villager", self.db)
            register_player(boss["id"], player["id"], self.db)
        return boss

    def rare_roll(self, luck=False):
        catalog = load_hero_catalog()
        cursor = 0
        for rarity, weight in catalog["settings"]["rarity_weights"].items():
            if rarity == "rare":
                return cursor
            if any(h["active"] and h["rarity"] == rarity for h in catalog["heroes"]):
                cursor += _rarity_weight_units(weight, rarity, luck_active=luck)
        self.fail("Current catalog must have a rare tier")

    def test_inactive_compensation_excluded_from_every_gacha_tier_with_and_without_luck(self):
        catalog = load_hero_catalog()
        engineer = next(h for h in catalog["heroes"] if h["code"] == ENGINEER)
        self.assertFalse(engineer["active"])
        for luck in (False, True):
            cursor = 0
            for rarity, weight in catalog["settings"]["rarity_weights"].items():
                eligible = [h for h in catalog["heroes"] if h["active"] and h["rarity"] == rarity]
                units = _rarity_weight_units(weight, rarity, luck_active=luck)
                if not eligible or not units:
                    continue
                with self.subTest(luck=luck, rarity=rarity):
                    def choose(pool):
                        self.assertEqual({h["code"] for h in pool}, {h["code"] for h in eligible})
                        self.assertNotIn(ENGINEER, [h["code"] for h in pool])
                        return pool[-1]
                    with patch("app.mini.gacha.secrets.randbelow", return_value=cursor), patch(
                        "app.mini.gacha.secrets.choice", side_effect=choose
                    ):
                        self.assertNotEqual(_choose_hero_code(luck_active=luck), ENGINEER)
                cursor += units

    def test_ordinary_rare_pull_does_not_award_compensation(self):
        price = load_hero_catalog()["settings"]["pull_price"]
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_players SET coins = ? WHERE id = ?", (price, self.players[0]["id"]))
        def choose(pool):
            self.assertNotIn(ENGINEER, [h["code"] for h in pool])
            return pool[-1]
        with patch("app.mini.gacha.secrets.randbelow", return_value=self.rare_roll()), patch(
            "app.mini.gacha.secrets.choice", side_effect=choose
        ):
            result = perform_gacha_pull(self.players[0]["id"], db_path=self.db)
        self.assertEqual(result["rarity"], "rare")
        self.assertNotEqual(result["code"], ENGINEER)
        self.assertEqual(result["balance"], 0)
        self.assertNotIn(ENGINEER, [h["code"] for h in get_player_heroes(self.players[0]["id"], self.db)])

    def test_individual_admin_grant_accepts_inactive_and_is_idempotent(self):
        player = self.players[0]["id"]
        first = grant_mini_hero(player, ENGINEER, self.db)
        second = grant_mini_hero(player, ENGINEER, self.db)
        self.assertTrue(first["applied"])
        self.assertTrue(first["auto_activated"])
        self.assertEqual(first["active"], 0)
        self.assertFalse(second["applied"])
        owned = self.owned_engineer(player)
        self.assertEqual((owned["copies"], owned["shards"]), (1, 0))

    def test_admin_grant_all_accepts_inactive_and_preserves_existing_ownership(self):
        grant_mini_hero(self.players[0]["id"], ENGINEER, self.db)
        before = self.owned_engineer(self.players[0]["id"])
        result = grant_mini_hero_all(self.world, ENGINEER, self.db)
        self.assertEqual((result["players"], result["granted"], result["skipped"]), (2, 1, 1))
        self.assertEqual(result["hero"]["active"], 0)
        repeat = grant_mini_hero_all(self.world, ENGINEER, self.db)
        self.assertEqual((repeat["granted"], repeat["skipped"]), (0, 2))
        self.assertEqual(self.owned_engineer(self.players[0]["id"]), before)
        self.assertEqual(self.owned_engineer(self.players[1]["id"])["shards"], 0)

    def test_catalog_deactivation_keeps_owned_hero_copies_stars_and_shards(self):
        old_catalog = copy.deepcopy(load_hero_catalog())
        next(h for h in old_catalog["heroes"] if h["code"] == ENGINEER)["active"] = True
        player = self.players[0]["id"]
        with patch("app.mini.heroes.load_hero_catalog", return_value=old_catalog):
            hero = grant_mini_hero(player, ENGINEER, self.db)
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_player_heroes SET copies = 3, stars = 2 WHERE player_id = ?", (player,))
            conn.execute("UPDATE mini_players SET shards = 77 WHERE id = ?", (player,))
            before = tuple(conn.execute("SELECT * FROM mini_player_heroes WHERE player_id = ?", (player,)).fetchone())
        sync_hero_catalog(self.db)
        owned = self.owned_engineer(player)
        self.assertEqual((owned["id"], owned["active"], owned["copies"], owned["stars"], owned["shards"], owned["is_active"]),
                         (hero["id"], 0, 3, 2, 77, 1))
        with connect_mini_db(self.db) as conn:
            self.assertEqual(tuple(conn.execute("SELECT * FROM mini_player_heroes WHERE player_id = ?", (player,)).fetchone()), before)

    def test_inactive_owned_hero_can_be_upgraded(self):
        player = self.players[0]["id"]
        hero = grant_mini_hero(player, ENGINEER, self.db)
        cost = upgrade_cost(hero["rarity"], 1)
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_players SET shards = ? WHERE id = ?", (cost, player))
        result = upgrade_hero(player, hero["id"], self.db)
        self.assertEqual((result["stars"], result["attack"], result["shards"]), (1, 63, 0))
        self.assertEqual(self.owned_engineer(player)["active"], 0)

    def test_inactive_battle_selection_and_emergency_salvage_still_work(self):
        villagers = [grant_mini_hero(p["id"], "Villager", self.db) for p in self.players]
        engineer = grant_mini_hero(self.players[0]["id"], ENGINEER, self.db)
        boss = self.registered_boss()
        selected = select_battle_hero(boss["id"], self.players[0]["id"], engineer["id"], self.db)
        self.assertEqual(selected["active"], 0)
        close_registration(boss["id"], self.db)
        # Both permitted pre-start states accept an owned inactive hero.
        select_battle_hero(boss["id"], self.players[0]["id"], villagers[0]["id"], self.db)
        select_battle_hero(boss["id"], self.players[0]["id"], engineer["id"], self.db)
        start_battle(boss["id"], db_path=self.db)
        set_active_hero(self.players[0]["id"], villagers[0]["id"], self.db)
        participant = list_participants(boss["id"], self.db)[0]
        self.assertEqual(participant["battle_hero_id"], engineer["id"])
        self.assertEqual(json.loads(participant["hero_snapshot_json"])["passive_key"], "emergency_salvage")
        with self.assertRaises(BossRegistrationClosed):
            select_battle_hero(boss["id"], self.players[0]["id"], villagers[0]["id"], self.db)
        with patch("app.mini.boss.abilities.engine._roll_success", return_value=True):
            for player in self.players:
                hit_boss(boss["id"], player["id"], db_path=self.db)
        self.assertEqual(self.owned_engineer(self.players[0]["id"])["shards"], 2)
        self.assertEqual(get_boss(boss["id"], self.db)["reward_shields"], boss["reward_shields"] - 1)

    def test_deactivation_and_reinitialization_preserve_running_battle(self):
        old_catalog = copy.deepcopy(load_hero_catalog())
        next(h for h in old_catalog["heroes"] if h["code"] == ENGINEER)["active"] = True
        with patch("app.mini.heroes.load_hero_catalog", return_value=old_catalog):
            grant_mini_hero(self.players[0]["id"], ENGINEER, self.db)
            grant_mini_hero(self.players[1]["id"], "Villager", self.db)
            boss = self.registered_boss()
            close_registration(boss["id"], self.db)
            start_battle(boss["id"], db_path=self.db)
        def snapshot():
            with connect_mini_db(self.db) as conn:
                return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                        for table in ("mini_bosses", "mini_boss_participants", "mini_boss_actions", "mini_boss_events")}
        before = snapshot()
        init_mini_db(self.db)
        init_boss_db(self.db)
        sync_hero_catalog(self.db)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.owned_engineer(self.players[0]["id"])["active"], 0)
