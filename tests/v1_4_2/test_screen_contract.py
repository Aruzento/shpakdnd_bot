import importlib
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from tests.v1_4_2.ui_screens import render_screen
from app.mini.heroes import get_player_heroes
from app.mini.shop import sync_shop_catalog,get_player_goods
from app.mini.items import grant_item_in_transaction
from app.mini.db import connect_mini_db

ROOT=Path(__file__).resolve().parents[2]
DESIGN=json.loads((ROOT/'docs/ui/v1.4.2/DND_Mini_UI_Map_UPDATED_2026-10-09(1).json').read_text(encoding='utf-8'))
LINE=json.loads((ROOT/'docs/ui/v1.4.2/DND_Mini_UI_Map_line1_btn_UPDATED_2026-10-09(1).json').read_text(encoding='utf-8'))


def handler(data):
    parts=data.split(':');prefix,action=parts[:2]
    if prefix=='miniboss':
        if action in ('hero','heroes'):return 'app.mini.boss.selection:boss_select_hero_callback' if action=='hero' else 'app.mini.boss.selection:boss_heroes_callback'
        module='app.mini.boss.handlers' if action in ('hit','forcefinish','start') else 'app.mini.boss.registration'
        names={'join':'boss_join_callback','leave':'boss_leave_callback','list':'boss_list_callback','create':'boss_create_callback','preview':'boss_preview_callback','announce':'boss_announce_callback','close':'boss_close_callback','start':'boss_start_callback','reopen':'boss_reopen_callback','republish':'boss_republish_callback','cancel':'boss_cancel_callback','forcefinish':'boss_force_finish_callback','hit':'boss_hit_callback'}
        return module+':'+names[action]
    names={
        'pvp':('events.pvp_handlers','action'),'guide':('ui.activities','combat_guide_callback'),'hevict':('ui.heroes','hero_evict_callback'),
        'launch':('handlers','launch_callback'),'create_help':('handlers','create_help_callback'),'home':('handlers','home_callback'),'character':('handlers','character_callback'),'charup':('ui.heroes','hero_upgrade_callback'),
        'collection':('ui.heroes','collection_callback'),'collectionopen':('ui.heroes','collection_open_callback'),'gacha':('ui.heroes','gacha_callback'),'gachapull':('ui.heroes','gacha_pull_callback'),'gacharepeat':('ui.heroes','gacha_repeat_callback'),
        'gachabatch':('ui.summons','batch_callback'),'batchview':('ui.summons','batch_result_callback'),'batchpage':('ui.summons','batch_result_callback'),'batchhero':('ui.summons','batch_result_callback'),'gachacoupon':('ui.summons','coupon_callback'),'couponpick':('ui.summons','coupon_callback'),'collectionback':('ui.heroes','collection_back_callback'),
        'hero':('ui.heroes','hero_card_callback'),'heroupgrade':('ui.heroes','hero_upgrade_callback'),'heroactive':('ui.heroes','hero_active_callback'),'heroshare':('ui.heroes','hero_share_callback'),'heroshards':('ui.heroes','hero_shards_callback'),'shardsell':('ui.heroes','shard_sell_callback'),'favorite':('ui.heroes','favorite_callback'),
        'select':('ui.hero_selector','selector_callback'),'daily':('ui.activities','daily_callback'),'rules':('ui.activities','rules_callback'),'rulespage':('ui.activities','rules_section_callback'),
        'inventory':('ui.inventory','inventory_callback'),'goods':('ui.inventory','goods_callback'),'useitems':('ui.inventory','use_items_callback'),'usepick':('ui.inventory','use_item_pick_callback'),'useconfirm':('ui.inventory','use_item_confirm_callback'),
        'shop':('ui.shop','shop_callback'),'shopcat':('ui.shop','shop_category_callback'),'shopitem':('ui.shop','shop_item_callback'),'shopbuy':('ui.shop','shop_buy_callback'),
        'equipment':('equipment.handlers','equipment_callback'),'tower':('tower.handlers','tower_callback'),'village':('village.handlers','home'),'vlg':('village.handlers','action'),
        'mythic':('mythic.handlers','home'),'myth':('mythic.handlers','action'),'duels':('duels.handlers','home'),'duel':('duels.handlers','action'),'events':('events.handlers','events_callback'),'ev':('events.handlers','event_game_callback'),'boss':('boss.registration','boss_home_callback')}
    if action in ('adventures','heroarea','fair','more'):return 'app.mini.handlers:submenu_callback'
    module,function=names[action];return 'app.mini.'+module+':'+function


class ScreenCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=MiniCase();cls.fixture.setUp()
        f=cls.fixture;sync_shop_catalog(f.world['id'],f.db)
        codes=[r['code'] for r in f.sql('SELECT code FROM mini_heroes ORDER BY id LIMIT 12')]
        for code in codes:f.hero(code)
        with connect_mini_db(f.db) as conn:
            conn.execute('BEGIN IMMEDIATE')
            for code in ('summon_ticket','rarity_coupon_uncommon','rarity_coupon_rare','rarity_coupon_legendary'):
                grant_item_in_transaction(conn,f.pid,code,2)
        cls.heroes=get_player_heroes(f.pid,f.db,sync_catalog=False)
        cls.goods=get_player_goods(f.pid,f.db)
        cls.snapshots={}
    @classmethod
    def tearDownClass(cls):
        out=os.environ.get('V142_UI_AUDIT_OUTPUT')
        if out:Path(out).write_text(json.dumps(cls.snapshots,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        cls.fixture.tearDown();cls.fixture.doCleanups()
    def verify(self,sid):
        f=self.fixture
        with patch('secrets.token_hex',side_effect=lambda n:'a'*(2*n)):
            markup,binding=render_screen(sid,f.world,f.player,self.heroes,self.goods,f.db)
        self.assertIsNotNone(markup)
        module,function=binding.split(':');self.assertTrue(callable(getattr(importlib.import_module(module),function)))
        callbacks=[]
        for row in markup.inline_keyboard:
            self.assertTrue(row)
            for button in row:
                self.assertTrue(button.text)
                if button.callback_data:
                    self.assertLessEqual(len(button.callback_data.encode()),64)
                    target=handler(button.callback_data);module,function=target.split(':')
                    self.assertTrue(callable(getattr(importlib.import_module(module),function)),target)
                    callbacks.append({'text':button.text,'callback':button.callback_data,'handler':target})
        self.snapshots[sid]={'renderer':binding,'rows':[[b.text for b in row] for row in markup.inline_keyboard],'transitions':callbacks}
        expected={'home':[2,1,2],'adventures':[2,2,1],'heroarea':[2,2,1],'more':[1,1,1,1],'collection':[1,2,2],'collection_all':[2,2,3,2],'collection_results':[2,2,3,2],'collection_filter':[3,3,1,2,2],'inventory':[2,3],'village':[2,3,2],'village_resident':[1,1,1],'village_mine':[2,1],'village_market':[2,1],'village_hunt':[2,1],'tower_ready':[2,1,2],'tower_active':[1,1,2],'tower_lost':[2,1,2],'character':[1,2],'character_no_hero':[2],'gacha':[2,2,3]}
        if sid in expected:self.assertEqual([len(r) for r in markup.inline_keyboard],expected[sid])
        if sid in ('collection_all','collection_results'):
            self.assertEqual(sum(':hero:' in b.callback_data for row in markup.inline_keyboard for b in row),4)
        if sid=='gacha_result':
            # Active first hero has no set-active action; share stays available.
            self.assertEqual([len(r) for r in markup.inline_keyboard],[2,2,3])


for screen in DESIGN['msg']:
    def test(self,sid=screen['id']):self.verify(sid)
    setattr(ScreenCoverageTests,'test_screen_'+screen['id'],test)


class DesignContractTests(unittest.TestCase):
    def test_all_109_ids_unique_and_two_editor_formats_identical(self):
        self.assertEqual(len(DESIGN['msg']),109)
        self.assertEqual(len({s['id'] for s in DESIGN['msg']}),109)
        for first,second in zip(DESIGN['msg'],LINE['msg']):
            second=dict(second)
            rows=[second.pop(key) for key in sorted([k for k in second if re.fullmatch(r'line\d+_btn',k)],key=lambda k:int(k[4:-4]))]
            self.assertEqual(first,dict(second,buttons=rows))
    def test_every_design_link_resolves_or_documented_safe_fallback(self):
        ids={s['id'] for s in DESIGN['msg']}
        for screen in DESIGN['msg']:
            for row in screen['buttons']:
                for b in row:
                    target=b.get('next_msg_id')
                    self.assertTrue(not target or target in ids or target in ('ui_back','nav_back'),(screen['id'],target))
    def test_original_shadow_catalogs_and_balance_are_untouched_in_followup(self):
        import subprocess
        result=subprocess.run(['git','-c',f'safe.directory={ROOT.as_posix()}','diff','293693d87692bb9e7f4567bcca895e799e7455d2','--','app/mini/content/heroes/shadow.json','app/mini/boss/content/bosses.json','app/mini/content/hero_images','docs/balance'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,'')
