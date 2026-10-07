import json
from decimal import Decimal
from unittest.mock import patch
from app.mini.village import service as v
from app.mini.availability import assert_available
from app.mini.db import connect_mini_db
from app.mini.wallet import add_coins,get_balance
from app.mini.schema import init_mini_db
from app.mini.tower import service as tower
from app.mini.boss.service import create_boss_event,register_player,close_registration,select_battle_hero
from app.mini.boss.combat import start_battle
from app.mini.boss.errors import BossError
from tests.v1_3.support import MiniCase


class VillageTests(MiniCase):
    def setUp(self):
        super().setUp();self.n=0;add_coins(self.pid,1000,'Тест',db_path=self.db)
    def mutate(self,act,now=1000,**kwargs):
        self.n+=1
        return v.mutate(self.pid,act,self.db,operation_key=kwargs.pop('operation_key',str(self.n)),now=now,**kwargs)
    def buy(self,now=1000):
        state=v.get_state(self.pid,self.db,now=now)
        return self.mutate('buy',now,expected_houses=state['houses'])
    def worker(self,code,stars,building,now=1000):
        hero=self.hero(code,stars);self.sql('UPDATE mini_player_heroes SET stars=? WHERE player_id=? AND hero_id=?',(stars,self.pid,hero['id']))
        self.mutate('settle',now,hero_id=hero['id']);self.mutate('assign',now,hero_id=hero['id'],building=building)
        return hero
    def lineup(self):
        self.buy();self.worker('reborn_crypt_keeper',2,'hunt');self.worker('Villager',1,'market');self.worker('CityBlacksmith',1,'mine')

    def test_all_five_prices_and_sixth_rejected(self):
        for number,price in enumerate(v.PRICES):
            balance=get_balance(self.pid,self.db);self.buy();self.assertEqual(get_balance(self.pid,self.db),balance-price)
        state=v.get_state(self.pid,self.db,now=1000);self.assertEqual(state['capacity'],50)
        with self.assertRaises(ValueError):self.buy()

    def test_initial_zero_houses_and_capacity(self):
        state=v.get_state(self.pid,self.db,now=1000)
        self.assertEqual((state['houses'],state['capacity'],state['next_price']),(0,0,10))
        with self.assertRaises(ValueError):self.mutate('settle',hero_id=self.hero()['id'])

    def test_insufficient_coins_atomic(self):
        self.sql('UPDATE mini_players SET coins=0 WHERE id=?',(self.pid,))
        with self.assertRaises(ValueError):self.buy()
        self.assertEqual(v.get_state(self.pid,self.db,now=1000)['houses'],0)

    def test_buy_idempotency_and_stale_callback(self):
        first=self.mutate('buy',expected_houses=0,operation_key='buy')
        self.assertFalse(self.mutate('buy',expected_houses=0,operation_key='buy')['applied'])
        with self.assertRaises(ValueError):self.mutate('buy',expected_houses=0)
        self.assertEqual(get_balance(self.pid,self.db),990)

    def test_capacity_ten_per_house(self):
        self.buy()
        codes=[h['code'] for h in self.sql('SELECT code FROM mini_heroes LIMIT 11')]
        for code in codes[:10]:self.mutate('settle',hero_id=self.hero(code)['id'])
        with self.assertRaises(ValueError):self.mutate('settle',hero_id=self.hero(codes[10])['id'])

    def test_worker_requires_resident_and_only_one_assignment(self):
        self.buy();hero=self.hero()
        with self.assertRaises(ValueError):self.mutate('assign',hero_id=hero['id'],building='mine')
        self.mutate('settle',hero_id=hero['id']);self.mutate('assign',hero_id=hero['id'],building='mine')
        self.mutate('assign',hero_id=hero['id'],building='market')
        row=v.get_state(self.pid,self.db,now=1000)['residents'][0];self.assertEqual(row['building'],'market')

    def test_eviction_removes_production_and_restores_battle_availability(self):
        self.buy();hero=self.worker('Villager',1,'market')
        with connect_mini_db(self.db) as conn:
            with self.assertRaises(ValueError):assert_available(conn,self.pid,hero['id'])
        self.mutate('evict',hero_id=hero['id'])
        with connect_mini_db(self.db) as conn:assert_available(conn,self.pid,hero['id'])
        self.assertEqual(v.get_state(self.pid,self.db,now=1000)['residents'],[])

    def test_food_gating_does_not_accumulate_food(self):
        self.buy();self.worker('Villager',1,'market');self.worker('CityBlacksmith',1,'mine')
        state=v.get_state(self.pid,self.db,now=4600);self.assertFalse(state['fed']);self.assertEqual(state['coins'],0)
        self.worker('reborn_crypt_keeper',2,'hunt',now=4600)
        state=v.get_state(self.pid,self.db,now=8200);self.assertTrue(state['fed']);self.assertEqual(state['coins'],Decimal('.125'))
        self.assertNotIn('food_units',state)

    def test_eight_hour_cap_persists_until_collect(self):
        self.lineup();state=v.get_state(self.pid,self.db,now=1000+10*3600)
        self.assertEqual(state['coins'],1);self.assertEqual(state['seconds_left'],0)
        self.assertEqual(v.get_state(self.pid,self.db,now=1000+20*3600)['coins'],1)
        result=self.mutate('collect',now=1000+20*3600);self.assertEqual((result['coins'],result['shards']),(1,1))
        self.assertEqual(v.get_state(self.pid,self.db,now=1000+21*3600)['coins'],Decimal('.125'))

    def test_fractional_remainder_survives_multiple_collections(self):
        self.lineup()
        self.assertEqual(self.mutate('collect',now=4600)['coins'],0)
        self.assertEqual(v.get_state(self.pid,self.db,now=8200)['coins'],Decimal('.25'))
        self.assertEqual(self.mutate('collect',now=8200)['coins'],0)
        self.assertEqual(v.get_state(self.pid,self.db,now=1000+8*3600)['coins'],1)

    def test_lineup_change_settles_previous_period(self):
        self.buy();self.worker('reborn_crypt_keeper',2,'hunt');hero=self.worker('Villager',1,'market')
        self.mutate('assign',now=4600,hero_id=hero['id'],building='mine')
        state=v.get_state(self.pid,self.db,now=8200)
        self.assertEqual(state['coins'],Decimal('.125'));self.assertEqual(state['shards'],Decimal('.125'))

    def test_house_purchase_settles_before_food_shortage(self):
        self.lineup();self.buy(now=4600)
        state=v.get_state(self.pid,self.db,now=8200)
        self.assertEqual(state['coins'],Decimal('.125'));self.assertFalse(state['fed'])

    def test_no_reset_of_cap_on_mutation(self):
        self.lineup();self.mutate('assign',now=1000+7*3600,hero_id=self.sql("SELECT id FROM mini_heroes WHERE code='Villager'")[0]['id'],building='market')
        self.assertEqual(v.get_state(self.pid,self.db,now=1000+9*3600)['coins'],1)

    def test_restart_and_idempotent_migration(self):
        self.lineup();v.get_state(self.pid,self.db,now=4600);init_mini_db(self.db);init_mini_db(self.db)
        self.assertEqual(v.get_state(self.pid,self.db,now=8200)['coins'],Decimal('.25'))
        self.assertEqual(len(v.get_state(self.pid,self.db,now=8200)['residents']),3)

    def test_star_cap_and_shadow_legendary_rate(self):
        self.buy();hero=self.worker('Asrael',20,'hunt')
        self.assertEqual(v.get_state(self.pid,self.db,now=1000)['hourly']['hunt'],Decimal('7.5'))
        self.assertEqual(v.RATE_UNITS['shadow'],v.RATE_UNITS['legendary'])

    def test_boost_partial_interval_and_extension(self):
        self.lineup()
        with connect_mini_db(self.db) as conn:
            import sqlite3
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            v.activate_boost(conn,self.pid,'coins',now=4600)
        self.assertEqual(v.get_state(self.pid,self.db,now=8200)['coins'],Decimal('.28125'))
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            self.assertEqual(v.activate_boost(conn,self.pid,'coins',now=8200),4600+16*3600)
        self.assertEqual(v.get_state(self.pid,self.db,now=11800)['coins'],Decimal('.4375'))

    def test_boost_expiry_intersection_after_collect(self):
        self.lineup()
        with connect_mini_db(self.db) as conn:
            import sqlite3
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE');v.activate_boost(conn,self.pid,'coins',now=1000)
        self.mutate('collect',now=1000+7*3600)
        state=v.get_state(self.pid,self.db,now=1000+9*3600)
        self.assertEqual(state['coins'],Decimal('.375'))
        self.assertEqual(get_balance(self.pid,self.db),991)

    def test_tower_and_boss_reject_resident_even_without_work(self):
        self.buy();hero=self.hero();self.mutate('settle',hero_id=hero['id'])
        self.assertEqual(tower.list_heroes(self.pid,self.db),[])
        with self.assertRaises(ValueError):tower.start_attempt(self.pid,hero['id'],1,self.db)
        boss=create_boss_event(self.world['id'],'training_dummy',1,self.db)
        with self.assertRaises(BossError):register_player(boss['id'],self.pid,self.db)
        self.mutate('evict',hero_id=hero['id']);register_player(boss['id'],self.pid,self.db)
        with self.assertRaises(ValueError):self.mutate('settle',hero_id=hero['id'])

    def test_stale_revision_and_payload_reuse(self):
        self.buy()
        with self.assertRaises(ValueError):self.mutate('buy',expected_houses=1,expected_revision=0)
        self.mutate('collect',operation_key='key')
        with self.assertRaises(ValueError):self.mutate('buy',expected_houses=1,operation_key='key')


    def test_configurable_mythic_rate_exact_and_invalid_settings(self):
        import json,tempfile
        from pathlib import Path
        from app.mini.village import balance
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'balance.json'
            with patch.object(balance,'BALANCE_PATH',path):
                path.write_text('{"mythic_hourly_rate":"2.5"}',encoding='utf-8')
                self.assertEqual(balance.load_balance()['mythic_rate_units'],80)
                for value in (0,-1,True,'NaN','Infinity','0.01','invalid'):
                    path.write_text(json.dumps({'mythic_hourly_rate':value}),encoding='utf-8')
                    with self.assertRaises(ValueError):balance.load_balance()

    def test_boost_current_hourly_rate_shown_and_upgrade_settles_previous_stars(self):
        from app.mini.hero_upgrades import upgrade_hero
        self.lineup();self.sql('UPDATE mini_players SET shards=100 WHERE id=?',(self.pid,))
        hero=self.sql("SELECT id FROM mini_heroes WHERE code='Villager'")[0]
        with connect_mini_db(self.db) as conn:
            conn.row_factory=__import__('sqlite3').Row;conn.execute('BEGIN IMMEDIATE');v.activate_boost(conn,self.pid,'coins',now=1000)
        self.assertEqual(v.get_state(self.pid,self.db,now=4600)['hourly']['market'],Decimal('0.15625'))
        with patch('app.mini.village.service.timestamp',return_value=4600):upgrade_hero(self.pid,hero['id'],self.db)
        self.assertEqual(v.get_state(self.pid,self.db,now=8200)['coins'],Decimal('0.46875'))


    def test_admin_delete_resident_rejected_without_losing_accrual_or_ownership(self):
        from app.mini.superadmin.parser import parse_command
        from app.mini.superadmin.service import execute
        self.lineup();scope=f"{self.world['chat_id']}:{self.world['thread_id']}"
        command=parse_command(f'/superdel {scope} @tester -p Villager')
        with self.assertRaisesRegex(ValueError,'деревни'):
            execute(self.owner,command,operation_key='delete-worker',db_path=self.db)
        self.assertEqual(v.get_state(self.pid,self.db,now=4600)['coins'],Decimal('0.125'))
        self.assertEqual(len(v.get_state(self.pid,self.db,now=4600)['residents']),3)
