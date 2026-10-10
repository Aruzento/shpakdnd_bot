import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.catalog import load_hero_catalog, load_shop_catalog, validate_content
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.shop import (
    ShopInsufficientFunds,
    ShopLimitReached,
    get_offers_by_category,
    get_player_goods,
    purchase_offer,
    sync_shop_catalog,
)
from app.mini.wallet import add_coins, get_balance
from app.mini.worlds import sync_configured_mini_worlds
from tests.topic_fixtures import isolated_topics


class MiniShopTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        worlds = sync_configured_mini_worlds(self.db)
        self.world_id = worlds[0]["id"]
        self.player = create_mini_player(
            self.world_id,
            888001,
            "@shop_tester",
            "Покупатель",
            self.db,
        )
        sync_shop_catalog(self.world_id, self.db)
        sync_hero_catalog(self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_catalog_files_load(self):
        shop = load_shop_catalog()
        heroes = load_hero_catalog()
        self.assertGreaterEqual(len(shop["categories"]), 4)
        self.assertGreaterEqual(len(shop["products"]), 1)
        self.assertGreaterEqual(len(heroes["heroes"]), 1)
        content = validate_content()
        self.assertGreaterEqual(content["active_heroes"], 1)

    def test_certificate_purchase_is_atomic(self):
        add_coins(
            self.player["id"],
            500,
            "Тест",
            db_path=self.db,
        )
        offers = get_offers_by_category(
            self.world_id,
            "dnd",
            self.db,
        )
        offer = offers[0]
        before = get_balance(self.player["id"], self.db)
        result = purchase_offer(
            self.player["id"],
            self.world_id,
            offer["id"],
            self.db,
        )
        self.assertEqual(
            result["balance"],
            before - offer["price"],
        )
        goods = get_player_goods(self.player["id"], self.db)
        self.assertEqual(len(goods["certificates"]), 1)

    def test_not_enough_money_does_not_create_purchase(self):
        offers = get_offers_by_category(
            self.world_id,
            "dnd",
            self.db,
        )
        with self.assertRaises(ShopInsufficientFunds):
            purchase_offer(
                self.player["id"],
                self.world_id,
                offers[0]["id"],
                self.db,
            )
        self.assertEqual(get_balance(self.player["id"], self.db), 0)
        goods = get_player_goods(self.player["id"], self.db)
        self.assertEqual(goods["certificates"], [])

    def test_per_player_limit(self):
        add_coins(
            self.player["id"],
            1000,
            "Тест",
            db_path=self.db,
        )
        offers = get_offers_by_category(
            self.world_id,
            "dnd",
            self.db,
        )
        limited = next(
            offer for offer in offers
            if offer["max_per_player"] == 1
        )
        purchase_offer(
            self.player["id"],
            self.world_id,
            limited["id"],
            self.db,
        )
        with self.assertRaises(ShopLimitReached):
            purchase_offer(
                self.player["id"],
                self.world_id,
                limited["id"],
                self.db,
            )


if __name__ == "__main__":
    unittest.main()
