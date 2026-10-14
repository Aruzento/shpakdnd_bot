import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.gacha import perform_gacha_batch,get_gacha_batch,get_gacha_state,GachaError
from app.mini.wallet import add_coins,get_balance
from app.mini.shop import sync_shop_catalog
from app.mini import registration
from app.mini.tower import service as tower
from app.mini.schema import init_mini_db


class BatchTests(MiniCase):
    def setUp(self):
        super().setUp();sync_shop_catalog(self.world['id'],self.db)
        add_coins(self.pid,10000,'Тест',db_path=self.db)
    def batch(self,key='test'):
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            return perform_gacha_batch(self.pid,self.db,operation_key=key)
    def test_ten_results_one_payment_and_auto_activation_once(self):
        before=get_balance(self.pid,self.db);result=self.batch()
        self.assertEqual(len(result['results']),10)
        self.assertEqual(before-get_balance(self.pid,self.db),10*get_gacha_state(self.pid,self.db)['pull_price'])
        self.assertEqual(len(self.sql("SELECT * FROM mini_wallet_transactions WHERE reference_type='gacha_batch'")),1)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)
        self.assertEqual(sum(r['auto_activated'] for r in result['results']),1)
        self.assertEqual(result,get_gacha_batch(self.pid,result['batch_id'],self.db))
    def test_priority_inside_batch_and_luck_expiry(self):
        self.sql('INSERT INTO mini_gacha_pity(player_id,uncommon_count,rare_count,legendary_count) VALUES(?,9,24,84)',(self.pid,))
        self.sql("INSERT INTO mini_player_effects(player_id,effect_key,charges) VALUES(?,'gacha_luck',4)",(self.pid,))
        result=self.batch()
        self.assertEqual([h['rarity'] for h in result['results'][:3]],['legendary','rare','uncommon'])
        self.assertEqual([h['luck_used'] for h in result['results']],[True]*4+[False]*6)
    def test_crosses_legendary_and_rare_thresholds_mid_batch(self):
        self.sql('INSERT INTO mini_gacha_pity(player_id,uncommon_count,rare_count,legendary_count) VALUES(?,0,20,80)',(self.pid,))
        result=self.batch()['results']
        self.assertEqual(result[4]['rarity'],'legendary');self.assertEqual(result[5]['rarity'],'rare')
    def test_rollback_after_partial_simulation(self):
        before=get_balance(self.pid,self.db)
        with patch('app.mini.gacha._choose_hero_code',side_effect=['Villager']*3+[RuntimeError('injected')]):
            with self.assertRaises(RuntimeError):perform_gacha_batch(self.pid,self.db,operation_key='failure')
        self.assertEqual(get_balance(self.pid,self.db),before)
        for table in ('mini_gacha_pulls','mini_gacha_batches','mini_player_heroes','mini_gacha_pity'):
            self.assertEqual(self.sql('SELECT * FROM '+table),[])
    def test_empty_pool_rolls_back_everything(self):
        self.sql('UPDATE mini_heroes SET active=0');before=get_balance(self.pid,self.db)
        with self.assertRaises(GachaError):self.batch()
        self.assertEqual(get_balance(self.pid,self.db),before);self.assertEqual(self.sql('SELECT * FROM mini_gacha_batches'),[])
    def test_exact_funds_and_no_ticket_consumption(self):
        price=get_gacha_state(self.pid,self.db)['pull_price']
        self.sql('UPDATE mini_players SET coins=? WHERE id=?',(price*10,self.pid))
        result=self.batch();self.assertEqual(result['balance'],0)
        self.assertFalse(any(r['used_ticket'] for r in result['results']))
    def test_insufficient_funds_no_partial_batch(self):
        self.sql('UPDATE mini_players SET coins=0 WHERE id=?',(self.pid,))
        with self.assertRaises(GachaError):self.batch()
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),[])
    def test_replay_restart_and_parallel_requests(self):
        before=get_balance(self.pid,self.db)
        with ThreadPoolExecutor(2) as pool:
            result=list(pool.map(lambda _:self.batch(),range(2)))
        self.assertEqual(result[0]['batch_id'],result[1]['batch_id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)
        init_mini_db(self.db);again=self.batch();self.assertTrue(again['repeated'])
        self.assertEqual(before-get_balance(self.pid,self.db),result[0]['cost_coins'])
    def test_new_token_new_batch_and_foreign_player_cannot_read(self):
        first=self.batch();second=self.batch('another')
        self.assertNotEqual(first['batch_id'],second['batch_id'])
        with self.assertRaises(GachaError):get_gacha_batch(self.pid+1,first['batch_id'],self.db)
    def test_forced_legendary_only_first_outcome(self):
        self.sql('INSERT INTO mini_gacha_guarantees(player_id,forced_legendary) VALUES(?,1)',(self.pid,))
        result=self.batch()['results'];self.assertTrue(result[0]['forced_legendary'])
        self.assertEqual(sum(r['forced_legendary'] for r in result),1)


class RegistrationTests(MiniCase):
    def token(self,user=123):return registration.begin(self.world['id'],user,self.db)
    def activate(self,token,user=123):return registration.activate(token,user,self.db)
    def complete(self,token,user=123,name='Новый герой'):
        return registration.complete(token,user,'@new',name,self.db)
    def test_private_session_restart_starter_once_and_replay(self):
        token=self.token();self.activate(token);init_mini_db(self.db)
        self.assertEqual(registration.pending(123,self.db)['token'],token)
        player=self.complete(token);self.assertTrue(player['newly_created']);self.assertTrue(player['onboarding_granted'])
        same=self.complete(token);self.assertFalse(same['newly_created']);self.assertEqual(player['id'],same['id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_onboarding_claims WHERE telegram_user_id=123')),1)
        self.assertIsNone(registration.pending(123,self.db))
    def test_foreign_expired_and_not_activated_rejected(self):
        token=self.token()
        with self.assertRaises(ValueError):self.activate(token,321)
        with self.assertRaises(ValueError):self.complete(token)
        self.sql('UPDATE mini_registration_requests SET expires_at=0 WHERE token=?',(token,))
        with self.assertRaises(ValueError):self.activate(token)
    def test_empty_long_command_names_no_creation(self):
        token=self.token();self.activate(token)
        for name in ('',' '*3,'x'*65,'/minicreate Foo'):
            with self.assertRaises(ValueError):self.complete(token,name=name)
        self.assertEqual(self.sql('SELECT * FROM mini_players WHERE telegram_user_id=123'),[])
    def test_parallel_complete_single_player_single_gift(self):
        token=self.token();self.activate(token)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.complete(token),range(2)))
        self.assertEqual(sum(r['newly_created'] for r in results),1)
        self.assertEqual(results[0]['id'],results[1]['id'])
    def test_disabled_world_cannot_create(self):
        token=self.token();self.activate(token);self.sql('UPDATE mini_worlds SET enabled=0 WHERE id=?',(self.world['id'],))
        with self.assertRaises(ValueError):self.complete(token)


