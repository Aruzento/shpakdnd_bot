import json
import unittest
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from tests.v1_3.support import MiniCase
from app.mini.tower.service import get_state,select_hero,start_selected_attempt,attack,list_heroes
from app.mini.tower.handlers import tower_callback
from app.mini.heroes import set_active_hero
from app.mini.players import create_mini_player
from app.mini.worlds import register_mini_world
from app.mini.schema import init_mini_db


class TowerSelectionTests(MiniCase):
    def setUp(self):
        super().setUp()
        self.first=self.hero('Lasar')['id']
        self.second=self.hero('Villager')['id']

    def test_selection_survives_restart_without_changing_global_active(self):
        select_hero(self.pid,self.first,self.db)
        init_mini_db(self.db)
        self.assertEqual(get_state(self.pid,self.db)['selected_hero']['id'],self.first)
        self.assertEqual(self.sql('SELECT active_hero_id FROM mini_players WHERE id=?',(self.pid,))[0]['active_hero_id'],self.second)
        set_active_hero(self.pid,self.first,self.db)
        self.assertEqual(get_state(self.pid,self.db)['selected_hero']['id'],self.first)

    def test_next_floor_uses_saved_selection_and_new_snapshot(self):
        select_hero(self.pid,self.first,self.db)
        a=start_selected_attempt(self.pid,1,self.first,self.db)
        self.assertEqual(attack(self.pid,a['id'],0,self.db,roller=lambda _:False)['attempt']['status'],'won')
        state=get_state(self.pid,self.db)
        self.assertEqual(state['floor']['floor'],2)
        self.assertEqual(state['selected_hero']['id'],self.first)
        b=start_selected_attempt(self.pid,2,self.first,self.db)
        self.assertEqual(b['hero_id'],self.first)
        self.assertNotEqual(a['id'],b['id'])

    def test_change_between_attempts_uses_new_hero(self):
        select_hero(self.pid,self.first,self.db)
        select_hero(self.pid,self.second,self.db,expected_floor=1)
        a=start_selected_attempt(self.pid,1,self.second,self.db)
        self.assertEqual(a['hero_id'],self.second)
        with self.assertRaises(ValueError):select_hero(self.pid,self.first,self.db,expected_floor=1)

    def test_selection_change_never_rewrites_active_snapshot(self):
        select_hero(self.pid,self.first,self.db)
        a=start_selected_attempt(self.pid,1,self.first,self.db)
        select_hero(self.pid,self.second,self.db)
        init_mini_db(self.db)
        state=get_state(self.pid,self.db)
        self.assertEqual(state['selected_hero']['id'],self.second)
        self.assertEqual(state['attempt']['hero_json'],a['hero_json'])
        self.assertEqual(state['attempt']['runtime_json'],a['runtime_json'])
        self.assertEqual(state['attempt']['hero_id'],self.first)

    def test_stale_start_and_floor_do_not_start_wrong_hero(self):
        select_hero(self.pid,self.first,self.db)
        select_hero(self.pid,self.second,self.db)
        with self.assertRaises(ValueError):start_selected_attempt(self.pid,1,self.first,self.db)
        with self.assertRaises(ValueError):start_selected_attempt(self.pid,2,self.second,self.db)
        self.assertIsNone(get_state(self.pid,self.db)['attempt'])

    def test_invalid_missing_not_owned_selection_is_cleared(self):
        for action in ('ownership','missing'):
            with self.subTest(action=action):
                # Fresh copy of a catalog row, so each invalidation is independent.
                row=self.sql('SELECT * FROM mini_heroes WHERE id=?',(self.second,))[0]
                code='selection_'+action
                data=dict(row);data.pop('id');data.update(code=code,name=code)
                columns=','.join(data)
                self.sql(f"INSERT INTO mini_heroes({columns}) VALUES({','.join('?' for _ in data)})",tuple(data.values()))
                identifier=self.sql('SELECT id FROM mini_heroes WHERE code=?',(code,))[0]['id']
                self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.pid,identifier))
                select_hero(self.pid,identifier,self.db)
                if action=='ownership':self.sql('DELETE FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(self.pid,identifier))
                if action=='missing':self.sql('DELETE FROM mini_heroes WHERE id=?',(identifier,))
                self.assertIsNone(get_state(self.pid,self.db)['selected_hero'])
                self.assertEqual(self.sql('SELECT * FROM mini_tower_selections WHERE player_id=?',(self.pid,)),[])
                with self.assertRaises(ValueError):select_hero(self.pid,identifier,self.db)

    def test_missing_selection_requires_choice(self):
        self.assertIsNone(get_state(self.pid,self.db)['selected_hero'])
        with self.assertRaises(ValueError):start_selected_attempt(self.pid,1,self.first,self.db)

    def test_player_and_world_isolation(self):
        world=register_mini_world(-100123,123,'Other',self.db)
        other=create_mini_player(world,900123,'@tester','Другой',self.db)
        self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(other['id'],self.first))
        select_hero(self.pid,self.second,self.db)
        select_hero(other['id'],self.first,self.db)
        self.assertEqual(get_state(self.pid,self.db)['selected_hero']['id'],self.second)
        self.assertEqual(get_state(other['id'],self.db)['selected_hero']['id'],self.first)
        with self.assertRaises(ValueError):select_hero(other['id'],self.second,self.db)


