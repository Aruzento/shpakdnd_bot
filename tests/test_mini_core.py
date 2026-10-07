import os
import tempfile
import unittest
from pathlib import Path
from importlib import import_module
from tests.topic_fixtures import (
    TEST_CHAT_ID, DND_THREAD_ID, MINI_THREAD_ID, TOPIC_SETTINGS_MODULES,
    isolated_topics, topic_settings,
)

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.db import connect_mini_db
from app.mini.players import create_mini_player
from app.mini.schema import MINI_TABLES, init_mini_db
from app.mini.wallet import (
    InsufficientFundsError,
    add_coins,
    get_balance,
    get_wallet_history,
    spend_coins,
)
from app.mini.worlds import (
    get_mini_world,
    get_mini_world_by_id,
    sync_configured_mini_worlds,
)


class MiniCoreTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_configured_world_is_created(self):
        worlds = sync_configured_mini_worlds(self.db)

        target = [
            world
            for world in worlds
            if world["chat_id"] == TEST_CHAT_ID
            and world["thread_id"] == MINI_THREAD_ID
        ]

        self.assertEqual(len(worlds), 1)
        self.assertEqual(len(target), 1)
        self.assertIsNone(get_mini_world(TEST_CHAT_ID, DND_THREAD_ID, self.db))

        world = get_mini_world(
            TEST_CHAT_ID,
            MINI_THREAD_ID,
            self.db,
        )
        self.assertIsNotNone(world)
        self.assertEqual(
            get_mini_world_by_id(world["id"], self.db)["thread_id"],
            MINI_THREAD_ID,
        )

    def test_wallet_is_atomic_and_idempotent(self):
        world_id = sync_configured_mini_worlds(self.db)[0]["id"]
        player = create_mini_player(
            world_id,
            123456,
            "@tester",
            "Тестовый персонаж",
            self.db,
        )

        first = add_coins(
            player["id"],
            25,
            "Награда",
            operation_key="reward:1",
            db_path=self.db,
        )
        second = add_coins(
            player["id"],
            25,
            "Награда",
            operation_key="reward:1",
            db_path=self.db,
        )

        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(get_balance(player["id"], self.db), 25)
        self.assertEqual(player["shards"], 0)

        spend_coins(
            player["id"],
            7,
            "Покупка",
            operation_key="purchase:1",
            db_path=self.db,
        )
        self.assertEqual(get_balance(player["id"], self.db), 18)

        with self.assertRaises(InsufficientFundsError):
            spend_coins(
                player["id"],
                100,
                "Слишком дорого",
                db_path=self.db,
            )

        self.assertEqual(get_balance(player["id"], self.db), 18)
        self.assertEqual(
            len(get_wallet_history(player["id"], 10, self.db)),
            2,
        )

    def test_all_mini_tables_are_created(self):
        import sqlite3

        with connect_mini_db(self.db) as conn:
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        self.assertTrue(set(MINI_TABLES).issubset(existing))


class TopicFixtureTests(unittest.TestCase):
    def test_all_imports_restore_original_settings_even_after_failure(self):
        modules = [import_module(name) for name in TOPIC_SETTINGS_MODULES]
        originals = [module.TOPIC_SETTINGS for module in modules]
        source = topic_settings()
        for fail in (False, True):
            with self.subTest(failure=fail):
                try:
                    with isolated_topics(source) as settings:
                        for module in modules:
                            self.assertIs(module.TOPIC_SETTINGS, settings)
                        settings[TEST_CHAT_ID][MINI_THREAD_ID]['mini'] = False
                        if fail:
                            raise RuntimeError('fixture cleanup')
                except RuntimeError as error:
                    self.assertEqual(str(error), 'fixture cleanup')
                for module, original in zip(modules, originals):
                    self.assertIs(module.TOPIC_SETTINGS, original)
                self.assertTrue(source[TEST_CHAT_ID][MINI_THREAD_ID]['mini'])


if __name__ == "__main__":
    unittest.main()
