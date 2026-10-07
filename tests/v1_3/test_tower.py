import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini.tower.service import start_attempt,attack,get_state,list_heroes
from app.mini.tower.combat import resolve_turn
from app.mini.tower.balance import reward_band
from app.mini.equipment.service import equip,grant_in_transaction,get_equipment


def attempt(*,hp=100,attack_value=10,faction='commoners',enemy_faction='neutral',features=(),passive='none',trait='none',attack_range='melee',class_tag='warrior',turn=0,shields=3,runtime=None):
    hero=dict(attack=attack_value,faction=faction,passive_key=passive,special_trait=trait,attack_range=attack_range,class_tag=class_tag,damage_type='slashing')
    enemy=dict(name='Enemy',max_hp=hp,faction=enemy_faction,features=list(features))
    return dict(hero_json=json.dumps(hero),enemy_json=json.dumps(enemy),runtime_json=json.dumps(runtime or {}),
                turn=turn,shields=shields,current_hp=hp,equipment_bonus=0)


class PureTowerTests(MiniCase):
    def test_correct_weak_neutral_matchups(self):
        for enemy,expected in [('beasts',20),('dark',2),('commoners',10),('neutral',10)]:
            with self.subTest(enemy=enemy):
                result=resolve_turn(attempt(enemy_faction=enemy),roller=lambda _:False)
                self.assertEqual(result['damage'],expected)
                self.assertEqual(result['current_hp'],100-expected)

    def test_equipment_enters_before_matchup_and_passive(self):
        a=attempt(attack_value=35,enemy_faction='beasts',passive='strong_start');a['equipment_bonus']=20
        self.assertEqual(resolve_turn(a,roller=lambda _:False)['damage'],100) # 55 ×2 ×2 capped by HP
        a=attempt(hp=1000,attack_value=35,enemy_faction='beasts',passive='strong_start');a['equipment_bonus']=20
        self.assertEqual(resolve_turn(a,roller=lambda _:False)['damage'],220)

    def test_three_shields_loss(self):
        a=attempt(hp=1000)
        for shields in (2,1,0):
            r=resolve_turn(a,roller=lambda _:False);self.assertEqual(r['shields'],shields);a.update(r)
        self.assertEqual(r['status'],'lost')

    def test_kill_prevents_retaliation(self):
        r=resolve_turn(attempt(hp=5),roller=lambda _:False)
        self.assertEqual(r['status'],'won');self.assertEqual(r['shields'],3)

    def test_mockery_skips_enemy_action(self):
        r=resolve_turn(attempt(hp=1000,turn=2,passive='mockery'),roller=lambda _:False)
        self.assertEqual(r['shields'],3)
        self.assertIn('enemy_skip',[e['type'] for e in json.loads(r['events_json'])])

    def test_guard_created_and_consumed_in_same_turn(self):
        r=resolve_turn(attempt(hp=1000,turn=2,passive='infernal_guard'),roller=lambda _:False)
        self.assertEqual(r['shields'],3)
        self.assertEqual(json.loads(r['runtime_json'])['hero']['reward_guard_charges'],0)

    def test_echo_consumed_without_retriggering_pipeline(self):
        a=attempt(hp=1000,turn=2,passive='battle_echo',runtime={'hero':{'reward_guard_charges':1}})
        a.update(resolve_turn(a,roller=lambda _:False))
        self.assertEqual(json.loads(a['runtime_json'])['hero']['pending_echo_damage'],5)
        r=resolve_turn(a,roller=lambda _:False)
        self.assertEqual(r['damage'],15)
        self.assertNotIn('pending_echo_damage',json.loads(r['runtime_json'])['hero'])

    def test_frenzy_extra_hit_does_not_increment_turn(self):
        r=resolve_turn(attempt(hp=1000,turn=3,passive='blood_frenzy'),roller=lambda _:False)
        self.assertEqual(r['turn'],4);self.assertEqual(r['damage'],24)

    def test_flying_requires_ranged_or_flying_hero(self):
        self.assertEqual(resolve_turn(attempt(features=['flying']),roller=lambda _:False)['damage'],0)
        for kwargs in [{'attack_range':'ranged'},{'trait':'flying'}]:
            self.assertEqual(resolve_turn(attempt(features=['flying'],**kwargs),roller=lambda _:False)['damage'],10)

    def test_armored_reduces_physical_melee(self):
        self.assertEqual(resolve_turn(attempt(features=['armored']),roller=lambda _:False)['damage'],8)

    def test_undead_revives_only_once(self):
        a=attempt(hp=100,attack_value=100,features=['undead'])
        r=resolve_turn(a,roller=lambda _:False)
        self.assertEqual(r['status'],'active');self.assertEqual(r['current_hp'],25)
        a.update(r);r=resolve_turn(a,roller=lambda _:False)
        self.assertEqual(r['status'],'won')

    def test_construct_regeneration_and_technical_counter(self):
        r=resolve_turn(attempt(features=['construct'],attack_value=20),roller=lambda _:False)
        self.assertEqual(r['current_hp'],90)
        r=resolve_turn(attempt(features=['construct'],attack_value=20,class_tag='technical'),roller=lambda _:False)
        self.assertEqual(r['current_hp'],80)

    def test_holy_regeneration_and_legacy_demon_counter(self):
        r=resolve_turn(attempt(features=['holy'],attack_value=30),roller=lambda _:False)
        self.assertEqual(r['current_hp'],95)
        r=resolve_turn(attempt(features=['holy'],attack_value=30,trait='demon'),roller=lambda _:False)
        self.assertEqual(r['current_hp'],70)

    def test_hero_holy_blinds_undead(self):
        r=resolve_turn(attempt(features=['undead'],trait='holy'),roller=lambda _:True)
        self.assertEqual(r['shields'],3)

    def test_hero_poison_deals_hp_damage(self):
        r=resolve_turn(attempt(trait='poisonous'),roller=lambda _:True)
        self.assertEqual(r['current_hp'],88)

    def test_construct_hero_recovers_shield(self):
        r=resolve_turn(attempt(trait='construct',shields=2),roller=lambda _:True)
        self.assertEqual(r['shields'],2)

    def test_echo_kill_does_not_trigger_primary_kill_passive(self):
        a=attempt(hp=10,passive='execution_protocol',turn=1,runtime={'hit_count':1,'hero':{'pending_echo_damage':10}})
        with patch('app.mini.tower.combat.abilities.resolve_kill',side_effect=AssertionError('echo has no kill hook')):
            r=resolve_turn(a,roller=lambda _:True)
        self.assertEqual(r['status'],'won');self.assertEqual(json.loads(r['runtime_json'])['hit_count'],1)

    def test_shields_zero_loses_before_poison_tick(self):
        a=attempt(hp=10,attack_value=9,trait='poisonous',shields=1)
        r=resolve_turn(a,roller=lambda _:True)
        self.assertEqual(r['status'],'lost');self.assertEqual(r['current_hp'],1)

    def test_kill_passive_banked_only_on_primary_victory(self):
        r=resolve_turn(attempt(hp=5,passive='execution_protocol'),roller=lambda _:True)
        self.assertEqual(json.loads(r['runtime_json'])['bonus_shards'],30)

    def test_combat_shard_proc_banked_until_victory(self):
        r=resolve_turn(attempt(hp=100,passive='emergency_salvage'),roller=lambda _:True)
        self.assertEqual(json.loads(r['runtime_json'])['bonus_shards'],2)
        self.assertEqual(r['status'],'active')

    def test_preparation_allows_real_fourth_turn_echo_and_frenzy(self):
        for passive in ('battle_echo','blood_frenzy'):
            a=attempt(hp=45 if passive=='battle_echo' else 55,attack_value=10,passive=passive)
            enemy=json.loads(a['enemy_json']);enemy['response']='prepare';a['enemy_json']=json.dumps(enemy)
            for _ in range(4):a.update(resolve_turn(a,roller=lambda _:False))
            self.assertEqual(a['status'],'won',passive)
            types=[e['type'] for e in json.loads(a['events_json'])]
            self.assertIn('echo' if passive=='battle_echo' else 'extra_attack',types)

    def test_preparation_is_one_persistent_enemy_action(self):
        a=attempt(hp=1000);enemy=json.loads(a['enemy_json']);enemy['response']='prepare';a['enemy_json']=json.dumps(enemy)
        first=resolve_turn(a,roller=lambda _:False);self.assertEqual(first['shields'],3)
        a.update(first);second=resolve_turn(a,roller=lambda _:False);self.assertEqual(second['shields'],2)
        self.assertEqual(json.loads(second['runtime_json'])['enemy_actions'],2)


