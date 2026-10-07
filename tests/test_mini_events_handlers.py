import os
import tempfile
import unittest
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini import handlers as mini_ui
from app.mini.events import handlers as ui, service
from app.mini.players import create_mini_player, get_mini_player, touch_mini_player
from app.mini.schema import init_mini_db
from app.mini.wallet import add_coins
from app.mini.worlds import get_mini_world_by_id, sync_configured_mini_worlds


class EventMenuTests(unittest.TestCase):
    def test_summon_is_separate_green_row_between_new_submenus(self):
        menu = mini_ui._player_menu(1, 101)
        self.assertEqual([b.text for b in menu.inline_keyboard[0]], ["⚔️ Приключения", "🧙 Герой"])
        self.assertEqual(len(menu.inline_keyboard[1]), 1)
        self.assertEqual(menu.inline_keyboard[1][0].text, "✨ Призвать героя")
        self.assertEqual(menu.inline_keyboard[1][0].style, "success")
        self.assertTrue(all(b.style is None for i, row in enumerate(menu.inline_keyboard)
                            if i != 1 for b in row))

    def test_bets_have_exit_active_round_has_no_exit_results_have_two_buttons(self):
        player = {"coins": 50, "shards": 2}
        _, bets = ui._screen(1, 101, player, "bets")
        self.assertEqual([b.text for r in bets.inline_keyboard for b in r],
                         ["🪙 1", "🪙 5", "🪙 10", "⬅️ Выйти"])
        session = {"id": 123, "stake": 5, "status": "active", "game_type": "rps", "payload": {}}
        _, active = ui._session_screen(1, 101, player, session)
        self.assertEqual([b.text for r in active.inline_keyboard for b in r], list(ui.RPS_LABELS.values()))
        session.update(status="resolved", payload={"choice": "saint", "bot_choice": "demon", "outcome": "win"})
        text, result = ui._session_screen(1, 101, player, session)
        self.assertIn("+5 монет", text)
        self.assertEqual([b.text for r in result.inline_keyboard for b in r],
                         ["🔁 Ещё раз!", "💰 Поменять ставку"])
        self.assertIn("🪙 50 монет • 🧩 2 осколков", text)

    def test_every_labyrinth_screen_hides_sequence_and_shows_progress_or_resources(self):
        player = {"coins": 95, "shards": 3}
        session = {"id": 12, "stake": 5, "status": "active", "game_type": "labyrinth",
                   "payload": {"sequence": ["left", "right", "left"]}}
        for step in (0, 1, 2):
            session["step"] = step
            text, menu = ui._session_screen(1, 101, player, session)
            self.assertIn(f"Путь: {step}/3", text)
            self.assertNotIn("left", text)
            self.assertNotIn("right", text)
            self.assertEqual(len(menu.inline_keyboard[0]), 2)
        for outcome in ("win", "loss"):
            session.update(status="resolved")
            session["payload"]["outcome"] = outcome
            text, menu = ui._session_screen(1, 101, player, session)
            self.assertNotIn("left", text)
            self.assertNotIn("right", text)
            self.assertIn("🪙 95 монет • 🧩 3 осколков", text)
            self.assertIn("10 монет" if outcome == "win" else "3 осколка", text)
            self.assertEqual(len(menu.inline_keyboard), 2)

    def test_callback_data_fits_telegram_limit(self):
        world, user = 2147483647, 9999999999999
        player = {"coins": 5, "shards": 0}
        menus = [ui._screen(world, user, player, s)[1] for s in ("events", "rps", "bets", "lab")]
        for game in ("rps", "labyrinth"):
            for status in ("active", "resolved"):
                session = {"id": 2147483647, "stake": 10 if game == "rps" else 5,
                           "status": status, "step": 2, "game_type": game,
                           "payload": {"choice": "saint", "bot_choice": "demon", "outcome": "win"}}
                menus.append(ui._session_screen(world, user, player, session)[1])
        self.assertTrue(all(len(b.callback_data.encode()) <= 64
                            for m in menus for r in m.inline_keyboard for b in r))


class EventCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        self.world = sync_configured_mini_worlds(self.db)[0]
        self.wid = self.world["id"]
        self.player = create_mini_player(self.wid, 101, "@tester", "Игрок", self.db)
        add_coins(self.player["id"], 20, "Начальный баланс", db_path=self.db)
        self.callback = SimpleNamespace(
            id="callback-1", data=f"mini:events:{self.wid}:101",
            from_user=SimpleNamespace(id=101, username="tester"),
            answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(chat=SimpleNamespace(id=self.world["chat_id"]),
                                    message_thread_id=self.world["thread_id"],
                                    ephemeral_message_id=7, delete_ephemeral=AsyncMock()),
        )
        for target, fn in (
            ("app.mini.ui.context.get_mini_world_by_id", get_mini_world_by_id),
            ("app.mini.ui.context.get_mini_player", get_mini_player),
            ("app.mini.ui.context.touch_mini_player", touch_mini_player),
            ("app.mini.events.handlers.get_mini_player", get_mini_player),
        ):
            mock_patch = patch(target, side_effect=partial(fn, db_path=self.db))
            mock_patch.start()
            self.addCleanup(mock_patch.stop)
        for name in ("start_session", "get_session", "get_active_session", "repeat_session",
                     "resolve_rps", "choose_direction"):
            mock_patch = patch.object(ui, name, side_effect=partial(getattr(service, name), db_path=self.db))
            mock_patch.start()
            self.addCleanup(mock_patch.stop)

    def data(self, payload):
        self.callback.data = f"mini:ev:{self.wid}:101:{payload}"

    def sent(self):
        return self.callback.bot.send_message.call_args.kwargs

    def resources(self):
        p = get_mini_player(self.wid, 101, self.db)
        return p["coins"], p["shards"]

    def start(self, game="rps"):
        return service.start_session(self.player["id"], self.wid, game, 5, "test", self.db)

    async def test_events_sends_ephemeral_and_deletes_previous(self):
        await ui.events_callback(self.callback)
        params = self.sent()["ephemeral_message_parameters"]
        self.assertEqual(params.receiver_user_id, 101)
        self.assertEqual(params.callback_query_id, "callback-1")
        self.assertEqual(self.sent()["chat_id"], self.world["chat_id"])
        self.assertIn("20 монет", self.sent()["text"])
        self.callback.message.delete_ephemeral.assert_awaited_once()

    async def test_public_fallback_still_sends_only_ephemeral(self):
        self.callback.message.ephemeral_message_id = None
        self.callback.message.edit_text = AsyncMock()
        await ui.events_callback(self.callback)
        self.assertEqual(self.sent()["ephemeral_message_parameters"].receiver_user_id, 101)
        self.callback.message.edit_text.assert_not_awaited()
        self.callback.message.delete_ephemeral.assert_not_awaited()

    async def test_other_owner_cannot_use_menu_or_game_button(self):
        self.callback.from_user.id = 102
        await ui.events_callback(self.callback)
        self.data("start.012345abcdef.5")
        await ui.event_game_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.assertTrue(self.callback.answer.call_args.kwargs["show_alert"])
        self.assertEqual(self.resources(), (20, 0))

    async def test_missing_character_and_disabled_world_are_rejected(self):
        with patch("app.mini.ui.context.get_mini_player", return_value=None):
            await ui.events_callback(self.callback)
        with patch("app.mini.ui.context.get_mini_world_by_id", return_value={"enabled": 0}):
            await ui.events_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()

    async def test_world_and_topic_mismatch_are_rejected(self):
        self.callback.message.chat.id += 1
        await ui.events_callback(self.callback)
        self.data("start.012345abcdef.5")
        await ui.event_game_callback(self.callback)
        self.callback.message.chat.id = self.world["chat_id"]
        self.callback.message.message_thread_id += 1
        await ui.event_game_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.assertEqual(self.resources(), (20, 0))

    async def test_other_players_session_is_rejected_even_with_correct_button_owner(self):
        other = create_mini_player(self.wid, 102, "@other", "Другой", self.db)
        add_coins(other["id"], 5, "Начальные", db_path=self.db)
        session = service.start_session(other["id"], self.wid, "rps", 5, "other", self.db)
        self.data(f"pick.{session['id']}.s")
        await ui.event_game_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.assertTrue(self.callback.answer.call_args.kwargs["show_alert"])
        self.assertEqual(self.resources(), (20, 0))

    async def test_cross_world_session_is_rejected(self):
        from app.mini.db import connect_mini_db
        with connect_mini_db(self.db) as conn:
            wid = conn.execute("INSERT INTO mini_worlds(chat_id, thread_id) VALUES (-100123, 5)").lastrowid
        other = create_mini_player(wid, 101, "@tester", "Другой мир", self.db)
        add_coins(other["id"], 5, "Начальные", db_path=self.db)
        session = service.start_session(other["id"], wid, "rps", 5, "other-world", self.db)
        self.data(f"pick.{session['id']}.s")
        await ui.event_game_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.assertEqual(self.resources(), (20, 0))

    async def test_active_session_reopens_after_restart_without_charge(self):
        with patch.object(service.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        service.choose_direction(self.player["id"], self.wid, session["id"], 0, "left", self.db)
        init_mini_db(self.db)
        await ui.events_callback(self.callback)
        self.assertIn("Путь: 1/3", self.sent()["text"])
        self.assertEqual(self.resources(), (15, 0))
        self.data("bets")
        await ui.event_game_callback(self.callback)
        self.assertIn("Путь: 1/3", self.sent()["text"])

    async def test_double_start_callbacks_with_different_ids_charge_once(self):
        self.data("start.012345abcdef.5")
        await ui.event_game_callback(self.callback)
        self.callback.id = "different-callback-id"
        await ui.event_game_callback(self.callback)
        self.assertEqual(self.resources(), (15, 0))
        self.assertIn("Ставка принята", self.sent()["text"])

    async def test_double_pick_callbacks_pay_once_and_result_is_personal(self):
        session = self.start()
        self.data(f"pick.{session['id']}.s")
        with patch.object(service.secrets, "choice", return_value="demon"):
            await ui.event_game_callback(self.callback)
        self.callback.id = "different-callback-id"
        self.data(f"pick.{session['id']}.d")
        await ui.event_game_callback(self.callback)
        self.assertEqual(self.resources(), (25, 0))
        self.assertIn("Ты: 😇 Святоша", self.sent()["text"])
        self.assertIn("🏆 Победа! +5 монет", self.sent()["text"])
        self.assertEqual(self.sent()["ephemeral_message_parameters"].receiver_user_id, 101)

    async def test_repeat_with_insufficient_coins_returns_to_bets_with_exit(self):
        from app.mini.db import connect_mini_db
        session = self.start()
        with patch.object(service.secrets, "choice", return_value="villager"):
            service.resolve_rps(self.player["id"], self.wid, session["id"], "saint", self.db)
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_players SET coins = 4 WHERE id = ?", (self.player["id"],))
        self.data(f"again.{session['id']}")
        await ui.event_game_callback(self.callback)
        self.assertIn("Недостаточно монет", self.sent()["text"])
        labels = [b.text for r in self.sent()["reply_markup"].inline_keyboard for b in r]
        self.assertIn("⬅️ Выйти", labels)
        self.assertEqual(self.resources(), (4, 0))

    async def test_duplicate_labyrinth_step_does_not_use_next_step(self):
        with patch.object(service.secrets, "choice", side_effect=("left", "right", "left")):
            session = self.start("labyrinth")
        self.data(f"turn.{session['id']}.0.l")
        await ui.event_game_callback(self.callback)
        self.callback.id = "different-callback-id"
        await ui.event_game_callback(self.callback)
        self.assertIn("Путь: 1/3", self.sent()["text"])
        self.assertEqual(self.resources(), (15, 0))

    async def test_duplicate_labyrinth_loss_callbacks_award_three_shards(self):
        with patch.object(service.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        self.data(f"turn.{session['id']}.0.r")
        await ui.event_game_callback(self.callback)
        self.callback.id = "different-callback-id"
        await ui.event_game_callback(self.callback)
        self.assertEqual(self.resources(), (15, 3))
        self.assertIn("🧩 Получено: 3 осколка", self.sent()["text"])

    async def test_malformed_callback_does_not_mutate_balance(self):
        for payload in ("start.bad.5", "pick.abc.s", "turn.1.0.bad", "enter", "unknown"):
            self.data(payload)
            await ui.event_game_callback(self.callback)
        self.callback.bot.send_message.assert_not_awaited()
        self.assertEqual(self.resources(), (20, 0))

    async def test_duplicate_labyrinth_victory_callbacks_pay_ten_once(self):
        with patch.object(service.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        for step in (0, 1, 2):
            self.data(f"turn.{session['id']}.{step}.l")
            await ui.event_game_callback(self.callback)
        self.callback.id = "duplicate-victory"
        await ui.event_game_callback(self.callback)
        self.assertEqual(self.resources(), (25, 0))
        self.assertIn("🪙 Получено: 10 монет", self.sent()["text"])
        self.assertIn("Итог попытки: +5 монет", self.sent()["text"])

    async def test_old_result_button_resumes_new_round(self):
        session = self.start()
        with patch.object(service.secrets, "choice", return_value="demon"):
            service.resolve_rps(self.player["id"], self.wid, session["id"], "saint", self.db)
        service.repeat_session(self.player["id"], self.wid, session["id"], self.db)
        self.data(f"pick.{session['id']}.s")
        await ui.event_game_callback(self.callback)
        self.assertIn("Ставка принята", self.sent()["text"])
        self.assertEqual(self.resources(), (20, 0))

    async def test_entry_insufficient_funds_never_starts_either_game(self):
        from app.mini.db import connect_mini_db
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_players SET coins = 0 WHERE id = ?", (self.player["id"],))
        for payload in ("start.012345abcdef.1", "enter.012345abcdef"):
            self.data(payload)
            await ui.event_game_callback(self.callback)
            self.assertIn("Недостаточно монет", self.sent()["text"])
            self.assertIsNone(service.get_active_session(self.player["id"], self.wid, self.db))
        self.assertEqual(self.resources(), (0, 0))
