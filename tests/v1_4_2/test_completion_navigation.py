"""Real SQLite services behind the changed UI, with Telegram transport mocked."""
import asyncio
import copy
import json
import unittest
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.v1_3.support import MiniCase
from tests.v1_4.test_ui import state as village_state
from app.mini import registration
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini.wallet import add_coins, get_balance
from app.mini.heroes import get_player_heroes, get_player_hero, get_active_hero, set_active_hero
from app.mini.players import get_mini_player, touch_mini_player
from app.mini.worlds import get_mini_world_by_id
from app.mini.gacha import get_gacha_state, perform_gacha_batch
from app.mini.ui import context, state as navigation, heroes as hero_ui, hero_selector, hero_cards, summons
from app.mini.ui import registration as registration_ui
from app.mini.tower import service as tower, handlers as tower_ui
from app.mini.tower.selection import render_selection, RARITIES
from app.mini.village import service as village, handlers as village_ui


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def callback(world, tail='', action='tower', uid=900123):
    return SimpleNamespace(id='query', data=f"mini:{action}:{world['id']}:{uid}"+(':'+tail if tail else ''),
        from_user=SimpleNamespace(id=uid, username='tester'), answer=AsyncMock(),
        bot=SimpleNamespace(send_message=AsyncMock(),send_photo=AsyncMock(),get_me=AsyncMock(return_value=SimpleNamespace(username='test_bot'))),
        message=SimpleNamespace(chat=SimpleNamespace(id=world['chat_id'],type='supergroup'),
            message_thread_id=world['thread_id'],ephemeral_message_id=17,delete_ephemeral=AsyncMock()))


