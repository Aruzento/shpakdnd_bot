import json
import unittest
from tests.v1_3 import test_tower as fixtures
from tests.v1_3.support import MiniCase
from app.mini.tower.combat import resolve_turn
from app.mini.tower.service import start_attempt, attack, get_state
from app.mini.schema import init_mini_db


def attempt(**kwargs):
    a=fixtures.attempt(**kwargs)
    runtime=json.loads(a['runtime_json']);runtime['class_rules_version']=1
    a['runtime_json']=json.dumps(runtime)
    return a


class TowerClassTests(unittest.TestCase):
    def test_warrior_stars_attack_plus_equipment_before_faction_passive(self):
        a=attempt(hp=10000,attack_value=80,enemy_faction='beasts',passive='strong_start')
        a['equipment_bonus']=20
        self.assertEqual(resolve_turn(a,roller=lambda _:False)['damage'],420)
        self.assertEqual(json.loads(a['hero_json'])['attack'],80)
        self.assertEqual(a['equipment_bonus'],20)

    def test_old_runtime_keeps_legacy_attack_and_no_beast_progression(self):
        for kind in ('warrior','beast'):
            a=fixtures.attempt(hp=10000,attack_value=100,class_tag=kind)
            result=resolve_turn(a,roller=lambda _:False)
            self.assertEqual(result['damage'],100)
            self.assertNotIn('class_successful_hits',json.loads(result['runtime_json'])['hero'])

    def test_beast_tenth_hit_linear_and_only_ordinary_hit_counts(self):
        a=attempt(hp=10000,attack_value=100,class_tag='beast',runtime={'hero':{'reward_guard_charges':20}})
        for n in range(10):
            result=resolve_turn(a,roller=lambda _:False)
            self.assertEqual(result['damage'],100+n*2)
            self.assertEqual(json.loads(result['runtime_json'])['hero']['class_successful_hits'],n+1)
            a.update(result)

    def test_beast_miss_no_progress_ranged_or_flying_reaches(self):
        for reach in ('none','ranged','flying'):
            a=attempt(hp=10000,attack_value=100,class_tag='beast',features=['flying'],
                      attack_range='ranged' if reach=='ranged' else 'melee',trait='flying' if reach=='flying' else 'none')
            result=resolve_turn(a,roller=lambda _:True)
            hits=json.loads(result['runtime_json'])['hero'].get('class_successful_hits',0)
            self.assertEqual((result['damage'],hits),(0,0) if reach=='none' else (100,1))

    def test_guardian_restore_one_capped_and_not_on_miss(self):
        for shields,flying,expected in ((2,False,3),(3,False,3),(2,True,2)):
            a=attempt(hp=10000,class_tag='guardian',shields=shields,features=['flying'] if flying else [])
            enemy=json.loads(a['enemy_json']);enemy['response']='prepare';a['enemy_json']=json.dumps(enemy)
            result=resolve_turn(a,roller=lambda _:True)
            self.assertEqual(result['shields'],expected)
            procs=[e for e in json.loads(result['events_json']) if e['type']=='class_guardian']
            self.assertEqual(len(procs),int(shields<3 and not flying))

    def test_extra_echo_poison_do_not_advance_beast_multiple_times(self):
        for passive,turn in [('blood_frenzy',3),('battle_echo',0),('none',0)]:
            a=attempt(hp=10000,attack_value=100,class_tag='beast',passive=passive,turn=turn,trait='poisonous',
                      runtime={'hero':{'pending_echo_damage':50,'reward_guard_charges':1}})
            result=resolve_turn(a,roller=lambda _:True)
            self.assertEqual(json.loads(result['runtime_json'])['hero']['class_successful_hits'],1)
            types=[e['type'] for e in json.loads(result['events_json'])]
            self.assertIn('echo',types)
            self.assertIn('poison_tick',types)
            if passive=='blood_frenzy':self.assertIn('extra_attack',types)

    def test_secondary_extra_attack_does_not_get_warrior_input_bonus(self):
        a=attempt(hp=10000,attack_value=100,passive='blood_frenzy',turn=3)
        events=json.loads(resolve_turn(a,roller=lambda _:False)['events_json'])
        # Existing frenzy's fourth ordinary strike is 145%, then a separate unbuffed hit.
        self.assertEqual(next(e['damage'] for e in events if e['type']=='extra_attack'),100)
        self.assertEqual(next(e['damage'] for e in events if e['type']=='attack'),152)

    def test_technical_only_blocks_construct_regeneration(self):
        for kind,expected in [('none',1000),('technical',900)]:
            a=attempt(hp=1000,attack_value=100,class_tag=kind,features=['construct'])
            result=resolve_turn(a,roller=lambda _:False)
            self.assertEqual(result['current_hp'],expected)
            self.assertEqual(result['shields'],2)

    def test_coin_class_effects_do_not_invent_tower_coin_pool(self):
        for kind in ('sneaky','healer'):
            result=resolve_turn(attempt(hp=10000,class_tag=kind),roller=lambda _:True)
            self.assertFalse(any(e['type'] in ('class_sneaky','class_healer') for e in json.loads(result['events_json'])))
            self.assertNotIn('class_stolen_coins',json.loads(result['runtime_json'])['hero'])

    def test_new_battle_resets_beast_and_undead_death_does_not_double_count(self):
        a=attempt(hp=50,attack_value=100,class_tag='beast',features=['undead'])
        result=resolve_turn(a,roller=lambda _:False)
        self.assertEqual(result['status'],'active')
        self.assertEqual(json.loads(result['runtime_json'])['hero']['class_successful_hits'],1)
        a.update(result);result=resolve_turn(a,roller=lambda _:False)
        self.assertEqual(result['status'],'won')
        self.assertEqual(json.loads(result['runtime_json'])['hero']['class_successful_hits'],2)
        fresh=resolve_turn(attempt(hp=1000,attack_value=100,class_tag='beast'),roller=lambda _:False)
        self.assertEqual(fresh['damage'],100)


class TowerClassPersistenceTests(MiniCase):
    def test_beast_runtime_and_frozen_hero_survive_reopen_and_catalog_change(self):
        h=self.hero()
        self.sql("UPDATE mini_heroes SET class_tag='beast',attack=100,passive_key='none' WHERE id=?",(h['id'],))
        a=start_attempt(self.pid,h['id'],1,self.db)
        enemy=json.loads(a['enemy_json']);enemy.update(max_hp=10000,features=[],faction='neutral')
        self.sql('UPDATE mini_tower_attempts SET enemy_json=?,current_hp=10000 WHERE id=?',(json.dumps(enemy),a['id']))
        result=attack(self.pid,a['id'],0,self.db,roller=lambda _:False)['attempt']
        self.assertEqual(json.loads(result['runtime_json'])['hero']['class_successful_hits'],1)
        self.sql("UPDATE mini_heroes SET attack=9999,class_tag='warrior' WHERE id=?",(h['id'],))
        self.sql('UPDATE mini_player_heroes SET stars=5 WHERE player_id=?',(self.pid,))
        init_mini_db(self.db)
        restored=get_state(self.pid,self.db)['attempt']
        self.assertEqual(restored['hero_json'],a['hero_json'])
        self.assertEqual(restored['runtime_json'],result['runtime_json'])
        second=attack(self.pid,a['id'],1,self.db,roller=lambda _:False)['attempt']
        events=json.loads(second['events_json'])
        self.assertEqual(next(e['damage'] for e in events if e['type']=='attack'),102)
        self.assertEqual(json.loads(second['runtime_json'])['hero']['class_successful_hits'],2)
        self.assertEqual(second['shields'],1)
