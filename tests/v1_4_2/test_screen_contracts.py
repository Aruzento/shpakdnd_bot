"""109 source screens rendered by real Python builders and matched to router filters.

These are synthetic UI contracts, not evidence of Telegram visual staging.
"""
import asyncio
import copy
import importlib
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.handlers import ROUTERS
from app.mini import handlers as mini
from app.mini.catalog import load_hero_catalog
from app.mini.hero_upgrades import hero_upgrade_state
from app.mini.ui import heroes,hero_selector,hero_cards,inventory,shop,activities,summons
from app.mini.equipment import handlers as equipment
from app.mini.tower import handlers as tower
from app.mini.tower.selection import render_selection
from app.mini.tower.catalog import get_floor
from app.mini.boss import presentation as boss, public as boss_public
from app.mini.boss.catalog import list_boss_templates
from app.mini.events import handlers as events
from app.mini.duels import handlers as duels
from app.mini.mythic import handlers as mythic
from app.mini.village import handlers as village
from app.mini.rules import SECTIONS
from tests.v1_4.test_ui import state as village_state

ROOT=Path(__file__).resolve().parents[2]
SOURCE=json.loads((ROOT/'docs/ui/v1.4.2-map.json').read_text(encoding='utf-8'))['msg']
WORLD=999999
USER=9999999999


def sample_heroes():
    base=next(h for h in load_hero_catalog()['heroes'] if h['code']=='Villager')
    return [dict(base,id=100000000+i,name=f'Герой {i}',stars=0,copies=1,shards=120,is_active=0) for i in range(1,21)]


