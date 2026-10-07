import copy
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.calculations import calculate_hit
from app.mini.boss.boss_abilities import engine as bosses
from app.mini.boss.boss_abilities.catalog import validate_config
from app.mini.combat.hero_abilities import engine as heroes
from app.mini.combat.hero_abilities import catalog as hero_catalog
from app.mini.boss.combat import hit_boss,start_battle
from app.mini.boss.service import create_boss_event,register_player,close_registration,get_boss
from app.mini.boss.runtime import boss_turn,boss_hits_reward
from app.mini.boss.repository import refresh_boss
from app.mini.boss.schema import init_boss_db
from app.mini.db import connect_mini_db
from tests.v1_3.support import MiniCase


def snapshot(key='none',**kwargs):
    return dict(faction='warriors',damage_type='slashing',class_tag='warrior',attack_range='ranged',
                special_trait='none',rarity='common',passive_key=key,**kwargs)


def boss_state(key):
    return dict(ability_key=key,ability_config_json='{}',ability_state_json='{}',faction='commoners',
                features_json='[]',feature_state_json='{}',trait_rules_version=1,class_rules_version=1,
                max_hp=1000,current_hp=500,reward_decay_percent=10,status='fighting')


class BossMathTests(unittest.TestCase):
    def hit(self,key,rarity,attack=200,**changes):
        boss=boss_state(key);boss.update(changes)
        hero=snapshot();hero['rarity']=rarity
        return calculate_hit(boss,dict(attack=attack,hit_count=0,damage_bonus_percent=100,hero_state_json='{}'),hero)

    def test_inversion_all_six_rarities_and_capped_healing(self):
        for rarity,damage in (('common',300),('uncommon',250),('rare',50),('legendary',2),('shadow',0)):
            with self.subTest(rarity=rarity):self.assertEqual(self.hit('collapse',rarity)['damage'],damage)
        heal=self.hit('collapse','mythic');self.assertEqual((heal['damage'],heal['hp_after']),(0,700))
        self.assertEqual(self.hit('collapse','mythic',current_hp=990)['hp_after'],1000)
        self.assertEqual(self.hit('collapse','legendary',attack=20)['damage'],0)

    def test_schoolboy_rejects_exact_rarities_and_keeps_normal_reach(self):
        for rarity in ('rare','legendary','mythic'):self.assertEqual(self.hit('waste_of_time',rarity)['damage'],0)
        for rarity in ('common','uncommon','shadow'):self.assertGreater(self.hit('waste_of_time',rarity)['damage'],0)
        b=boss_state('waste_of_time');b['features_json']='["flying"]';hero=snapshot();hero['attack_range']='melee'
        self.assertEqual(calculate_hit(b,dict(attack=200,hit_count=0,damage_bonus_percent=0),hero)['damage'],0)

    def test_dummy_each_real_hit_one_even_with_passive_and_modifiers(self):
        for rarity in ('common','uncommon','rare','legendary','shadow','mythic'):
            self.assertEqual(self.hit('training',rarity,attack=100000)['damage'],1)
        b=boss_state('training');h=snapshot('bitter');p=dict(attack=46,hit_count=1,damage_bonus_percent=999)
        self.assertEqual(calculate_hit(b,p,h)['damage'],1)
        self.assertEqual(calculate_hit(b,p,h,extra_percent=50)['damage'],1)
        self.assertEqual(bosses.boss_turn(b,[])['reward_attacks'],0)

    def test_simple_preserves_own_passive_ignores_faction_armor_flying_and_potion(self):
        b=boss_state('simple');b['features_json']='["armored","flying"]'
        h=snapshot('bitter');h['attack_range']='melee'
        p=dict(attack=46,hit_count=1,damage_bonus_percent=999)
        for faction in ('commoners','beasts','dark','warriors','monsters','neutral'):
            b['faction']=faction;self.assertEqual(calculate_hit(b,p,h)['damage'],96)
        self.assertEqual(calculate_hit(b,p,h,extra_percent=50)['damage'],23)

    def test_decoy_bounded_consumed_on_success_or_failure(self):
        state={}
        for n in range(1,9):state=heroes.resolve_after_attack('bone_decoy',hit_number=n,actual_hp_damage=1,state=state)['state']
        self.assertEqual(state,{'bone_decoy':True})
        for success in (True,False):
            calls=[]
            result=heroes.resolve_reward_defense('bone_decoy',state=state,shields=3,roller=lambda chance:calls.append(chance) or success)
            self.assertEqual(result['blocked'],success);self.assertNotIn('bone_decoy',result['state']);self.assertEqual(calls,[25])

    def test_last_watch_success_once_failed_attempt_retry_and_no_reward_hp_save(self):
        state={}
        failed=heroes.resolve_reward_defense('last_watch',state=state,shields=1,roller=lambda _:False)
        self.assertNotIn('last_watch_used',failed['state'])
        saved=heroes.resolve_reward_defense('last_watch',state=failed['state'],shields=1,roller=lambda chance:chance==35)
        self.assertTrue(saved['blocked'])
        roll=lambda _:self.fail('must not roll')
        self.assertFalse(heroes.resolve_reward_defense('last_watch',state=saved['state'],shields=1,roller=roll)['blocked'])
        self.assertFalse(heroes.resolve_reward_defense('last_watch',state={},shields=0,roller=roll)['blocked'])

    def test_bitter_every_second_own_attack_and_olaf_every_third(self):
        for n in range(1,7):
            bitter=heroes.resolve_attack('bitter',base_damage=46,hit_number=n,boss_hp_before=200,boss_max_hp=1000)
            self.assertEqual(bitter['damage'],96 if n%2==0 else 46)
            olaf=heroes.resolve_attack('thawing_frenzy',base_damage=150,hit_number=n,boss_hp_before=1000,boss_max_hp=1000)
            self.assertEqual(olaf['extra_attacks'],2 if n%3==0 else 0);self.assertEqual(olaf['extra_attack_percent'],50 if n%3==0 else 100)

    def test_seal_blocks_active_then_disappears_ordinary_attack_retained(self):
        for key in ('critical_strike','paralysis','rapier','banishment'):
            b=boss_state(key);b['ability_state_json']='{"grave_seal":true,"boss_turns":2}'
            result=bosses.boss_turn(b,[{'player_id':1},{'player_id':2}],roller=lambda _:True)
            self.assertEqual(result['reward_attacks'],1);self.assertFalse(result['ignore_shields']);self.assertEqual(result['participant_changes'],[])
            self.assertNotIn('grave_seal',json.loads(result['boss_changes']['ability_state_json']))
        b=boss_state('collapse');b['ability_state_json']='{"grave_seal":true}'
        result=bosses.boss_turn(b,[]);self.assertTrue(json.loads(result['boss_changes']['ability_state_json'])['grave_seal'])

    def test_seal_suppresses_next_transformation_at_end_not_passive_trait(self):
        b=boss_state('transformation');b['ability_state_json']='{"grave_seal":true}'
        result=bosses.after_boss_turn(b,[dict(hit_count=1,hero_snapshot_json=json.dumps(snapshot()))])
        self.assertNotIn('form',json.loads(result['boss_changes']['ability_state_json']))
        self.assertNotIn('grave_seal',json.loads(result['boss_changes']['ability_state_json']))
        b=boss_state('none');b['features_json']='["armored"]';b['ability_state_json']='{"grave_seal":true}'
        self.assertEqual(self.hit('simple','common',attack=20,features_json='["armored"]')['damage'],20)
        self.assertEqual(bosses.after_boss_turn(b)['boss_changes'],{})

    def test_transform_only_attackers_exact_fields_replaces_form_hp_unchanged(self):
        b=boss_state('transformation')
        candidates=[dict(hit_count=0,hero_snapshot_json=json.dumps(snapshot())),dict(hit_count=1,hero_snapshot_json=json.dumps({**snapshot('bitter'),'stars':100,'attack':9999,'owner':42}))]
        chooser=lambda rows:rows[0]
        result=bosses.after_boss_turn(b,candidates,chooser=chooser)
        form=json.loads(result['boss_changes']['ability_state_json'])['form']
        self.assertEqual(form,{'faction':'warriors','special_trait':'none','passive_key':'bitter'})
        self.assertNotIn('current_hp',result['boss_changes']);self.assertNotIn('max_hp',result['boss_changes'])
        b.update(result['boss_changes']);candidates[1]['hero_snapshot_json']=json.dumps({**snapshot('none'),'faction':'beasts','special_trait':'armored'})
        result=bosses.after_boss_turn(b,candidates,chooser=chooser)
        self.assertEqual(json.loads(result['boss_changes']['ability_state_json'])['form']['passive_key'],'none')

    def test_copied_attack_passive_uses_boss_damage_not_hero_attack(self):
        b=boss_state('transformation');b['ability_state_json']=json.dumps({'boss_turns':1,'form':{'faction':'warriors','special_trait':'none','passive_key':'bitter'}})
        result=bosses.boss_turn(b,[]);self.assertEqual(result['copied_decay_percent'],15)

    def test_copied_olaf_extra_hits_keep_fifty_percent_without_hero_stats(self):
        b=boss_state('transformation');b['ability_state_json']=json.dumps({'boss_turns':2,'form':{'faction':'warriors','special_trait':'armored','passive_key':'thawing_frenzy'}})
        result=bosses.boss_turn(b,[])
        self.assertEqual(result['reward_attacks'],3);self.assertEqual(result['attack_decay_percents'],[10,5,5])

    def test_invalid_new_config_and_state_rejected(self):
        for key in ('collapse','waste_of_time','training','transformation','oneshot','simple'):
            with self.assertRaises(ValueError):validate_config(key,{'unexpected':1})
        for state in ({'form':{'attack':5}},{'form':{'faction':'invalid','special_trait':'none','passive_key':'none'}},{'grave_seal':2},{'form':{'faction':[],'special_trait':'none','passive_key':'none'}}):
            b=boss_state('transformation');b['ability_state_json']=json.dumps(state)
            with self.assertRaises(ValueError):bosses.boss_turn(b,[])
        for field in ('bone_decoy','grave_seal','last_watch_used'):
            with self.assertRaises(ValueError):heroes.hero_state(json.dumps({field:1}))

    def test_invalid_new_passive_effect_values_rejected(self):
        data=hero_catalog.load_ability_catalog()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'abilities.json'
            with patch.object(hero_catalog,'ABILITIES_JSON',path):
                for key,field,value in (('bitter','every',0),('last_watch','chance_percent',101),('bone_decoy','every',True),('thawing_frenzy','extra_attacks',100),('arise_common','trigger','attack')):
                    bad=copy.deepcopy(data);bad['abilities'][key]['effects'][0][field]=value;path.write_text(json.dumps(bad),encoding='utf-8')
                    with self.assertRaises(ValueError):hero_catalog.load_ability_catalog()


