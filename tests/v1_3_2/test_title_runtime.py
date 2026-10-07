"""Real runtime DB chain: only Telegram transport and config are substituted."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests.v1_3.support import MiniCase
from app.context import get_topic_admin
from app.mini.titles.handlers import supertitle_handler
from app.mini.titles.service import issue_title, get_title_target, get_active_title
from app.mini.schema import init_mini_db
from app.mini.presentation import format_player_mention
from app.mini.boss.public import participant_label
from app.mini.superadmin.service import execute
from app.mini.superadmin.parser import parse_command
from app.mini.notifications import enqueue_expired_titles, publish_pending_notifications


class RuntimeTitleTests(MiniCase, unittest.IsolatedAsyncioTestCase):
    def message(self):
        username=get_topic_admin(self.world['chat_id'],self.world['thread_id']).lstrip('@')
        return SimpleNamespace(from_user=SimpleNamespace(id=42,username=username),
            chat=SimpleNamespace(id=self.world['chat_id']),message_thread_id=self.world['thread_id'],
            message_id=132,text='/supertitle @tester 7d "Гроза кабанов"',answer=AsyncMock())

    async def test_real_handler_commits_before_immediate_runtime_read(self):
        message=self.message()
        with patch('app.config.DB_PATH',self.db):
            await supertitle_handler(message)
            self.assertEqual(message.answer.call_args.args[0],'👑 [Гроза кабанов]@tester — титул на 7 дн.')
            target=get_title_target(self.world['id'],'@tester')
            self.assertEqual(target['id'],self.pid)
            self.assertEqual(get_active_title(self.pid),'Гроза кабанов')
            self.assertEqual(format_player_mention(target),'[Гроза кабанов]@tester')
        record=self.sql('SELECT * FROM mini_titles')[0]
        self.assertEqual(record['player_id'],self.pid)
        self.assertEqual(record['expires_at']-record['created_at'],604800)
        self.assertEqual(record['operation_key'],f"title:{self.world['chat_id']}:132")

    async def test_public_boss_and_superlook_share_real_title_after_restart(self):
        with patch('app.config.DB_PATH',self.db):
            await supertitle_handler(self.message())
            before=self.sql('SELECT * FROM mini_titles')
            init_mini_db(self.db)
            self.assertEqual(self.sql('SELECT * FROM mini_titles'),before)
            self.assertIn('[Гроза кабанов]@tester',participant_label(dict(self.player,player_id=self.pid,queue_position=1,hero_name='Герой')))
            command=parse_command(f"/superlook {self.world['chat_id']}:{self.world['thread_id']} @tester -c")
            self.assertIn('[Гроза кабанов]@tester',execute(self.owner,command))

    async def test_real_expiration_outbox_publishes_once_without_expired_prefix(self):
        with patch('app.config.DB_PATH',self.db):
            await supertitle_handler(self.message())
            await publish_pending_notifications(SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=140))),self.db)
            deadline=self.sql('SELECT expires_at FROM mini_titles')[0]['expires_at']
            self.assertEqual(format_player_mention(self.player,now=deadline),'@tester')
            self.assertEqual(enqueue_expired_titles(now=deadline),1)
            init_mini_db(self.db)
            self.assertEqual(enqueue_expired_titles(now=deadline+100),0)
            bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=141)))
            await publish_pending_notifications(bot)
            await publish_pending_notifications(bot)
            bot.send_message.assert_awaited_once()
            self.assertEqual(bot.send_message.call_args.kwargs['text'],'@tester больше не "Гроза кабанов". Какая жалость..')
            self.assertEqual(self.sql("SELECT status FROM mini_public_notifications WHERE kind='title_expired'"),[{'status':'sent'}])

    def test_service_defaults_follow_runtime_configuration_after_import(self):
        # Regression: defaults captured at import wrote into a different DB from the formatter.
        with patch('app.config.DB_PATH',self.db):
            target=get_title_target(self.world['id'],'@tester')
            record=issue_title(target['id'],'Хранитель',86400,42,'runtime-service')
            self.assertEqual(get_active_title(self.pid),'Хранитель')
            self.assertEqual(format_player_mention(target),'[Хранитель]@tester')
        self.assertEqual(self.sql('SELECT id FROM mini_titles')[0]['id'],record['id'])