class NavigationIntegrationTests(MiniCase):
    def patches(self):
        stack=ExitStack()
        for module, name, fn in ((context,'get_mini_world_by_id',get_mini_world_by_id),
                (context,'get_mini_player',get_mini_player),(context,'touch_mini_player',touch_mini_player),
                (hero_ui,'get_player_heroes',get_player_heroes),(hero_ui,'get_player_hero',get_player_hero),
                (hero_ui,'get_gacha_state',get_gacha_state),(hero_ui,'set_active_hero',set_active_hero),
                (hero_ui,'is_favorite',__import__('app.mini.favorites',fromlist=['is_favorite']).is_favorite),(hero_ui,'restore',navigation.restore),(hero_ui,'remember',navigation.remember)):
            if hasattr(module,name):stack.enter_context(patch.object(module,name,side_effect=partial(fn,db_path=self.db)))
        return stack

    def test_legacy_operation_replay_is_message_scoped_not_query_or_other_chat(self):
        cb=callback(self.world);key=navigation.legacy_operation(cb,'gacha:coins')
        cb.id='second-delivery'
        self.assertEqual(navigation.legacy_operation(cb,'gacha:coins'),key)
        cb.message.chat.id+=1
        self.assertNotEqual(navigation.legacy_operation(cb,'gacha:coins'),key)
        cb.message.chat.id-=1;cb.message.ephemeral_message_id+=1
        self.assertNotEqual(navigation.legacy_operation(cb,'gacha:coins'),key)

    def test_context_rejects_wrong_owner_topic_chat_and_disabled_world_before_player_read(self):
        cb=callback(self.world,'nonce',action='gachabatch')
        with self.patches(),patch.object(context,'get_mini_player',wraps=partial(get_mini_player,db_path=self.db)) as read:
            cb.from_user.id+=1
            self.assertIsNone(asyncio.run(context.load_extended_context(cb,'gachabatch')));read.assert_not_called()
            cb.from_user.id-=1;cb.message.message_thread_id=self.world['thread_id']+1
            self.assertIsNone(asyncio.run(context.load_extended_context(cb,'gachabatch')));read.assert_not_called()
            cb.message.message_thread_id=self.world['thread_id'];cb.message.chat.id=-987654
            self.assertIsNone(asyncio.run(context.load_extended_context(cb,'gachabatch')));read.assert_not_called()
            cb.message.chat.id=self.world['chat_id']
            self.sql('UPDATE mini_worlds SET enabled=0 WHERE id=?',(self.world['id'],))
            self.assertIsNone(asyncio.run(context.load_extended_context(cb,'gachabatch')));read.assert_not_called()

    def test_private_callback_is_owner_bound_and_inline_without_chat_fails_closed(self):
        cb=callback(self.world,action='home');cb.message.chat=SimpleNamespace(type='private',id=900123)
        with self.patches():
            self.assertIsNotNone(asyncio.run(context.load_personal_context(cb)))
            cb.message.chat.id+=1
            self.assertIsNone(asyncio.run(context.load_personal_context(cb)))
            cb.message=None
            self.assertIsNone(asyncio.run(context.load_personal_context(cb)))

    def test_collection_card_active_and_return_keep_page_filter_across_restart(self):
        heroes=[self.hero(row['code']) for row in self.sql("SELECT code FROM mini_heroes WHERE rarity='common' ORDER BY id LIMIT 9")]
        options=hero_selector.options()[0];token=hero_selector.DIGITS[options.index('common')+1]+'000000'
        saved=navigation.remember(self.pid,'collection',dict(view='results',page=1,filter=token),self.db)
        init_mini_db(self.db)
        cb=callback(self.world,f"{heroes[4]['id']}.c~{saved}",action='heroactive')
        with self.patches(),patch.object(hero_ui,'send_hero_card',AsyncMock()) as send:
            asyncio.run(hero_ui.hero_active_callback(cb))
            markup=send.await_args.args[-1]
            self.assertTrue(any(b.callback_data.endswith(':'+saved) for b in buttons(markup)))
        cb.data=f"mini:collectionback:{self.world['id']}:900123:{saved}"
        with self.patches(),patch('app.mini.heroes.get_player_heroes',side_effect=partial(get_player_heroes,db_path=self.db)),patch.object(hero_ui,'get_favorites',return_value=[]),patch.object(hero_ui,'_send_private_text_from_callback',AsyncMock()) as send:
            asyncio.run(hero_ui.collection_back_callback(cb))
            markup=send.await_args.args[-1]
            self.assertTrue(any('2 / 3' in b.text for b in buttons(markup)))
            self.assertEqual(sum(b.callback_data.startswith('mini:hero:') for b in buttons(markup)),4)
            self.assertTrue(any(token in b.callback_data for b in buttons(markup)))
        with self.assertRaises(ValueError):navigation.restore(self.pid+1,saved,self.db)

    def test_removed_filtered_heroes_clamp_return_page_without_losing_filter(self):
        heroes=[self.hero(row['code']) for row in self.sql("SELECT code FROM mini_heroes WHERE rarity='common' LIMIT 5")]
        token=hero_selector.DIGITS[hero_selector.options()[0].index('common')+1]+'000000'
        saved=navigation.remember(self.pid,'collection',dict(view='results',page=8,filter=token),self.db)
        self.sql('DELETE FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(self.pid,heroes[-1]['id']))
        cb=callback(self.world,saved,action='collectionback')
        with self.patches(),patch('app.mini.heroes.get_player_heroes',side_effect=partial(get_player_heroes,db_path=self.db)),patch.object(hero_ui,'get_favorites',return_value=[]),patch.object(hero_ui,'_send_private_text_from_callback',AsyncMock()) as send:
            asyncio.run(hero_ui.collection_back_callback(cb))
        self.assertTrue(any('1 / 1' in b.text for b in buttons(send.await_args.args[-1])))

    def test_batch_handler_double_click_replays_and_next_ten_has_fresh_operation(self):
        add_coins(self.pid,1000,'Test',db_path=self.db);cb=callback(self.world,'12345678',action='gachabatch')
        with self.patches(),patch.object(summons,'perform_gacha_batch',side_effect=partial(perform_gacha_batch,db_path=self.db)),patch.object(summons,'send_private_text_from_callback',AsyncMock()) as send,patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            asyncio.run(summons.batch_callback(cb));init_mini_db(self.db);cb.id='other-query';asyncio.run(summons.batch_callback(cb))
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)
        markup=send.await_args.args[-1];new=next(b.callback_data for b in buttons(markup) if b.text=='✨ Ещё 10')
        self.assertNotEqual(new,cb.data)
        self.assertEqual(len(self.sql("SELECT * FROM mini_wallet_transactions WHERE reference_type='gacha_batch'")),1)

    def test_batch_result_pages_are_owned_and_open_card_without_economic_write(self):
        add_coins(self.pid,1000,'Test',db_path=self.db)
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            batch=perform_gacha_batch(self.pid,self.db,operation_key='test')
        cb=callback(self.world,f"{batch['batch_id']}.9",action='batchhero')
        from app.mini.gacha import get_gacha_batch
        balance=get_balance(self.pid,self.db)
        with self.patches(),patch.object(summons,'get_gacha_batch',side_effect=partial(get_gacha_batch,db_path=self.db)),patch.object(summons,'show_gacha_pull_result',AsyncMock()) as show:
            asyncio.run(summons.batch_result_callback(cb))
        self.assertEqual(show.await_args.args[3]['pull_id'],batch['results'][9]['pull_id'])
        self.assertTrue(show.await_args.kwargs['return_callback'].endswith('.9'))
        self.assertEqual(get_balance(self.pid,self.db),balance)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)

    def test_coupon_from_gacha_and_inventory_use_one_service_and_replay(self):
        from app.mini.items import grant_item_in_transaction,use_inventory_item,get_inventory_item
        from app.mini.ui import inventory
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');grant_item_in_transaction(conn,self.pid,'rarity_coupon_rare',1)
        state=get_gacha_state(self.pid,self.db);coupon=state['coupons'][0]
        _,markup=summons.coupon_screen(state,self.world['id'],900123,coupon['item_id'])
        data=next(b.callback_data for b in buttons(markup) if b.text=='✅ Применить купон')
        cb=callback(self.world,action='useitem');cb.data=data
        with self.patches(),patch.object(inventory,'get_inventory_item',side_effect=partial(get_inventory_item,db_path=self.db)),patch.object(inventory,'use_inventory_item',side_effect=partial(use_inventory_item,db_path=self.db)),patch('app.mini.items.get_previous_item_use',side_effect=partial(__import__('app.mini.items',fromlist=['get_previous_item_use']).get_previous_item_use,db_path=self.db)),patch.object(inventory,'show_gacha_pull_result',AsyncMock()) as show:
            asyncio.run(inventory.use_item_confirm_callback(cb));init_mini_db(self.db);cb.id='repeat';asyncio.run(inventory.use_item_confirm_callback(cb))
        self.assertEqual(show.await_count,2)
        self.assertEqual(show.await_args.args[3]['rarity'],'rare')
        self.assertEqual(len(self.sql('SELECT * FROM mini_item_uses')),1)
        self.assertEqual(len(self.sql('SELECT * FROM mini_player_heroes')),1)
        self.assertEqual(get_gacha_state(self.pid,self.db)['pity'],dict(uncommon=0,rare=0,legendary=0))

    def test_public_share_claim_survives_restart_and_repeated_telegram_callback(self):
        self.assertTrue(navigation.claim_publication(self.pid,'share:hero-result',self.db))
        init_mini_db(self.db)
        self.assertFalse(navigation.claim_publication(self.pid,'share:hero-result',self.db))
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims=list(pool.map(lambda _:navigation.claim_publication(self.pid,'share:concurrent',self.db),range(2)))
        self.assertEqual(sum(claims),1)

    def test_boss_wrong_chat_never_reaches_registration_or_combat_mutation(self):
        from app.mini.boss import registration as boss_registration,handlers as boss_combat
        cb=callback(self.world,action='unused');cb.message.chat.id=-123456
        with self.patches(),patch.object(boss_registration,'register_player') as join,patch.object(boss_combat,'hit_boss') as hit:
            cb.data=f"miniboss:join:{self.world['id']}:1";asyncio.run(boss_registration.boss_join_callback(cb))
            cb.data=f"miniboss:hit:{self.world['id']}:1:1:1";asyncio.run(boss_combat.boss_hit_callback(cb))
        join.assert_not_called();hit.assert_not_called()

    def test_saved_batch_card_display_stays_immutable_after_catalog_and_stars_change(self):
        add_coins(self.pid,1000,'Test',db_path=self.db)
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            result=perform_gacha_batch(self.pid,self.db,operation_key='snapshot')['results'][0]
        old=result['name']
        self.sql("UPDATE mini_heroes SET name='Renamed',attack=99999 WHERE id=?",(result['id'],))
        self.sql('UPDATE mini_player_heroes SET stars=3 WHERE player_id=?',(self.pid,))
        cb=callback(self.world)
        with patch.object(hero_cards,'get_gacha_state',side_effect=partial(get_gacha_state,db_path=self.db)),patch.object(hero_cards,'get_player_hero',side_effect=partial(get_player_hero,db_path=self.db)),patch.object(hero_cards,'send_hero_card',AsyncMock()) as send:
            asyncio.run(hero_cards.show_gacha_pull_result(cb,self.world,self.player,result))
        self.assertIn(old,send.await_args.args[3]);self.assertNotIn('Renamed',send.await_args.args[3])
        self.assertNotIn('99999',send.await_args.args[3])
        # Actions still use current owned state, not old upgrade/star values.
        self.assertEqual(send.await_args.args[2]['stars'],3)

    def test_private_registration_navigation_stays_private_text_and_photo(self):
        from app.mini.ui.transport import send_private_text_from_callback
        cb=callback(self.world);cb.message.chat=SimpleNamespace(id=900123,type='private');cb.message.ephemeral_message_id=None
        asyncio.run(send_private_text_from_callback(cb,self.world,'Menu',None))
        self.assertEqual(cb.bot.send_message.await_args.kwargs['chat_id'],900123)
        self.assertNotIn('ephemeral_message_parameters',cb.bot.send_message.await_args.kwargs)
        with patch.object(hero_cards,'get_hero_image',return_value=__file__):
            asyncio.run(hero_cards.send_hero_card(cb,self.world,{'code':'Villager'},'Card',None))
        self.assertEqual(cb.bot.send_photo.await_args.kwargs['chat_id'],900123)
        self.assertNotIn('message_thread_id',cb.bot.send_photo.await_args.kwargs)
        self.assertNotIn('ephemeral_message_parameters',cb.bot.send_photo.await_args.kwargs)

    def test_telegram_send_failure_after_batch_commit_replays_without_new_payment(self):
        from aiogram.exceptions import TelegramAPIError
        add_coins(self.pid,1000,'Test',db_path=self.db);cb=callback(self.world,'12345678',action='gachabatch')
        with self.patches(),patch.object(summons,'perform_gacha_batch',side_effect=partial(perform_gacha_batch,db_path=self.db)),patch.object(summons,'send_private_text_from_callback',AsyncMock(side_effect=[TelegramAPIError(method='sendMessage',message='timeout'),None])),patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            asyncio.run(summons.batch_callback(cb));self.assertIn('Результат сохранён',cb.answer.await_args.args[0])
            init_mini_db(self.db);cb.id='after-restart';asyncio.run(summons.batch_callback(cb))
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)
        self.assertEqual(get_balance(self.pid,self.db),700)

    def test_favorite_replacement_keeps_source_and_uses_bounded_callback_for_large_ids(self):
        world=dict(self.world,id=999999);uid=9999999999;hid=100000004;old=[100000001,100000002,100000003];token='aabbccdd'
        cb=callback(world,f'add.{hid}~{token}',action='favorite',uid=uid)
        with patch.object(hero_ui,'_load_extended_context',AsyncMock(return_value=(world,self.player,f'add.{hid}~{token}'))),patch.object(hero_ui,'restore',return_value={}),patch.object(hero_ui,'get_player_hero',side_effect=lambda pid,hid:dict(id=hid,name=str(hid))),patch.object(hero_ui,'get_favorites',return_value=old),patch.object(hero_ui,'_send_private_text_from_callback',AsyncMock()) as send:
            asyncio.run(hero_ui.favorite_callback(cb))
        markup=send.await_args.args[-1]
        self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons(markup)))
        self.assertTrue(buttons(markup)[0].callback_data.endswith('~'+token))
        self.assertTrue(buttons(markup)[-1].callback_data.endswith('.'+token))

    def test_private_registration_can_open_events_with_shared_scope_validation(self):
        from app.mini.events import handlers as events_ui
        cb=callback(self.world,action='events');cb.message.chat=SimpleNamespace(id=900123,type='private')
        with self.patches(),patch.object(events_ui,'_show',AsyncMock()) as show:
            asyncio.run(events_ui.events_callback(cb))
        show.assert_awaited_once()


