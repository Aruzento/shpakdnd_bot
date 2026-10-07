import json
from concurrent.futures import ThreadPoolExecutor
from app.mini.duels import service as d
from app.mini.duels.combat import resolve
from app.mini.players import create_mini_player
from app.mini.wallet import add_coins,get_balance
from app.mini.schema import init_mini_db
from app.mini.village import service as v
from tests.v1_3.support import MiniCase


class DuelTests(MiniCase):
    def setUp(self):
        super().setUp();self.other=create_mini_player(self.world['id'],991117,'@rival','Соперник',self.db)['id']
        self.hero1=self.hero('Villager');self.hero2=self.hero('olaf_taliy')
        self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.other,self.hero2['id']))
        for pid in (self.pid,self.other):add_coins(pid,100,'Тест',db_path=self.db)
    def challenge(self,key='one',hero=None,now=1000):
        return d.challenge(self.pid,self.other,(hero or self.hero1)['id'],key,self.db,now=now)
    def select(self,duel,hid=None,now=1000):
        return d.act(duel['id'],self.other,'select',self.db,hero_id=hid or self.hero2['id'],now=now)
    def accept(self,duel,now=1000):return d.act(duel['id'],self.other,'accept',self.db,now=now)

    def test_reserve_refuse_and_double_refund(self):
        duel=self.challenge();self.assertEqual(get_balance(self.pid,self.db),90)
        d.act(duel['id'],self.other,'refuse',self.db,now=1001)
        self.assertEqual(get_balance(self.pid,self.db),100)
        self.assertFalse(d.act(duel['id'],self.other,'refuse',self.db,now=1002)['applied'])
        self.assertEqual(get_balance(self.pid,self.db),100)

    def test_challenge_idempotency_and_payload_conflict(self):
        first=self.challenge();again=self.challenge()
        self.assertEqual(first['id'],again['id']);self.assertFalse(again['applied'])
        with self.assertRaises(ValueError):self.challenge(hero=self.hero2)
        self.assertEqual(get_balance(self.pid,self.db),90)

    def test_expiry_exact_boundary_after_restart(self):
        duel=self.challenge();init_mini_db(self.db)
        self.assertEqual(d.expire(self.db,now=1119),0)
        self.assertEqual(d.expire(self.db,now=1120),1)
        self.assertEqual(d.expire(self.db,now=1120),0)
        self.assertEqual(self.accept(duel,now=1121)['status'],'expired')
        self.assertEqual(get_balance(self.pid,self.db),100)

    def test_insufficient_challenger_rolls_back_all_state(self):
        self.sql('UPDATE mini_players SET coins=9 WHERE id=?',(self.pid,))
        with self.assertRaises(ValueError):self.challenge()
        self.assertEqual(self.sql('SELECT * FROM mini_duels'),[]);self.assertEqual(self.sql('SELECT * FROM mini_duel_locks'),[])

    def test_insufficient_defender_refunds_challenger(self):
        duel=self.challenge();self.select(duel);self.sql('UPDATE mini_players SET coins=9 WHERE id=?',(self.other,))
        self.assertEqual(self.accept(duel)['status'],'insufficient')
        self.assertEqual(get_balance(self.pid,self.db),100);self.assertEqual(get_balance(self.other,self.db),9)

    def test_accept_win_bank_payout_and_no_double_shards(self):
        duel=self.challenge();self.select(duel);result=self.accept(duel)
        self.assertEqual(result['status'],'finished');self.assertEqual(json.loads(result['result_json'])['winner_id'],self.other)
        self.assertEqual(get_balance(self.pid,self.db),90);self.assertEqual(get_balance(self.other,self.db),107)
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))[0]['shards'],3)
        self.assertFalse(self.accept(duel)['applied']);self.assertEqual(get_balance(self.other,self.db),107)
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))[0]['shards'],3)

    def test_draw_refunds_both_no_shards(self):
        self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.other,self.hero1['id']))
        duel=self.challenge();self.select(duel,self.hero1['id']);result=self.accept(duel)
        self.assertTrue(json.loads(result['result_json'])['draw'])
        self.assertEqual(get_balance(self.pid,self.db),100);self.assertEqual(get_balance(self.other,self.db),100)
        self.assertTrue(all(r['shards']==0 for r in self.sql('SELECT shards FROM mini_players')))

    def test_simultaneous_round_draw_with_equal_attack(self):
        self.assertTrue(resolve({'attack':3},{'attack':3})['draw'])
        self.assertEqual(len(resolve({'attack':3},{'attack':3})['rounds']),3)

    def test_snapshot_frozen_challenger_before_accept(self):
        duel=self.challenge();self.sql('UPDATE mini_heroes SET attack=1000 WHERE id=?',(self.hero1['id'],))
        self.select(duel);self.accept(duel)
        self.assertEqual(get_balance(self.other,self.db),107)

    def test_defender_can_change_until_accept_and_stale_accept_rejected(self):
        self.sql('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(self.other,self.hero1['id']))
        duel=self.challenge();self.select(duel,self.hero1['id']);self.select(duel)
        with self.assertRaises(ValueError):d.act(duel['id'],self.other,'accept',self.db,expected_hero_id=self.hero1['id'],now=1000)
        self.assertEqual(get_balance(self.other,self.db),100)

    def test_only_defender_can_accept_select_or_refuse(self):
        duel=self.challenge()
        for action in ('select','accept','refuse'):
            with self.assertRaises(ValueError):d.act(duel['id'],self.pid,action,self.db,hero_id=self.hero2['id'],now=1000)
        with self.assertRaises(ValueError):d.act(duel['id'],self.other,'cancel',self.db,now=1000)

    def test_one_pending_per_player_in_either_role(self):
        self.challenge()
        with self.assertRaises(ValueError):d.challenge(self.other,self.pid,self.hero2['id'],'other',self.db,now=1000)
        with self.assertRaises(ValueError):self.challenge(key='second')

    def test_cancel_and_new_duel_has_no_cooldown(self):
        duel=self.challenge();d.act(duel['id'],self.pid,'cancel',self.db,now=1000)
        self.assertTrue(self.challenge(key='next')['applied'])

    def test_village_resident_unavailable_and_pending_hero_cannot_move_in(self):
        v.mutate(self.pid,'buy',self.db,operation_key='buy',expected_houses=0,now=1000)
        v.mutate(self.pid,'settle',self.db,hero_id=self.hero1['id'],operation_key='settle',now=1000)
        with self.assertRaises(ValueError):self.challenge()
        v.mutate(self.pid,'evict',self.db,hero_id=self.hero1['id'],operation_key='evict',now=1000)
        self.challenge()
        with self.assertRaises(ValueError):v.mutate(self.pid,'settle',self.db,hero_id=self.hero1['id'],operation_key='settle2',now=1000)

    def test_concurrent_accept_pays_once(self):
        duel=self.challenge();self.select(duel)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.accept(duel),range(2)))
        self.assertEqual(sum(r['applied'] for r in results),1)
        self.assertEqual(get_balance(self.other,self.db),107)

    def test_concurrent_challenge_reserves_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.challenge(),range(2)))
        self.assertEqual(sum(r['applied'] for r in results),1);self.assertEqual(get_balance(self.pid,self.db),90)

    def test_resolver_ignores_boss_specific_tags(self):
        plain=resolve({'attack':10},{'attack':30})
        tagged=resolve({'attack':10,'passive_key':'bitter','special_trait':'armored','faction':'dark'}, {'attack':30,'special_trait':'flying'})
        self.assertEqual(plain,tagged)


    def test_admin_cannot_delete_pending_duel_challenger_or_defender_hero(self):
        from unittest.mock import patch
        from app.mini.superadmin.parser import parse_command
        from app.mini.superadmin.service import execute
        duel=self.challenge();self.select(duel)
        scope=f"{self.world['chat_id']}:{self.world['thread_id']}"
        for target,code in ((900123,'Villager'),(991117,'olaf_taliy')):
            command=parse_command(f'/superdel {scope} {target} -p {code}')
            with patch('app.mini.village.service.timestamp',return_value=1000),self.assertRaisesRegex(ValueError,'дуэли'):
                execute(self.owner,command,operation_key='delete:'+code,db_path=self.db)
        self.assertEqual(get_balance(self.pid,self.db),90)
        self.assertEqual(self.accept(duel)['status'],'finished')