def render_screen(sid):
    """A representative live state for each design ID; never invoke economic writes."""
    owned=sample_heroes();hero=owned[0];wid,user=WORLD,USER
    state=dict(pull_price=30,tickets=3,coins=10000,shards=120,owned=20,total=54,
        rarity_chances=dict(common=69,uncommon=25,rare=5,legendary=1),pity=dict(uncommon=6,rare=14,legendary=37),
        coupons=[dict(item_id=i,code='rarity_coupon_'+rarity,name=rarity,quantity=3) for i,rarity in enumerate(('uncommon','rare','legendary'),1)])
    world=dict(id=wid,name='Мир',currency_name='монет',chat_id=-1001,thread_id=9)
    player=dict(id=1,coins=10000,shards=120,character_name='Игрок',telegram_user_id=user)
    goods=dict(inventory=[dict(item_id=1,name='Купон',quantity=2,effect_key='rarity_coupon_rare')],certificates=[dict(purchase_id=2,title='Сертификат')],effects=[])
    if sid=='launcher':return mini._launcher_text(world),mini._launcher_menu(wid),mini._launcher_menu
    if sid=='registration':return 'Создай персонажа',mini._create_menu(wid,user),mini._create_menu
    if sid=='home':return mini._home_content(player,hero),mini._player_menu(wid,user),mini._player_menu
    if sid in mini.SUBMENUS:
        text,markup=mini.submenu(sid,wid,user);return text,markup,mini.submenu
    if sid in ('character','character_no_hero'):
        return hero_cards.hero_caption(hero) if sid=='character' else 'Нет героя',hero_cards.profile_menu(wid,user,hero if sid=='character' else None),hero_cards.profile_menu
    if sid=='gacha':return heroes._format_gacha(world,state),heroes._gacha_menu(wid,user,state),heroes._gacha_menu
    if sid=='gacha_batch_result':
        result=dict(hero,is_duplicate=True,shards_awarded=1)
        batch=dict(batch_id=11,results=[dict(result,name=f'Герой {i}') for i in range(10)],cost_coins=300,balance=9700,shards_awarded=10,pity=state['pity'])
        text,markup=summons.batch_screen(batch,wid,user,5);return text,markup,summons.batch_screen
    if sid=='gacha_coupon':
        text,markup=summons.coupon_screen(state,wid,user,2);return text,markup,summons.coupon_screen
    if sid=='gacha_result':return hero_cards.hero_caption(hero),hero_cards.hero_card_menu(wid,user,hero,state,allow_share=True),hero_cards.hero_card_menu
    if sid.startswith('collection'):
        view={'collection':'collection','collection_favorites':'home','collection_all':'all','collection_filter':'filter','collection_results':'results'}.get(sid)
        if not view:view='f'+str(hero_selector.FIELDS.index(sid.removeprefix('collection_filter_option_')))
        text,markup=hero_selector.render_selector(owned,[h['id'] for h in owned[:3]],'c',wid,user,view=view,page=1,return_token='aabbccdd')
        return text,markup,hero_selector.render_selector
    if sid=='hero_card':return hero_cards.hero_caption(hero,full=True),hero_cards.hero_card_menu(wid,user,hero,state,collection=True,nav_token='aabbccdd'),hero_cards.hero_card_menu
    if sid=='shards_sell':return heroes._format_shard_sell(hero,10000),heroes._shard_sell_menu(wid,user,hero,nav_token='aabbccdd'),heroes._shard_sell_menu
    if sid=='mythic':
        with patch.object(mythic.pets,'state',return_value={'pets':[],'mythic_shards':5}):text,markup=mythic.screen(1,wid,user)
        return text,markup,mythic.screen
    if sid=='daily_result':return 'Результат ежедневного боя',activities._daily_result_menu(wid,user),activities._daily_result_menu
    if sid=='inventory':return inventory._format_inventory(world,player,goods),inventory._inventory_menu(wid,user,goods),inventory._inventory_menu
    if sid=='useitems':return 'Использование предметов',inventory._use_items_menu(wid,user,goods),inventory._use_items_menu
    if sid in ('usepick_item','usepick_certificate'):return 'Подтверждение',inventory._use_item_confirm_menu(wid,user,'item' if sid=='usepick_item' else 'cert',1),inventory._use_item_confirm_menu
    if sid in ('item_use_result','certificate_result'):return 'Результат',inventory._item_use_result_menu(wid,user),inventory._item_use_result_menu
    if sid=='shop':
        with patch.object(shop,'get_shop_categories',return_value=[dict(code=key,title=title,product_count=n) for key,title,n in [('summons','🎴 Призывы',4),('mini','⚔️ Для Mini',2),('dnd','🎲 Для D&D',2),('fun','🎉 Приколы',2)]]):markup=shop._shop_main_menu(wid,user)
        return 'Магазин',markup,shop._shop_main_menu
    if sid=='shop_category':
        with patch.object(shop,'get_offers_by_category',return_value=[dict(title='Купон',price=500,id=1)]):markup=shop._shop_category_menu(wid,user,'summons')
        return 'Призывы',markup,shop._shop_category_menu
    if sid=='shop_item':return 'Купон',shop._shop_item_menu(wid,user,1,500),shop._shop_item_menu
    if sid=='shop_purchase':return 'Покупка',shop._shop_after_purchase_menu(wid,user),shop._shop_after_purchase_menu
    if sid.startswith('equipment'):
        mode=sid.removeprefix('equipment_') if sid!='equipment' else 'home'
        equip=[dict(code=f'eq_helmet_{i:03}',slot='helmet',name=f'Шлем {i}',attack_bonus=i,quantity=1) for i in range(1,20)]
        text,markup=equipment.render_equipment(dict(owned=equip,equipped={'helmet':equip[0]}),wid,user,1,mode)
        return text,markup,equipment.render_equipment
    if sid.startswith('tower_selector') or sid.startswith('boss_selector') or sid=='boss_hero_select':
        mode='t' if sid.startswith('tower') else 'b'
        view={'all':'all','filter':'filter','option':'f0','results':'results','select':'home'}[sid.rsplit('_',1)[-1]]
        text,markup=hero_selector.render_selector(owned,[h['id'] for h in owned[:3]],mode,wid,user,77,view=view,page=1)
        return text,markup,hero_selector.render_selector
    if sid in ('tower_select','tower_heroes_by_rarity','tower_hero_card'):
        state=dict(completed=False,attempt=None,floor=get_floor(1),selected_hero=None)
        stage={'tower_select':'rarity','tower_heroes_by_rarity':'list','tower_hero_card':'card'}[sid]
        text,markup,_=render_selection(state,owned,wid,user,stage=stage,rarity=0,page=1,hero_id=owned[8]['id'])
        return text,markup,render_selection
    if sid.startswith('tower'):
        floor=get_floor(1);state=dict(completed=False,floor=floor,attempt=None,last_attempt=None,equipment_bonus=0,selected_hero=None)
        if sid=='tower_ready':state['selected_hero']=hero
        if sid in ('tower_active','tower_lost','tower_won'):
            state['attempt']=dict(id=77,status={'tower_active':'active','tower_lost':'lost','tower_won':'won'}[sid],
                floor=1,enemy_json=json.dumps(floor),hero_json=json.dumps(hero),equipment_bonus=0,current_hp=10,shields=3,events_json='[]',turn=1)
        if sid=='tower_completed':state.update(completed=True,floor=None)
        text,markup=tower.render_state(state,wid,user);return text,markup,tower.render_state
    if sid=='boss_none':return 'Нет босса',boss.no_boss_menu(wid,user,is_admin=True),boss.no_boss_menu
    template=list_boss_templates()[0];boss_state=dict(template,id=77,status='announced',current_hp=template['max_hp'],current_round=1,current_turn_position=1,reward_items_json='[]')
    if sid=='boss':return boss.format_private_boss(boss_state,[],joined=True),boss.boss_private_menu(wid,user,boss_state,joined=True,is_admin=True),boss.boss_private_menu
    if sid=='boss_create':return 'Объявить',boss.template_menu(wid,user),boss.template_menu
    if sid=='boss_preview':return 'Босс',boss.preview_menu(wid,user,template['code']),boss.preview_menu
    if sid=='boss_participants':
        from app.mini.ui.navigation import back_menu
        return boss_public.format_participants([]),back_menu(wid,user,'boss'),back_menu
    if sid=='boss_public_announcement':return boss_public.format_public_boss(boss_state,[]),boss_public.public_boss_menu(wid,boss_state),boss_public.public_boss_menu
    if sid=='boss_public_turn':
        boss_state['status']='fighting';return 'Ход',boss_public.public_turn_menu(wid,boss_state),boss_public.public_turn_menu
    if sid.startswith('events'):
        screen={'events':'events','events_rps':'rps','events_rps_bets':'bets','events_lab':'lab'}.get(sid)
        if screen:text,markup=events._screen(wid,user,player,screen);return text,markup,events._screen
        session=dict(id=77,stake=5,step=1,status='active' if sid in ('events_rps_choice','events_lab_active') else 'resolved',
            game_type='rps' if sid.startswith('events_rps') else 'labyrinth',payload=dict(choice='saint',bot_choice='demon',outcome='loss' if sid=='events_lab_loss' else 'win'))
        text,markup=events._session_screen(wid,user,player,session);return text,markup,events._session_screen
    if sid=='duel_public_challenge':return duels.public_challenge_text(player,{'username':'rival'},77),duels.public_challenge_menu(wid,user),duels.public_challenge_menu
    if sid.startswith('duel'):
        view={'duel_select_hero':'heroes','duel_pick_defender_hero':'pick','duel_select_rival':'rivals'}.get(sid,'home')
        pending=dict(id=77,status='pending',challenger_id=1,defender_id=2,defender_hero_id=hero['id'],challenger_snapshot='{"name":"А"}',defender_snapshot='{"name":"Б"}',expires_at=1200)
        if sid=='duels_pending_defender':player=dict(player,id=2)
        with ExitStack() as stack:
            stack.enter_context(patch.object(duels.service,'get_state',return_value=pending if 'pending' in sid else None))
            stack.enter_context(patch.object(duels.service,'timestamp',return_value=1000))
            stack.enter_context(patch.object(duels,'get_player_heroes',return_value=owned))
            stack.enter_context(patch.object(duels,'filter_heroes',side_effect=lambda h,p:h))
            conn=SimpleNamespace(row_factory=None,execute=lambda *args:[dict(id=i,character_name=f'Соперник {i}',username='@rival') for i in range(1,20)])
            from unittest.mock import MagicMock
            connection=MagicMock();connection.__enter__.return_value=conn
            stack.enter_context(patch.object(duels,'connect_mini_db',return_value=connection))
            text,markup=duels.screen(world,player,user,view,value=77,page=1)
        return text,markup,duels.screen
    if sid.startswith('village'):
        view=sid.removeprefix('village_') if sid!='village' else 'home'
        if view.endswith('_remove'):view='workers_'+view.removesuffix('_remove')
        elif view.endswith('_add'):view='add_'+view.removesuffix('_add')
        if view=='resident':view='hero'
        residents=[dict(hero_id=h['id'],name=h['name'],building=None if n<10 else ('mine','market','hunt')[n%3]) for n,h in enumerate(owned)]
        state=village_state(3,residents);state['unsettled_count']=10
        with patch.object(village,'get_player_heroes',return_value=[dict(h,id=h['id']+100) for h in owned]):
            text,markup=village.screen(state,wid,user,view,page=1,hero_id=owned[0]['id'])
        return text,markup,village.screen
    if sid=='rules':return 'Правила',activities.rules_menu(wid,user),activities.rules_menu
    if sid.startswith('rules_'):return SECTIONS[sid.removeprefix('rules_')][1],activities.rules_menu(wid,user,section=True),activities.rules_menu
    raise AssertionError('Missing screen implementation: '+sid)


