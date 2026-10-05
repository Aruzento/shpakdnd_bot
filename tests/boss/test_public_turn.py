import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.public import (
    format_public_turn,
    public_boss_menu,
    replace_public_turn,
)


class FakeBot:
    def __init__(self):
        self.sent = []
        self.deleted = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=456)

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)


class PublicBossTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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
        self.assertIn("🔄 Раунд: 2", text)

    def test_static_fighting_card_has_no_hit_button(self):
        markup = public_boss_menu(1, self.boss)
        labels = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertNotIn("⚔️ Ударить босса", labels)
        self.assertIn("👥 Участники", labels)

    @patch("app.mini.boss.public.set_turn_message")
    @patch("app.mini.boss.public.list_participants")
    async def test_after_hit_new_public_turn_replaces_old_one(
        self,
        mocked_participants,
        mocked_set_turn_message,
    ):
        mocked_participants.return_value = self.participants
        bot = FakeBot()
        world = {"id": 1, "chat_id": -1001, "thread_id": 2684}

        applied = await replace_public_turn(
            bot,
            world,
            self.boss,
            notice="💥 @first наносит 10 урона.",
        )

        self.assertTrue(applied)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("💥 @first наносит 10 урона.", bot.sent[0]["text"])
        self.assertIn("⚔️ Ход: @second", bot.sent[0]["text"])
        self.assertEqual(bot.deleted[0]["message_id"], 123)
        mocked_set_turn_message.assert_called_once_with(7, 456)


if __name__ == "__main__":
    unittest.main()