class RegistrationUIIntegrationTests(MiniCase):
    def private(self,text):
        return SimpleNamespace(text=text,chat=SimpleNamespace(type='private',id=88661),from_user=SimpleNamespace(id=88661,username='new_player'),answer=AsyncMock())

    def test_private_deep_link_name_home_gift_no_public_name_and_restart(self):
        token=registration.begin(self.world['id'],88661,self.db)
        message=self.private('/start mini_reg_'+token)
        with patch.object(registration_ui.service,'activate',side_effect=partial(registration.activate,db_path=self.db)),patch.object(registration_ui,'get_mini_player',side_effect=partial(get_mini_player,db_path=self.db)):
            asyncio.run(registration_ui.start_private(message))
        self.assertIn('личном чате',message.answer.await_args.args[0]);init_mini_db(self.db)
        request=registration.pending(88661,self.db)
        message.text='  Новый   герой  '
        with patch.object(registration_ui.service,'complete',side_effect=partial(registration.complete,db_path=self.db)),patch.object(registration_ui,'get_mini_world_by_id',side_effect=partial(get_mini_world_by_id,db_path=self.db)),patch.object(registration_ui,'get_active_hero',side_effect=partial(get_active_hero,db_path=self.db)):
            asyncio.run(registration_ui.name_message(message,request));asyncio.run(registration_ui.name_message(message,request))
        self.assertIn('Персонаж создан',message.answer.await_args_list[-2].args[0])
        self.assertNotIn('Персонаж создан',message.answer.await_args.args[0])
        self.assertNotIn('Стартовый подарок',message.answer.await_args.args[0])
        self.assertEqual([len(r) for r in message.answer.await_args.kwargs['reply_markup'].inline_keyboard],[2,1,2])
        player=get_mini_player(self.world['id'],88661,self.db)
        self.assertEqual(player['character_name'],'Новый герой');self.assertEqual(get_gacha_state(player['id'],self.db)['tickets'],3)
        self.assertEqual(self.sql('SELECT * FROM mini_public_notifications WHERE player_id=?',(player['id'],)),[])

    def test_name_filter_never_swallows_group_text_or_other_commands(self):
        message=self.private('Имя')
        with patch.object(registration_ui.service,'pending',return_value={'token':'owner'}) as lookup:
            self.assertTrue(asyncio.run(registration_ui.PendingName()(message)))
            for text in ('/inv','/start','/supercoins'):
                message.text=text;self.assertFalse(asyncio.run(registration_ui.PendingName()(message)))
            message.text='Имя';message.chat.type='supergroup';lookup.reset_mock()
            self.assertFalse(asyncio.run(registration_ui.PendingName()(message)));lookup.assert_not_called()

    def test_cancel_and_malformed_name_stay_private_without_mutation(self):
        token=registration.begin(self.world['id'],88661,self.db);request=registration.activate(token,88661,self.db)
        message=self.private('<b>Имя</b>')
        with patch.object(registration_ui.service,'complete',side_effect=partial(registration.complete,db_path=self.db)):
            asyncio.run(registration_ui.name_message(message,request))
        self.assertIsNotNone(registration.pending(88661,self.db));self.assertIsNone(get_mini_player(self.world['id'],88661,self.db))
        message.text='/cancel'
        with patch.object(registration_ui.service,'cancel',side_effect=partial(registration.cancel,db_path=self.db)):
            asyncio.run(registration_ui.name_message(message,request))
        self.assertIsNone(registration.pending(88661,self.db))

    def test_existing_hero_home_can_show_image_in_private_chat(self):
        self.hero();message=self.private('/start');message.answer_photo=AsyncMock()
        with patch.object(registration_ui,'get_active_hero',side_effect=partial(get_active_hero,db_path=self.db)),patch.object(registration_ui,'get_hero_image',return_value=__file__):
            asyncio.run(registration_ui.reply_home(message,self.world,self.player))
        message.answer_photo.assert_awaited_once();message.answer.assert_not_awaited()


