import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from tests.v1_3.support import MiniCase
from tests.topic_fixtures import TEST_CHAT_ID, DND_THREAD_ID, MINI_THREAD_ID, isolated_topics
from app.mini.tower.handlers import tower_callback,render_state
from app.mini.equipment.handlers import equipment_callback,render_equipment
from app.mini.tower.service import get_state,start_attempt
from app.mini.handlers import _player_menu,submenu
from app.mini.ui.context import shop_context


class UIContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(isolated_topics())

    async def test_personal_owner_required_for_new_screens(self):
        for action in ('tower','equipment'):
            callback=SimpleNamespace(data=f'mini:{action}:1:20',from_user=SimpleNamespace(id=21),answer=AsyncMock())
            self.assertIsNone(await shop_context(callback,action));callback.answer.assert_awaited_once()

    async def test_tower_attack_stale_callback_is_alerted(self):
        callback=SimpleNamespace(data='mini:tower:1:20:hit.1.0',from_user=SimpleNamespace(id=20),answer=AsyncMock())
        world={'id':1,'chat_id':TEST_CHAT_ID,'thread_id':MINI_THREAD_ID};player={'id':2}
        with patch('app.mini.tower.handlers.shop_context',AsyncMock(return_value=(world,player,'hit.1.0'))),patch('app.mini.tower.handlers.get_state',return_value={'attempt':None}),patch('app.mini.tower.handlers.attack',side_effect=ValueError('stale')) as attack_mock,patch('app.mini.tower.handlers.send_private_text_from_callback',AsyncMock()) as send:
            await tower_callback(callback);attack_mock.assert_called_once_with(2,1,0);send.assert_not_called()
        callback.answer.assert_awaited_once_with('stale',show_alert=True)

    async def test_equipment_uses_shared_personal_transport(self):
        callback=SimpleNamespace(from_user=SimpleNamespace(id=20),answer=AsyncMock())
        world={'id':1,'chat_id':TEST_CHAT_ID,'thread_id':MINI_THREAD_ID};player={'id':2}
        with patch('app.mini.equipment.handlers.shop_context',AsyncMock(return_value=(world,player,''))),patch('app.mini.equipment.handlers.get_equipment',return_value={'equipped':{},'owned':[],'attack_bonus':0}),patch('app.mini.equipment.handlers.send_private_text_from_callback',AsyncMock()) as send:
            await equipment_callback(callback);send.assert_awaited_once()
        self.assertIs(send.call_args.args[0],callback)
        self.assertEqual(send.call_args.args[1],world)
        self.assertIn("🛡 Экипировка",send.call_args.args[2])

    async def test_equipment_rejects_ordinary_topic_before_data_or_transport(self):
        callback=SimpleNamespace(from_user=SimpleNamespace(id=20),answer=AsyncMock())
        world={'id':1,'chat_id':TEST_CHAT_ID,'thread_id':DND_THREAD_ID};player={'id':2}
        with patch('app.mini.equipment.handlers.shop_context',AsyncMock(return_value=(world,player,''))),patch('app.mini.equipment.handlers.get_equipment') as read,patch('app.mini.equipment.handlers.send_private_text_from_callback',AsyncMock()) as send:
            await equipment_callback(callback)
            read.assert_not_called();send.assert_not_awaited()
        callback.answer.assert_awaited_once_with('Эта тема больше не работает в режиме Mini.',show_alert=True)

    async def test_equipment_personal_transport_uses_fixture_chat_and_topic(self):
        callback=SimpleNamespace(id='fixture-callback',from_user=SimpleNamespace(id=20),
            answer=AsyncMock(),bot=SimpleNamespace(send_message=AsyncMock()),message=None)
        world={'id':1,'chat_id':TEST_CHAT_ID,'thread_id':MINI_THREAD_ID};player={'id':2}
        with patch('app.mini.equipment.handlers.shop_context',AsyncMock(return_value=(world,player,''))),patch('app.mini.equipment.handlers.get_equipment',return_value={'equipped':{},'owned':[],'attack_bonus':0}):
            await equipment_callback(callback)
        callback.bot.send_message.assert_awaited_once()
        kwargs=callback.bot.send_message.call_args.kwargs
        self.assertEqual(kwargs['chat_id'],TEST_CHAT_ID)
        self.assertEqual(kwargs['message_thread_id'],MINI_THREAD_ID)
        self.assertEqual(kwargs['ephemeral_message_parameters'].receiver_user_id,20)
        self.assertEqual(kwargs['ephemeral_message_parameters'].callback_query_id,'fixture-callback')

    def test_launcher_has_tower_and_equipment(self):
        buttons=[b for action in ('adventures','heroarea') for row in submenu(action,1,20)[1].inline_keyboard for b in row]
        self.assertIn('🏰 Испытания',[b.text for b in buttons])
        self.assertIn('🛡 Экипировка',[b.text for b in buttons])


class UIScreenTests(MiniCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        super().setUp()

    def test_enemy_visible_before_hero_selection(self):
        text,markup=render_state(get_state(self.pid,self.db),1,900123)
        for value in ['🏰 Испытания: 1/200','HP: 6','● Простолюдины','Особенности:','3/3','Бонус экипировки:']:self.assertIn(value,text)
        self.assertTrue(any('Выбрать героя' in b.text for row in markup.inline_keyboard for b in row))

    def test_fighting_screen_contains_shields_attack_and_matchup(self):
        hero=self.hero();a=start_attempt(self.pid,hero['id'],1,self.db)
        text,markup=render_state(get_state(self.pid,self.db),1,900123)
        for value in ['ATK 3','3/3','Урон по фракции:','HP сейчас:']:self.assertIn(value,text)
        data=[b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn(f'mini:tower:1:900123:hit.{a["id"]}.0',data)
        self.assertTrue(all(len(x.encode())<=64 for x in data))

    def test_complete_screen_does_not_offer_201(self):
        self.floor(200);text,markup=render_state(get_state(self.pid,self.db),1,900123)
        self.assertIn('200 этажей пройдены',text)
        self.assertNotIn('201',text)
