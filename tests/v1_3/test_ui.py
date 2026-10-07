import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from tests.v1_3.support import MiniCase
from app.mini.tower.handlers import tower_callback,render_state
from app.mini.equipment.handlers import equipment_callback,render_equipment
from app.mini.tower.service import get_state,start_attempt
from app.mini.handlers import _player_menu
from app.mini.ui.context import shop_context


class UIContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_owner_required_for_new_screens(self):
        for action in ('tower','equipment'):
            callback=SimpleNamespace(data=f'mini:{action}:1:20',from_user=SimpleNamespace(id=21),answer=AsyncMock())
            self.assertIsNone(await shop_context(callback,action));callback.answer.assert_awaited_once()

    async def test_tower_attack_stale_callback_is_alerted(self):
        callback=SimpleNamespace(data='mini:tower:1:20:hit.1.0',from_user=SimpleNamespace(id=20),answer=AsyncMock())
        world={'id':1,'chat_id':-1003376315265,'thread_id':2684};player={'id':2}
        with patch('app.mini.tower.handlers.shop_context',AsyncMock(return_value=(world,player,'hit.1.0'))),patch('app.mini.tower.handlers.get_state',return_value={'attempt':None}),patch('app.mini.tower.handlers.attack',side_effect=ValueError('stale')),patch('app.mini.tower.handlers.send_private_text_from_callback',AsyncMock()) as send:
            await tower_callback(callback);send.assert_not_called()
        self.assertTrue(callback.answer.call_args.kwargs['show_alert'])

    async def test_equipment_uses_shared_personal_transport(self):
        callback=SimpleNamespace(from_user=SimpleNamespace(id=20),answer=AsyncMock())
        world={'id':1,'chat_id':-1003376315265,'thread_id':2684};player={'id':2}
        with patch('app.mini.equipment.handlers.shop_context',AsyncMock(return_value=(world,player,''))),patch('app.mini.equipment.handlers.get_equipment',return_value={'equipped':{},'owned':[],'attack_bonus':0}),patch('app.mini.equipment.handlers.send_private_text_from_callback',AsyncMock()) as send:
            await equipment_callback(callback);send.assert_awaited_once()

    def test_launcher_has_tower_and_equipment(self):
        buttons=[b for row in _player_menu(1,20).inline_keyboard for b in row]
        self.assertIn('🏰 Испытания',[b.text for b in buttons])
        self.assertIn('🛡 Экипировка',[b.text for b in buttons])


class UIScreenTests(MiniCase):
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
