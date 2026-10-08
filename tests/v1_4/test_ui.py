import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from app.mini.village import handlers as village
from app.mini.duels import handlers as duels
from app.mini.mythic import handlers as mythic
from app.mini.ui import activities
from app.mini.handlers import router,submenu
from app.mini.ui.context import load_extended_context


def buttons(markup):return [b for row in markup.inline_keyboard for b in row]


def state(houses=0,residents=None):
    return dict(player_id=1,houses=houses,capacity=10*houses,revision=123456,next_price=village.service.PRICES[houses] if houses<5 else None,
        residents=residents or [],hourly={k:Decimal('0.5') for k in ('market','mine','hunt','consumption')},
        fed=True,seconds_left=1800,coins=Decimal('1.125'),shards=Decimal('0.125'),boosts={})


class NewScreensTests(unittest.TestCase):
    def test_dynamic_one_purchase_button_and_all_houses(self):
        for count,price in enumerate(village.service.PRICES):
            text,markup=village.screen(state(count),1,42)
            purchases=[b for b in buttons(markup) if b.text.startswith('🏠 Купить')]
            self.assertEqual(len(purchases),1);self.assertIn(str(price),purchases[0].text)
            self.assertIn(f'Дома {count}/5',text)
        _,markup=village.screen(state(5),1,42)
        self.assertFalse(any('Купить' in b.text for b in buttons(markup)))
        self.assertTrue(any('Все дома куплены' in b.text for b in buttons(markup)))

    def test_fifty_residents_paginated_and_last_page_reachable(self):
        residents=[dict(hero_id=n,name=f'Житель {n}',building='mine') for n in range(1,51)]
        for page in range(7):
            text,markup=village.screen(state(5,residents),1,42,'residents',page)
            people=[b for b in buttons(markup) if '.hero.' in b.callback_data or ':hero.' in b.callback_data]
            self.assertEqual(len(people),8 if page<6 else 2)
            self.assertIn(f'Страница {page+1}/7',text)
        self.assertTrue(any('Житель 50' in b.text for b in people))

    def test_worker_transfers_remove_job_and_eviction_buttons(self):
        text,markup=village.screen(state(1,[dict(hero_id=123456789,name='Житель',building='mine')]),999999,9999999999,'hero',hero_id=123456789)
        labels=[b.text for b in buttons(markup)]
        for expected in ('⛏ Шахта','🏪 Рынок','🏹 Охота','Снять с производства','Выселить'):self.assertIn(expected,labels)
        self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons(markup)))
        payloads=[b.callback_data.split(':',4)[4] for b in buttons(markup) if ':x.' in b.callback_data]
        self.assertTrue(all(p.startswith('x.') for p in payloads));self.assertEqual(len(payloads),5)

    def test_resident_without_work_is_in_list_and_cap_food_visible(self):
        s=state(1,[dict(hero_id=1,name='Житель',building=None)]);s['seconds_left']=0;s['fed']=False
        text,_=village.screen(s,1,42);self.assertIn('предел 8 часов',text);self.assertIn('не хватает еды',text);self.assertIn('1.125',text)
        _,markup=village.screen(s,1,42,'residents');self.assertTrue(any('Без работы' in b.text for b in buttons(markup)))

    def test_mythic_empty_catalog_and_large_code_compact_craft_callback(self):
        with patch.object(mythic.service,'list_heroes',return_value=[]):text,_=mythic.screen(1,1,42)
        self.assertIn('пока не добавлены',text)
        hero=dict(id=123456789,code='mythic_'+'long'*100,name='Мифический герой',fragment_cost=5,fragments=6,owned=False)
        with patch.object(mythic.service,'list_heroes',return_value=[hero]):text,markup=mythic.screen(1,999999,9999999999)
        self.assertIn('6/5',text);self.assertTrue(any('craft.123456789' in b.callback_data for b in buttons(markup)))
        self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons(markup)))
        hero['owned']=True
        with patch.object(mythic.service,'list_heroes',return_value=[hero]):_,markup=mythic.screen(1,1,42)
        self.assertFalse(any('craft.' in b.callback_data for b in buttons(markup)))

    def test_duel_defender_change_accept_refuse_and_challenger_cancel(self):
        d=dict(id=1,status='pending',challenger_id=1,defender_id=2,challenger_snapshot='{"name":"Инициатор"}',defender_snapshot='{"name":"Защитник"}',defender_hero_id=3,expires_at=1120)
        with patch.object(duels.service,'get_state',return_value=d),patch.object(duels.service,'timestamp',return_value=1000):
            text,markup=duels.screen({'id':1},{'id':2},42)
            self.assertIn('120 сек.',text)
            self.assertEqual([b.text for b in buttons(markup)[:-1]],['Выбрать / сменить героя','Согласен','Отказ'])
            self.assertIn('accept.1.3',buttons(markup)[1].callback_data)
            _,markup=duels.screen({'id':1},{'id':1},41)
            self.assertEqual([b.text for b in buttons(markup)[:-1]],['Отменить вызов'])

    def test_events_and_v1_4_features_remain_registered_and_visible(self):
        names={child.name for child in router.sub_routers}
        self.assertTrue({'mini_village','mini_duels','mini_mythic'}<=names)
        from app.handlers import ROUTERS, mini_events_router
        self.assertIn(mini_events_router,ROUTERS)
        self.assertEqual(mini_events_router.name,'mini_events')
        _,markup=submenu('fair',1,42);self.assertTrue(any('деревня' in b.text.lower() for b in buttons(markup)))
        self.assertEqual([b.text for b in buttons(markup)[:-1]],['🛒 Магазин','🎪 События','🏘 Моя деревня'])
        self.assertEqual(buttons(markup)[1].callback_data,'mini:events:1:42')


