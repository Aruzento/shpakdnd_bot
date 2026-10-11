import json
import sqlite3
from datetime import datetime,timezone
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.boss.schema import init_boss_db
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.service import create_boss_event,register_player,close_registration
from app.mini.boss.combat import start_battle,hit_boss,force_finish_battle
from app.mini.boss.rewards import finish_victory,finish_failure
from app.mini.items import use_inventory_item,grant_item_in_transaction,get_effect_charges
from app.mini.shop import sync_shop_catalog
from app.mini.schema import init_mini_db
from app.mini.db import connect_mini_db
from app.mini.combat.hero_abilities import engine


class LanternTests(MiniCase):
    def setUp(self):
        super().setUp();init_boss_db(self.db);sync_shop_catalog(self.world['id'],self.db)
        self.now=datetime(2026,10,11,12,tzinfo=timezone.utc)
        self.hero()
    def activate(self,key='lantern'):
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');grant_item_in_transaction(conn,self.pid,'spirit_lantern',1)
            item_id=conn.execute("SELECT id FROM mini_items WHERE code='spirit_lantern'").fetchone()[0]
        return use_inventory_item(self.pid,item_id,operation_key=key,db_path=self.db)
    def prepare(self,*,extractable=True,mapped=True):
        template=get_boss_template('Vov4ik')
        template.update(max_hp=3,min_players=1,ability_key='none',ability_text='Без способности',features=[],faction='commoners',shadow_extractable=extractable)
        if not mapped:template['shadow_hero_code']=None
        with patch('app.mini.boss.service.get_boss_template',return_value=template):
            boss=create_boss_event(self.world['id'],'Vov4ik',1,self.db)
        register_player(boss['id'],self.pid,self.db);close_registration(boss['id'],self.db)
        return boss
    def start(self,**kwargs):
        boss=self.prepare(**kwargs);start_battle(boss['id'],now=self.now,db_path=self.db);return boss
    def snapshot(self,boss):return json.loads(self.sql('SELECT hero_snapshot_json FROM mini_boss_participants WHERE boss_id=?',(boss['id'],))[0]['hero_snapshot_json'])
    def shadows(self):return self.sql("SELECT ph.* FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE player_id=? AND h.rarity='shadow'",(self.pid,))
    def finish(self,boss,fn):
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            return fn(conn,conn.execute('SELECT * FROM mini_bosses WHERE id=?',(boss['id'],)).fetchone(),self.now)

    def test_activation_replay_persistence_and_frozen_charge(self):
        first=self.activate();init_mini_db(self.db)
        repeated=use_inventory_item(self.pid,first['item_id'],operation_key='lantern',db_path=self.db)
        self.assertTrue(repeated['repeated']);self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),1)
        boss=self.start();self.assertTrue(self.snapshot(boss)['spirit_lantern'])
        self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),0)
        self.activate('next');self.assertTrue(self.snapshot(boss)['spirit_lantern'])
        self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),1)

    def test_guaranteed_new_shadow_without_arise_and_repeat_duplicate_upgrade(self):
        for n in range(2):
            self.activate(str(n));boss=self.start()
            with patch('app.mini.shadow._roll_success',return_value=False) as roll:
                hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
            roll.assert_not_called();self.assertEqual(self.shadows()[0]['stars'],n)
        self.assertEqual(len(self.sql('SELECT * FROM mini_shadow_rolls')),2)

    def test_arise_and_lantern_one_attempt_even_after_restart(self):
        self.hero('SonDzhi');self.activate();boss=self.start()
        hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        init_mini_db(self.db);self.finish(boss,finish_victory)
        self.assertEqual(len(self.sql('SELECT * FROM mini_shadow_rolls')),1)
        self.assertEqual(self.shadows()[0]['stars'],0)

    def test_disabled_missing_and_invalid_mapping_keep_charge(self):
        self.activate()
        for kwargs in ({'extractable':False},{'mapped':False}):
            boss=self.start(**kwargs);self.assertFalse(self.snapshot(boss)['spirit_lantern'])
            self.finish(boss,finish_failure)
            self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),1)
        boss=self.prepare();self.sql("UPDATE mini_bosses SET shadow_hero_code='missing' WHERE id=?",(boss['id'],))
        start_battle(boss['id'],now=self.now,db_path=self.db)
        self.assertFalse(self.snapshot(boss)['spirit_lantern']);self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),1)

    def test_defeat_no_hit_and_admin_victory_never_extract(self):
        for fn in (finish_failure,finish_victory):
            self.activate(fn.__name__);boss=self.start();self.finish(boss,fn)
            self.assertEqual(self.shadows(),[]);self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),0)
        self.activate('admin');boss=self.start()
        self.sql('UPDATE mini_boss_participants SET hit_count=1 WHERE boss_id=?',(boss['id'],))
        force_finish_battle(boss['id'],now=self.now,db_path=self.db)
        self.assertEqual(self.shadows(),[])

    def test_activation_during_battle_does_not_retroactively_grant(self):
        boss=self.start();self.activate()
        self.assertFalse(self.snapshot(boss)['spirit_lantern'])
        hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        self.assertEqual(self.shadows(),[]);self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),1)

    def test_start_transaction_failure_retains_charge_and_ready_state(self):
        self.activate();boss=self.prepare()
        self.sql("CREATE TRIGGER fail_snapshot BEFORE UPDATE OF hero_snapshot_json ON mini_boss_participants BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):start_battle(boss['id'],now=self.now,db_path=self.db)
        self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),1)
        self.assertEqual(self.sql('SELECT status FROM mini_bosses WHERE id=?',(boss['id'],))[0]['status'],'ready')

    def test_saved_mapping_is_used_even_if_live_catalog_changes(self):
        self.activate();boss=self.start()
        with patch('app.mini.boss.catalog.get_boss_template',return_value=None):
            hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        code=self.sql('SELECT h.code FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE h.rarity=?',('shadow',))[0]['code']
        self.assertEqual(code,'shadow_Vov4ik')

    def test_faction_changes_each_round_once_and_survives_restart(self):
        self.hero('shadow_crimson_vampire');self.activate();boss=self.prepare()
        self.sql('UPDATE mini_bosses SET max_hp=10000,reward_shields=20,reward_shields_max=20 WHERE id=?',(boss['id'],))
        with patch.object(engine,'_roll_success',return_value=True):start_battle(boss['id'],now=self.now,db_path=self.db)
        self.assertEqual(self.snapshot(boss)['faction'],'beasts');self.assertTrue(self.snapshot(boss)['spirit_lantern'])
        init_mini_db(self.db);init_boss_db(self.db)
        self.assertEqual(self.snapshot(boss)['faction'],'beasts')
        with patch.object(engine,'_roll_success',return_value=False):hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        self.assertEqual(self.snapshot(boss)['faction'],'beasts')
        with patch.object(engine,'_roll_success',return_value=True):hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        self.assertEqual(self.snapshot(boss)['faction'],'dark')
        state=json.loads(self.sql('SELECT hero_state_json FROM mini_boss_participants WHERE boss_id=?',(boss['id'],))[0]['hero_state_json'])
        self.assertEqual(state['shadow_round'],3)

    def test_banishment_leaves_treasure_without_lantern_shadow_or_double_reward(self):
        from app.mini.boss.rewards import finish_banishment
        self.hero('shadow_Vov4ik');self.activate();boss=self.start()
        self.sql('UPDATE mini_bosses SET current_hp=10000,max_hp=10000 WHERE id=?',(boss['id'],))
        self.sql('UPDATE mini_boss_participants SET hit_count=4 WHERE boss_id=?',(boss['id'],))
        with patch.object(engine,'_roll_success',return_value=True):result=hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        self.assertTrue(result['battle_ended']);self.assertEqual(result['state']['boss']['battle_result'],'banished')
        self.assertGreater(result['boss_hp_after'],0);self.assertEqual(self.sql('SELECT * FROM mini_shadow_rolls'),[])
        self.assertEqual(get_effect_charges(self.pid,'spirit_lantern',self.db),0)
        before=self.sql('SELECT coins,shards FROM mini_players WHERE id=?',(self.pid,))
        self.finish(boss,finish_banishment);self.assertEqual(self.sql('SELECT coins,shards FROM mini_players WHERE id=?',(self.pid,)),before)