class TowerSelectionUITests(MiniCase,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp();self.hero_id=self.hero('Lasar')['id']

    async def invoke(self,tail=''):
        cb=SimpleNamespace(id='tower',from_user=SimpleNamespace(id=900123),answer=AsyncMock(),
            bot=SimpleNamespace(send_message=AsyncMock()),message=SimpleNamespace(ephemeral_message_id='old',delete_ephemeral=AsyncMock()))
        with patch('app.mini.tower.handlers.shop_context',return_value=(self.world,self.player,tail)), \
             patch('app.mini.tower.handlers.get_state',side_effect=partial(get_state,db_path=self.db)), \
             patch('app.mini.tower.handlers.list_heroes',side_effect=partial(list_heroes,db_path=self.db)), \
             patch('app.mini.tower.handlers.select_hero',side_effect=partial(select_hero,db_path=self.db)), \
             patch('app.mini.tower.handlers.start_selected_attempt',side_effect=partial(start_selected_attempt,db_path=self.db)), \
             patch('app.mini.tower.handlers.get_favorites',return_value=[]):
            await tower_callback(cb)
        return cb

    async def test_no_saved_hero_opens_shared_selector_and_pick_saves(self):
        cb=await self.invoke()
        sent=cb.bot.send_message.call_args.kwargs
        self.assertIn('⭐ Избранные',sent['text'])
        self.assertIn('👥 Показать всех',[b.text for r in sent['reply_markup'].inline_keyboard for b in r])
        selected=await self.invoke(f'pick.1.{self.hero_id}')
        self.assertEqual(get_state(self.pid,self.db)['selected_hero']['id'],self.hero_id)
        self.assertIn('Текущий герой:',selected.bot.send_message.call_args.kwargs['text'])
        selected.message.delete_ephemeral.assert_awaited_once()

    async def test_next_floor_offers_start_and_change_without_selector(self):
        select_hero(self.pid,self.hero_id,self.db)
        a=start_selected_attempt(self.pid,1,self.hero_id,self.db)
        attack(self.pid,a['id'],0,self.db,roller=lambda _:False)
        cb=await self.invoke()
        sent=cb.bot.send_message.call_args.kwargs
        labels=[b.text for r in sent['reply_markup'].inline_keyboard for b in r]
        self.assertIn('⚔️ Начать бой',labels);self.assertIn('🔄 Сменить героя',labels)
        self.assertNotIn('👥 Показать всех',labels)
        self.assertIn(f'start.2.{self.hero_id}',str(sent['reply_markup']))
        change=await self.invoke('heroes.2.0')
        self.assertIn('⭐ Избранные',change.bot.send_message.call_args.kwargs['text'])

    async def test_gacha_inactive_saved_hero_still_offers_start(self):
        select_hero(self.pid,self.hero_id,self.db)
        self.sql('UPDATE mini_heroes SET active=0 WHERE id=?',(self.hero_id,))
        cb=await self.invoke()
        sent=cb.bot.send_message.call_args.kwargs
        self.assertIn('Текущий герой:',sent['text'])
        self.assertIn('⚔️ Начать бой',[b.text for row in sent['reply_markup'].inline_keyboard for b in row])
        self.assertEqual(get_state(self.pid,self.db)['selected_hero']['id'],self.hero_id)

    async def test_stale_pick_cannot_change_selection_after_floor_progress(self):
        self.floor(1)
        cb=await self.invoke(f'pick.1.{self.hero_id}')
        cb.bot.send_message.assert_not_awaited()
        self.assertTrue(cb.answer.call_args.kwargs['show_alert'])

    async def test_ui_change_saves_then_start_freezes_selected_hero(self):
        old=self.hero('Villager')['id']
        select_hero(self.pid,old,self.db)
        await self.invoke('heroes.1.0')
        await self.invoke(f'pick.1.{self.hero_id}')
        self.assertEqual(get_state(self.pid,self.db)['selected_hero']['id'],self.hero_id)
        cb=await self.invoke(f'start.1.{self.hero_id}')
        self.assertIn('⚔️ Атаковать',[b.text for row in cb.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard for b in row])
        state=get_state(self.pid,self.db)
        self.assertEqual(state['attempt']['hero_id'],self.hero_id)
        stale=await self.invoke(f'pick.1.{old}')
        stale.bot.send_message.assert_not_awaited()
        self.assertEqual(get_state(self.pid,self.db)['attempt']['hero_json'],state['attempt']['hero_json'])
