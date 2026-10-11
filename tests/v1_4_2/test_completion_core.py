import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.gacha import perform_gacha_batch, get_gacha_batch, get_gacha_state, GachaError, GachaInsufficientFunds, GachaNoHeroes
from app.mini.wallet import add_coins, get_balance
from app.mini.items import grant_item_in_transaction, get_effect_charges
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini import registration


class BatchTests(MiniCase):
    def setUp(self):
        super().setUp();add_coins(self.pid,10000,'Test',db_path=self.db)
    def batch(self,key='ten'):
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            return perform_gacha_batch(self.pid,self.db,operation_key=key)
    def test_ten_rows_one_payment_first_activation_duplicates_and_immutable_result(self):
        before=get_balance(self.pid,self.db);batch=self.batch()
        self.assertEqual(len(batch['results']),10)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_batch_pulls')),10)
        self.assertEqual(len(self.sql("SELECT * FROM mini_wallet_transactions WHERE reference_type='gacha_batch'")),1)
        self.assertEqual(get_balance(self.pid,self.db),before-10*batch['pull_price'])
        self.assertEqual(sum(r['auto_activated'] for r in batch['results']),1)
        self.assertEqual(batch['shards_awarded'],sum(r['shards_awarded'] for r in batch['results']))
        self.assertEqual(batch,get_gacha_batch(self.pid,batch['batch_id'],self.db))
        self.sql("UPDATE mini_heroes SET name='Edited' WHERE code='Villager'")
        self.assertNotEqual(get_gacha_batch(self.pid,batch['batch_id'],self.db)['results'][0]['name'],'Edited')
    def test_process_crash_mid_transaction_rolls_back_and_next_process_can_retry(self):
        import os,subprocess,sys
        from pathlib import Path
        code="""import os,sys
import app.mini.gacha as g
calls=0
def choose(**kwargs):
 global calls
 calls+=1
 if calls==6:os._exit(19)
 return 'Villager'
g._choose_hero_code=choose
g.perform_gacha_batch(int(sys.argv[2]),sys.argv[1],operation_key='crashed')
"""
        result=subprocess.run([sys.executable,'-c',code,str(self.db),str(self.pid)],
                              cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,19,result.stderr)
        self.assertEqual(get_balance(self.pid,self.db),10000)
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_batches'),[])
        self.assertEqual(self.sql('SELECT * FROM mini_player_heroes'),[])
        init_mini_db(self.db)
        self.assertEqual(len(self.batch('crashed')['results']),10)

    def test_inactive_coupon_not_offered_as_available_in_gacha(self):
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');grant_item_in_transaction(conn,self.pid,'rarity_coupon_rare',1)
        self.assertEqual(len(get_gacha_state(self.pid,self.db)['coupons']),1)
        self.sql("UPDATE mini_items SET active=0 WHERE code='rarity_coupon_rare'")
        self.assertEqual(get_gacha_state(self.pid,self.db)['coupons'],[])

    def test_exact_full_price_leaves_zero_without_requiring_an_eleventh_payment(self):
        price=get_gacha_state(self.pid,self.db)['pull_price']
        self.sql('UPDATE mini_players SET coins=? WHERE id=?',(10*price,self.pid))
        result=self.batch()
        self.assertEqual(result['balance'],0)
        self.assertEqual(get_balance(self.pid,self.db),0)
        self.assertEqual(sum(r['cost_coins'] for r in result['results']),10*price)

    def test_sequential_crossing_three_guarantees_preserves_deferred_exact_counters(self):
        self.sql('INSERT INTO mini_gacha_pity(player_id,uncommon_count,rare_count,legendary_count) VALUES(?,9,24,84)',(self.pid,))
        batch=self.batch()
        self.assertEqual([r['rarity'] for r in batch['results'][:3]],['legendary','rare','uncommon'])
        self.assertEqual(batch['pity'],dict(uncommon=7,rare=8,legendary=9))
    def test_guarantee_crossed_mid_batch(self):
        self.sql('INSERT INTO mini_gacha_pity(player_id,uncommon_count,rare_count,legendary_count) VALUES(?,0,20,80)',(self.pid,))
        batch=self.batch()
        self.assertEqual(batch['results'][4]['rarity'],'legendary')
        self.assertEqual(batch['results'][5]['rarity'],'rare')
        self.assertEqual(batch['results'][9]['rarity'],'uncommon')
    def test_luck_each_roll_tickets_retained_and_admin_guarantee_once(self):
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE');grant_item_in_transaction(conn,self.pid,'summon_ticket',2)
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',3)",(self.pid,))
        self.sql('INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)',(self.pid,))
        batch=self.batch()
        self.assertEqual(sum(r['luck_used'] for r in batch['results']),3)
        self.assertEqual(sum(r['forced_legendary'] for r in batch['results']),1)
        self.assertEqual(get_effect_charges(self.pid,'gacha_luck',self.db),0)
        self.assertEqual(get_gacha_state(self.pid,self.db)['tickets'],5)
    def test_insufficient_money_or_empty_pool_has_no_partial_rows(self):
        self.sql('UPDATE mini_players SET coins=1 WHERE id=?',(self.pid,))
        with self.assertRaises(GachaInsufficientFunds):self.batch()
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_batches'),[])
        self.sql('UPDATE mini_players SET coins=10000 WHERE id=?',(self.pid,))
        self.sql('UPDATE mini_heroes SET active=0')
        with self.assertRaises(GachaNoHeroes):self.batch()
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])
    def test_fault_at_sixth_roll_rolls_back_payment_heroes_shards_pity_and_luck(self):
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',7)",(self.pid,))
        self.sql("CREATE TRIGGER fail_six BEFORE INSERT ON mini_gacha_pulls WHEN (SELECT COUNT(*) FROM mini_gacha_pulls)=5 BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.batch()
        for table in ('mini_gacha_batches','mini_gacha_batch_pulls','mini_gacha_pulls','mini_player_heroes','mini_gacha_pity'):
            self.assertEqual(self.sql('SELECT * FROM '+table),[])
        self.assertEqual(get_balance(self.pid,self.db),10000)
        self.assertEqual(get_effect_charges(self.pid,'gacha_luck',self.db),7)
        self.assertIsNone(self.sql('SELECT active_hero_id FROM mini_players WHERE id=?',(self.pid,))[0]['active_hero_id'])
    def test_missing_due_pool_in_middle_rolls_back_entire_batch(self):
        self.sql('INSERT INTO mini_gacha_pity(player_id,legendary_count) VALUES(?,80)',(self.pid,))
        self.sql("UPDATE mini_heroes SET active=0 WHERE rarity='legendary'")
        with self.assertRaises(GachaNoHeroes):self.batch()
        self.assertEqual(get_balance(self.pid,self.db),10000)
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])
        self.assertEqual(get_gacha_state(self.pid,self.db)['pity']['legendary'],80)
    def test_concurrent_double_click_restart_and_new_operation(self):
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'),ThreadPoolExecutor(max_workers=2) as pool:
            batches=list(pool.map(lambda _:perform_gacha_batch(self.pid,self.db,operation_key='same'),range(2)))
        self.assertEqual(batches[0]['batch_id'],batches[1]['batch_id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)
        init_mini_db(self.db)
        self.assertTrue(self.batch('same')['repeated'])
        self.assertNotEqual(self.batch('new')['batch_id'],batches[0]['batch_id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),20)
    def test_batch_is_owner_scoped_and_requires_operation(self):
        batch=self.batch()
        with self.assertRaises(GachaError):get_gacha_batch(self.pid+1,batch['batch_id'],self.db)
        with self.assertRaises(GachaError):self.batch('')
    def test_repeated_migration_retains_batches_and_members(self):
        batch=self.batch()
        for _ in range(3):init_mini_db(self.db)
        self.assertEqual(batch,get_gacha_batch(self.pid,batch['batch_id'],self.db))
        self.assertEqual(self.sql('PRAGMA foreign_key_check'),[])


class PrivateRegistrationTests(MiniCase):
    user=88661
    def begin(self):return registration.begin(self.world['id'],self.user,self.db,now=1000)
    def test_owner_expiry_private_activation_and_restart(self):
        token=self.begin()
        with self.assertRaises(ValueError):registration.activate(token,self.user+1,self.db,now=1001)
        with self.assertRaises(ValueError):registration.activate(token,self.user,self.db,now=2800)
        self.assertIsNone(registration.pending(self.user,self.db,now=1001))
        registration.activate(token,self.user,self.db,now=1001);init_mini_db(self.db)
        self.assertEqual(registration.pending(self.user,self.db,now=1002)['token'],token)
    def test_creation_starter_exactly_once_and_saved_owner(self):
        token=self.begin();registration.activate(token,self.user,self.db,now=1001)
        p=registration.complete(token,self.user,'@name','  Герой   теста  ',self.db,now=1002)
        q=registration.complete(token,self.user,'@other','Новое имя',self.db,now=1003)
        self.assertEqual(p['id'],q['id']);self.assertEqual(q['character_name'],'Герой теста')
        self.assertTrue(p['onboarding_granted']);self.assertFalse(q['onboarding_granted'])
        self.assertIsNone(registration.pending(self.user,self.db,now=1004))
        self.assertEqual(get_gacha_state(p['id'],self.db)['tickets'],3)
    def test_invalid_name_does_not_consume_request_or_create_player(self):
        token=self.begin();registration.activate(token,self.user,self.db,now=1001)
        for name in ('','a'*65,'<b>Имя</b>','/admin','Имя\u202e','Имя\x00'):
            with self.subTest(name=repr(name)),self.assertRaises(ValueError):
                registration.complete(token,self.user,'@name',name,self.db,now=1002)
        self.assertIsNotNone(registration.pending(self.user,self.db,now=1003))
        self.assertEqual(self.sql('SELECT id FROM mini_players WHERE telegram_user_id=?',(self.user,)),[])
    def test_concurrent_completion_gives_one_player_and_gift(self):
        token=self.begin();registration.activate(token,self.user,self.db,now=1001)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:registration.complete(token,self.user,'','Имя',self.db,now=1002),range(2)))
        self.assertEqual(results[0]['id'],results[1]['id'])
        self.assertEqual(sum(r['onboarding_granted'] for r in results),1)
    def test_disabled_world_and_cancellation_fail_closed(self):
        token=self.begin();registration.activate(token,self.user,self.db,now=1001)
        registration.cancel(self.user,self.db)
        self.assertIsNone(registration.pending(self.user,self.db,now=1002))
        with self.assertRaises(ValueError):registration.complete(token,self.user,'','Имя',self.db,now=1002)
        self.sql('UPDATE mini_worlds SET enabled=0 WHERE id=?',(self.world['id'],))
        with self.assertRaises(ValueError):registration.activate(token,self.user,self.db,now=1002)
    def test_injected_onboarding_failure_rolls_back_request_and_player(self):
        token=self.begin();registration.activate(token,self.user,self.db,now=1001)
        with patch('app.mini.onboarding.grant_starter_in_transaction',side_effect=RuntimeError('fault')),self.assertRaises(RuntimeError):
            registration.complete(token,self.user,'','Имя',self.db,now=1002)
        self.assertIsNotNone(registration.pending(self.user,self.db,now=1003))
        self.assertEqual(self.sql('SELECT id FROM mini_players WHERE telegram_user_id=?',(self.user,)),[])

    def test_stale_unused_request_after_another_creation_does_not_capture_private_text(self):
        first=self.begin();second=self.begin()
        registration.activate(first,self.user,self.db,now=1001)
        player=registration.complete(first,self.user,'','Имя',self.db,now=1002)
        registration.activate(second,self.user,self.db,now=1003)
        self.assertIsNone(registration.pending(self.user,self.db,now=1004))
        self.assertEqual(self.sql('SELECT player_id FROM mini_registration_requests WHERE token=?',(second,))[0]['player_id'],player['id'])
