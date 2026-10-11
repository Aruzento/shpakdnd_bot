import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.db import connect_mini_db
from app.mini.gacha import perform_gacha_pull, get_gacha_state, GachaNoHeroes, GachaInsufficientFunds
from app.mini.pity import LIMITS, required_rarity, format_progress
from app.mini.shop import sync_shop_catalog, purchase_offer
from app.mini.items import use_inventory_item, grant_item_in_transaction, get_effect_charges
from app.mini.wallet import add_coins, get_balance
from app.mini.schema import init_mini_db
from app.mini.players import create_mini_player


class Gacha142Tests(MiniCase):
    def setUp(self):
        super().setUp();sync_shop_catalog(self.world['id'],self.db)
        add_coins(self.pid,10000,'Тест',db_path=self.db)

    def counters(self): return get_gacha_state(self.pid,self.db)['pity']
    def set_counts(self,u=0,r=0,l=0):
        self.sql('INSERT OR REPLACE INTO mini_gacha_pity(player_id,uncommon_count,rare_count,legendary_count) VALUES(?,?,?,?)',(self.pid,u,r,l))
    def pull(self,code='Villager',*,key='',payment='coins'):
        with patch('app.mini.gacha._choose_hero_code',return_value=code):
            return perform_gacha_pull(self.pid,payment,self.db,operation_key=key)
    def item(self,code,quantity=1):
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');grant_item_in_transaction(conn,self.pid,code,quantity)
            return conn.execute('SELECT id FROM mini_items WHERE code=?',(code,)).fetchone()[0]

    def test_zero_for_legacy_player_and_persistent_bounded_counts(self):
        self.assertEqual(self.counters(),dict.fromkeys(LIMITS,0))
        self.pull();init_mini_db(self.db)
        self.assertEqual(self.counters(),dict.fromkeys(LIMITS,1))
        with self.assertRaises(sqlite3.IntegrityError): self.set_counts(-1)
        with self.assertRaises(sqlite3.IntegrityError): self.set_counts(11)

    def test_exact_tenth_twentyfifth_eightyfifth_and_new_cycles(self):
        for rarity,limit in LIMITS.items():
            with self.subTest(rarity=rarity):
                self.set_counts()
                # Let other due counters run without clearing the target counter.
                for n in range(1,limit):
                    result=self.pull()
                    self.assertNotEqual(result['rarity'],rarity)
                    self.assertEqual(self.counters()[rarity],n)
                result=self.pull()
                self.assertEqual(result['rarity'],rarity)
                self.assertEqual(self.counters()[rarity],0)
                self.pull();self.assertEqual(self.counters()[rarity],1)

    def test_early_exact_rarity_resets_only_its_counter(self):
        for rarity in LIMITS:
            self.set_counts(4,6,8)
            code=self.sql('SELECT code FROM mini_heroes WHERE active=1 AND rarity=? LIMIT 1',(rarity,))[0]['code']
            result=self.pull(code)
            self.assertEqual(result['rarity'],rarity)
            expected={key:0 if key==rarity else value+1 for key,value in zip(LIMITS,(4,6,8))}
            self.assertEqual(self.counters(),expected)

    def test_priority_deferred_ready_guarantees_and_single_hero(self):
        self.set_counts(9,24,84)
        for rarity,expected in (
            ('legendary',{'uncommon':10,'rare':25,'legendary':0}),
            ('rare',{'uncommon':10,'rare':0,'legendary':1}),
            ('uncommon',{'uncommon':0,'rare':1,'legendary':2}),
        ):
            self.assertEqual(self.pull()['rarity'],rarity);self.assertEqual(self.counters(),expected)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),3)
        self.assertIn('10/10 — гарант готов',format_progress({'uncommon':10}))

    def test_players_independent(self):
        other=create_mini_player(self.world['id'],456,'@other','Другой',self.db)
        self.set_counts(9,24,84);self.pull()
        self.assertEqual(get_gacha_state(other['id'],self.db)['pity'],dict.fromkeys(LIMITS,0))

    def test_ticket_standard_counts_and_consumes_luck(self):
        self.item('summon_ticket');self.set_counts(9)
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',2)",(self.pid,))
        before=get_balance(self.pid,self.db);result=self.pull(payment='ticket')
        self.assertEqual(result['rarity'],'uncommon');self.assertTrue(result['luck_used'])
        self.assertEqual(get_effect_charges(self.pid,'gacha_luck',self.db),1)
        self.assertEqual(get_balance(self.pid,self.db),before)

    def test_admin_guarantee_overrides_lower_pity_once_and_preserves_it(self):
        self.set_counts(9,24,0)
        self.sql('INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1) ON CONFLICT(player_id) DO UPDATE SET forced_legendary=1',(self.pid,))
        result=self.pull();self.assertEqual(result['rarity'],'legendary')
        self.assertEqual(self.counters(),dict(uncommon=10,rare=25,legendary=0))
        self.assertEqual(self.pull()['rarity'],'rare')

    def test_coupons_purchase_exact_rarity_duplicates_no_pity_luck_or_guarantee_changes(self):
        self.set_counts(9,24,84)
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',3)",(self.pid,))
        self.sql('INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1) ON CONFLICT(player_id) DO UPDATE SET forced_legendary=1',(self.pid,))
        for rarity,price in zip(LIMITS,(100,500,3500)):
            code='rarity_coupon_'+rarity
            offer=self.sql('SELECT * FROM mini_shop_offers WHERE code=?',(code,))[0]
            before=get_balance(self.pid,self.db)
            self.assertEqual(offer['price'],price)
            purchase_offer(self.pid,self.world['id'],offer['id'],self.db,operation_key='buy:'+rarity)
            self.assertEqual(get_balance(self.pid,self.db),before-price)
            item_id=offer['item_id']
            result=use_inventory_item(self.pid,item_id,operation_key=code,db_path=self.db)
            hero=result['summon_result'];self.assertEqual(hero['rarity'],rarity)
            self.assertEqual(self.counters(),dict(uncommon=9,rare=24,legendary=84))
            init_mini_db(self.db)
            again=use_inventory_item(self.pid,item_id,operation_key=code,db_path=self.db)
            self.assertTrue(again['repeated']);self.assertEqual(again['summon_result'],hero)
            self.item(code)
            with patch('secrets.choice',side_effect=lambda rows: next(h for h in rows if h['id']==hero['id'])):
                duplicate=use_inventory_item(self.pid,item_id,operation_key=code+'duplicate',db_path=self.db)
            self.assertTrue(duplicate['summon_result']['is_duplicate'])
            self.assertGreater(duplicate['summon_result']['shards_awarded'],0)
        self.assertEqual(get_effect_charges(self.pid,'gacha_luck',self.db),3)
        self.assertEqual(self.sql('SELECT forced_legendary FROM mini_gacha_guarantees WHERE player_id=?',(self.pid,))[0]['forced_legendary'],1)

    def test_empty_coupon_pool_retains_item_and_state(self):
        item_id=self.item('rarity_coupon_rare');self.set_counts(9,24,84)
        self.sql("UPDATE mini_heroes SET active=0 WHERE rarity='rare'")
        with self.assertRaises(ValueError):use_inventory_item(self.pid,item_id,operation_key='empty',db_path=self.db)
        self.assertEqual(self.sql('SELECT quantity FROM mini_inventory WHERE player_id=? AND item_id=?',(self.pid,item_id))[0]['quantity'],1)
        self.assertEqual(self.sql('SELECT * FROM mini_item_uses'),[])
        self.assertEqual(self.counters()['rare'],24)

    def test_empty_guarantee_pool_and_insufficient_coins_roll_back_everything(self):
        self.set_counts(9,24,84);self.sql("UPDATE mini_heroes SET active=0 WHERE rarity='legendary'")
        before=get_balance(self.pid,self.db)
        with self.assertRaises(GachaNoHeroes):self.pull(key='empty')
        self.assertEqual(get_balance(self.pid,self.db),before);self.assertEqual(self.counters()['legendary'],84)
        self.sql('UPDATE mini_players SET coins=0 WHERE id=?',(self.pid,))
        with self.assertRaises(GachaInsufficientFunds):self.pull()
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])

    def test_fault_after_hero_grant_rolls_back_hero_payment_luck_and_pity(self):
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',2)",(self.pid,))
        self.sql("CREATE TRIGGER deny_pull BEFORE INSERT ON mini_gacha_pulls BEGIN SELECT RAISE(ABORT,'injected'); END")
        before=get_balance(self.pid,self.db)
        with self.assertRaises(sqlite3.IntegrityError):self.pull(key='fault')
        self.assertEqual(get_balance(self.pid,self.db),before)
        self.assertEqual(self.counters(),dict.fromkeys(LIMITS,0))
        self.assertEqual(self.sql('SELECT * FROM mini_player_heroes'),[])
        self.assertEqual(get_effect_charges(self.pid,'gacha_luck',self.db),2)

    def test_admin_hero_grant_does_not_change_pity(self):
        from app.mini.superadmin.parser import parse_command
        from app.mini.superadmin.service import execute
        self.set_counts(9,24,84)
        hero=self.sql("SELECT code FROM mini_heroes WHERE rarity='legendary' AND active=1 LIMIT 1")[0]['code']
        command=parse_command(f'/superadd {self.world["chat_id"]}:{self.world["thread_id"]} @tester -p {hero}')
        execute(self.owner,command,operation_key='admin-hero',db_path=self.db)
        self.assertEqual(self.counters(),dict(uncommon=9,rare=24,legendary=84))
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])

    def test_only_active_coupon_pool_and_last_ticket_result_replay(self):
        from app.mini.gacha import get_previous_gacha_pull
        hero=self.sql("SELECT id FROM mini_heroes WHERE rarity='rare' AND active=1 LIMIT 1")[0]['id']
        self.sql("UPDATE mini_heroes SET active=0 WHERE rarity='rare' AND id!=?",(hero,))
        coupon=self.item('rarity_coupon_rare')
        result=use_inventory_item(self.pid,coupon,operation_key='only-active',db_path=self.db)
        self.assertEqual(result['summon_result']['id'],hero)
        ticket=self.item('summon_ticket')
        self.sql('UPDATE mini_inventory SET quantity=1 WHERE player_id=? AND item_id=?',(self.pid,ticket))
        self.pull(key='last-ticket',payment='ticket')
        init_mini_db(self.db)
        saved=get_previous_gacha_pull(self.pid,'last-ticket',self.db)
        self.assertTrue(saved['repeated']);self.assertEqual(saved['payment'],'ticket')
        self.assertEqual(saved['tickets'],0);self.assertIsNone(get_previous_gacha_pull(self.pid+1,'last-ticket',self.db))

    def test_concurrent_callback_gives_one_pull_one_update_and_replay_after_restart(self):
        self.set_counts(9,24,84)
        before=get_balance(self.pid,self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.pull(key='same'),range(2)))
        self.assertEqual(results[0]['pull_id'],results[1]['pull_id'])
        self.assertEqual(get_balance(self.pid,self.db),before-results[0]['cost_coins'])
        self.assertEqual(self.counters(),dict(uncommon=10,rare=25,legendary=0))
        init_mini_db(self.db);self.assertTrue(self.pull(key='same')['repeated'])
        with self.assertRaises(ValueError):self.pull(key='same',payment='ticket')

    def test_concurrent_coupon_callback_and_fault_are_atomic(self):
        item_id=self.item('rarity_coupon_uncommon',2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:use_inventory_item(self.pid,item_id,operation_key='coupon',db_path=self.db),range(2)))
        self.assertEqual(results[0]['summon_result'],results[1]['summon_result'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_item_uses')),1)
        self.sql("CREATE TRIGGER deny_use BEFORE INSERT ON mini_item_uses BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):use_inventory_item(self.pid,item_id,operation_key='fault',db_path=self.db)
        self.assertEqual(self.sql('SELECT quantity FROM mini_inventory WHERE player_id=? AND item_id=?',(self.pid,item_id))[0]['quantity'],1)