class NewCallbackTests(unittest.IsolatedAsyncioTestCase):
    def callback(self,data):return SimpleNamespace(data=data,from_user=SimpleNamespace(id=42,username='tester'),answer=AsyncMock())

    async def test_all_new_screens_use_personal_ephemeral_transport(self):
        world={'id':1};player={'id':1}
        for module in (village,duels,mythic):
            cb=self.callback('unused')
            with patch.object(module,'load_personal_context',AsyncMock(return_value=(world,player))),patch.object(module,'screen',return_value=('Экран',None)),patch.object(module,'send_private_text_from_callback',AsyncMock()) as send:
                if module==village:
                    with patch.object(module.service,'get_state',return_value=state()):await module.home(cb)
                else:await module.home(cb)
                send.assert_awaited_once_with(cb,world,'Экран',None)

    async def test_foreign_callback_rejected_before_any_economic_action(self):
        world={'id':1,'enabled':True}
        for prefix in ('vlg','duel','myth'):
            cb=self.callback(f'mini:{prefix}:1:999:x')
            with patch('app.mini.ui.context.get_mini_world_by_id',return_value=world) as lookup:
                self.assertIsNone(await load_extended_context(cb,prefix));lookup.assert_not_called()
            cb.answer.assert_awaited_once();self.assertTrue(cb.answer.await_args.kwargs['show_alert'])

    async def test_daily_visual_long_streak_cycle_one(self):
        cb=self.callback('mini:daily:1:42')
        result=dict(claimed=True,coins_earned=10,rarity='common',streak=22,cycle_day=1,story_text='История',balance=10,chest_code=None)
        with patch.object(activities,'_load_personal_context',AsyncMock(return_value=({'id':1,'currency_name':'монет'},{'id':1,'character_name':'Герой'}))),patch.object(activities,'claim_daily',return_value=result),patch.object(activities,'_edit_private',AsyncMock()) as edit:
            await activities.daily_callback(cb)
        text=edit.await_args.args[1]
        for phrase in ('Серия: 22 дня','День цикла: 1/7','Бонус: +0%'):self.assertIn(phrase,text)
