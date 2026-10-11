"""Runtime fixtures for every design screen, shared by tests and the audit report.

All keyboard rows come from actual builders; inline-only legacy screens are
exercised by the corresponding handler tests and their identical small builder.
No actions/visible_if expressions from the design documents are evaluated.
"""
import json
from unittest.mock import patch
from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as K
from app.mini import handlers as home
from app.mini.ui import heroes as hu,hero_selector as sel,hero_cards as card,inventory as inv,shop as shop,activities as act,gacha_extra as extra
from app.mini.tower import handlers as tower,selection
from app.mini.tower.catalog import get_floor
from app.mini.village import handlers as village
from app.mini.duels import handlers as duel
from app.mini.events import handlers as events
from app.mini.boss import presentation as boss,public
from app.mini.equipment.handlers import render_equipment
from app.mini.mythic import handlers as mythic
from app.mini.db import connect_mini_db
from tests.v1_4.test_ui import state as village_state


def render_screen(sid,w,p,heroes,goods,db):
    wid,u=w['id'],p['telegram_user_id'];hero=heroes[0]
    g={'pull_price':10,'tickets':2,'coupons':3,'pity':{},'coins':1000}
    def simple(label,data):return K(inline_keyboard=[[B(text=label,callback_data=data)]])
    if sid=='launcher':return home._launcher_menu(wid),'app.mini.handlers:_launcher_menu'
    if sid=='registration':return home._create_menu(wid,u),'app.mini.handlers:_create_menu'
    if sid=='home':return home._player_menu(wid,u),'app.mini.handlers:_player_menu'
    if sid in home.SUBMENUS:return home.submenu(sid,wid,u)[1],'app.mini.handlers:submenu'
    if sid in ('character','character_no_hero'):return home.character_menu(wid,u,hero if sid=='character' else None),'app.mini.handlers:character_menu'
    if sid=='gacha':return hu._gacha_menu(wid,u,g),'app.mini.ui.heroes:_gacha_menu'
    if sid=='gacha_result':return card.hero_card_menu(wid,u,hero,g,allow_share=True),'app.mini.ui.hero_cards:hero_card_menu'
    if sid=='gacha_batch_result':
        batch={'batch_id':1,'cost_coins':100,'balance':900,'shards_awarded':10,'pity':{},'results':[dict(hero,is_duplicate=False) for _ in range(10)]}
        return extra.batch_menu(batch,wid,u)[1],'app.mini.ui.gacha_extra:batch_menu'
    if sid=='gacha_coupon':return extra.coupon_menu(goods,wid,u)[1],'app.mini.ui.gacha_extra:coupon_menu'
    if sid.startswith('collection'):
        view={'collection':'home','collection_favorites':'favorites','collection_all':'all','collection_filter':'filter','collection_results':'results'}.get(sid)
        if view is None:view='f'+str(sel.FIELDS.index(sid.removeprefix('collection_filter_option_')))
        return sel.render_selector(heroes,[hero['id']],'c',wid,u,view=view)[1],'app.mini.ui.hero_selector:render_selector'
    if sid=='hero_card':return card.hero_card_menu(wid,u,dict(hero,shards=15,origin='~a0~0000000'),g,collection=True),'app.mini.ui.hero_cards:hero_card_menu'
    if sid=='shards_sell':return hu._shard_sell_menu(wid,u,dict(hero,shards=15)),'app.mini.ui.heroes:_shard_sell_menu'
    if sid=='mythic':
        with patch.object(mythic.service,'list_heroes',return_value=[]):return mythic.screen(p['id'],wid,u)[1],'app.mini.mythic.handlers:screen'
    if sid=='daily_result':return act._daily_result_menu(wid,u),'app.mini.ui.activities:_daily_result_menu'
    if sid=='inventory':return inv._inventory_menu(wid,u,goods),'app.mini.ui.inventory:_inventory_menu'
    if sid=='useitems':return inv._use_items_menu(wid,u,goods),'app.mini.ui.inventory:_use_items_menu'
    if sid.startswith('usepick_'):return inv._use_item_confirm_menu(wid,u,'item' if sid.endswith('item') else 'cert',1),'app.mini.ui.inventory:_use_item_confirm_menu'
    if sid in ('item_use_result','certificate_result'):return inv._item_use_result_menu(wid,u),'app.mini.ui.inventory:_item_use_result_menu'
    if sid=='shop':
        categories=[{'code':c,'title':label,'product_count':1} for c,label in [('summons','🎴 Призывы'),('mini','⚔️ Для Mini'),('dnd','🎲 Для D&D'),('fun','🎉 Приколы')]]
        with patch.object(shop,'get_shop_categories',return_value=categories):return shop._shop_main_menu(wid,u),'app.mini.ui.shop:_shop_main_menu'
    if sid=='shop_category':
        with patch.object(shop,'get_offers_by_category',return_value=[{'id':1,'title':'Фонарь духов','price':2500}]):return shop._shop_category_menu(wid,u,'mini'),'app.mini.ui.shop:_shop_category_menu'
    if sid=='shop_item':return shop._shop_item_menu(wid,u,1,2500),'app.mini.ui.shop:_shop_item_menu'
    if sid=='shop_purchase':return shop._shop_after_purchase_menu(wid,u),'app.mini.ui.shop:_shop_after_purchase_menu'
    if sid.startswith('equipment'):
        eq={'owned':[],'equipped':{}}
        mode=sid.removeprefix('equipment_') if sid!='equipment' else 'home'
        return render_equipment(eq,wid,u,mode=mode)[1],'app.mini.equipment.handlers:render_equipment'
    if sid.startswith(('tower_selector','boss_selector')) or sid=='boss_hero_select':
        mode='t' if sid.startswith('tower') else 'b'
        view='home' if sid=='boss_hero_select' else sid.rsplit('_',1)[1]
        if view=='option':view='f0'
        return sel.render_selector(heroes,[hero['id']],mode,wid,u,1,view=view)[1],'app.mini.ui.hero_selector:render_selector'
    if sid.startswith('tower'):
        state={'floor':get_floor(1),'attempt':None,'selected_hero':hero,'equipment_bonus':0,'completed':False}
        if sid in ('tower_select','tower_heroes_by_rarity','tower_hero_card'):
            rarity=selection.RARITIES.index(hero['rarity']) if sid!='tower_select' else None
            return selection.render(state,heroes,wid,u,rarity=rarity,hero_id=hero['id'] if sid=='tower_hero_card' else None)[1],'app.mini.tower.selection:render'
        if sid in ('tower_active','tower_lost','tower_won'):
            state['attempt']={'id':1,'floor':1,'status':sid.removeprefix('tower_'),'hero_json':json.dumps(hero),'enemy_json':json.dumps(state['floor']),'equipment_bonus':0,'current_hp':10,'shields':3,'turn':1,'events_json':'[]'}
        if sid=='tower_completed':state.update(completed=True,floor=None)
        return tower.render_state(state,wid,u)[1],'app.mini.tower.handlers:render_state'
    if sid.startswith('boss'):
        b={'id':1,'status':'announced','current_round':1,'current_turn_position':1}
        if sid=='boss_none':return boss.no_boss_menu(wid,u,is_admin=True),'app.mini.boss.presentation:no_boss_menu'
        if sid=='boss':return boss.boss_private_menu(wid,u,b,joined=True,is_admin=True),'app.mini.boss.presentation:boss_private_menu'
        if sid=='boss_create':return boss.template_menu(wid,u),'app.mini.boss.presentation:template_menu'
        if sid=='boss_preview':return boss.preview_menu(wid,u,'Vov4ik'),'app.mini.boss.presentation:preview_menu'
        if sid=='boss_participants':return boss.participants_menu(wid,u,1),'app.mini.boss.presentation:participants_menu'
        if sid=='boss_public_announcement':return public.public_boss_menu(wid,b),'app.mini.boss.public:public_boss_menu'
        if sid=='boss_public_turn':return public.public_turn_menu(wid,dict(b,status='fighting')),'app.mini.boss.public:public_turn_menu'
    if sid.startswith('events'):
        view=sid.removeprefix('events_') if sid!='events' else 'events'
        if view in ('events','rps','rps_bets','lab'):return events._screen(wid,u,p,'bets' if view=='rps_bets' else view)[1],'app.mini.events.handlers:_screen'
        session={'id':1,'stake':1,'step':0,'game_type':'rps' if view.startswith('rps') else 'lab','status':'active' if view in ('rps_choice','lab_active') else 'finished','payload':{'outcome':'loss' if view=='lab_loss' else 'win','choice':'saint','bot_choice':'demon'}}
        return events._session_screen(wid,u,p,session)[1],'app.mini.events.handlers:_session_screen'
    if sid.startswith(('duels','duel_')):
        if sid=='duel_public_challenge':return duel.public_challenge_menu(wid,u),'app.mini.duels.handlers:public_challenge_menu'
        state=None
        if sid.startswith('duels_pending'):
            state={'id':1,'status':'pending','challenger_snapshot':json.dumps({'name':'A'}),'defender_snapshot':json.dumps({'name':'B'}),'expires_at':9999999999,'defender_id':p['id'] if sid.endswith('defender') else p['id']+1,'defender_hero_id':hero['id']}
        view={'duel_select_hero':'heroes','duel_pick_defender_hero':'pick','duel_select_rival':'rivals'}.get(sid,'home')
        with patch.object(duel.service,'get_state',return_value=state),patch.object(duel,'get_player_heroes',return_value=heroes),patch.object(duel,'filter_heroes',side_effect=lambda hs,pid:hs),patch.object(duel,'connect_mini_db',side_effect=lambda:connect_mini_db(db)):
            return duel.screen(w,p,u,view)[1],'app.mini.duels.handlers:screen'
    if sid.startswith('village'):
        residents=[{'hero_id':h['id'],'name':h['name'],'building':None if n==0 else 'mine' if n==1 else 'market' if n==2 else 'hunt','stars':h.get('stars',0)} for n,h in enumerate(heroes[:4])]
        v=village_state(1,residents)
        view={'village':'home','village_resident':'hero','village_houses':'houses','village_residents':'residents','village_add':'add','village_evict':'evict'}.get(sid)
        if view is None:
            view=sid.removeprefix('village_')
            if view.endswith('_add'):view='add_'+view[:-4]
            if view.endswith('_remove'):view='workers_'+view[:-7]
        with patch.object(village,'get_player_heroes',return_value=heroes):return village.screen(v,wid,u,view,hero_id=hero['id'])[1],'app.mini.village.handlers:screen'
    if sid=='rules' or sid.startswith('rules_'):return act.rules_menu(wid,u,section=sid!='rules'),'app.mini.ui.activities:rules_menu'
    raise AssertionError('Unbound screen: '+sid)