class TowerRetryTests(MiniCase):
    def lost(self):
        hero=self.hero();tower.select_hero(self.pid,hero['id'],self.db,expected_floor=1)
        attempt=tower.start_selected_attempt(self.pid,1,hero['id'],self.db)
        self.sql("UPDATE mini_tower_attempts SET status='lost',shields=0 WHERE id=?",(attempt['id'],))
        return attempt
    def test_retry_really_starts_once_after_restart(self):
        old=self.lost();init_mini_db(self.db)
        new=tower.retry_attempt(self.pid,old['id'],self.db)
        self.assertNotEqual(old['id'],new['id']);self.assertEqual(new['status'],'active');self.assertEqual(new['shields'],3)
        self.assertEqual(new['id'],tower.retry_attempt(self.pid,old['id'],self.db)['id'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_tower_attempts')),2)
        self.assertEqual(self.sql('SELECT * FROM mini_tower_rewards'),[])
    def test_parallel_retry_one_new_attempt(self):
        old=self.lost()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:tower.retry_attempt(self.pid,old['id'],self.db),range(2)))
        self.assertEqual(results[0]['id'],results[1]['id'])
    def test_active_selection_blocked_with_and_without_expected_floor(self):
        old=self.lost();tower.retry_attempt(self.pid,old['id'],self.db)
        for kwargs in ({},{'expected_floor':1}):
            with self.assertRaises(ValueError):tower.select_hero(self.pid,old['hero_id'],self.db,**kwargs)
    def test_foreign_and_victory_retry_rejected(self):
        old=self.lost()
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid+1,old['id'],self.db)
        self.sql("UPDATE mini_tower_attempts SET status='won' WHERE id=?",(old['id'],))
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid,old['id'],self.db)
    def test_retry_rechecks_availability_and_keeps_equipment_snapshot(self):
        old=self.lost()
        self.sql('INSERT INTO mini_villages(player_id,houses,cycle_started,settled_at) VALUES(?,1,0,0)',(self.pid,))
        self.sql('INSERT INTO mini_village_residents(player_id,hero_id) VALUES(?,?)',(self.pid,old['hero_id']))
        with self.assertRaises(ValueError):tower.retry_attempt(self.pid,old['id'],self.db)
        self.assertEqual(len(self.sql('SELECT * FROM mini_tower_attempts')),1)