def registered_handlers():
    seen=set();result=[]
    def visit(router):
        if id(router) in seen:return
        seen.add(id(router));result.extend(router.callback_query.handlers)
        for child in router.sub_routers:visit(child)
    for router in ROUTERS:visit(router)
    return result


async def button_handlers(markup):
    result=[]
    for row in markup.inline_keyboard:
        actions=[]
        for b in row:
            cb=SimpleNamespace(data=b.callback_data,from_user=SimpleNamespace(id=USER),message=None)
            matches=[]
            for handler in registered_handlers():
                if (await handler.check(cb))[0]:matches.append(handler.callback.__module__+'.'+handler.callback.__name__)
            if not matches:raise AssertionError('Unregistered button '+str(b.callback_data))
            actions.append(matches)
        result.append(actions)
    return result


class ScreenContractTests(unittest.TestCase):
    pass


def contract_test(sid):
    def test(self):
        with patch('secrets.token_hex',return_value='11223344'):
            text,markup,_=render_screen(sid)
        self.assertTrue(text);self.assertTrue(markup.inline_keyboard)
        for row in markup.inline_keyboard:
            self.assertTrue(row)
            for b in row:
                self.assertTrue(b.text);self.assertLessEqual(len(b.callback_data.encode()),64)
                if b.text in ('🏠 Главная','⬅️ На главную'):
                    self.assertEqual(b.callback_data,f'mini:home:{WORLD}:{USER}')
                if b.callback_data.startswith('mini:') and not b.callback_data.startswith('mini:launch:'):
                    self.assertEqual(b.callback_data.split(':')[2:4],[str(WORLD),str(USER)])
        asyncio.run(button_handlers(markup))
    return test


