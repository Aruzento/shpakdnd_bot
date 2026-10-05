import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.admin_grants import (
    get_mini_player_by_username,
    grant_mini_coins,
    grant_mini_coins_all,
    grant_mini_item,
    grant_mini_item_all,
    grant_mini_hero,
    grant_mini_hero_all,
    list_mini_items,
)
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.shop import sync_shop_catalog
from app.mini.wallet import get_balance, get_wallet_history
from app.mini.worlds import sync_configured_mini_worlds


class MiniAdminGrantTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        worlds = sync_configured_mini_worlds(self.db)
        self.world_id = worlds[0]["id"]
        self.player = create_mini_player(
            self.world_id,
            999001,
            "@grant_tester",
            "Получатель",
            self.db,
        )
        sync_shop_catalog(self.world_id, self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_find_player_by_username(self):
        player = get_mini_player_by_username(
            self.world_id,
            "@GRANT_TESTER",
            self.db,
        )
        self.assertIsNotNone(player)
        self.assertEqual(player["id"], self.player["id"])

    def test_admin_coin_grant_updates_wallet_history(self):
        result = grant_mini_coins(
            self.player["id"],
            123,
            "@arukozento",
            self.db,
        )
        self.assertEqual(result["balance"], 123)
        self.assertEqual(get_balance(self.player["id"], self.db), 123)
        history = get_wallet_history(self.player["id"], 10, self.db)
        self.assertEqual(history[0]["amount"], 123)
        self.assertIn("Админ-выдача", history[0]["reason"])

    def test_admin_item_grant(self):
        items = list_mini_items(self.db)
        self.assertGreaterEqual(len(items), 1)
        item = items[0]
        first = grant_mini_item(
            self.player["id"],
            item["id"],
            self.db,
        )
        second = grant_mini_item(
            self.player["id"],
            item["id"],
            self.db,
        )
        self.assertEqual(first["quantity"], 1)
        self.assertEqual(second["quantity"], 2)

    def test_admin_item_grant_accepts_item_code(self):
        result = grant_mini_item(
            self.player["id"],
            "summon_ticket",
            self.db,
        )
        self.assertEqual(result["code"], "summon_ticket")
        self.assertEqual(result["quantity"], 1)

    def test_admin_coin_grant_all_players(self):
        second = create_mini_player(
            self.world_id,
            999002,
            "@grant_tester_two",
            "Второй",
            self.db,
        )
        result = grant_mini_coins_all(
            self.world_id,
            25,
            "@arukozento",
            self.db,
        )
        self.assertEqual(result["players"], 2)
        self.assertEqual(result["total"], 50)
        self.assertEqual(get_balance(self.player["id"], self.db), 25)
        self.assertEqual(get_balance(second["id"], self.db), 25)

    def test_admin_item_grant_all_players_by_code(self):
        second = create_mini_player(
            self.world_id,
            999003,
            "@grant_tester_three",
            "Третий",
            self.db,
        )
        result = grant_mini_item_all(
            self.world_id,
            "summon_ticket",
            self.db,
        )
        self.assertEqual(result["players"], 2)
        self.assertEqual(result["item"]["code"], "summon_ticket")

        from app.mini.db import connect_mini_db
        with connect_mini_db(self.db) as conn:
            rows = conn.execute(
                """
                SELECT inv.player_id, inv.quantity
                FROM mini_inventory inv
                JOIN mini_items i ON i.id = inv.item_id
                WHERE i.code = 'summon_ticket'
                ORDER BY inv.player_id
                """
            ).fetchall()
        self.assertEqual(
            [(int(row[0]), int(row[1])) for row in rows],
            [(self.player["id"], 1), (second["id"], 1)],
        )

    def test_admin_hero_grant_by_code_is_idempotent(self):
        first = grant_mini_hero(
            self.player["id"],
            "Villager",
            self.db,
        )
        second = grant_mini_hero(
            self.player["id"],
            "Villager",
            self.db,
        )
        self.assertTrue(first["applied"])
        self.assertTrue(first["auto_activated"])
        self.assertFalse(second["applied"])
        self.assertFalse(second["auto_activated"])

    def test_admin_hero_grant_all_players_by_code(self):
        second = create_mini_player(
            self.world_id,
            999004,
            "@grant_tester_four",
            "Четвёртый",
            self.db,
        )
        result = grant_mini_hero_all(
            self.world_id,
            "Villager",
            self.db,
        )
        self.assertEqual(result["players"], 2)
        self.assertEqual(result["granted"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["hero"]["code"], "Villager")

        repeat = grant_mini_hero_all(
            self.world_id,
            "Villager",
            self.db,
        )
        self.assertEqual(repeat["granted"], 0)
        self.assertEqual(repeat["skipped"], 2)

        from app.mini.db import connect_mini_db
        with connect_mini_db(self.db) as conn:
            count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM mini_player_heroes ph
                    JOIN mini_heroes h ON h.id = ph.hero_id
                    WHERE h.code = 'Villager'
                      AND ph.player_id IN (?, ?)
                    """,
                    (self.player["id"], second["id"]),
                ).fetchone()[0]
            )
        self.assertEqual(count, 2)

    def test_invalid_item_rejected(self):
        with self.assertRaises(ValueError):
            grant_mini_item(
                self.player["id"],
                999999,
                self.db,
            )


if __name__ == "__main__":
    unittest.main()
