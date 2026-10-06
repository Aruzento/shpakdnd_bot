import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.handlers import (
    _battle_hero_menu, _boss_private_menu, boss_heroes_callback,
    boss_select_hero_callback,
)


class HeroSelectionMenuTests(unittest.TestCase):
    def test_selection_button_only_for_registered_prebattle(self):
        for status in ("announced", "ready", "fighting", "defeated"):
            for joined in (True, False):
                menu = _boss_private_menu(
                    1, 101, {"id": 3, "status": status}, joined=joined, is_admin=False,
                )
                labels = [b.text for row in menu.inline_keyboard for b in row]
                self.assertEqual("🎴 Выбрать героя" in labels, joined and status in ("announced", "ready"))

    def test_pagination_has_owned_hero_ids_and_clamps_pages(self):
        heroes = [{"id": n, "name": f"Hero {n}", "rarity": "rare"} for n in range(1, 19)]
        first = _battle_hero_menu(1, 101, 3, heroes, 0)
        self.assertEqual(first.inline_keyboard[0][0].text, "Hero 1 • rare")
        self.assertEqual(first.inline_keyboard[0][0].callback_data, "miniboss:hero:1:101:3:1")
        self.assertEqual(len([row for row in first.inline_keyboard if row[0].callback_data.startswith("miniboss:hero:")]), 8)
        last = _battle_hero_menu(1, 101, 3, heroes, 999)
        self.assertEqual(last.inline_keyboard[0][0].text, "Hero 17 • rare")
        self.assertEqual(last.inline_keyboard[-2][0].callback_data, "miniboss:heroes:1:101:3:1")
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for row in last.inline_keyboard for b in row))


class HeroSelectionCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.callback = SimpleNamespace(
            id="v2-selector-callback",
            data="miniboss:heroes:1:101:3:0",
            from_user=SimpleNamespace(id=101),
            answer=AsyncMock(),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(ephemeral_message_id=7, delete_ephemeral=AsyncMock()),
        )
        self.world = {"id": 1, "chat_id": -1001, "thread_id": 42}
        self.player = {"id": 10}
        self.boss = {"id": 3, "world_id": 1, "status": "announced"}
        patches = [
            patch("app.mini.boss.selection._load_world", return_value=self.world),
            patch("app.mini.boss.selection._load_player", return_value=self.player),
            patch("app.mini.boss.selection.get_boss", return_value=self.boss),
            patch("app.mini.boss.selection.list_participants", return_value=[{"player_id": 10}]),
        ]
        for mock_patch in patches:
            mock_patch.start()
            self.addCleanup(mock_patch.stop)

    async def test_selector_sends_ephemeral_to_callback_owner(self):
        with patch("app.mini.boss.selection.get_player_heroes", return_value=[{"id": 9, "name": "Hero", "rarity": "rare"}]) as heroes:
            await boss_heroes_callback(self.callback)
        heroes.assert_called_once_with(10)
        sent = self.callback.bot.send_message.call_args.kwargs
        params = sent["ephemeral_message_parameters"]
        self.assertEqual(params.receiver_user_id, 101)
        self.assertEqual(sent["chat_id"], -1001)
        self.callback.message.delete_ephemeral.assert_awaited_once()

    async def test_selector_rejects_other_owner(self):
        self.callback.from_user.id = 102
        await boss_heroes_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.assertTrue(self.callback.answer.call_args.kwargs["show_alert"])

    async def test_selector_rejects_unregistered_and_started(self):
        with patch("app.mini.boss.selection.list_participants", return_value=[]):
            await boss_heroes_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.boss["status"] = "fighting"
        await boss_heroes_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()

    async def test_selection_uses_service_then_returns_to_boss_home(self):
        self.callback.data = "miniboss:hero:1:101:3:9"
        with patch("app.mini.boss.selection.select_battle_hero", return_value={"name": "Hero"}) as select, patch("app.mini.boss.selection.show_boss_home", new_callable=AsyncMock) as home:
            await boss_select_hero_callback(self.callback)
        select.assert_called_once_with(3, 10, 9)
        home.assert_awaited_once_with(self.callback, self.world, self.player)
        self.assertEqual(self.callback.answer.call_args.args[0], "✅ На этот бой выбран: Hero")

    async def test_selection_race_with_start_reports_service_error(self):
        from app.mini.boss.service import BossError
        self.callback.data = "miniboss:hero:1:101:3:9"
        with patch("app.mini.boss.selection.select_battle_hero", side_effect=BossError("Бой уже начался")), patch("app.mini.boss.selection.show_boss_home", new_callable=AsyncMock) as home:
            await boss_select_hero_callback(self.callback)
        home.assert_not_awaited()
        self.assertTrue(self.callback.answer.call_args.kwargs["show_alert"])

    async def test_cross_world_callback_is_rejected(self):
        self.boss["world_id"] = 2
        await boss_heroes_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
