import copy
import json
import sqlite3
from datetime import datetime,timezone
from unittest.mock import patch
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.combat import hit_boss,start_battle
from app.mini.boss.service import create_boss_event,register_player,close_registration,get_boss
from app.mini.boss.rewards import finish_victory,finish_failure
from app.mini.combat.hero_abilities.engine import arise_chance
from app.mini.heroes import sync_hero_catalog
from app.mini.catalog import load_hero_catalog
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from tests.v1_3.support import MiniCase


class ShadowTests(MiniCase):
    def setUp(self):
        super().setUp();self.now=datetime(2026,10,7,10,tzinfo=timezone.utc)
        catalog=load_hero_catalog();hero=copy.deepcopy(catalog['heroes'][0]);hero.update(code='fixture_shadow',name='Тень',rarity='shadow')
        catalog['heroes'].append(hero)
        self.catalog_patch=patch('app.mini.catalog._read_hero_catalog',return_value=catalog);self.catalog_patch.start();self.addCleanup(self.catalog_patch.stop)
        sync_hero_catalog(self.db)
    def battle(self,hero_code='hobgoblin_grave_shaman',*,mapped=True,extractable=True):
        self.hero(hero_code)
        template=get_boss_template('simple_village_guy');template.update(max_hp=3,min_players=1,shadow_extractable=extractable)
        if mapped:template['shadow_hero_code']='fixture_shadow'
        with patch('app.mini.boss.service.get_boss_template',return_value=template):boss=create_boss_event(self.world['id'],'simple_village_guy',1,self.db)
        register_player(boss['id'],self.pid,self.db);close_registration(boss['id'],self.db);start_battle(boss['id'],now=self.now,db_path=self.db)
        return boss
    def victory(self,boss,chance_result=True):
        with patch('app.mini.shadow._roll_success',return_value=chance_result) as roll:
            result=hit_boss(boss['id'],self.pid,now=self.now,db_path=self.db)
        return result,roll
    def shadow(self):return self.sql("SELECT ph.* FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND h.rarity='shadow'",(self.pid,))

    def test_canonical_arise_chances_not_universal_summoner(self):
        for code,chance in (('hobgoblin_grave_shaman',1),('drow_shadow_necromancer',5),('SonDzhi',10)):
            boss=self.battle(code);_,roll=self.victory(boss,False);roll.assert_called_once_with(chance)
        self.assertIsNone(arise_chance({'class_name':'summoner','passive_key':'none'}))

    def test_first_unlock_and_duplicate_star_not_shards(self):
        boss=self.battle();self.victory(boss);self.assertEqual(self.shadow()[0]['stars'],0)
        boss=self.battle();self.victory(boss);self.assertEqual(self.shadow()[0]['stars'],1)
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))[0]['shards'],0)

    def test_missing_mapping_is_valid_no_roll(self):
        boss=self.battle(mapped=False);_,roll=self.victory(boss);roll.assert_not_called();self.assertEqual(self.shadow(),[])

    def test_missing_counterpart_is_valid_no_roll(self):
        boss=self.battle();self.sql("UPDATE mini_bosses SET shadow_hero_code='not_installed' WHERE id=?",(boss['id'],))
        _,roll=self.victory(boss);roll.assert_not_called()

    def test_shadow_extractable_false_denies_roll(self):
        boss=self.battle(extractable=False);_,roll=self.victory(boss);roll.assert_not_called()

    def test_no_arise_passive_no_roll(self):
        boss=self.battle('Villager');_,roll=self.victory(boss);roll.assert_not_called()

    def test_failed_roll_persisted_and_not_repeated(self):
        boss=self.battle();self.victory(boss,False);self.assertEqual(self.shadow(),[])
        self.assertEqual(self.sql('SELECT success FROM mini_shadow_rolls')[0]['success'],0)
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            with patch('app.mini.shadow._roll_success') as roll:finish_victory(conn,conn.execute('SELECT * FROM mini_bosses WHERE id=?',(boss['id'],)).fetchone(),self.now)
            roll.assert_not_called()

    def test_no_attack_even_admin_victory_no_roll(self):
        boss=self.battle()
        from app.mini.boss.combat import force_finish_battle
        with patch('app.mini.shadow._roll_success') as roll:force_finish_battle(boss['id'],now=self.now,db_path=self.db)
        roll.assert_not_called();self.assertEqual(self.shadow(),[])

    def test_defeat_never_extracts(self):
        boss=self.battle();self.sql('UPDATE mini_boss_participants SET hit_count=1 WHERE boss_id=?',(boss['id'],))
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            with patch('app.mini.shadow._roll_success') as roll:finish_failure(conn,conn.execute('SELECT * FROM mini_bosses WHERE id=?',(boss['id'],)).fetchone(),self.now)
            roll.assert_not_called()
        self.assertEqual(self.shadow(),[])

    def test_repeated_finalization_after_restart_no_double_roll(self):
        boss=self.battle();self.victory(boss);init_mini_db(self.db)
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            with patch('app.mini.shadow._roll_success') as roll:finish_victory(conn,conn.execute('SELECT * FROM mini_bosses WHERE id=?',(boss['id'],)).fetchone(),self.now)
            roll.assert_not_called()
        self.assertEqual(self.shadow()[0]['stars'],0);self.assertEqual(len(self.sql('SELECT * FROM mini_shadow_rolls')),1)

    def test_failed_unlock_rolls_back_roll_and_finalization(self):
        boss=self.battle()
        self.sql("CREATE TRIGGER deny_shadow BEFORE INSERT ON mini_player_heroes BEGIN SELECT RAISE(ABORT,'denied'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.victory(boss)
        self.assertEqual(self.sql('SELECT * FROM mini_shadow_rolls'),[])
        self.assertEqual(get_boss(boss['id'],self.db)['status'],'fighting')

    def test_training_dummy_forbids_extraction_in_snapshot(self):
        boss=create_boss_event(self.world['id'],'training_dummy',1,self.db)
        self.assertEqual(boss['shadow_extractable'],0)


    def test_duplicate_worker_settles_previous_star_rate_before_upgrade(self):
        from app.mini.village import service as village
        from app.mini.wallet import add_coins
        from decimal import Decimal
        add_coins(self.pid,1000,'Тест',db_path=self.db)
        first=self.battle();self.victory(first)
        shadow=self.shadow()[0]['hero_id']
        self.sql('UPDATE mini_player_heroes SET stars=1 WHERE player_id=? AND hero_id=?',(self.pid,shadow))
        start=int(self.now.timestamp())
        village.mutate(self.pid,'buy',self.db,operation_key='house',expected_houses=0,now=start)
        village.mutate(self.pid,'settle',self.db,hero_id=shadow,operation_key='resident',now=start)
        village.mutate(self.pid,'assign',self.db,hero_id=shadow,building='market',operation_key='worker',now=start)
        keeper=self.hero('reborn_crypt_keeper',stars=2)
        village.mutate(self.pid,'settle',self.db,hero_id=keeper['id'],operation_key='foodresident',now=start)
        village.mutate(self.pid,'assign',self.db,hero_id=keeper['id'],building='hunt',operation_key='foodworker',now=start)
        second=self.battle();self.now=self.now.replace(hour=11);self.victory(second)
        accrued=village.get_state(self.pid,self.db,now=start+3600)
        self.assertEqual(accrued['coins'],Decimal('2.5'));self.assertEqual(self.shadow()[0]['stars'],2)
        self.assertEqual(village.get_state(self.pid,self.db,now=start+7200)['coins'],Decimal('6.25'))
