import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.handlers import _BOSS_PUBLISH_LOCKS, _publish_boss


class FakeBot:
    def __init__(self):
        self.sent_messages = []

    async def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)
        return SimpleNamespace(message_id=900)


class BossAnnouncementDedupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _BOSS_PUBLISH_LOCKS.clear()
        self.world = {"id": 1, "chat_id": -1001, "thread_id": 2684}
        self.boss = {
            "id": 5,
            "world_id": 1,
            "name": "Босс",
            "description": "Тест",
            "status": "announced",
            "current_hp": 100,
            "max_hp": 100,
            "min_players": 2,
            "reward_coins": 10,
            "reward_items_json": "[]",
            "reward_shields": 3,
            "reward_shields_max": 3,
            "reward_percent": 100,
            "image_path": "",
            "signup_message_id": None,
        }

    @patch("app.mini.boss.ui.get_boss")
    async def test_existing_announcement_is_not_sent_again(self, mocked_get_boss):
        fresh = dict(self.boss)
        fresh["signup_message_id"] = 777
        mocked_get_boss.return_value = fresh
        bot = FakeBot()
        callback = SimpleNamespace(bot=bot)

        await _publish_boss(callback, self.world, dict(self.boss))

        self.assertEqual(bot.sent_messages, [])

    @patch("app.mini.boss.ui.get_boss")
    async def test_stale_republish_callback_is_ignored(self, mocked_get_boss):
        fresh = dict(self.boss)
        fresh["signup_message_id"] = 888
        mocked_get_boss.return_value = fresh
        bot = FakeBot()
        callback = SimpleNamespace(bot=bot)

        await _publish_boss(
            callback,
            self.world,
            dict(self.boss),
            replace_existing=True,
            expected_message_id=777,
        )

        self.assertEqual(bot.sent_messages, [])


if __name__ == "__main__":
    unittest.main()
