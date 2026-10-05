import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.catalog import get_boss_template, list_boss_templates
from app.mini.boss.schema import BOSS_TABLES, init_boss_db
from app.mini.boss.service import (
    BossError,
    BossNotEnoughPlayers,
    close_registration,
    create_boss_event,
    get_active_boss,
    list_participants,
    register_player,
    reopen_registration,
    unregister_player,
)
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds


class MiniBossRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.world_id = sync_configured_mini_worlds(self.db)[0]["id"]
        sync_hero_catalog(self.db)

        self.player1 = self._create_player(701, "@boss_one", "Первый")
        self.player2 = self._create_player(702, "@boss_two", "Второй")

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_player(self, user_id: int, username: str, name: str) -> dict:
        player = create_mini_player(
            self.world_id, user_id, username, name, self.db
        )
        with connect_mini_db(self.db) as conn:
            hero_id = int(conn.execute(
                "SELECT id FROM mini_heroes WHERE code = 'Villager'"
            ).fetchone()[0])
            conn.execute(
                """
                INSERT INTO mini_player_heroes (player_id, hero_id, copies, shards, stars)
                VALUES (?, ?, 1, 0, 0)
                """,
                (player["id"], hero_id),
            )
            conn.execute(
                "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
                (hero_id, player["id"]),
            )
            conn.commit()
        return player

    def test_boss_tables_are_created_by_boss_module(self):
        with connect_mini_db(self.db) as conn:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertTrue(set(BOSS_TABLES).issubset(existing))

    def test_legacy_boss_table_gets_new_columns(self):
        old_db = Path(self.tempdir.name) / "old_boss.db"
        with connect_mini_db(old_db) as conn:
            conn.execute(
                """
                CREATE TABLE mini_bosses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    world_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'announced'
                )
                """
            )
            conn.commit()

        init_boss_db(old_db)
        with connect_mini_db(old_db) as conn:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(mini_bosses)").fetchall()
            }
        self.assertTrue(
            {"template_code", "image_path", "signup_message_id", "signup_message_kind"}
            .issubset(columns)
        )

    def test_catalog_isolated_and_loads(self):
        templates = list_boss_templates()
        self.assertGreaterEqual(len(templates), 1)
        self.assertIsNotNone(get_boss_template("training_golem"))

    def test_create_and_register(self):
        boss = create_boss_event(
            self.world_id, "training_golem", 999, self.db
        )
        self.assertEqual(boss["status"], "announced")
        self.assertEqual(get_active_boss(self.world_id, self.db)["id"], boss["id"])

        first = register_player(boss["id"], self.player1["id"], self.db)
        repeat = register_player(boss["id"], self.player1["id"], self.db)
        self.assertTrue(first["applied"])
        self.assertFalse(repeat["applied"])
        self.assertEqual(len(list_participants(boss["id"], self.db)), 1)

    def test_only_one_active_boss_per_world(self):
        create_boss_event(self.world_id, "training_golem", 999, self.db)
        with self.assertRaises(BossError):
            create_boss_event(self.world_id, "training_golem", 999, self.db)

    def test_close_requires_minimum_then_reopen(self):
        boss = create_boss_event(
            self.world_id, "training_golem", 999, self.db
        )
        register_player(boss["id"], self.player1["id"], self.db)
        with self.assertRaises(BossNotEnoughPlayers):
            close_registration(boss["id"], self.db)

        register_player(boss["id"], self.player2["id"], self.db)
        ready = close_registration(boss["id"], self.db)
        self.assertEqual(ready["status"], "ready")
        positions = [p["queue_position"] for p in list_participants(boss["id"], self.db)]
        self.assertEqual(positions, [1, 2])

        opened = reopen_registration(boss["id"], self.db)
        self.assertEqual(opened["status"], "announced")

    def test_registration_requires_active_hero(self):
        player = create_mini_player(
            self.world_id, 703, "@boss_nohero", "Без героя", self.db
        )
        boss = create_boss_event(
            self.world_id, "training_golem", 999, self.db
        )
        with self.assertRaises(BossError):
            register_player(boss["id"], player["id"], self.db)

    def test_unregister_during_signup(self):
        boss = create_boss_event(
            self.world_id, "training_golem", 999, self.db
        )
        register_player(boss["id"], self.player1["id"], self.db)
        self.assertTrue(unregister_player(boss["id"], self.player1["id"], self.db))
        self.assertEqual(list_participants(boss["id"], self.db), [])


if __name__ == "__main__":
    unittest.main()
