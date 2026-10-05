import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.schema import init_boss_db
from app.mini.db import connect_mini_db
from app.mini.items import (
    EFFECT_BOSS_DAMAGE,
    EFFECT_BOSS_PHANTOM,
    EFFECT_GACHA_LUCK,
    get_effect_charges,
    get_certificate_for_use,
    mark_certificate_requested,
    use_inventory_item,
)
from app.mini.players import create_mini_player, get_mini_player
from app.mini.schema import init_mini_db
from app.mini.shop import get_offers_by_category, purchase_offer, sync_shop_catalog
from app.mini.worlds import sync_configured_mini_worlds


class MiniItemUseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.world_id = sync_configured_mini_worlds(self.db)[0]["id"]
        self.player = create_mini_player(
            self.world_id, 7001, "@item_tester", "Алхимик", self.db
        )
        sync_shop_catalog(self.world_id, self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def _grant(self, code: str, quantity: int = 1) -> int:
        with connect_mini_db(self.db) as conn:
            row = conn.execute(
                "SELECT id FROM mini_items WHERE code = ?", (code,)
            ).fetchone()
            self.assertIsNotNone(row, code)
            item_id = int(row[0])
            conn.execute(
                """
                INSERT INTO mini_inventory (player_id, item_id, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(player_id, item_id) DO UPDATE SET
                    quantity = mini_inventory.quantity + excluded.quantity
                """,
                (self.player["id"], item_id, quantity),
            )
            conn.commit()
        return item_id

    def _quantity(self, item_id: int) -> int:
        with connect_mini_db(self.db) as conn:
            row = conn.execute(
                """
                SELECT quantity FROM mini_inventory
                WHERE player_id = ? AND item_id = ?
                """,
                (self.player["id"], item_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def test_coin_pouch_gives_10_to_30_and_is_idempotent_per_operation(self):
        item_id = self._grant("boss_coin_pouch", 2)
        result = use_inventory_item(
            self.player["id"],
            item_id,
            operation_key="same-click",
            amount_picker=lambda low, high: 23,
            db_path=self.db,
        )
        self.assertEqual(result["amount"], 23)
        self.assertEqual(result["coins"], 23)
        self.assertEqual(self._quantity(item_id), 1)

        repeated = use_inventory_item(
            self.player["id"],
            item_id,
            operation_key="same-click",
            amount_picker=lambda low, high: 30,
            db_path=self.db,
        )
        self.assertTrue(repeated["repeated"])
        self.assertEqual(repeated["amount"], 23)
        self.assertEqual(self._quantity(item_id), 1)
        self.assertEqual(
            get_mini_player(self.world_id, 7001, self.db)["coins"], 23
        )

    def test_shard_casket_gives_10_to_30_shards(self):
        item_id = self._grant("boss_shard_casket")
        result = use_inventory_item(
            self.player["id"],
            item_id,
            operation_key="casket-1",
            amount_picker=lambda low, high: 17,
            db_path=self.db,
        )
        self.assertEqual(result["amount"], 17)
        self.assertEqual(result["shards"], 17)
        self.assertEqual(self._quantity(item_id), 0)

    def test_potions_add_future_effect_charges(self):
        cases = [
            ("luck_potion", EFFECT_GACHA_LUCK),
            ("damage_potion", EFFECT_BOSS_DAMAGE),
            ("phantom_participation_potion", EFFECT_BOSS_PHANTOM),
        ]
        for index, (code, effect_key) in enumerate(cases, start=1):
            item_id = self._grant(code)
            result = use_inventory_item(
                self.player["id"],
                item_id,
                operation_key=f"potion-{index}",
                db_path=self.db,
            )
            self.assertEqual(result["charges"], 1)
            self.assertEqual(
                get_effect_charges(self.player["id"], effect_key, self.db), 1
            )
            self.assertEqual(self._quantity(item_id), 0)

    def test_certificate_moves_to_requested(self):
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_players SET coins = 1000 WHERE id = ?",
                (self.player["id"],),
            )
            conn.commit()
        offers = get_offers_by_category(self.world_id, "fun", self.db)
        offer = next(o for o in offers if o["code"] == "chat_title")
        purchase = purchase_offer(
            self.player["id"], self.world_id, offer["id"], self.db
        )
        purchase_id = purchase["purchase_id"]
        self.assertIsNotNone(
            get_certificate_for_use(self.player["id"], purchase_id, self.db)
        )
        result = mark_certificate_requested(
            self.player["id"], purchase_id, self.db
        )
        self.assertEqual(result["status"], "requested")
        self.assertIsNone(
            get_certificate_for_use(self.player["id"], purchase_id, self.db)
        )


if __name__ == "__main__":
    unittest.main()