for screen in SOURCE:
    setattr(ScreenContractTests,'test_screen_'+screen['id'],contract_test(screen['id']))


class MapContractTests(unittest.TestCase):
    def test_all_109_ids_unique_and_editorial_formats_have_identical_buttons(self):
        editor=json.loads((ROOT/'docs/ui/v1.4.2-editor-map.json').read_text(encoding='utf-8'))['msg']
        self.assertEqual(len(SOURCE),109);self.assertEqual(len({s['id'] for s in SOURCE}),109)
        self.assertEqual([s['id'] for s in SOURCE],[s['id'] for s in editor])
        ids={s['id'] for s in SOURCE}
        for standard,lines in zip(SOURCE,editor):
            rows=[lines[key] for key in sorted((key for key in lines if re.fullmatch(r'line\d+_btn',key)),key=lambda key:int(key[4:-4]))]
            self.assertEqual(standard['buttons'],rows,standard['id'])
            for row in rows:
                for b in row:self.assertIn(b['next_msg_id'],ids|{'ui_back','nav_back','current','same','previous',''} ,standard['id'])

    def test_final_matrix_covers_each_renderer_handler_action_and_test(self):
        records=json.loads((ROOT/'docs/ui/v1.5-matrix.json').read_text(encoding='utf-8'))['screens']
        self.assertEqual([r['screen'] for r in records],[s['id'] for s in SOURCE])
        for record in records:
            self.assertTrue(record['action']);self.assertTrue(record['renderer'])
            self.assertTrue(hasattr(ScreenContractTests,record['test'].rsplit('.',1)[-1]))
            with patch('secrets.token_hex',return_value='11223344'):
                _,markup,renderer=render_screen(record['screen'])
            self.assertEqual(renderer.__module__+'.'+renderer.__name__,record['renderer'])
            self.assertEqual([[b.text for b in row] for row in markup.inline_keyboard],[[b['text'] for b in row] for row in record['buttons']])
            self.assertEqual(asyncio.run(button_handlers(markup)),[[b['handlers'] for b in row] for row in record['buttons']])

    def test_changed_layout_rows_and_collection_four_vs_boss_six(self):
        expected={'home':[2,1,2],'adventures':[2,2,1],'heroarea':[2,2,1],'fair':[1,1,1,1],
            'more':[1,1,1,1,1],'character':[1,2],'character_no_hero':[2],'gacha':[2,2,3],
            'gacha_batch_result':[1,3,1,3],'gacha_result':[2,2,3],'collection':[1,2,2],
            'collection_favorites':[2,1,2,2],'collection_all':[2,2,3,2],
            'collection_filter':[3,3,1,2,2],'collection_results':[2,2,3,2],
            'inventory':[2,3],'village':[2,3,1,2],'village_houses':[1,2,1,2],
            'village_residents':[2,2,3,2],'village_resident':[1,1,1],
            'village_mine':[1,2,1],'village_market':[1,2,1],'village_hunt':[1,2,1],
            'tower':[2,2],'tower_ready':[2,1,2],'tower_active':[1,1,2],'tower_lost':[2,1,2]}
        for sid,rows in expected.items():
            with self.subTest(screen=sid):self.assertEqual([len(r) for r in render_screen(sid)[1].inline_keyboard],rows)
        for sid,count in [('collection_all',4),('boss_selector_all',6),('tower_selector_all',6)]:
            markup=render_screen(sid)[1]
            self.assertEqual(sum(b.callback_data.startswith('mini:hero:') or b.callback_data.startswith('miniboss:hero:') or ':pick.' in b.callback_data for row in markup.inline_keyboard for b in row),count)

    def test_conditional_buttons_empty_inventory_gacha_max_upgrade_and_houses(self):
        world,user=WORLD,USER;hero=sample_heroes()[0]
        markup=heroes._gacha_menu(world,user,dict(pull_price=30,tickets=0,coupons=[]))
        self.assertEqual([len(r) for r in markup.inline_keyboard],[2,3])
        self.assertEqual([len(r) for r in inventory._inventory_menu(world,user,dict(inventory=[],certificates=[])).inline_keyboard],[1,3])
        hero['stars']=4;hero['upgrade']=hero_upgrade_state(hero)
        self.assertEqual(len(hero_cards.profile_menu(world,user,hero).inline_keyboard),1)
        state=village_state(5);state['unsettled_count']=0
        markup=village.screen(state,world,user,'houses')[1]
        self.assertFalse(any('Купить' in b.text or 'Поселить' in b.text or 'Выселить' in b.text for row in markup.inline_keyboard for b in row))

    def test_pure_new_builders_do_not_open_sqlite_or_execute_map_actions(self):
        with patch('app.mini.db.connect_mini_db',side_effect=AssertionError('render must be pure')):
            for sid in ('home','gacha','gacha_batch_result','gacha_coupon','collection_all','collection_filter','hero_card','tower_select','tower_heroes_by_rarity','tower_hero_card','village_houses','village_residents'):
                render_screen(sid)