class NewShadowPassiveTests(MiniCase):
    def test_new_shadow_passives_in_tower_keep_round_state_and_damage_contracts(self):
        from tests.v1_3.test_tower import attempt
        from app.mini.tower.combat import resolve_turn
        shape=attempt(hp=1000,faction='dark',passive='shadow_shapeshifter')
        shape.update(resolve_turn(shape,roller=lambda _:True))
        self.assertEqual(json.loads(shape['runtime_json'])['hero']['shadow_faction'],'beasts')
        shape.update(resolve_turn(shape,roller=lambda _:True))
        self.assertEqual(json.loads(shape['runtime_json'])['hero']['shadow_faction'],'dark')
        rapier=resolve_turn(attempt(hp=1000,features=['armored'],passive='shadow_rapier'),roller=lambda _:True)
        self.assertEqual(rapier['damage'],28)
        hydra=resolve_turn(attempt(hp=1000,turn=3,shields=2,passive='shadow_hydra_regeneration'),roller=lambda _:False)
        self.assertEqual(hydra['shields'],2)

    def attack(self,key,n=1,proc=True):
        return engine.resolve_attack(key,base_damage=20,hit_number=n,boss_hp_before=1000,boss_max_hp=2000,roller=lambda _:proc)

    def test_banishment_every_fifth_and_single_extra_attack(self):
        for n in range(1,6):self.assertEqual(self.attack('shadow_banishment',n)['banish_boss'],n==5)
        self.assertFalse(self.attack('shadow_banishment',5,False)['banish_boss'])
        self.assertEqual(self.attack('shadow_critical_strike')['extra_attacks'],1)
        self.assertEqual(self.attack('shadow_critical_strike',proc=False)['extra_attacks'],0)

    def test_pure_rapier_bonus_and_hydra_fourth_hit(self):
        self.assertEqual(self.attack('shadow_rapier')['pure_bonus'],20)
        self.assertEqual(self.attack('shadow_rapier',proc=False)['pure_bonus'],0)
        for n in range(1,5):
            result=engine.resolve_after_attack('shadow_hydra_regeneration',hit_number=n,actual_hp_damage=10,state={})
            self.assertEqual(result['restore_shield'],n==4)

    def test_shadow_faction_round_roll_retains_lantern_and_no_repeat_roll(self):
        hero={'passive_key':'shadow_shapeshifter','faction':'dark','spirit_lantern':True}
        first=engine.resolve_round_start(hero,state={},round_number=1,roller=lambda _:True)
        self.assertEqual(first['hero']['faction'],'beasts');self.assertTrue(first['hero']['spirit_lantern'])
        roller=__import__('unittest').mock.Mock(return_value=True)
        again=engine.resolve_round_start(first['hero'],state=first['state'],round_number=1,roller=roller)
        roller.assert_not_called();self.assertEqual(again['hero'],first['hero']);self.assertEqual(again['state'],first['state'])
        second=engine.resolve_round_start(first['hero'],state=first['state'],round_number=2,roller=roller)
        self.assertEqual(second['hero']['faction'],'dark');roller.assert_called_once_with(50)
