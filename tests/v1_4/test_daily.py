from datetime import date,datetime,timedelta,timezone
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from app.mini.daily import claim_daily,_normalize_claim_date
from app.mini.schema import init_mini_db
from app.mini.weekly import grant_chest,sync_items_in_transaction
from app.mini.db import connect_mini_db
from app.mini.items import use_inventory_item,grant_item_in_transaction
from app.mini.village.service import get_state
from tests.v1_3.support import MiniCase


class StreakTests(MiniCase):
    def claim(self,day):
        with patch('app.mini.daily._pick_reward',return_value=('common',10)):
            return claim_daily(self.pid,'Герой',day,self.db)

    def test_consecutive_cycle_seven_to_eight(self):
        for n in range(8):
            result=self.claim(date(2026,10,1)+timedelta(days=n))
            self.assertEqual(result['streak'],n+1);self.assertEqual(result['cycle_day'],n%7+1)
            self.assertEqual(result['coins_earned'],10+n%7)
        self.assertEqual(len(self.sql('SELECT * FROM mini_daily_chests')),1)

    def test_long_streak_twenty_two_and_cycle_one(self):
        for n in range(22):result=self.claim(date(2026,10,1)+timedelta(days=n))
        self.assertEqual((result['streak'],result['cycle_day'],result['coins_earned']),(22,1,10))
        self.assertEqual(len(self.sql('SELECT * FROM mini_daily_chests')),3)

    def test_missed_day_resets_both(self):
        self.claim('2026-10-01');self.claim('2026-10-02');result=self.claim('2026-10-04')
        self.assertEqual((result['streak'],result['cycle_day']),(1,1))

    def test_repeated_claim_and_chest_exactly_once(self):
        for n in range(7):first=self.claim(date(2026,10,1)+timedelta(days=n))
        again=self.claim('2026-10-07');self.assertFalse(again['claimed']);self.assertEqual(again['chest_code'],first['chest_code'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_daily_chests')),1)
        self.assertEqual(self.sql('SELECT coins FROM mini_players WHERE id=?',(self.pid,))[0]['coins'],91)

    def test_timezone_calendar_midnight_boundary(self):
        with patch('app.mini.daily.TIMEZONE',__import__('zoneinfo').ZoneInfo('Europe/Moscow')):
            self.assertEqual(_normalize_claim_date(datetime(2026,10,6,20,59,tzinfo=timezone.utc)),'2026-10-06')
            self.assertEqual(_normalize_claim_date(datetime(2026,10,6,21,0,tzinfo=timezone.utc)),'2026-10-07')
        with self.assertRaises(ValueError):_normalize_claim_date(datetime(2026,10,6))

    def test_restart_keeps_streak_and_claim_snapshot(self):
        self.claim('2026-10-01');init_mini_db(self.db);self.assertEqual(self.claim('2026-10-02')['streak'],2)
        self.assertEqual(self.claim('2026-10-01')['streak'],1)

    def test_historical_claims_recover_consecutive_streak(self):
        for day in ('2026-10-01','2026-10-02'):
            self.sql("INSERT INTO mini_daily_claims(player_id,claim_date,encounter_key,story_text,coins_earned) VALUES(?,?,'common:test','Test',10)",(self.pid,day))
        self.assertEqual(self.claim('2026-10-03')['streak'],3)

    def test_concurrent_day_seven_only_one_chest(self):
        for n in range(6):self.claim(date(2026,10,1)+timedelta(days=n))
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.claim('2026-10-07'),range(2)))
        self.assertEqual(sum(r['claimed'] for r in results),1);self.assertEqual(len(self.sql('SELECT * FROM mini_daily_chests')),1)

    def test_chest_categories_and_random_boss_egg(self):
        import sqlite3
        categories=('tower_equipment_chest','boss_coin_pouch','boss_shard_casket','egg','summon_ticket','village_gold_boost','village_shards_boost')
        for n,code in enumerate(categories):
            with connect_mini_db(self.db) as conn:
                conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
                def choose(pool):
                    return pool[0] if isinstance(pool[0],dict) else code
                result=grant_chest(conn,self.pid,f'2026-10-{n+1:02}',chooser=choose)
                self.assertTrue(result.startswith('boss_egg_') if code=='egg' else result==code)
        self.assertEqual(len(self.sql('SELECT * FROM mini_daily_chests')),7)

    def test_boost_item_atomic_and_idempotent_extension(self):
        import sqlite3
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE');sync_items_in_transaction(conn);grant_item_in_transaction(conn,self.pid,'village_gold_boost',2)
        iid=self.sql("SELECT id FROM mini_items WHERE code='village_gold_boost'")[0]['id']
        with patch('app.mini.village.service.timestamp',return_value=1000):
            first=use_inventory_item(self.pid,iid,operation_key='one',db_path=self.db)
            again=use_inventory_item(self.pid,iid,operation_key='one',db_path=self.db)
            second=use_inventory_item(self.pid,iid,operation_key='two',db_path=self.db)
        self.assertTrue(again['repeated']);self.assertEqual(second['boost_ends_at'],first['boost_ends_at']+28800)
        self.assertEqual(self.sql('SELECT quantity FROM mini_inventory WHERE player_id=? AND item_id=?',(self.pid,iid))[0]['quantity'],0)

    def test_equipment_chest_uses_existing_owned_equipment(self):
        import sqlite3
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE');sync_items_in_transaction(conn);grant_item_in_transaction(conn,self.pid,'tower_equipment_chest',1)
        iid=self.sql("SELECT id FROM mini_items WHERE code='tower_equipment_chest'")[0]['id']
        result=use_inventory_item(self.pid,iid,operation_key='chest',db_path=self.db)
        self.assertEqual(self.sql('SELECT quantity FROM mini_equipment_owned WHERE player_id=? AND code=?',(self.pid,result['equipment_code']))[0]['quantity'],1)
