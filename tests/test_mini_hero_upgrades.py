import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.hero_upgrades import (
    HeroUpgradeInsufficientShards,
    HeroUpgradeMaxStars,
    calculate_attack,
    max_stars_for_rarity,
    upgrade_cost,
    upgrade_hero,
)
from app.mini.heroes import get_active_hero, get_player_hero, sync_hero_catalog
from app.mini.db import connect_mini_db
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds


class MiniHeroUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        worlds = sync_configured_mini_worlds(self.db)
        self.world_id = worlds[0]["id"]
        self.player = create_mini_player(
            self.world_id,
            991001,
            "@upgrade_tester",
            "Кузнец звёзд",
            self.db,
        )
        sync_hero_catalog(self.db)

        with connect_mini_db(self.db) as conn:
            hero_id = int(conn.execute(
                "SELECT id FROM mini_heroes WHERE code = 'Villager'"
            ).fetchone()[0])
            conn.execute(
                """
                INSERT INTO mini_player_heroes (
                    player_id, hero_id, copies, shards, stars
                ) VALUES (?, ?, 1, 0, 0)
                """,
                (self.player["id"], hero_id),
            )
            conn.execute(
                "UPDATE mini_players SET active_hero_id = ?, shards = 200 WHERE id = ?",
                (hero_id, self.player["id"]),
            )
            conn.commit()
        self.hero_id = hero_id

    def tearDown(self):
        self.tempdir.cleanup()


    def test_existing_database_gets_stars_column(self):
        old_db = Path(self.tempdir.name) / "old_schema.db"
        with connect_mini_db(old_db) as conn:
            conn.execute(
                """
                CREATE TABLE mini_player_heroes (
                    player_id INTEGER NOT NULL,
                    hero_id INTEGER NOT NULL,
                    copies INTEGER NOT NULL DEFAULT 1,
                    shards INTEGER NOT NULL DEFAULT 0,
                    obtained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (player_id, hero_id)
                )
                """
            )
            conn.commit()

        init_mini_db(old_db)

        with connect_mini_db(old_db) as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(mini_player_heroes)"
                ).fetchall()
            }
        self.assertIn("stars", columns)

    def test_attack_formula_matches_villager_example(self):
        self.assertEqual(
            [calculate_attack(3, stars) for stars in range(5)],
            [3, 4, 6, 9, 13],
        )

    def test_cost_and_limits(self):
        self.assertEqual(upgrade_cost("common", 1), 10)
        self.assertEqual(upgrade_cost("common", 4), 40)
        self.assertEqual(upgrade_cost("uncommon", 5), 100)
        self.assertEqual(upgrade_cost("rare", 6), 240)
        self.assertEqual(upgrade_cost("legendary", 7), 700)
        self.assertEqual(max_stars_for_rarity("common"), 4)
        self.assertEqual(max_stars_for_rarity("uncommon"), 5)
        self.assertEqual(max_stars_for_rarity("rare"), 6)
        self.assertIsNone(max_stars_for_rarity("legendary"))

    def test_common_can_upgrade_to_four_stars(self):
        attacks = []
        costs = []
        for _ in range(4):
            result = upgrade_hero(
                self.player["id"], self.hero_id, self.db
            )
            attacks.append(result["attack"])
            costs.append(result["shards_spent"])

        self.assertEqual(attacks, [4, 6, 9, 13])
        self.assertEqual(costs, [10, 20, 30, 40])

        hero = get_player_hero(
            self.player["id"], self.hero_id, self.db
        )
        self.assertEqual(hero["stars"], 4)
        self.assertEqual(hero["attack"], 13)
        self.assertEqual(hero["shards"], 100)

        active = get_active_hero(self.player["id"], self.db)
        self.assertEqual(active["attack"], 13)

        with self.assertRaises(HeroUpgradeMaxStars):
            upgrade_hero(self.player["id"], self.hero_id, self.db)

    def test_upgrade_requires_enough_shards(self):
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_players SET shards = 9 WHERE id = ?",
                (self.player["id"],),
            )
            conn.commit()

        with self.assertRaises(HeroUpgradeInsufficientShards):
            upgrade_hero(self.player["id"], self.hero_id, self.db)

    def test_legacy_hero_shards_migrate_to_shared_balance(self):
        legacy_db = Path(self.tempdir.name) / "legacy_shards.db"
        with connect_mini_db(legacy_db) as conn:
            conn.execute(
                """
                CREATE TABLE mini_players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    world_id INTEGER NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    character_name TEXT NOT NULL,
                    coins INTEGER NOT NULL DEFAULT 0,
                    active_hero_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE mini_player_heroes (
                    player_id INTEGER NOT NULL,
                    hero_id INTEGER NOT NULL,
                    copies INTEGER NOT NULL DEFAULT 1,
                    shards INTEGER NOT NULL DEFAULT 0,
                    stars INTEGER NOT NULL DEFAULT 0,
                    obtained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (player_id, hero_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO mini_players (
                    id, world_id, telegram_user_id, character_name
                ) VALUES (1, 1, 1001, 'Legacy')
                """
            )
            conn.execute(
                "INSERT INTO mini_player_heroes (player_id, hero_id, shards) VALUES (1, 10, 15)"
            )
            conn.execute(
                "INSERT INTO mini_player_heroes (player_id, hero_id, shards) VALUES (1, 11, 25)"
            )
            conn.commit()

        init_mini_db(legacy_db)

        with connect_mini_db(legacy_db) as conn:
            shards = conn.execute(
                "SELECT shards FROM mini_players WHERE id = 1"
            ).fetchone()[0]
            legacy_left = conn.execute(
                "SELECT SUM(shards) FROM mini_player_heroes WHERE player_id = 1"
            ).fetchone()[0]

        self.assertEqual(shards, 40)
        self.assertEqual(legacy_left, 0)


if __name__ == "__main__":
    unittest.main()
