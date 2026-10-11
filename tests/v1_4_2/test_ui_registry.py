import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from concurrent.futures import ThreadPoolExecutor
from tests.v1_3.support import MiniCase
from tests.v1_4.test_ui import state,buttons
from app.mini.village import handlers as village,service
from app.mini.duels.handlers import public_challenge_text
from app.mini.item_registry import load_definitions,get_definition,sync_in_transaction
from app.mini.catalog import load_shop_catalog,load_hero_catalog,hero_image_path
from app.mini.boss.catalog import load_boss_catalog
from app.mini.db import connect_mini_db
from app.mini.shop import sync_shop_catalog,purchase_offer
from app.mini.wallet import add_coins,get_balance


class InterfaceTests(unittest.TestCase):
    def test_one_two_three_four_and_fifty_residents_in_two_columns(self):
        for count in (0,1,2,3,4,5,50):
            residents=[dict(hero_id=i,name=f'Герой {i}',building=None) for i in range(count)]
            pages=max(1,(count+3)//4)
            seen=[]
            for page in range(pages):
                text,markup=village.screen(state(5,residents),1,42,'residents',page)
                hero_rows=[row for row in markup.inline_keyboard if any(':hero.' in b.callback_data for b in row)]
                self.assertTrue(all(len(row)<=2 for row in hero_rows));self.assertLessEqual(len(hero_rows),2)
                seen += [b.text for row in hero_rows for b in row]
                self.assertEqual(markup.inline_keyboard[-1][-1].text,'⬅️ Назад')
            self.assertEqual(len(seen),count)

    def test_card_back_page_and_building_menus_candidates_and_workers(self):
        residents=[dict(hero_id=i,name=f'Герой {i}',building='market' if i%2 else 'mine') for i in range(8)]
        _,markup=village.screen(state(5,residents),1,42,'hero',1,hero_id=6)
        self.assertEqual([b.text for b in buttons(markup)],['Снять с производства','Выселить','Назад'])
        self.assertTrue(buttons(markup)[-1].callback_data.endswith('residents.1'))
        for job in service.BUILDINGS:
            _,menu=village.screen(state(5,residents),1,42,job)
            self.assertEqual([b.text for b in buttons(menu)],['➕ Добавить','➖ Убрать','⬅️ Назад'])
            _,candidates=village.screen(state(5,residents),1,42,'add_'+job)
            _,workers=village.screen(state(5,residents),1,42,'workers_'+job)
            self.assertFalse(any(':x.' in b.callback_data for b in buttons(candidates)))
            if job=='hunt':self.assertIn('Пока пусто',village.screen(state(5,residents),1,42,'workers_hunt')[0])
            self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons(candidates)+buttons(workers)))

    def test_duel_defender_mentions_and_escaping(self):
        challenger={'character_name':'Лазарь <&>'}
        for raw in ('defender','@defender','@@defender'):
            text=public_challenge_text(challenger,{'username':raw},15)
            self.assertIn('вызывает @defender',text);self.assertNotIn('@@',text)
            self.assertIn('Лазарь &lt;&amp;&gt;',text)
        text=public_challenge_text(challenger,{'character_name':'Имя <x>','telegram_user_id':42},15)
        self.assertIn('tg://user?id=42',text);self.assertIn('Имя &lt;x&gt;',text)
        text=public_challenge_text(challenger,{'username':'bad<name>','character_name':'Имя'},15)
        self.assertNotIn('bad<name>',text)

    def test_shadow_catalog_all_dynamic_mappings_images_passives_and_no_gacha(self):
        from app.mini.combat.hero_abilities.catalog import configured_ability_keys
        heroes={h['code']:h for h in load_hero_catalog()['heroes']}
        shadows=[h for h in heroes.values() if h['rarity']=='shadow']
        self.assertTrue(shadows)
        for h in shadows:
            self.assertIsNotNone(hero_image_path(h['image']));self.assertIn(h['passive_key'],configured_ability_keys())
        for boss in load_boss_catalog()['bosses']:
            if boss.get('shadow_hero_code'):self.assertEqual(heroes[boss['shadow_hero_code']]['rarity'],'shadow')

    def test_registry_canonical_definitions_and_conflict_rejection(self):
        definitions=load_definitions()
        for code in ('rarity_coupon_uncommon','rarity_coupon_rare','rarity_coupon_legendary','spirit_lantern','summon_ticket','village_gold_boost'):
            self.assertEqual(get_definition(code),definitions[code]);self.assertEqual(definitions[code]['kind'],'consumable')
        shop=copy.deepcopy(load_shop_catalog());shop['products'].append(copy.deepcopy(shop['products'][0]))
        shop['products'][-1]['item']['name']='Конфликт'
        with patch('app.mini.item_registry.load_shop_catalog',return_value=shop),self.assertRaisesRegex(ValueError,'Conflicting'):
            load_definitions()
        self.assertTrue(any(item['kind']=='equipment' for item in definitions.values()))


