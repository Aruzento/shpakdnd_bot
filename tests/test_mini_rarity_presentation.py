
"""Presentation regression: rarity stays immediately before the summoned name."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, sentinel

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.presentation import rarity_emoji
from app.mini.ui.hero_cards import hero_caption
from app.mini.ui.heroes import _collection_menu, gacha_pull_callback, gacha_repeat_callback

EXPECTED = {"common": "⚪", "uncommon": "🟢", "rare": "🟣", "legendary": "🟡"}


def result(duplicate=False, ticket=False):
    return {
        "id": 11, "is_duplicate": duplicate, "used_ticket": ticket,
        "cost_coins": 10, "balance": 90, "auto_activated": not duplicate,
        "shards_awarded": 5, "copies": 2, "shards": 12,
    }


class RarityPresentationTests(unittest.TestCase):
    def test_all_rarities_in_new_duplicate_coin_and_ticket_results(self):
        for rarity, emoji in EXPECTED.items():
            hero = {"id": 11, "name": "Герой", "rarity": rarity}
            for duplicate in (False, True):
                for ticket in (False, True):
                    with self.subTest(rarity=rarity, duplicate=duplicate, ticket=ticket):
                        caption = hero_caption(hero, pull_result=result(duplicate, ticket))
                        heading = caption.splitlines()[0]
                        self.assertTrue(heading.startswith(f"{emoji} Герой • "))
                        self.assertEqual(heading.count(emoji), 1)
                        self.assertIn("Дубликат:" if duplicate else "Новый герой добавлен", caption)
                        self.assertIn("Потрачено: 1 билет" if ticket else "Потрачено: 10", caption)

    def test_collection_and_regular_card_have_one_matching_emoji(self):
        for rarity, emoji in EXPECTED.items():
            with self.subTest(rarity=rarity):
                hero = {"id": 11, "name": "Герой", "rarity": rarity}
                menu = _collection_menu(1, 2, {"heroes": [hero],"favorites":[11]})
                label = menu.inline_keyboard[0][0].text
                self.assertEqual(label, f"{emoji} Герой ★0")
                self.assertTrue(hero_caption(hero).startswith(f"{emoji} Герой • "))
                self.assertEqual(label.count(emoji), 1)

    def test_missing_or_unknown_rarity_keeps_existing_white_fallback(self):
        self.assertEqual(rarity_emoji(None), "⚪")
        self.assertEqual(rarity_emoji("unknown"), "⚪")


class GachaResultRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_and_repeat_routes_render_same_rarity_for_both_payments(self):
        hero = {"id": 11, "name": "Герой", "rarity": "rare"}
        world, player = {"id": 1}, {"id": 2}
        for handler in (gacha_pull_callback, gacha_repeat_callback):
            for payment in ("coins", "ticket"):
                for duplicate in (False, True):
                    with self.subTest(handler=handler.__name__, payment=payment, duplicate=duplicate):
                        callback = SimpleNamespace(
                            from_user=SimpleNamespace(id=3), answer=AsyncMock(),
                        )
                        pull = result(duplicate, payment == "ticket")
                        with (
                            patch("app.mini.ui.heroes._load_extended_context", return_value=(world, player, payment)),
                            patch("app.mini.ui.heroes.perform_gacha_pull", return_value=pull) as service,
                            patch("app.mini.ui.hero_cards.get_gacha_state", return_value={}),
                            patch("app.mini.ui.hero_cards.get_player_hero", return_value=hero),
                            patch("app.mini.ui.hero_cards.hero_card_menu", return_value=sentinel.markup),
                            patch("app.mini.ui.hero_cards.send_hero_card", new_callable=AsyncMock) as send,
                        ):
                            await handler(callback)
                        service.assert_called_once_with(2, payment=payment)
                        send.assert_awaited_once()
                        heading = send.call_args.args[3].splitlines()[0]
                        self.assertTrue(heading.startswith("🟣 Герой • "))
                        self.assertEqual(heading.count("🟣"), 1)
