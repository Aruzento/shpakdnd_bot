import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.public import (
    _TURN_PUBLISH_LOCKS,
    format_public_boss,
    format_public_turn,
    public_boss_menu,
    public_turn_menu,
    publish_admin_victory,
    replace_public_turn,
)


class FakeBot:
    def __init__(self):
        self.sent = []
        self.deleted = []
        self.edited = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=456)

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)


class PublicBossTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _TURN_PUBLISH_LOCKS.clear()
        self.boss = {
            "id": 7,
            "name": "Тестовый босс",
            "status": "fighting",
            "current_hp": 90,
            "max_hp": 100,
            "current_round": 2,
            "current_turn_position": 2,
            "skip_after_hours": 4,
            "reward_coins": 30,
            "reward_items_json": "[]",
            "reward_shields": 2,
            "reward_shields_max": 3,
            "reward_percent": 100,
            "battle_result": "",
            "turn_message_id": 123,
        }
        self.participants = [
            {
                "queue_position": 1,
                "username": "@first",
                "character_name": "Первый",
                "hero_name": "Герой 1",
                "battle_attack": 10,
            },
            {
                "queue_position": 2,
                "username": "@second",
                "character_name": "Второй",
                "hero_name": "Герой 2",
                "battle_attack": 20,
            },
        ]

    def test_turn_message_mentions_current_player(self):
        text = format_public_turn(self.boss, self.participants)
        self.assertIn("⚔️ Ход: @second", text)
        self.assertIn("❤️ HP: 90/100", text)
        self.assertIn("РАУНД 2", text)

    def test_turn_button_contains_round_and_position_token(self):
        markup = public_turn_menu(1, self.boss)
        self.assertIsNotNone(markup)
        callback_data = markup.inline_keyboard[0][0].callback_data
        self.assertEqual(callback_data, "miniboss:hit:1:7:2:2")

    def test_static_fighting_card_has_no_hit_button(self):
        markup = public_boss_menu(1, self.boss)
        labels = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertNotIn("⚔️ Ударить босса", labels)
        self.assertIn("👥 Участники", labels)

    def test_admin_victory_card_says_everyone_was_rewarded(self):
        boss = dict(self.boss)
        boss.update(
            {
                "status": "defeated",
                "battle_result": "admin_victory",
                "current_hp": 0,
                "min_players": 2,
                "description": "Тест",
            }
        )
        text = format_public_boss(boss, self.participants)
        self.assertIn("Награда выдана всем зарегистрированным участникам", text)

    @patch("app.mini.boss.public.set_turn_message")
    async def test_admin_victory_replaces_turn_with_public_message(
        self,
        mocked_set_turn_message,
    ):
        bot = FakeBot()
        world = {"id": 1, "chat_id": -1001, "thread_id": 2684}
        boss = dict(self.boss)
        boss["status"] = "defeated"

        applied = await publish_admin_victory(
            bot,
            world,
            boss,
            old_turn_message_id=123,
        )

        self.assertTrue(applied)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("Сами боги услышали клич", bot.sent[0]["text"])
        self.assertIn("Поздравляю, вы победили", bot.sent[0]["text"])
        self.assertEqual(bot.deleted[0]["message_id"], 123)
        mocked_set_turn_message.assert_called_once_with(7, None)

    @patch("app.mini.boss.public.get_boss")
    @patch("app.mini.boss.public.set_turn_message")
    @patch("app.mini.boss.public.list_participants")
    async def test_after_hit_new_public_turn_replaces_old_one(
        self,
        mocked_participants,
        mocked_set_turn_message,
        mocked_get_boss,
    ):
        mocked_participants.return_value = self.participants
        mocked_get_boss.return_value = dict(self.boss)
        bot = FakeBot()
        world = {"id": 1, "chat_id": -1001, "thread_id": 2684}
        snapshot = dict(self.boss)

        applied = await replace_public_turn(
            bot,
            world,
            snapshot,
            notice="💥 @first наносит 10 урона.",
        )

        self.assertTrue(applied)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("💥 @first наносит 10 урона.", bot.sent[0]["text"])
        self.assertIn("⚔️ Ход: @second", bot.sent[0]["text"])
        self.assertEqual(bot.deleted[0]["message_id"], 123)
        mocked_set_turn_message.assert_called_once_with(7, 456, notice_json=ANY)

    @patch("app.mini.boss.public.get_boss")
    @patch("app.mini.boss.public.set_turn_message")
    async def test_stale_turn_snapshot_does_not_publish_duplicate(
        self,
        mocked_set_turn_message,
        mocked_get_boss,
    ):
        fresh = dict(self.boss)
        fresh["turn_message_id"] = 456
        mocked_get_boss.return_value = fresh
        bot = FakeBot()
        world = {"id": 1, "chat_id": -1001, "thread_id": 2684}
        stale = dict(self.boss)

        applied = await replace_public_turn(
            bot,
            world,
            stale,
            notice="устаревшее обновление",
        )

        self.assertTrue(applied)
        self.assertEqual(bot.sent, [])
        mocked_set_turn_message.assert_not_called()
        self.assertEqual(stale["turn_message_id"], 456)

    @patch("app.mini.boss.public.get_boss")
    @patch("app.mini.boss.public.set_turn_message")
    async def test_older_game_state_does_not_overwrite_newer_turn(
        self,
        mocked_set_turn_message,
        mocked_get_boss,
    ):
        fresh = dict(self.boss)
        fresh["current_turn_position"] = 1
        fresh["current_round"] = 3
        mocked_get_boss.return_value = fresh
        bot = FakeBot()
        world = {"id": 1, "chat_id": -1001, "thread_id": 2684}
        stale = dict(self.boss)

        applied = await replace_public_turn(bot, world, stale, notice="старый ход")

        self.assertTrue(applied)
        self.assertEqual(bot.sent, [])
        mocked_set_turn_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