class TowerPersistenceTests(MiniCase):
    def start(self,code='Villager',stars=0,floor=1):
        hero=self.hero(code,stars);return start_attempt(self.pid,hero['id'],floor,self.db)

    def hit(self,a,**kwargs):
        return attack(self.pid,a['id'],a['turn'],self.db,roller=lambda _:False,**kwargs)

    def test_stars_match_existing_combat(self):
        a=self.start(stars=2)
        self.assertEqual(json.loads(a['hero_json'])['attack'],6)

    def test_start_disallows_unowned_and_duplicate_attempt(self):
        with self.assertRaises(ValueError):start_attempt(self.pid,99999,1,self.db)
        a=self.start()
        with self.assertRaises(ValueError):start_attempt(self.pid,a['hero_id'],1,self.db)
        self.assertEqual(len(self.sql("SELECT * FROM mini_tower_attempts WHERE status='active'")),1)

    def test_loss_retry_resets_shields_and_changes_hero(self):
        self.floor(40);a=self.start(floor=41)
        for _ in range(3):a=self.hit(a)['attempt']
        self.assertEqual(a['status'],'lost')
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))[0]['shards'],0)
        other=self.hero('CityBlacksmith');b=start_attempt(self.pid,other['id'],41,self.db)
        self.assertEqual(b['shields'],3);self.assertNotEqual(a['hero_id'],b['hero_id'])
        self.assertEqual(get_state(self.pid,self.db)['highest_cleared'],40)

    def test_restart_restores_active_attempt(self):
        self.floor(40);a=self.start(floor=41);a=self.hit(a)['attempt'];init_mini_db(self.db)
        restored=get_state(self.pid,self.db)['attempt']
        self.assertEqual(restored,a if 'damage' not in a else {k:v for k,v in a.items() if k not in {'damage','faction_percent'}})
        self.assertEqual(self.hit(restored)['attempt']['turn'],2)

    def test_shards_and_progress_awarded_once_no_coins(self):
        a=self.start('Lasar');result=self.hit(a)
        self.assertEqual(result['attempt']['status'],'won')
        self.assertEqual(result['reward']['shards'],2);self.assertIsNone(result['reward']['equipment'])
        self.assertEqual(get_state(self.pid,self.db)['highest_cleared'],1)
        self.assertEqual(self.sql('SELECT coins FROM mini_players WHERE id=?',(self.pid,))[0]['coins'],0)
        with self.assertRaises(ValueError):self.hit(a)
        self.assertEqual(len(self.sql('SELECT * FROM mini_tower_rewards')),1)
        with self.assertRaises(ValueError):start_attempt(self.pid,a['hero_id'],1,self.db)

    def test_duplicate_attack_callback_cannot_advance_two_turns(self):
        self.floor(40);a=self.start(floor=41);self.hit(a)
        with self.assertRaises(ValueError):self.hit(a)
        self.assertEqual(get_state(self.pid,self.db)['attempt']['turn'],1)

    def test_concurrent_attack_callback_commits_once(self):
        a=self.start('Lasar')
        def hit():
            try:return self.hit(a)['attempt']['status']
            except ValueError:return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:hit(),range(2)))
        self.assertCountEqual(results,['won','stale'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_tower_rewards')),1)

    def test_milestone_chest_uses_early_band_and_persists(self):
        self.floor(4);a=self.start('Lasar',floor=5);r=self.hit(a)['reward'];e=r['equipment']
        self.assertLessEqual(e['attack_bonus'],12)
        self.assertEqual(len(get_equipment(self.pid,self.db)['owned']),1)
        init_mini_db(self.db);self.assertEqual(get_state(self.pid,self.db)['highest_cleared'],5)
        with self.assertRaises(ValueError):self.hit(a)
        self.assertEqual(len(get_equipment(self.pid,self.db)['owned']),1)

    def test_chest_avoids_owned_when_unseen_pool_exists(self):
        with connect_mini_db(self.db) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE');grant_in_transaction(c,self.pid,'eq_cloak_001')
        self.floor(4);r=self.hit(self.start('Lasar',floor=5),chooser=lambda pool:pool[0])['reward']
        self.assertNotEqual(r['equipment']['code'],'eq_cloak_001')

    def test_exhausted_chest_pool_keeps_duplicate_without_shard_conversion(self):
        with connect_mini_db(self.db) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
            for slot in ('helmet','ring','cloak'):
                for b in range(1,13):grant_in_transaction(c,self.pid,f'eq_{slot}_{b:03}')
        self.floor(4);r=self.hit(self.start('Lasar',floor=5),chooser=lambda pool:pool[0])['reward']
        self.assertEqual(r['shards'],2)
        self.assertEqual(sum(e['quantity'] for e in get_equipment(self.pid,self.db)['owned']),37)

    def test_reward_failure_rolls_back_attack_shards_claim_and_progress(self):
        self.floor(4);a=self.start('Lasar',floor=5)
        with patch('app.mini.tower.service.grant_in_transaction',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):self.hit(a)
        self.assertEqual(get_state(self.pid,self.db)['highest_cleared'],4)
        self.assertEqual(get_state(self.pid,self.db)['attempt']['turn'],0)
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))[0]['shards'],0)
        self.assertEqual(self.sql('SELECT * FROM mini_tower_rewards'),[])
        self.assertEqual(self.hit(a)['attempt']['status'],'won')

    def test_read_paths_do_not_sync_catalog_or_migrate(self):
        self.hero()
        with patch('app.mini.schema.init_mini_db',side_effect=AssertionError),patch('app.mini.heroes.sync_hero_catalog',side_effect=AssertionError):
            self.assertEqual(len(list_heroes(self.pid,self.db)),1);get_state(self.pid,self.db)

    def test_full_200_floor_cycle_40_chests_no_201(self):
        hero=self.hero('Lasar',stars=20)
        for f in range(1,201):
            a=start_attempt(self.pid,hero['id'],f,self.db)
            while a['status']=='active':a=self.hit(a)['attempt']
            self.assertEqual(a['status'],'won',f)
        state=get_state(self.pid,self.db)
        self.assertTrue(state['completed']);self.assertIsNone(state['floor'])
        rewards=self.sql('SELECT * FROM mini_tower_rewards')
        self.assertEqual(len(rewards),200)
        self.assertEqual(sum(r['equipment_code'] is not None for r in rewards),40)
        self.assertEqual(sum(r['shards'] for r in rewards),1100)
        with self.assertRaises(ValueError):start_attempt(self.pid,hero['id'],201,self.db)

    def test_passive_shards_are_not_paid_on_loss_or_retry(self):
        self.floor(139);a=self.start('panic_dungeon_engineer',floor=140)
        while a['status']=='active':a=attack(self.pid,a['id'],a['turn'],self.db,roller=lambda _:True)['attempt']
        self.assertEqual(a['status'],'lost')
        self.assertGreater(json.loads(a['runtime_json'])['bonus_shards'],0)
        self.assertEqual(self.sql('SELECT shards FROM mini_players WHERE id=?',(self.pid,))[0]['shards'],0)
        self.assertEqual(self.sql('SELECT * FROM mini_tower_rewards'),[])

    def test_hero_attack_and_traits_are_frozen_for_active_attempt(self):
        self.floor(40);a=self.start('Villager',floor=41)
        self.sql('UPDATE mini_player_heroes SET stars=4 WHERE player_id=?',(self.pid,))
        self.sql("UPDATE mini_heroes SET attack=1000,special_trait='flying' WHERE id=?",(a['hero_id'],))
        restored=get_state(self.pid,self.db)['attempt']
        hero=json.loads(restored['hero_json'])
        self.assertEqual(hero['attack'],3);self.assertEqual(hero['special_trait'],'none')

    def test_200th_floor_win_survives_restart_without_duplicate_reward(self):
        self.floor(199);a=self.start('Lasar',stars=20,floor=200)
        result=self.hit(a);self.assertEqual(result['attempt']['status'],'won')
        init_mini_db(self.db)
        self.assertTrue(get_state(self.pid,self.db)['completed'])
        with self.assertRaises(ValueError):self.hit(a)
        self.assertEqual(len(self.sql('SELECT * FROM mini_tower_rewards')),1)


class TowerBalanceTests(MiniCase):
    def test_counterpick_rare_can_win_where_legendary_wrong_faction_loses(self):
        self.floor(139)
        legendary=self.hero('Lasar',stars=3);rare=self.hero('witcher_isekai',stars=5)
        # A late-stage +200 loadout reachable in the configured reward bands.
        with connect_mini_db(self.db) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
            for slot,bonus in [('helmet',65),('ring',65),('cloak',70)]:
                grant_in_transaction(c,self.pid,f'eq_{slot}_{bonus:03}')
        for slot,bonus in [('helmet',65),('ring',65),('cloak',70)]:equip(self.pid,f'eq_{slot}_{bonus:03}',self.db)
        results=[]
        for h in (legendary,rare):
            a=start_attempt(self.pid,h['id'],140,self.db)
            while a['status']=='active':a=attack(self.pid,a['id'],a['turn'],self.db,roller=lambda _:False)['attempt']
            results.append(a['status'])
        self.assertEqual(results,['lost','won'])