class RegistryIntegrationTests(MiniCase):
    def test_duel_public_defender_sent_once_across_callback_restart(self):
        import asyncio
        from app.mini.duels import handlers,service
        from app.mini.players import create_mini_player
        from app.mini.schema import init_mini_db
        from app.mini.ui.callbacks import number
        hero=self.hero();add_coins(self.pid,20,'Тест',db_path=self.db)
        defender=create_mini_player(self.world['id'],991117,'@rival','Соперник',self.db)
        payload=f'c.{number(defender["id"])}.{number(hero["id"])}.nonce'
        cb=SimpleNamespace(from_user=SimpleNamespace(id=900123),answer=AsyncMock(),bot=SimpleNamespace(send_message=AsyncMock()))
        original_challenge=service.challenge
        with patch.object(handlers,'load_extended_context',AsyncMock(return_value=(self.world,self.player,payload))),patch.object(service,'challenge',side_effect=lambda *args:original_challenge(*args,db_path=self.db)),patch.object(handlers,'connect_mini_db',side_effect=lambda:connect_mini_db(self.db)),patch.object(handlers,'screen',return_value=('Дуэль',None)),patch.object(handlers,'send_private_text_from_callback',AsyncMock()):
            asyncio.run(handlers.action(cb));init_mini_db(self.db);asyncio.run(handlers.action(cb))
        cb.bot.send_message.assert_awaited_once()
        self.assertIn('вызывает @rival',cb.bot.send_message.await_args.kwargs['text'])
        self.assertEqual(cb.bot.send_message.await_args.kwargs['parse_mode'],'HTML')
        self.assertEqual(get_balance(self.pid,self.db),10)

    def test_sync_keeps_ids_inventory_and_all_reward_sources(self):
        from app.mini.items import grant_item_in_transaction
        from app.mini.boss.catalog import sync_boss_reward_items
        from app.mini.weekly import sync_items_in_transaction
        sync_shop_catalog(self.world['id'],self.db)
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');grant_item_in_transaction(conn,self.pid,'spirit_lantern',2)
        before=self.sql('SELECT * FROM mini_inventory')
        ids=self.sql('SELECT id,code FROM mini_items ORDER BY id')
        sync_boss_reward_items(self.db)
        with connect_mini_db(self.db) as conn:conn.execute('BEGIN IMMEDIATE');sync_items_in_transaction(conn)
        sync_shop_catalog(self.world['id'],self.db)
        self.assertEqual(before,self.sql('SELECT * FROM mini_inventory'));self.assertEqual(ids,self.sql('SELECT id,code FROM mini_items ORDER BY id'))
        self.assertEqual(self.sql('PRAGMA integrity_check')[0]['integrity_check'],'ok')

    def test_concurrent_shop_callback_purchase_once_and_replay_after_restart(self):
        sync_shop_catalog(self.world['id'],self.db);add_coins(self.pid,1000,'Тест',db_path=self.db)
        offer=self.sql("SELECT * FROM mini_shop_offers WHERE code='rarity_coupon_rare'")[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:purchase_offer(self.pid,self.world['id'],offer['id'],self.db,operation_key='purchase'),range(2)))
        self.assertEqual(results[0]['purchase_id'],results[1]['purchase_id']);self.assertEqual(get_balance(self.pid,self.db),500)
        self.assertEqual(self.sql('SELECT quantity FROM mini_inventory WHERE player_id=? AND item_id=?',(self.pid,offer['item_id']))[0]['quantity'],1)
        with self.assertRaises(ValueError):purchase_offer(self.pid,self.world['id'],offer['id']+1,self.db,operation_key='purchase')
        self.sql("INSERT INTO mini_worlds(chat_id,thread_id,name) VALUES(-123456,0,'Другой мир')")
        other=self.sql('SELECT id FROM mini_worlds WHERE chat_id=-123456')[0]['id']
        with self.assertRaises(ValueError):purchase_offer(self.pid,other,offer['id'],self.db,operation_key='purchase')


class WorkerNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumed_ticket_callback_restores_saved_result(self):
        from app.mini.ui import inventory
        cb=SimpleNamespace(from_user=SimpleNamespace(id=42),id='telegram-id',answer=AsyncMock())
        saved={'payment':'ticket','repeated':True,'code':'Villager'}
        with patch.object(inventory,'_load_extended_context',AsyncMock(return_value=({'id':1},{'id':2},'item,3,nonce'))),patch.object(inventory,'get_inventory_item',return_value=None),patch('app.mini.items.get_previous_item_use',return_value=None),patch('app.mini.gacha.get_previous_gacha_pull',return_value=saved) as previous,patch.object(inventory,'show_gacha_pull_result',AsyncMock()) as show:
            await inventory.use_item_confirm_callback(cb)
        previous.assert_called_once_with(2,'itemuse:item,3,nonce');show.assert_awaited_once_with(cb,{'id':1},{'id':2},saved)

    async def test_assign_and_remove_stay_in_current_worker_screen(self):
        for action,expected in (('am0','add_mine'),('wm0','workers_mine')):
            payload='x.1.a.1.'+('m' if action=='am0' else '-')+'.1.token.'+action
            cb=SimpleNamespace(from_user=SimpleNamespace(id=42),answer=AsyncMock())
            with patch.object(village,'load_extended_context',AsyncMock(return_value=({'id':1},{'id':1},payload))),patch.object(service,'mutate',return_value={'applied':True}),patch.object(service,'get_state',return_value=state()),patch.object(village,'screen',return_value=('Экран',None)) as screen,patch.object(village,'send_private_text_from_callback',AsyncMock()):
                await village.action(cb)
            self.assertEqual(screen.call_args.args[3],expected)

    async def test_stale_card_rejected_before_render(self):
        cb=SimpleNamespace(from_user=SimpleNamespace(id=42),answer=AsyncMock())
        with patch.object(village,'load_extended_context',AsyncMock(return_value=({'id':1},{'id':1},'hero.1.0.1'))),patch.object(service,'get_state',return_value=state()),patch.object(village,'screen') as screen:
            await village.action(cb)
        screen.assert_not_called();self.assertTrue(cb.answer.await_args.kwargs['show_alert'])
