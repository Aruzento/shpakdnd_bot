import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from aiogram.exceptions import TelegramAPIError,TelegramBadRequest
from aiogram.methods import SendMessage
from tests.v1_3.support import MiniCase
from app.mini.notifications import enqueue_expired_titles,publish_pending_notifications,claim_notification,recover_interrupted_notifications,mini_notice_watch_loop
from app.mini.titles.service import issue_title
from app.mini.schema import init_mini_db
from app.mini.players import create_mini_player
from app.mini.handlers import minicreate_handler


class NotificationTests(MiniCase,unittest.IsolatedAsyncioTestCase):
    def bot(self):return SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)))

    async def test_public_welcome_once_after_restart_with_actual_character_name(self):
        bot=self.bot();await publish_pending_notifications(bot,self.db)
        init_mini_db(self.db);recover_interrupted_notifications(self.db)
        await publish_pending_notifications(bot,self.db)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.kwargs['text'],'🎉 Приветствуем нового героя: Герой — @tester')
        self.assertEqual(bot.send_message.call_args.kwargs['message_thread_id'],self.world['thread_id'])

    async def test_expiration_is_published_once_after_restart(self):
        self.sql("UPDATE mini_public_notifications SET status='sent'")
        issue_title(self.pid,'Гроза кабанов',86400,42,'one',self.db,now=1000)
        self.assertEqual(enqueue_expired_titles(self.db,now=87400),1)
        init_mini_db(self.db)
        bot=self.bot();await publish_pending_notifications(bot,self.db)
        self.assertEqual(enqueue_expired_titles(self.db,now=99999),0)
        await publish_pending_notifications(bot,self.db)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.kwargs['text'],'@tester больше не "Гроза кабанов". Какая жалость..')

    async def test_replacement_cancels_pending_old_expiration(self):
        self.sql("UPDATE mini_public_notifications SET status='sent'")
        issue_title(self.pid,'Старый',86400,42,'one',self.db,now=1000)
        enqueue_expired_titles(self.db,now=87400)
        issue_title(self.pid,'Новый',86400,42,'two',self.db,now=87401)
        bot=self.bot();await publish_pending_notifications(bot,self.db)
        bot.send_message.assert_not_awaited()
        self.assertEqual(self.sql("SELECT status FROM mini_titles WHERE operation_key='two'")[0]['status'],'active')

    async def test_concurrent_publishers_send_once(self):
        bot=self.bot();await asyncio.gather(publish_pending_notifications(bot,self.db),publish_pending_notifications(bot,self.db))
        bot.send_message.assert_awaited_once()

    async def test_uncertain_delivery_is_not_retried(self):
        bot=self.bot();bot.send_message.side_effect=TelegramAPIError(method=SendMessage(chat_id=1,text='x'),message='timeout')
        await publish_pending_notifications(bot,self.db)
        init_mini_db(self.db);recover_interrupted_notifications(self.db)
        await publish_pending_notifications(bot,self.db)
        bot.send_message.assert_awaited_once()
        self.assertEqual(self.sql('SELECT status FROM mini_public_notifications')[0]['status'],'uncertain')

    async def test_confirmed_rejection_can_recover(self):
        bot=self.bot();bot.send_message.side_effect=TelegramBadRequest(method=SendMessage(chat_id=1,text='x'),message='rejected')
        await publish_pending_notifications(bot,self.db)
        self.assertEqual(self.sql('SELECT status FROM mini_public_notifications')[0]['status'],'pending')
        bot.send_message.side_effect=None
        await publish_pending_notifications(bot,self.db)
        self.assertEqual(self.sql('SELECT status FROM mini_public_notifications')[0]['status'],'sent')

    async def test_reserved_dispatch_survives_crash_without_duplicate(self):
        row=claim_notification(self.sql('SELECT id FROM mini_public_notifications')[0]['id'],self.db)
        self.assertIsNotNone(row);self.assertEqual(recover_interrupted_notifications(self.db),1)
        bot=self.bot();await publish_pending_notifications(bot,self.db);bot.send_message.assert_not_awaited()

    async def test_watcher_runs_recovery_pass_immediately(self):
        with patch('app.mini.duels.service.expire'),patch('app.mini.notifications.enqueue_expired_titles') as expire,patch('app.mini.notifications.publish_pending_notifications',AsyncMock()) as publish,patch('app.mini.notifications.asyncio.sleep',AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await mini_notice_watch_loop(self.bot())
        expire.assert_called_once();publish.assert_awaited_once()

    async def test_creation_handler_welcomes_then_shows_gift_and_gacha_link(self):
        async def publish(bot, **kwargs):
            await publish_pending_notifications(bot,self.db,**kwargs)
        msg=SimpleNamespace(from_user=SimpleNamespace(id=900124,username='newuser'),chat=SimpleNamespace(id=self.world['chat_id']),
            message_thread_id=self.world['thread_id'],text='/minicreate "Фактическое имя"',bot=self.bot())
        with patch('app.mini.handlers.ensure_configured_mini_world',return_value=dict(self.world,enabled=True)),patch('app.mini.handlers.get_mini_player',return_value=None),patch('app.mini.handlers.create_mini_player',side_effect=lambda *a:create_mini_player(*a,db_path=self.db)),patch('app.mini.handlers.publish_pending_notifications',side_effect=publish),patch('app.mini.handlers._send_private',AsyncMock()) as private:
            await minicreate_handler(msg)
        self.assertEqual(msg.bot.send_message.call_args.kwargs['text'],'🎉 Приветствуем нового героя: Фактическое имя — @newuser')
        self.assertIn('3 билета призыва',private.call_args.args[1])
        self.assertTrue(any(b.callback_data==f"mini:gacha:{self.world['id']}:900124" for row in private.call_args.args[2].inline_keyboard for b in row))

    async def test_failed_creation_handler_does_not_publish_or_show_gift(self):
        msg=SimpleNamespace(from_user=SimpleNamespace(id=900124,username='newuser'),chat=SimpleNamespace(id=self.world['chat_id']),
            message_thread_id=self.world['thread_id'],text='/minicreate "Имя"',bot=self.bot())
        with patch('app.mini.handlers.ensure_configured_mini_world',return_value=dict(self.world,enabled=True)),patch('app.mini.handlers.get_mini_player',return_value=None),patch('app.mini.handlers.create_mini_player',side_effect=ValueError('Ошибка')),patch('app.mini.handlers.publish_pending_notifications',AsyncMock()) as publish,patch('app.mini.handlers._send_private',AsyncMock()) as private:
            await minicreate_handler(msg)
        publish.assert_not_awaited();self.assertNotIn('3 билета',private.call_args.args[1])