class TowerRetryIntegrationTests(MiniCase):
    def lost(self):
        hero=self.hero();tower.select_hero(self.pid,hero['id'],self.db,expected_floor=1)
        attempt=tower.start_selected_attempt(self.pid,1,hero['id'],self.db)
        self.sql("UPDATE mini_tower_attempts SET status='lost',shields=0 WHERE id=?",(attempt['id'],))
        return hero,attempt

    def test_retry_concurrent_once_restart_and_real_fresh_snapshot(self):
        hero,old=self.lost();self.sql('UPDATE mini_player_heroes SET stars=2 WHERE player_id=? AND hero_id=?',(self.pid,hero['id']))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:tower.retry_attempt(self.pid,old['id'],self.db),range(2)))
        self.assertEqual(results[0]['id'],results[1]['id']);fresh=results[0]
        self.assertNotEqual(fresh['id'],old['id']);self.assertEqual((fresh['shields'],fresh['turn'],fresh['status']),(3,0,'active'))
        self.assertEqual(json.loads(fresh['hero_json'])['stars'],2)
        self.assertEqual(fresh['current_hp'],json.loads(fresh['enemy_json'])['max_hp'])
        init_mini_db(self.db);self.assertEqual(tower.retry_attempt(self.pid,old['id'],self.db)['id'],fresh['id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_tower_attempts')),2)
        self.assertEqual(self.sql('SELECT * FROM mini_tower_rewards'),[])

    def test_retry_rollback_if_ledger_insert_fails_and_can_then_retry(self):
        _,old=self.lost()
        self.sql("CREATE TRIGGER fail_retry BEFORE INSERT ON mini_tower_retries BEGIN SELECT RAISE(ABORT,'fault'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):tower.retry_attempt(self.pid,old['id'],self.db)
        self.assertIsNone(tower.get_state(self.pid,self.db)['attempt']);self.assertEqual(len(self.sql('SELECT * FROM mini_tower_attempts')),1)
        self.sql('DROP TRIGGER fail_retry');self.assertEqual(tower.retry_attempt(self.pid,old['id'],self.db)['status'],'active')

    def test_retry_wrong_owner_stale_floor_or_other_active_attempt_fails(self):
        _,old=self.lost()
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid+1,old['id'],self.db)
        self.floor(1)
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid,old['id'],self.db)
        self.floor(0);tower.start_selected_attempt(self.pid,1,old['hero_id'],self.db)
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid,old['id'],self.db)

    def test_active_attempt_blocks_all_selector_paths_and_saved_lost_state_is_visible(self):
        hero,old=self.lost();self.assertEqual(tower.get_state(self.pid,self.db)['last_attempt']['id'],old['id'])
        markup=tower_ui.render_state(tower.get_state(self.pid,self.db),self.world['id'],900123)[1]
        self.assertTrue(any(b.callback_data.endswith(f"retry.{old['id']}") for b in buttons(markup)))
        tower.retry_attempt(self.pid,old['id'],self.db)
        with self.assertRaises(ValueError):tower.select_hero(self.pid,hero['id'],self.db)
        with self.assertRaises(ValueError):render_selection(tower.get_state(self.pid,self.db),tower.list_heroes(self.pid,self.db),1,900123)

    def test_retry_cannot_use_a_newly_settled_or_deleted_hero(self):
        hero,old=self.lost();add_coins(self.pid,10,'Test',db_path=self.db)
        village.mutate(self.pid,'buy',self.db,operation_key='house',expected_houses=0);village.mutate(self.pid,'settle',self.db,hero_id=hero['id'],operation_key='settle')
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid,old['id'],self.db)
        village.mutate(self.pid,'evict',self.db,hero_id=hero['id'],operation_key='evict')
        self.sql('DELETE FROM mini_player_heroes WHERE player_id=?',(self.pid,))
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid,old['id'],self.db)

    def test_three_stages_choose_stays_on_photo_card_and_back_list(self):
        hero=self.hero();state=tower.get_state(self.pid,self.db);heroes=tower.list_heroes(self.pid,self.db)
        _,menu,_=render_selection(state,heroes,self.world['id'],900123)
        rarity=RARITIES.index(hero['rarity']);self.assertTrue(any(f'rarity.1.{rarity}.0' in b.callback_data for b in buttons(menu)))
        _,menu,_=render_selection(state,heroes,1,900123,stage='list',rarity=rarity)
        self.assertTrue(any('card.1.' in b.callback_data for b in buttons(menu)))
        cb=callback(self.world,f"choose.1.{rarity}.{hero['id']}.0")
        with patch.object(tower_ui,'shop_context',AsyncMock(return_value=(self.world,self.player,cb.data.split(':',4)[4]))),patch.object(tower_ui,'get_state',side_effect=partial(tower.get_state,db_path=self.db)),patch.object(tower_ui,'list_heroes',side_effect=partial(tower.list_heroes,db_path=self.db)),patch.object(tower_ui,'select_hero',side_effect=partial(tower.select_hero,db_path=self.db)),patch.object(tower_ui,'send_hero_card',AsyncMock()) as send:
            asyncio.run(tower_ui.tower_callback(cb))
        self.assertEqual(send.await_args.args[2]['id'],hero['id'])
        labels=[b.text for b in buttons(send.await_args.args[-1])]
        self.assertIn('✅ Герой выбран',labels);self.assertIn('⬅️ К героям',labels);self.assertIn('🏰 К испытанию',labels)


    def test_long_hero_caption_preserves_counterpick_hint_within_photo_limit(self):
        hero=self.hero();state=tower.get_state(self.pid,self.db)
        rarity=RARITIES.index(hero['rarity'])
        with patch('app.mini.tower.selection.hero_caption',return_value='Описание '*1000):
            text,_,_=render_selection(state,[hero],1,900123,stage='card',rarity=rarity,hero_id=hero['id'])
        self.assertLessEqual(len(text),1020)
        self.assertIn(f"Против {state['floor']['name']}: фракция ×",text)
        self.assertIn('Особенности врага:',text)