class BossIntegrationTests(MiniCase):
    def setUp(self):
        super().setUp();self.now=datetime(2026,10,7,10,tzinfo=timezone.utc)
    def battle(self,code,hero='Villager',**changes):
        self.hero(hero);template=get_boss_template(code);template.update(min_players=1,reward_coins=100,**changes)
        with patch('app.mini.boss.service.get_boss_template',return_value=template):b=create_boss_event(self.world['id'],code,1,self.db)
        register_player(b['id'],self.pid,self.db);close_registration(b['id'],self.db);start_battle(b['id'],now=self.now,db_path=self.db)
        return b['id']
    def hit(self,b,n=0):return hit_boss(b,self.pid,now=self.now+timedelta(seconds=n),db_path=self.db)
    def reward_attack(self,b):
        with connect_mini_db(self.db) as conn:
            conn.row_factory=__import__('sqlite3').Row;conn.execute('BEGIN IMMEDIATE')
            return boss_hits_reward(conn,refresh_boss(conn,b),self.now)

    def test_olaf_dummy_three_primary_plus_two_real_hits_no_counter_recursion(self):
        b=self.battle('training_dummy','olaf_taliy');self.hit(b);self.hit(b,1);result=self.hit(b,2)
        self.assertEqual((result['damage'],result['extra_damage']),(1,2));self.assertEqual(get_boss(b,self.db)['current_hp'],25)
        p=self.sql('SELECT hit_count,total_damage FROM mini_boss_participants WHERE boss_id=?',(b,))[0]
        self.assertEqual(p,{'hit_count':3,'total_damage':5})
        self.assertEqual(get_boss(b,self.db)['reward_percent'],100)
        self.assertEqual(self.sql("SELECT damage FROM mini_boss_actions WHERE boss_id=? AND action_type='extra_attack'",(b,)),[{'damage':1},{'damage':1}])

    def test_olaf_extra_hits_stop_immediately_on_death(self):
        b=self.battle('training_dummy','olaf_taliy',max_hp=4);self.hit(b);self.hit(b,1);result=self.hit(b,2)
        self.assertEqual(result['extra_damage'],1);self.assertEqual(get_boss(b,self.db)['status'],'defeated')
        self.assertEqual(len(self.sql("SELECT * FROM mini_boss_actions WHERE boss_id=? AND action_type='extra_attack'",(b,))),1)

    def test_oneshot_zero_shields_destroys_reward_including_temporary_hp(self):
        b=self.battle('one_punch_mob');self.sql('UPDATE mini_bosses SET reward_temp_hp=150 WHERE id=?',(b,))
        self.reward_attack(b);state=get_boss(b,self.db)
        self.assertEqual((state['reward_percent'],state['reward_temp_hp'],state['status']),(0,0,'failed'))

    def test_oneshot_acquired_shield_consumed_reward_survives(self):
        b=self.battle('one_punch_mob');self.sql('UPDATE mini_bosses SET reward_shields=1 WHERE id=?',(b,))
        self.reward_attack(b);state=get_boss(b,self.db)
        self.assertEqual((state['reward_shields'],state['reward_percent'],state['status']),(0,100,'fighting'))
        self.reward_attack(b);self.assertEqual(get_boss(b,self.db)['status'],'failed')

    def test_doppelganger_form_persisted_after_attack_and_restart(self):
        b=self.battle('doppelganger','Kirito');self.hit(b)
        before=get_boss(b,self.db);form=json.loads(before['ability_state_json'])['form']
        self.assertEqual(set(form),{'faction','special_trait','passive_key'});self.assertEqual(form['passive_key'],'bitter')
        init_boss_db(self.db);after=get_boss(b,self.db)
        self.assertEqual(after['ability_state_json'],before['ability_state_json']);self.assertEqual(after['current_hp'],before['current_hp'])
        self.hit(b,1);self.assertLess(get_boss(b,self.db)['current_hp'],before['current_hp'])

    def test_decoy_and_lastwatch_preserve_reward_shield_in_real_lifecycle(self):
        for code,key in (('bone_hyena','bone_decoy'),('reborn_crypt_keeper','last_watch')):
            b=self.battle('training_dummy',code)
            self.sql('UPDATE mini_bosses SET reward_shields=1 WHERE id=?',(b,))
            if key=='bone_decoy':self.sql('UPDATE mini_boss_participants SET hero_state_json=? WHERE boss_id=?',(json.dumps({'bone_decoy':True}),b))
            with patch('app.mini.combat.hero_abilities.engine._roll_success',return_value=True):result=self.reward_attack(b)
            self.assertEqual(result['type'],key);self.assertEqual(get_boss(b,self.db)['reward_shields'],1)
            with patch('app.mini.combat.hero_abilities.engine._roll_success',return_value=False): self.reward_attack(b)
            self.assertEqual(get_boss(b,self.db)['reward_shields'],0)
            self.sql("UPDATE mini_bosses SET status='defeated' WHERE id=?",(b,))


    def test_decoy_consumed_by_actual_attack_even_when_reward_guard_intercepts(self):
        b=self.battle('training_dummy','bone_hyena')
        self.sql('UPDATE mini_bosses SET reward_shields=1 WHERE id=?',(b,))
        self.sql('UPDATE mini_boss_participants SET hero_state_json=? WHERE boss_id=?',
                 (json.dumps({'bone_decoy':True,'reward_guard_charges':1}),b))
        with patch('app.mini.combat.hero_abilities.engine._roll_success',return_value=False):result=self.reward_attack(b)
        self.assertEqual(result['type'],'reward_guard')
        state=json.loads(self.sql('SELECT hero_state_json FROM mini_boss_participants WHERE boss_id=?',(b,))[0]['hero_state_json'])
        self.assertNotIn('bone_decoy',state);self.assertEqual(state['reward_guard_charges'],0)
