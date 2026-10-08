import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('BOT_TOKEN','test-token')
from app.mini.schema import init_mini_db, MINI_TABLES
from app.mini.boss.schema import init_boss_db, BOSS_TABLES
from app.mini.db import connect_mini_db
from app.mini.migrations import MIGRATIONS
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.players import create_mini_player
from app.mini.heroes import sync_hero_catalog
from app.mini.shop import sync_shop_catalog
from app.mini.wallet import add_coins
from tests.topic_fixtures import isolated_topics

class MiniMigrationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(isolated_topics())
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/'mini.db'

    def snapshot(self):
        with connect_mini_db(self.db) as conn:
            return {table:conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in (*MINI_TABLES,*BOSS_TABLES)}

    def seed(self):
        init_mini_db(self.db); init_boss_db(self.db)
        world=sync_configured_mini_worlds(self.db)[0]['id']
        player=create_mini_player(world,555,'@tester','Игрок',self.db)['id']
        sync_hero_catalog(self.db); sync_shop_catalog(world,self.db)
        from app.mini.equipment.service import sync_catalog
        sync_catalog(self.db)
        add_coins(player,900,'Seed','test',1,'seed',self.db)
        with connect_mini_db(self.db) as conn:
            hero=conn.execute("SELECT id FROM mini_heroes WHERE code='Villager'").fetchone()[0]
            item=conn.execute("SELECT id FROM mini_items WHERE code='boss_coin_pouch'").fetchone()[0]
            offer=conn.execute('SELECT id FROM mini_shop_offers LIMIT 1').fetchone()[0]
            conn.execute('UPDATE mini_worlds SET launcher_message_id=77 WHERE id=?',(world,))
            conn.execute('UPDATE mini_players SET shards=17, active_hero_id=? WHERE id=?',(hero,player))
            conn.execute('INSERT INTO mini_player_heroes(player_id,hero_id,copies,stars) VALUES(?,?,4,3)',(player,hero))
            conn.execute('INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(?,?,5)',(player,item))
            conn.execute("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',2)",(player,))
            conn.execute("INSERT INTO mini_item_uses(player_id,item_id,effect_key,result_json,operation_key) VALUES(?,?,'boss_coin_pouch','{\"amount\":12}','use')",(player,item))
            conn.execute("INSERT INTO mini_daily_claims(player_id,claim_date,encounter_key,story_text,coins_earned,item_id) VALUES(?,'2026-10-05','rare:test','История',30,?)",(player,item))
            conn.execute('INSERT INTO mini_purchases(player_id,offer_id,total_price) VALUES(?,?,20)',(player,offer))
            conn.execute('INSERT INTO mini_gacha_pulls(player_id,hero_id,cost_coins,is_duplicate,shards_awarded) VALUES(?,?,30,1,5)',(player,hero))
            session=conn.execute("INSERT INTO mini_event_sessions(player_id,game_type,stake,status,payload_json,start_key) VALUES(?,'rps',5,'resolved','{\"outcome\":\"win\"}','start')",(player,)).lastrowid
            conn.execute("INSERT INTO mini_event_requests(player_id,operation_key,session_id) VALUES(?,'resolve',?)",(player,session))
            boss=conn.execute("INSERT INTO mini_bosses(world_id,name,max_hp,current_hp,status,created_by_user_id,ability_state_json,turn_notice_json) VALUES(?,'Старый бой',100,60,'fighting',555,'{\"turns\":2}','{\"text\":\"Ход\"}')",(world,)).lastrowid
            loadout={'faction':'commoners','damage_type':'slashing','class_tag':'none','attack_range':'melee','special_trait':'none','passive_key':'none','passive_text':'Сохранено'}
            conn.execute("INSERT INTO mini_boss_participants(boss_id,player_id,queue_position,hero_id,attack,hero_snapshot_json,hero_state_json) VALUES(?,?,1,?,9,?,'{\"pending_echo_damage\":7}')",(boss,player,hero,json.dumps(loadout)))
            conn.execute("INSERT INTO mini_boss_actions(boss_id,player_id,round_number,action_type,damage,boss_hp_after) VALUES(?,?,2,'attack',9,60)",(boss,player))
            conn.execute("INSERT INTO mini_boss_events(boss_id,round_number,event_type,target_player_id,event_json,boss_hp_after) VALUES(?,2,'battle_echo',?,'{\"damage\":7}',60)",(boss,player))
        # V1.3 tables join the same preservation assertion; populate each table,
        # including an active runtime snapshot and a historical first-clear claim.
        from app.mini.tower.catalog import get_floor
        with connect_mini_db(self.db) as conn:
            conn.execute("INSERT INTO mini_equipment_owned(player_id,code,quantity) VALUES(?,'eq_helmet_010',2)",(player,))
            conn.execute("INSERT INTO mini_equipment_slots(player_id,slot,code) VALUES(?,'helmet','eq_helmet_010')",(player,))
            conn.execute("INSERT INTO mini_tower_progress(player_id,highest_cleared) VALUES(?,1)",(player,))
            snapshot=json.dumps(dict(id=hero,name='Villager',attack=9,stars=3,**loadout))
            won=conn.execute("""INSERT INTO mini_tower_attempts
                (player_id,floor,hero_id,hero_json,enemy_json,equipment_bonus,current_hp,status)
                VALUES(?,1,?,?,?,10,0,'won')""",(player,hero,snapshot,json.dumps(get_floor(1)))).lastrowid
            conn.execute("""INSERT INTO mini_tower_attempts
                (player_id,floor,hero_id,hero_json,enemy_json,equipment_bonus,current_hp,shields,turn,runtime_json)
                VALUES(?,2,?,?,?,10,4,2,1,?)""",(player,hero,snapshot,json.dumps(get_floor(2)),
                    json.dumps({'hero':{'pending_echo_damage':2},'creature':{},'hit_count':1})))
            conn.execute("""INSERT INTO mini_tower_rewards(player_id,floor,attempt_id,shards,equipment_code)
                VALUES(?,1,?,2,'eq_helmet_010')""",(player,won))
            conn.execute("INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)",(player,))
            conn.execute("""INSERT INTO mini_superadmin_audit
                (admin_user_id,action,world_id,target_user_id,player_id,resource,operation_key)
                VALUES(555,'superluck',?,555,?,'gacha_guarantee','fixture')""",(world,player))
        from app.mini.favorites import add_favorite
        add_favorite(player,hero,self.db)
        from app.mini.tower.service import select_hero
        select_hero(player,hero,self.db)
        from app.mini.titles.service import issue_title
        issue_title(player,'Хранитель',86400,555,'fixture:title',self.db,now=1000)
        with connect_mini_db(self.db) as conn:
            other=conn.execute("INSERT INTO mini_players(world_id,telegram_user_id,character_name) VALUES(?,556,'Другой')",(world,)).lastrowid
            conn.execute('INSERT INTO mini_onboarding_claims(world_id,telegram_user_id,player_id,tickets) VALUES(?,556,?,0)',(world,other))
            conn.execute('INSERT INTO mini_villages VALUES(?,1,1000,2000,123,456,2)',(player,))
            conn.execute("INSERT INTO mini_village_residents VALUES(?,?,'mine')",(player,hero))
            conn.execute("INSERT INTO mini_village_boosts VALUES(?,'coins',1000,29800)",(player,))
            conn.execute("INSERT INTO mini_village_operations VALUES(?,'fixture','buy','{}')",(player,))
            conn.execute("INSERT INTO mini_daily_streak VALUES(?,'2026-10-05',22)",(player,))
            conn.execute("INSERT INTO mini_daily_chests VALUES(?,'2026-10-05','boss_coin_pouch')",(player,))
            conn.execute("INSERT INTO mini_mythic_fragments VALUES(?,'future_mythic',17)",(player,))
            conn.execute("INSERT INTO mini_mythic_grants VALUES(?,'fixture','future_mythic',17,'test')",(player,))
            conn.execute("INSERT INTO mini_shadow_rolls VALUES(?,?,?,5,'future_shadow',0)",(boss,player,hero))
            duel=conn.execute("INSERT INTO mini_duels(challenger_id,defender_id,challenger_hero_id,challenger_snapshot,expires_at,operation_key) VALUES(?,?,?,'{}',1120,'fixture')",(player,other,hero)).lastrowid
            conn.execute('INSERT INTO mini_duel_locks VALUES(?,?)',(player,duel))
        return player

    def test_fresh_schema_contains_all_tables_and_survives_repeated_init(self):
        init_mini_db(self.db); init_boss_db(self.db)
        before=self.snapshot(); init_mini_db(self.db); init_boss_db(self.db)
        self.assertEqual(self.snapshot(),before)
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_existing_database_preserves_every_table_and_runtime_snapshot(self):
        self.seed(); before=self.snapshot()
        for _ in range(3): init_mini_db(self.db); init_boss_db(self.db)
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(all(before.values()),'Fixture must cover every Mini table')

    def test_legacy_columns_and_shards_migrate_once_without_losing_history(self):
        player=self.seed()
        with connect_mini_db(self.db) as conn:
            conn.execute('DROP INDEX idx_mini_wallet_operation_key')
            conn.execute('ALTER TABLE mini_wallet_transactions DROP COLUMN operation_key')
            conn.execute('ALTER TABLE mini_players DROP COLUMN shards')
            conn.execute('ALTER TABLE mini_player_heroes DROP COLUMN stars')
            conn.execute('ALTER TABLE mini_worlds DROP COLUMN launcher_message_id')
            conn.execute('UPDATE mini_player_heroes SET shards=23')
            old_wallet=conn.execute('SELECT id,player_id,amount,balance_after,reason,reference_type,reference_id,created_at FROM mini_wallet_transactions').fetchall()
        init_mini_db(self.db); init_boss_db(self.db)
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute('SELECT coins,shards FROM mini_players WHERE id=?',(player,)).fetchone(),(900,23))
            self.assertEqual(conn.execute('SELECT copies,shards,stars FROM mini_player_heroes').fetchone(),(4,0,0))
            self.assertEqual(conn.execute('SELECT id,player_id,amount,balance_after,reason,reference_type,reference_id,created_at FROM mini_wallet_transactions').fetchall(),old_wallet)
            self.assertEqual(conn.execute('SELECT operation_key FROM mini_wallet_transactions').fetchone()[0],'')
        after=self.snapshot(); init_mini_db(self.db); init_boss_db(self.db)
        self.assertEqual(self.snapshot(),after)

    def test_failed_migration_rolls_back_ddl_and_data_then_can_retry(self):
        def fail(conn):
            conn.execute('CREATE TABLE must_rollback (id INTEGER)')
            conn.execute("INSERT INTO mini_worlds(chat_id,thread_id) VALUES(1,1)")
            raise RuntimeError('migration failure')
        with patch('app.mini.migrations.MIGRATIONS',(*MIGRATIONS,fail)):
            with self.assertRaises(RuntimeError): init_mini_db(self.db)
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])
        init_mini_db(self.db); init_boss_db(self.db)
        self.assertEqual(len(self.snapshot()),len(MINI_TABLES)+len(BOSS_TABLES))
