"""User paths: handlers use real services against isolated SQLite fixtures."""
import unittest
from functools import partial
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import InlineKeyboardMarkup
from tests.v1_3.support import MiniCase
from app.mini import registration, sharing
from app.mini import handlers as home
from app.handlers.common import mini_registration_start as start_handler
from app.mini.ui import context, transport, heroes, hero_cards, hero_selector, summons as gacha_extra, registration as ui_registration, state as ui_state
from app.mini.gacha import perform_gacha_batch
from app.mini.wallet import add_coins
from app.mini.tower import handlers as tower_ui, service as tower
from app.mini.village import handlers as village


def callback(user=900123,data='mini:home:1:900123',chat=-100,thread=2,kind='supergroup',ephemeral=None):
    return NS(id='delivery-a',data=data,from_user=NS(id=user,username='tester'),answer=AsyncMock(),
        bot=NS(send_message=AsyncMock(),get_me=AsyncMock(return_value=NS(username='test_bot'))),
        message=NS(chat=NS(id=chat,type=kind),message_thread_id=thread,message_id=12,
                   ephemeral_message_id=ephemeral,delete_ephemeral=AsyncMock(),edit_text=AsyncMock()))


class NavigationFlows(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=MiniCase();self.f.setUp();self.addCleanup(self.f.doCleanups);self.addCleanup(self.f.tearDown)
        self.w,self.p,self.db=dict(self.f.world,enabled=1),self.f.player,self.f.db
    async def test_registration_button_private_name_then_home(self):
        user=900124;cb=callback(user,data=f"mini:create_help:{self.w['id']}:{user}",chat=self.w['chat_id'],thread=self.w['thread_id'])
        with patch.object(home,'get_mini_world_by_id',return_value=self.w),patch.object(home,'get_mini_player',return_value=None),patch.object(context,'is_mini_topic',return_value=True),patch.object(registration,'begin',side_effect=partial(registration.begin,db_path=self.db)),patch.object(home,'_edit_private',new_callable=AsyncMock) as send:
            await home.create_help_callback(cb)
        url=send.call_args.args[2].inline_keyboard[0][0].url
        self.assertIn('https://t.me/test_bot?start=mini_reg_',url)
        token=url.split('mini_reg_')[1]
        message=NS(from_user=cb.from_user,chat=NS(type='private'),text='/start mini_reg_'+token,answer=AsyncMock(),bot=cb.bot)
        with patch.object(registration,'activate',side_effect=partial(registration.activate,db_path=self.db)),patch.object(ui_registration,'get_mini_player',return_value=None),patch.object(registration,'is_mini_topic',return_value=True):
            await start_handler(message)
        self.assertIn('личном чате',message.answer.call_args.args[0]);message.text='Новый герой';message.answer.reset_mock()
        with patch.object(registration,'pending',side_effect=partial(registration.pending,db_path=self.db)),patch.object(registration,'complete',side_effect=partial(registration.complete,db_path=self.db)),patch.object(registration,'is_mini_topic',return_value=True),patch.object(ui_registration,'get_active_hero',return_value=None),patch.object(ui_registration,'get_mini_world_by_id',return_value=self.w):
            await ui_registration.name_message(message,registration.pending(user))
        text=message.answer.call_args.args[0];markup=message.answer.call_args.kwargs['reply_markup']
        self.assertIn('Персонаж создан',text);self.assertIn('первого героя',text)
        self.assertEqual([len(r) for r in markup.inline_keyboard],[2,1,2])
        self.assertEqual(self.f.sql('SELECT character_name FROM mini_players WHERE telegram_user_id=?',(user,))[0]['character_name'],'Новый герой')
    async def test_other_player_cannot_start_registration(self):
        cb=callback(user=4,data=f"mini:create_help:{self.w['id']}:900123")
        with patch.object(registration,'begin') as begin:await home.create_help_callback(cb)
        begin.assert_not_called();self.assertTrue(cb.answer.call_args.kwargs['show_alert'])
    async def test_batch_button_replayed_after_new_delivery_does_not_spend_twice(self):
        add_coins(self.p['id'],1000,'test',db_path=self.db);cb=callback()
        with patch.object(gacha_extra,'load_extended_context',return_value=(self.w,self.p,'a'*12)),patch.object(gacha_extra,'perform_gacha_batch',side_effect=partial(perform_gacha_batch,db_path=self.db)),patch.object(gacha_extra,'send_private_text_from_callback',new_callable=AsyncMock) as send:
            await gacha_extra.batch_callback(cb);cb.id='delivery-b';await gacha_extra.batch_callback(cb)
        self.assertEqual(len(self.f.sql('SELECT * FROM mini_gacha_pulls')),10)
        self.assertEqual(len(self.f.sql("SELECT * FROM mini_wallet_transactions WHERE reference_type='gacha_batch'")),1)
        self.assertEqual(send.await_count,2)
    async def test_tower_rarity_card_selection_stays_in_card_without_active_change(self):
        hero=self.f.hero();second=self.f.sql("SELECT * FROM mini_heroes WHERE rarity='common' AND id!=? LIMIT 1",(hero['id'],))[0]
        self.f.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.p['id'],second['id']))
        cb=callback()
        with patch.object(tower_ui,'shop_context',return_value=(self.w,self.p,f"choose.1.0.{second['id']}")),patch.object(tower_ui,'is_mini_topic',return_value=True),patch.object(tower_ui,'get_state',side_effect=partial(tower.get_state,db_path=self.db)),patch.object(tower_ui,'list_heroes',side_effect=partial(tower.list_heroes,db_path=self.db)),patch.object(tower_ui,'select_hero',side_effect=partial(tower.select_hero,db_path=self.db)),patch.object(tower_ui,'send_hero_card',new_callable=AsyncMock) as send:
            await tower_ui.tower_callback(cb)
        self.assertEqual(send.call_args.args[2]['id'],second['id'])
        self.assertEqual(send.call_args.args[4].inline_keyboard[0][0].text,'✅ Герой выбран')
        self.assertEqual(self.f.sql('SELECT active_hero_id FROM mini_players WHERE id=?',(self.p['id'],))[0]['active_hero_id'],hero['id'])
        self.assertEqual(tower.get_state(self.p['id'],self.db)['selected_hero']['id'],second['id'])
    async def test_stale_tower_floor_has_no_side_effects(self):
        cb=callback()
        with patch.object(tower_ui,'shop_context',return_value=(self.w,self.p,'choose.2.0.1')),patch.object(tower_ui,'is_mini_topic',return_value=True),patch.object(tower_ui,'get_state',side_effect=partial(tower.get_state,db_path=self.db)),patch.object(tower_ui,'select_hero') as select:
            await tower_ui.tower_callback(cb)
        select.assert_not_called();self.assertTrue(cb.answer.call_args.kwargs['show_alert'])
    async def test_legacy_village_card_cannot_assign_or_mutate_invalid_return(self):
        for payload in ('x.0.a.1.m.1.aaaa','x.0.c.0.-.1.aaaa.bad0','x.0.a.1.m.1.aaaa.ak0'):
            cb=callback()
            with patch.object(village,'load_extended_context',return_value=(self.w,self.p,payload)),patch.object(village.service,'mutate') as mutate:
                await village.action(cb)
            mutate.assert_not_called();self.assertTrue(cb.answer.call_args.kwargs['show_alert'])
    async def test_callback_scope_rejects_other_chat_thread_or_disabled_mini(self):
        world=dict(self.w,chat_id=-100,thread_id=2)
        for cb,enabled in ((callback(chat=-200),True),(callback(thread=3),True),(callback(),False)):
            with patch.object(context,'is_mini_topic',return_value=enabled):self.assertFalse(await context.valid_world_callback(cb,world))
        with patch.object(context,'is_mini_topic',return_value=True):
            self.assertTrue(await context.valid_world_callback(callback(chat=900123,thread=None,kind='private'),world))
    async def test_public_legacy_menu_is_replaced_privately(self):
        cb=callback();markup=InlineKeyboardMarkup(inline_keyboard=[])
        with patch('app.mini.worlds.get_mini_world_by_id',return_value=dict(self.w,chat_id=-100,thread_id=2)):
            await transport.edit_private(cb,'Личные данные',markup)
        cb.message.edit_text.assert_not_called()
        args=cb.bot.send_message.call_args.kwargs
        self.assertEqual(args['ephemeral_message_parameters'].receiver_user_id,900123)
        self.assertEqual(args['chat_id'],-100)
    async def test_private_same_text_is_benign_and_missing_photo_falls_back(self):
        cb=callback(chat=900123,kind='private');markup=InlineKeyboardMarkup(inline_keyboard=[])
        cb.message.edit_text.side_effect=TelegramBadRequest(method=EditMessageText(text='x'),message='message is not modified')
        self.assertIs(await transport.edit_private(cb,'x',markup),cb.message)
        cb.bot.send_message.assert_not_called()
        with patch.object(hero_cards,'get_hero_image',return_value=None):
            await hero_cards.send_hero_card(cb,self.w,{},'x',markup)
        self.assertNotIn('ephemeral_message_parameters',cb.bot.send_message.call_args.kwargs)
        self.assertEqual(cb.bot.send_message.call_args.kwargs['chat_id'],900123)
    def test_collection_card_and_shard_sale_keep_page_and_filter(self):
        hero=dict(self.f.hero(),shards=20)
        token=ui_state.remember(self.p['id'],'collection',dict(view='results',page=2,filter='0000000'),self.db)
        menu=hero_cards.hero_card_menu(1,900123,hero,{},collection=True,nav_token=token)
        buttons=[b for r in menu.inline_keyboard for b in r]
        self.assertTrue(next(b.callback_data for b in buttons if 'Продать' in b.text).endswith('~'+token))
        self.assertEqual(buttons[-1].callback_data,'mini:collectionback:1:900123:'+token)
        self.assertEqual(ui_state.restore(self.p['id'],token,self.db),dict(view='results',page=2,filter='0000000'))
        sale=heroes._shard_sell_menu(1,900123,hero,nav_token=token)
        self.assertTrue(sale.inline_keyboard[-1][0].callback_data.endswith('.'+token))
        self.assertIn(',all,',sale.inline_keyboard[-2][0].callback_data)
        self.assertIn('20',sale.inline_keyboard[-2][0].text)
    def test_collection_pagination_clamps_first_last_and_shrunken_list(self):
        hs=[dict(self.f.hero(),id=i) for i in range(1,10)]
        for page,target in ((-1,0),(2,2),(99,2)):
            _,menu=hero_selector.render_selector(hs,[],'c',1,900123,view='all',page=page)
            nav=menu.inline_keyboard[-2];self.assertEqual(len(nav),3)
            self.assertIn(f'all.{target}.',nav[1].callback_data)
        _,menu=hero_selector.render_selector(hs[:1],[],'c',1,900123,view='results',page=99)
        self.assertEqual(len(menu.inline_keyboard[0]),1);self.assertIn('results.0.',menu.inline_keyboard[-2][1].callback_data)
    def test_public_share_claim_survives_restart_and_isolated_players(self):
        from app.mini.schema import init_mini_db
        self.assertTrue(sharing.claim(self.p['id'],'hero:1:token',self.db))
        init_mini_db(self.db)
        self.assertFalse(sharing.claim(self.p['id'],'hero:1:token',self.db))
        self.assertTrue(sharing.claim(self.p['id'],'hero:1:new',self.db))
        from app.mini.players import create_mini_player
        other=create_mini_player(self.w['id'],900125,'@second','Второй',self.db)
        self.assertTrue(sharing.claim(other['id'],'hero:1:token',self.db))
