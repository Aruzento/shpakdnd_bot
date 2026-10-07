from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import unittest
from tests.v1_3.support import MiniCase
from app.mini.titles.service import parse_command,parse_duration,issue_title,get_active_title,get_title_target
from app.mini.presentation import format_player_mention
from app.mini.schema import init_mini_db
from app.mini.notifications import enqueue_expired_titles
from app.mini.titles.handlers import supertitle_handler
from app.context import get_topic_admin


class TitleTests(MiniCase):
    def grant(self,text='Гроза кабанов',now=1000,key='one'):
        return issue_title(self.pid,text,parse_duration('7d'),42,key,self.db,now=now)

    def test_parser_seven_days_and_command_suffix(self):
        self.assertEqual(parse_command('/supertitle@bot @Tester 7d "Гроза кабанов"'),('@tester',604800,'Гроза кабанов'))

    def test_invalid_duration_is_rejected(self):
        for value in ('0d','-1d','7','7h','1.5d','366d','abc'):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_duration(value)

    def test_invalid_title_and_syntax_are_rejected(self):
        for value in ('',' '*4,'a'*49,'[Подмена]','строка\nстрока','строка\u2028строка'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.grant(value)
        for text in ('/supertitle @tester 7d','/supertitle @tester 7d ""','/supertitle @tester 7d "Открыто','/supertitle tester 7d "Титул"','/unknown @tester 7d "Титул"'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_command(text)

    def test_active_title_formats_mention_without_changing_username(self):
        self.grant()
        self.assertEqual(format_player_mention(self.player,self.db,now=1001),'[Гроза кабанов]@tester')
        self.assertEqual(self.sql('SELECT username FROM mini_players')[0]['username'],'@tester')
        self.assertEqual(format_player_mention(dict(player_id=self.pid,username='@tester'),self.db,now=1001),'[Гроза кабанов]@tester')
        self.assertEqual(format_player_mention({'username':'','character_name':'Имя'},self.db),'Имя')

    def test_restart_keeps_expiration_and_active_title(self):
        record=self.grant();init_mini_db(self.db)
        self.assertEqual(self.sql('SELECT expires_at FROM mini_titles')[0]['expires_at'],record['expires_at'])
        self.assertEqual(get_active_title(self.pid,self.db,now=1002),'Гроза кабанов')

    def test_title_disappears_at_deadline_without_watcher(self):
        title=self.grant()
        self.assertEqual(format_player_mention(self.player,self.db,now=title['expires_at']),'@tester')
        self.assertIsNone(get_active_title(self.pid,self.db,now=title['expires_at']))

    def test_replacement_resets_deadline_and_old_expiration_does_not_apply(self):
        first=self.grant();second=self.grant('Новый',now=2000,key='two')
        self.assertEqual(second['expires_at'],2000+604800)
        self.assertEqual(enqueue_expired_titles(self.db,now=first['expires_at']),0)
        self.assertEqual(get_active_title(self.pid,self.db,now=first['expires_at']),'Новый')
        self.assertEqual(len(self.sql("SELECT * FROM mini_titles WHERE status='active'")),1)

    def test_repeat_command_cannot_extend_or_replace_current_title(self):
        first=self.grant();second=self.grant('Новый',2000,'two');repeat=self.grant(now=3000)
        self.assertEqual(repeat['expires_at'],first['expires_at'])
        self.assertEqual(get_active_title(self.pid,self.db,now=3000),second['text'])

    def test_missing_or_ambiguous_player_is_rejected(self):
        with self.assertRaises(ValueError):get_title_target(self.world['id'],'@absent',self.db)
        with self.assertRaises(ValueError):issue_title(9999,'Титул',86400,42,'absent',self.db)
        from app.mini.players import create_mini_player
        create_mini_player(self.world['id'],900124,'@tester','Другой',self.db)
        with self.assertRaises(ValueError):get_title_target(self.world['id'],'@tester',self.db)

    def test_boss_notices_and_turn_use_shared_title_presentation(self):
        from app.mini.boss.public import participant_label,_participant_mention,boss_attack_passive_lines
        from app.mini.boss.notices import boss_event_line,timeout_event_lines
        import time
        self.grant(now=int(time.time()))
        participant=dict(self.player,player_id=self.pid,queue_position=1,hero_name='Герой')
        with patch('app.config.DB_PATH',self.db):
            for text in (participant_label(participant),_participant_mention(participant),
                         boss_event_line({'type':'banishment','player_id':self.pid},[participant]),
                         timeout_event_lines({'skipped':[participant]})[0],
                         boss_attack_passive_lines({'passive_events':[dict(participant,message='Защита')]})[0]):
                self.assertIn('[Гроза кабанов]@tester',text)


class TitlePermissionTests(MiniCase,unittest.IsolatedAsyncioTestCase):
    def message(self,username):
        return SimpleNamespace(from_user=SimpleNamespace(id=42,username=username),chat=SimpleNamespace(id=self.world['chat_id']),
            message_thread_id=self.world['thread_id'],message_id=14,text='/supertitle @tester 7d "Гроза кабанов"',answer=AsyncMock())

    async def test_topic_admin_can_issue(self):
        admin=get_topic_admin(self.world['chat_id'],self.world['thread_id']).lstrip('@');msg=self.message(admin)
        with patch('app.mini.titles.handlers.ensure_configured_mini_world',return_value=dict(self.world,enabled=True)),patch('app.mini.titles.handlers.get_title_target',side_effect=lambda w,u:get_title_target(w,u,self.db)),patch('app.mini.titles.handlers.issue_title',side_effect=lambda *a:issue_title(*a,db_path=self.db)),patch('app.config.DB_PATH',self.db):
            await supertitle_handler(msg)
        self.assertEqual(get_active_title(self.pid,self.db),'Гроза кабанов')
        self.assertIn('[Гроза кабанов]@tester',msg.answer.call_args.args[0])

    async def test_nonadmin_cannot_issue(self):
        msg=self.message('outsider')
        with patch('app.mini.titles.handlers.ensure_configured_mini_world',return_value=dict(self.world,enabled=True)),patch('app.mini.titles.handlers.issue_title') as issue:
            await supertitle_handler(msg);issue.assert_not_called()
        self.assertEqual(self.sql('SELECT * FROM mini_titles'),[])
        msg.answer.assert_awaited_once()

    async def test_command_outside_mini_is_ignored(self):
        msg=self.message('outsider')
        with patch('app.mini.titles.handlers.ensure_configured_mini_world',return_value=None),patch('app.mini.titles.handlers.issue_title') as issue:
            await supertitle_handler(msg);issue.assert_not_called()
        msg.answer.assert_not_awaited()
