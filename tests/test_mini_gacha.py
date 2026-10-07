import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.gacha import (
    GachaInsufficientFunds,
    _rarity_weight_units,
    get_gacha_state,
    perform_gacha_pull,
)
from app.mini.db import connect_mini_db
from app.mini.heroes import (
    get_active_hero,
    get_collection_summary,
    get_player_hero,
    set_active_hero,
    sync_hero_catalog,
)
from app.mini.players import create_mini_player, get_mini_player
from app.mini.boss.schema import init_boss_db
from app.mini.items import EFFECT_GACHA_LUCK, get_effect_charges, use_inventory_item
from app.mini.schema import init_mini_db
from app.mini.shop import (
    get_offers_by_category,
    purchase_offer,
    sync_shop_catalog,
)
from app.mini.wallet import add_coins, get_balance
from app.mini.worlds import sync_configured_mini_worlds


class MiniGachaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        worlds = sync_configured_mini_worlds(self.db)
        self.world_id = worlds[0]["id"]
        self.player = create_mini_player(
            self.world_id,
            999001,
            "@gacha_tester",
            "Призыватель",
            self.db,
        )
        sync_hero_catalog(self.db)
        sync_shop_catalog(self.world_id, self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_coin_pull_adds_hero_and_auto_activates_first(self):
        add_coins(self.player["id"], 100, "Тест", db_path=self.db)

        with patch("app.mini.gacha._choose_hero_code", return_value="Villager"):
            result = perform_gacha_pull(
                self.player["id"],
                payment="coins",
                db_path=self.db,
            )

        self.assertFalse(result["is_duplicate"])
        self.assertTrue(result["auto_activated"])
        self.assertEqual(get_balance(self.player["id"], self.db), 70)

        active = get_active_hero(self.player["id"], self.db)
        self.assertEqual(active["code"], "Villager")

        summary = get_collection_summary(self.player["id"], self.db)
        self.assertEqual(summary["owned"], 1)

    def test_duplicate_adds_copy_and_shards(self):
        add_coins(self.player["id"], 100, "Тест", db_path=self.db)

        with patch("app.mini.gacha._choose_hero_code", return_value="Villager"):
            first = perform_gacha_pull(
                self.player["id"], "coins", self.db
            )
            second = perform_gacha_pull(
                self.player["id"], "coins", self.db
            )

        self.assertFalse(first["is_duplicate"])
        self.assertTrue(second["is_duplicate"])
        self.assertEqual(second["copies"], 2)
        self.assertEqual(second["shards_awarded"], 10)
        self.assertEqual(second["shards"], 10)
        player = get_mini_player(self.world_id, 999001, self.db)
        self.assertEqual(player["shards"], 10)

    def test_ticket_purchase_and_ticket_pull(self):
        add_coins(self.player["id"], 100, "Тест", db_path=self.db)
        offers = get_offers_by_category(self.world_id, "summons", self.db)
        ticket_offer = next(o for o in offers if o["code"] == "summon_ticket")
        purchase_offer(
            self.player["id"], self.world_id, ticket_offer["id"], self.db
        )

        before = get_balance(self.player["id"], self.db)
        state = get_gacha_state(self.player["id"], self.db)
        self.assertEqual(state["tickets"], 4)

        with patch("app.mini.gacha._choose_hero_code", return_value="CityBlacksmith"):
            result = perform_gacha_pull(
                self.player["id"], "ticket", self.db
            )

        self.assertTrue(result["used_ticket"])
        self.assertEqual(result["cost_coins"], 0)
        self.assertEqual(result["tickets"], 3)
        self.assertEqual(get_balance(self.player["id"], self.db), before)

    def test_cannot_pull_with_not_enough_coins(self):
        with patch("app.mini.gacha._choose_hero_code", return_value="Villager"):
            with self.assertRaises(GachaInsufficientFunds):
                perform_gacha_pull(
                    self.player["id"], "coins", self.db
                )

        self.assertEqual(
            get_collection_summary(self.player["id"], self.db)["owned"],
            0,
        )


    def test_luck_potion_boosts_weights_and_is_consumed_by_one_pull(self):
        self.assertEqual(_rarity_weight_units(1, "legendary", luck_active=True), 110)
        self.assertEqual(_rarity_weight_units(5, "rare", luck_active=True), 650)
        self.assertEqual(_rarity_weight_units(69, "common", luck_active=True), 6900)

        with connect_mini_db(self.db) as conn:
            item_id = int(conn.execute(
                "SELECT id FROM mini_items WHERE code = 'luck_potion'"
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO mini_inventory (player_id, item_id, quantity) VALUES (?, ?, 1)",
                (self.player["id"], item_id),
            )
            conn.commit()

        use_inventory_item(
            self.player["id"], item_id, operation_key="luck", db_path=self.db
        )
        self.assertEqual(
            get_effect_charges(self.player["id"], EFFECT_GACHA_LUCK, self.db), 1
        )
        add_coins(self.player["id"], 100, "Тест", db_path=self.db)

        with patch("app.mini.gacha._choose_hero_code", return_value="Villager") as choose:
            result = perform_gacha_pull(self.player["id"], "coins", self.db)

        self.assertTrue(result["luck_used"])
        choose.assert_called_once_with(luck_active=True)
        self.assertEqual(
            get_effect_charges(self.player["id"], EFFECT_GACHA_LUCK, self.db), 0
        )

    def test_can_change_active_hero_only_if_owned(self):
        add_coins(self.player["id"], 100, "Тест", db_path=self.db)

        with patch("app.mini.gacha._choose_hero_code", return_value="Villager"):
            first = perform_gacha_pull(self.player["id"], "coins", self.db)
        with patch("app.mini.gacha._choose_hero_code", return_value="CityBlacksmith"):
            second = perform_gacha_pull(self.player["id"], "coins", self.db)

        hero = get_player_hero(self.player["id"], second["id"], self.db)
        selected = set_active_hero(self.player["id"], hero["id"], self.db)
        self.assertEqual(selected["code"], "CityBlacksmith")
        self.assertEqual(
            get_active_hero(self.player["id"], self.db)["code"],
            "CityBlacksmith",
        )

        with self.assertRaises(ValueError):
            set_active_hero(self.player["id"], 999999, self.db)


if __name__ == "__main__":
    unittest.main()
