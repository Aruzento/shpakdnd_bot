import asyncio
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import SendMessage
from app.mini.boss.public import (
    _TURN_PUBLISH_LOCKS, ensure_public_turn, format_public_boss,
    format_public_turn, replace_public_turn,
)
from app.mini.boss.notices import boss_event_line, combat_event_lines, timeout_event_lines
from app.mini.boss.watcher import boss_watch_loop
from app.mini.handlers import (
    _hero_caption, _home_content, _send_home_from_callback,
    character_callback, home_callback, launch_callback,
)
from app.mini.boss.handlers import boss_hit_callback, boss_start_callback
from app.mini.presentation import FACTION_LABELS


def api_error(cls=TelegramAPIError, message="timeout"):
    return cls(method=SendMessage(chat_id=-1001, text="test"), message=message)


class MessageFormatTests(unittest.TestCase):
    def setUp(self):
        self.hero = {
            "id": 9, "name": "Ведьмак", "faction": "warriors", "stars": 2, "attack": 80,
            "damage_type": "slashing", "class_tag": "martial", "attack_range": "melee",
            "special_trait": "armored", "passive_text": "Каждый третий удар усилен.",
            "description": "Описание героя.", "image_path": "witcher_isekai.png",
        }
        self.boss = {
            "id": 7, "name": "Багровый лорд", "faction": "dark", "description": "Описание босса.",
            "ability_text": "Восстанавливает здоровье.", "features_json": '["undead"]',
            "current_hp": 480, "max_hp": 480, "min_players": 2, "status": "announced",
            "reward_coins": 50, "reward_percent": 100, "reward_shields": 3,
            "reward_shields_max": 3, "reward_items_json": "[]",
            "current_round": 1, "current_turn_position": 1, "skip_after_hours": 4,
        }
        self.participants = [{"player_id": 10, "queue_position": 1, "username": "@user", "hero_name": "Ведьмак"}]

    def test_announcement_template_and_optional_rewards(self):
        text = format_public_boss(self.boss, self.participants)
        self.assertTrue(text.startswith("👹 Багровый лорд • 🌑 Тьма"))
        for expected in ("❤️ HP: 480/480", "💫 Способность: Восстанавливает здоровье.",
                         "‼️ Особенности: Нежить", "🎁 Награда: 50 монет • 🛡 Щиты: 3",
                         "🎁 Доп. награда: Нет."):
            self.assertIn(expected, text)
        self.boss["reward_items_json"] = '[{"code": "boss_coin_pouch", "quantity": 2}]'
        self.assertIn("×2", format_public_boss(self.boss, self.participants))

    def test_turn_template(self):
        self.boss["status"] = "fighting"
        expected = (
            "РАУНД 1\n\n👹 Багровый лорд - ❤️ HP: 480/480\n\n"
            "🛡 Щиты: 3/3 • 💎 Состояние награды: 100%\n\n"
            '⚔️ Ход: @user - "Ведьмак"\n⏳ На ход: 4 ч.'
        )
        self.assertEqual(format_public_turn(self.boss, self.participants), expected)

    def test_home_template(self):
        text = _home_content({"coins": 45, "shards": 12}, self.hero)
        self.assertTrue(text.startswith("Ведьмак • ⚔️ Воины • ⭐ 2"))
        for expected in ("Рубящий | Боевой класс", "Ближний бой | Бронированный",
                         "⚔️ Урон: 80", "🪙 45 монет • 🧩 12 осколков"):
            self.assertIn(expected, text)

    def test_character_template_and_gacha_result_are_preserved(self):
        text = _hero_caption(self.hero)
        self.assertTrue(text.startswith("Ведьмак • ⚔️ Воины • ⭐ 2"))
        self.assertIn("💫 Особый эффект: Каждый третий удар усилен.", text)
        self.assertTrue(text.endswith("Описание героя."))
        result = {
            "is_duplicate": False, "used_ticket": True, "balance": 10, "auto_activated": False,
        }
        self.assertIn("Новый герой добавлен", _hero_caption(self.hero, pull_result=result))
        self.assertIn("Потрачено: 1 билет", _hero_caption(self.hero, pull_result=result))

    def test_all_factions_have_readable_labels(self):
        self.assertEqual(set(FACTION_LABELS), {"commoners", "beasts", "monsters", "warriors", "dark", "neutral"})

    def test_initial_custom_classes_have_russian_labels(self):
        for tag, expected in (("warrior", "Воин"), ("guardian", "Страж"), ("mage", "Маг")):
            with self.subTest(tag=tag):
                self.hero["class_tag"] = tag
                self.assertIn(expected, _hero_caption(self.hero))

    def test_every_ability_event_has_a_message(self):
        kinds = (
            "paralysis", "paralysis_skip", "critical_strike", "banishment",
            "banishment_no_target", "shapeshifter", "shapeshifter_unchanged", "rapier",
            "hydra_regeneration", "kamikaze_destroyed_reward", "kamikaze_absorbed",
            "magic_shield_activated", "magic_shield_removed", "magic_shield_blocked",
        )
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertTrue(boss_event_line({"type": kind, "player_id": 10, "faction": "dark"}, self.participants))
        self.assertIn("@user", boss_event_line({"type": "banishment", "player_id": 10}, self.participants))

    def test_critical_notice_includes_both_reward_impacts_without_duplicate_passives(self):
        passive = {"username": "@user", "message": "+2 осколка"}
        result = {
            "state": {"participants": self.participants},
            "reward_events": [{
                "type": "reward_damage", "reward_percent": 90,
                "boss_events": [{"type": "critical_strike"}],
                "passive_events": [passive, passive],
                "attacks": [
                    {"type": "shield", "shields": 0, "passive_events": [passive]},
                    {"type": "reward_damage", "reward_percent": 90, "passive_events": [passive]},
                ],
            }],
        }
        lines = combat_event_lines(result)
        self.assertEqual(sum("+2 осколка" in line for line in lines), 2)
        self.assertTrue(any("щит" in line for line in lines))
        self.assertTrue(any("90%" in line for line in lines))
        self.assertTrue(any("Критический" in line for line in lines))

    def test_saved_notice_survives_refresh_but_not_next_turn(self):
        self.boss["status"] = "fighting"
        self.boss["turn_notice_json"] = json.dumps({
            "status": "fighting", "round": 1, "position": 1, "text": "⚡ Босс парализовал @user.",
        })
        self.assertIn("парализовал", format_public_turn(self.boss, self.participants))
        self.boss["current_round"] = 2
        self.assertNotIn("парализовал", format_public_turn(self.boss, self.participants))

    def test_forced_skip_never_claims_timeout(self):
        lines = timeout_event_lines({
            "skipped": [], "forced_skip_events": [{"type": "paralysis_skip", "player_id": 10}],
            "state": {"participants": self.participants},
        })
        self.assertIn("@user", " ".join(lines))
        self.assertNotIn("таймер", " ".join(lines))


class TurnMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _TURN_PUBLISH_LOCKS.clear()
        self.world = {"id": 1, "chat_id": -1001, "thread_id": 42}
        self.boss = {
            "id": 7, "name": "Босс", "current_hp": 90, "max_hp": 100, "status": "fighting",
            "current_round": 2, "current_turn_position": 1, "skip_after_hours": 4,
            "reward_shields": 2, "reward_shields_max": 3, "reward_percent": 100,
            "reward_coins": 10, "reward_items_json": "[]", "battle_result": "",
            "turn_message_id": 123, "turn_message_kind": "text",
        }
        self.participants = [{
            "player_id": 10, "queue_position": 1, "username": "@user",
            "hero_name": "Battle hero", "hero_image_path": "Villager.png", "active_hero_id": 999,
        }]
        self.bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=456)),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=789)),
            delete_message=AsyncMock(), edit_message_caption=AsyncMock(), edit_message_text=AsyncMock(),
        )
        self.addCleanup(_TURN_PUBLISH_LOCKS.clear)
        patches = [
            patch("app.mini.boss.public.get_boss", side_effect=lambda _: dict(self.boss)),
            patch("app.mini.boss.public.list_participants", return_value=self.participants),
            patch("app.mini.boss.public.set_turn_message", side_effect=self.save_message),
        ]
        for mock_patch in patches:
            mock_patch.start()
            self.addCleanup(mock_patch.stop)

    def save_message(self, boss_id, message_id, *, kind="text", notice_json=None):
        self.boss["turn_message_id"] = message_id
        self.boss["turn_message_kind"] = kind
        if notice_json is not None:
            self.boss["turn_notice_json"] = notice_json

    async def test_turn_sends_fixed_battle_hero_photo(self):
        self.assertTrue(await replace_public_turn(self.bot, self.world, dict(self.boss), notice="⚡ Паралич."))
        kwargs = self.bot.send_photo.call_args.kwargs
        self.assertEqual(Path(kwargs["photo"].path).name, "Villager.png")
        self.assertIn('⚔️ Ход: @user - "Battle hero"', kwargs["caption"])
        self.assertIn("Паралич", kwargs["caption"])
        self.assertEqual(self.boss["turn_message_kind"], "photo")
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_awaited_once_with(chat_id=-1001, message_id=123)

    async def test_concurrent_publish_does_not_duplicate_photo(self):
        snapshot = dict(self.boss)
        await asyncio.gather(
            replace_public_turn(self.bot, self.world, dict(snapshot)),
            replace_public_turn(self.bot, self.world, dict(snapshot)),
        )
        self.bot.send_photo.assert_awaited_once()

    async def test_watcher_recovery_upgrades_old_text_once_then_edits_caption(self):
        await ensure_public_turn(self.bot, self.world, dict(self.boss))
        await ensure_public_turn(self.bot, self.world, dict(self.boss))
        self.bot.send_photo.assert_awaited_once()
        self.bot.edit_message_caption.assert_awaited_once()
        self.bot.edit_message_text.assert_not_awaited()

    async def test_missing_photo_has_text_fallback(self):
        self.participants[0]["hero_image_path"] = "does-not-exist.png"
        self.assertTrue(await replace_public_turn(self.bot, self.world, dict(self.boss)))
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.boss["turn_message_kind"], "text")

    async def test_rejected_photo_has_exactly_one_safe_text_fallback(self):
        self.bot.send_photo.side_effect = api_error(TelegramBadRequest, "photo rejected")
        self.assertTrue(await replace_public_turn(self.bot, self.world, dict(self.boss)))
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()

    async def test_uncertain_photo_send_never_creates_text_duplicate(self):
        self.bot.send_photo.side_effect = api_error()
        self.assertFalse(await replace_public_turn(self.bot, self.world, dict(self.boss)))
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()
        self.assertEqual(self.boss["turn_message_id"], 123)

    async def test_uncertain_caption_edit_never_creates_duplicate(self):
        self.boss["turn_message_kind"] = "photo"
        self.bot.edit_message_caption.side_effect = api_error()
        self.assertFalse(await ensure_public_turn(self.bot, self.world, dict(self.boss)))
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_not_modified_photo_caption_is_success(self):
        self.boss["turn_message_kind"] = "photo"
        self.bot.edit_message_caption.side_effect = api_error(TelegramBadRequest, "message is not modified")
        self.assertTrue(await ensure_public_turn(self.bot, self.world, dict(self.boss)))
        self.bot.send_photo.assert_not_awaited()

    async def test_notice_survives_watcher_caption_edit(self):
        await replace_public_turn(self.bot, self.world, dict(self.boss), notice="🚪 Босс изгнал @user.")
        await ensure_public_turn(self.bot, self.world, dict(self.boss))
        self.assertIn("изгнал @user", self.bot.edit_message_caption.call_args.kwargs["caption"])

    async def test_recovery_before_callback_keeps_ability_notice_without_duplicate(self):
        snapshot = dict(self.boss)
        await ensure_public_turn(self.bot, self.world, dict(snapshot))
        await replace_public_turn(self.bot, self.world, snapshot, notice="⚡ Босс парализовал @user.")
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()
        self.assertIn("парализовал @user", self.bot.edit_message_caption.call_args.kwargs["caption"])
        await ensure_public_turn(self.bot, self.world, dict(self.boss))
        self.assertIn("парализовал @user", self.bot.edit_message_caption.call_args.kwargs["caption"])

    async def test_long_ability_notice_uses_text_without_caption_truncation(self):
        notice = "💚 Гидра восстановила 100 HP.\n" + "Событие героя. " * 90 + "\n⚡ Босс парализовал @user."
        self.assertTrue(await replace_public_turn(self.bot, self.world, dict(self.boss), notice=notice))
        self.bot.send_photo.assert_not_awaited()
        self.assertIn("парализовал @user", self.bot.send_message.call_args.kwargs["text"])
        await ensure_public_turn(self.bot, self.world, dict(self.boss))
        self.assertIn("парализовал @user", self.bot.edit_message_text.call_args.kwargs["text"])
        self.bot.send_message.assert_awaited_once()

    async def test_completed_turn_uses_victory_text_not_unrelated_hero(self):
        self.boss.update(status="defeated", current_hp=0)
        self.assertTrue(await replace_public_turn(self.bot, self.world, dict(self.boss), notice="🛡 Взрыв поглощён."))
        self.bot.send_photo.assert_not_awaited()
        self.assertIn("Босс повержен", self.bot.send_message.call_args.kwargs["text"])


class HomeMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world = {"id": 1, "chat_id": -1001, "thread_id": 42, "enabled": 1}
        self.player = {"id": 10, "coins": 45, "shards": 12, "username": "@user", "character_name": "Player"}
        self.hero = {
            "id": 9, "name": "Battle hero", "stars": 0, "attack": 3,
            "description": "Описание.", "passive_text": "Нет особых способностей.",
            "image_path": "Villager.png",
        }
        self.callback = SimpleNamespace(
            id="callback", data="mini:home:1:101", from_user=SimpleNamespace(id=101, username="user"),
            answer=AsyncMock(), bot=SimpleNamespace(
                send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
                send_message=AsyncMock(return_value=SimpleNamespace(message_id=2)),
            ),
            message=SimpleNamespace(ephemeral_message_id=55, delete_ephemeral=AsyncMock(),
                                    chat=SimpleNamespace(id=-1001)),
        )

    async def test_home_callback_sends_active_hero_ephemeral_photo(self):
        with patch("app.mini.handlers._load_personal_context", new_callable=AsyncMock, return_value=(self.world, self.player)), patch("app.mini.handlers.get_active_hero", return_value=self.hero):
            await home_callback(self.callback)
        sent = self.callback.bot.send_photo.call_args.kwargs
        self.assertIn("Battle hero", sent["caption"])
        self.assertEqual(sent["ephemeral_message_parameters"].receiver_user_id, 101)
        self.callback.message.delete_ephemeral.assert_awaited_once()

    async def test_launcher_stays_public_and_home_is_ephemeral_photo(self):
        self.callback.data = "mini:launch:1"
        self.callback.message.ephemeral_message_id = None
        with patch("app.mini.handlers.get_mini_world_by_id", return_value=self.world), patch("app.mini.handlers.get_mini_player", return_value=self.player), patch("app.mini.handlers.touch_mini_player"), patch("app.mini.handlers.get_active_hero", return_value=self.hero):
            await launch_callback(self.callback)
        self.callback.bot.send_photo.assert_awaited_once()
        self.callback.message.delete_ephemeral.assert_not_awaited()

    async def test_character_callback_sends_new_character_caption(self):
        self.callback.data = "mini:character:1:101"
        with patch("app.mini.handlers._load_personal_context", new_callable=AsyncMock, return_value=(self.world, self.player)), patch("app.mini.handlers.get_active_hero", return_value=self.hero):
            await character_callback(self.callback)
        caption = self.callback.bot.send_photo.call_args.kwargs["caption"]
        self.assertIn("Особый эффект", caption)
        self.assertTrue(caption.endswith("Описание."))

    async def test_home_without_hero_falls_back_to_private_text(self):
        with patch("app.mini.handlers.get_active_hero", return_value=None):
            await _send_home_from_callback(self.callback, self.world, self.player)
        self.callback.bot.send_photo.assert_not_awaited()
        self.assertIn("не выбран", self.callback.bot.send_message.call_args.kwargs["text"])

    async def test_uncertain_private_photo_does_not_send_second_message(self):
        self.callback.bot.send_photo.side_effect = api_error()
        with patch("app.mini.handlers.get_active_hero", return_value=self.hero):
            with self.assertRaises(TelegramAPIError):
                await _send_home_from_callback(self.callback, self.world, self.player)
        self.callback.bot.send_message.assert_not_awaited()
        self.callback.message.delete_ephemeral.assert_not_awaited()


class NoticeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_watcher_announces_forced_skip_without_timeout_claim(self):
        boss = {"id": 7, "world_id": 1, "status": "fighting"}
        result = {
            "changed": True, "skipped": [],
            "forced_skip_events": [{"type": "paralysis_skip", "player_id": 10}],
            "state": {"boss": boss, "participants": [{"player_id": 10, "username": "@target"}]},
        }
        with patch("app.mini.boss.watcher.list_fighting_bosses", return_value=[boss]), patch("app.mini.boss.watcher.advance_expired_turns", return_value=result), patch("app.mini.boss.watcher.get_mini_world_by_id", return_value={"id": 1}), patch("app.mini.boss.watcher.refresh_public_boss", new_callable=AsyncMock), patch("app.mini.boss.watcher.replace_public_turn", new_callable=AsyncMock) as publish, patch("app.mini.boss.watcher.asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await boss_watch_loop(SimpleNamespace())
        notice = publish.call_args.kwargs["notice"]
        self.assertIn("@target", notice)
        self.assertIn("паралича", notice)
        self.assertNotIn("таймер", notice)

    async def test_hit_callback_publishes_boss_and_hero_events(self):
        boss = {"id": 7, "world_id": 1, "status": "fighting", "current_round": 1, "current_turn_position": 1}
        callback = SimpleNamespace(
            data="miniboss:hit:1:7:1:1", from_user=SimpleNamespace(id=101, username="attacker"),
            answer=AsyncMock(), bot=SimpleNamespace(), message=None,
        )
        player = {"id": 10, "username": "@attacker", "character_name": "Hero"}
        result = {
            "damage": 3, "applied": True, "battle_ended": False, "passive_events": [],
            "boss_events": [{"type": "magic_shield_removed"}],
            "reward_event": {
                "type": "shield", "shields": 2,
                "passive_events": [{"username": "@attacker", "message": "+2 осколка"}],
            },
            "reward_events": [{
                "type": "shield", "shields": 2,
                "boss_events": [{"type": "banishment", "player_id": 11}],
                "passive_events": [{"username": "@attacker", "message": "+2 осколка"}],
            }],
            "state": {"boss": boss, "participants": [{"player_id": 11, "username": "@target"}]},
        }
        with patch("app.mini.boss.handlers._load_world", return_value={"id": 1}), patch("app.mini.boss.handlers._load_player", return_value=player), patch("app.mini.boss.handlers.get_boss", return_value=boss), patch("app.mini.boss.handlers.hit_boss", return_value=result), patch("app.mini.boss.handlers.refresh_public_boss", new_callable=AsyncMock), patch("app.mini.boss.handlers.replace_public_turn", new_callable=AsyncMock) as publish:
            await boss_hit_callback(callback)
        notice = publish.call_args.kwargs["notice"]
        self.assertIn("снял щит", notice)
        self.assertIn("изгнал @target", notice)
        self.assertIn("+2 осколка", notice)

    async def test_start_callback_publishes_start_ability_event(self):
        callback = SimpleNamespace(bot=SimpleNamespace(), answer=AsyncMock(), message=None)
        world = {"id": 1}
        boss = {"id": 7}
        state = {
            "boss": boss, "current": {"username": "@user"}, "participants": [],
            "boss_events": [{"type": "magic_shield_activated"}],
        }
        with patch("app.mini.boss.handlers._admin_action_context", new_callable=AsyncMock, return_value=(world, boss, 101)), patch("app.mini.boss.handlers.start_battle", return_value=state), patch("app.mini.boss.handlers.refresh_public_boss", new_callable=AsyncMock), patch("app.mini.boss.handlers.replace_public_turn", new_callable=AsyncMock) as publish:
            await boss_start_callback(callback)
        self.assertIn("активировал магический щит", publish.call_args.kwargs["notice"])
