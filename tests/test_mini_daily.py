import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.daily import claim_daily, get_daily_claim
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.wallet import get_balance, get_wallet_history
from app.mini.worlds import sync_configured_mini_worlds
from tests.topic_fixtures import isolated_topics


class MiniDailyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"

        init_mini_db(self.db)

        worlds = sync_configured_mini_worlds(self.db)
        self.world_id = worlds[0]["id"]

        self.player = create_mini_player(
            self.world_id,
            777001,
            "@daily_tester",
            "Дед Максим",
            self.db,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_daily_can_be_claimed_once_per_day(self):
        first = claim_daily(
            self.player["id"],
            self.player["character_name"],
            "2026-10-04",
            self.db,
        )

        second = claim_daily(
            self.player["id"],
            self.player["character_name"],
            "2026-10-04",
            self.db,
        )

        self.assertTrue(first["claimed"])
        self.assertFalse(second["claimed"])

        self.assertEqual(
            first["coins_earned"],
            second["coins_earned"],
        )
        self.assertEqual(
            first["story_text"],
            second["story_text"],
        )

        self.assertEqual(
            get_balance(self.player["id"], self.db),
            first["coins_earned"],
        )

        history = get_wallet_history(
            self.player["id"],
            10,
            self.db,
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0]["reason"],
            "Ежедневное приключение",
        )

    def test_next_day_can_be_claimed_again(self):
        day_one = claim_daily(
            self.player["id"],
            self.player["character_name"],
            "2026-10-04",
            self.db,
        )

        day_two = claim_daily(
            self.player["id"],
            self.player["character_name"],
            "2026-10-05",
            self.db,
        )

        self.assertTrue(day_one["claimed"])
        self.assertTrue(day_two["claimed"])

        self.assertEqual(
            get_balance(self.player["id"], self.db),
            day_one["coins_earned"] + day_two["coins_earned"],
        )

        self.assertEqual(
            len(get_wallet_history(self.player["id"], 10, self.db)),
            2,
        )

    def test_daily_claim_is_persisted(self):
        result = claim_daily(
            self.player["id"],
            self.player["character_name"],
            "2026-10-04",
            self.db,
        )

        stored = get_daily_claim(
            self.player["id"],
            "2026-10-04",
            self.db,
        )

        self.assertIsNotNone(stored)
        self.assertEqual(
            stored["coins_earned"],
            result["coins_earned"],
        )
        self.assertEqual(
            stored["story_text"],
            result["story_text"],
        )
        self.assertIn(
            stored["rarity"],
            {"common", "rare", "legendary"},
        )


if __name__ == "__main__":
    unittest.main()