class VillageChangedNavigationTests(MiniCase):
    def test_settle_assign_remove_evict_stay_screen_with_revision_and_restart(self):
        hero=self.hero();add_coins(self.pid,100,'Test',db_path=self.db)
        village.mutate(self.pid,'buy',self.db,operation_key='house',now=1000,expected_houses=0)
        original_state=village.get_state
        with ExitStack() as stack:
            stack.enter_context(patch.object(village_ui,'load_extended_context',side_effect=lambda cb,prefix: (self.world,self.player,cb.data.split(':',4)[4])))
            stack.enter_context(patch.object(village_ui.service,'get_state',side_effect=partial(village.get_state,db_path=self.db,now=1000)))
            stack.enter_context(patch.object(village_ui.service,'mutate',side_effect=partial(village.mutate,db_path=self.db,now=1000)))
            stack.enter_context(patch.object(village_ui,'get_player_heroes',side_effect=partial(get_player_heroes,db_path=self.db)))
            send=stack.enter_context(patch.object(village_ui,'send_private_text_from_callback',AsyncMock()))
            for view in ('add','add_mine','workers_mine','evict'):
                state=original_state(self.pid,self.db,now=1000)
                _,markup=village_ui.screen(state,self.world['id'],900123,view)
                data=next(b.callback_data for b in buttons(markup) if ':x.' in b.callback_data)
                cb=callback(self.world);cb.data=data
                asyncio.run(village_ui.action(cb));init_mini_db(self.db);asyncio.run(village_ui.action(cb))
                self.assertEqual(send.await_args.args[3].inline_keyboard[-1][-1].text,'⬅️ Назад')
                self.assertNotIn(hero['name'],[b.text.split(' •')[0].removeprefix('🚪 ') for b in buttons(send.await_args.args[3])])
                if view=='add_mine':self.assertEqual(original_state(self.pid,self.db,now=1000)['residents'][0]['building'],'mine')
                if view=='workers_mine':self.assertIsNone(original_state(self.pid,self.db,now=1000)['residents'][0]['building'])
            self.assertEqual(original_state(self.pid,self.db,now=1000)['residents'],[])
            self.assertEqual(len(self.sql('SELECT * FROM mini_player_heroes')),1)
            self.assertEqual(get_balance(self.pid,self.db),90)

    def test_invalid_return_context_or_old_direct_assign_rejected_before_mutation(self):
        for tail in ('x.1.a.1.m.1.token.bad!','x.1.a.1.m.1.token','x.1.a.1.m.1.token.c0'):
            cb=callback(self.world,tail,action='vlg')
            with patch.object(village_ui,'load_extended_context',AsyncMock(return_value=(self.world,self.player,tail))),patch.object(village_ui.service,'mutate') as mutate:
                asyncio.run(village_ui.action(cb))
            mutate.assert_not_called();self.assertTrue(cb.answer.await_args.kwargs['show_alert'])

    def test_worker_lists_never_implicitly_transfer_or_show_unsettled_heroes(self):
        residents=[dict(hero_id=n,name=f'Герой {n}',building=None if n<10 else 'mine') for n in range(20)]
        state=village_state(2,residents)
        _,markup=village_ui.screen(state,1,900123,'add_market',1)
        self.assertEqual(sum(':x.' in b.callback_data for b in buttons(markup)),2)
        self.assertTrue(all('Герой 1' not in b.text for b in buttons(markup) if ':x.' in b.callback_data))
        _,markup=village_ui.screen(state,1,900123,'workers_market')
        self.assertFalse(any(':x.' in b.callback_data for b in buttons(markup)))
        _,markup=village_ui.screen(state,1,900123,'hero',2,hero_id=10)
        self.assertEqual([b.text for b in buttons(markup)],['Снять с производства','Выселить','Назад'])
        self.assertTrue(buttons(markup)[-1].callback_data.endswith('residents.2'))
