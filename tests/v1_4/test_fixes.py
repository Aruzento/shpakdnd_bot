"""Post-audit fixes: isolated fixtures only, no published Shadow content."""
import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.mini.boss import catalog as boss_catalog
from app.mini.boss.boss_abilities import engine as bosses
from app.mini.boss.calculations import calculate_hit
from app.mini.boss.combat import start_battle, hit_boss, get_combat_state
from app.mini.boss.loadouts import battle_loadout, snapshot_base_attack
from app.mini.boss.repository import refresh_boss
from app.mini.boss.runtime import boss_turn, extra_attack, apply_turn_start
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import create_boss_event, register_player, close_registration, get_boss
from app.mini.catalog import load_hero_catalog, HERO_CATALOG_DIR
from app.mini.combat import creatures
from app.mini.combat.hero_abilities import engine as heroes
from app.mini.combat.hero_abilities import catalog as ability_catalog
from app.mini.db import connect_mini_db
from app.mini.hero_upgrades import calculate_attack
from tests.v1_3.support import MiniCase
from tests.v1_4.test_bosses import boss_state, snapshot


class ShadowHitTests(unittest.TestCase):
    def hit(self, key, n, *, attack=70, base_attack=14, state=None, boss=None, hero=None,
            potion=0, extra=None, participants=None):
        h=snapshot(key);h.update(faction='commoners',class_tag='none',rarity='shadow',base_attack=base_attack)
        h.update(hero or {})
        b=boss_state('none');b.update(current_hp=10000,max_hp=10000,faction='commoners');b.update(boss or {})
        p=dict(attack=attack,hit_count=n-1,damage_bonus_percent=potion,hero_state_json=json.dumps(state or {}))
        return calculate_hit(b,p,h,participants,extra_percent=extra)

    def test_all_shadow_keys_are_canonical_with_no_published_content(self):
        keys={'shadow_collapse','shadow_waste_of_time','shadow_transformation','shadow_oneshot','shadow_simple'}
        self.assertTrue(keys <= ability_catalog.configured_ability_keys())
        self.assertEqual(json.loads((HERO_CATALOG_DIR/'shadow.json').read_text(encoding='utf-8'))['heroes'],[])
        self.assertEqual(json.loads((HERO_CATALOG_DIR/'mythic.json').read_text(encoding='utf-8'))['heroes'],[])

    def test_collapse_first_two_normal_third_base_times_two_and_sixth_repeats(self):
        self.assertEqual([self.hit('shadow_collapse',n)['damage'] for n in range(1,7)], [70,70,28,70,70,28])

    def test_collapse_ignores_stars_and_equipment_in_current_attack(self):
        for attack in (14,70,99999):
            self.assertEqual(self.hit('shadow_collapse',3,attack=attack)['damage'],28)

    def test_collapse_ignores_all_faction_matchups(self):
        for faction in ('commoners','beasts','monsters','warriors','dark','neutral'):
            self.assertEqual(self.hit('shadow_collapse',3,boss={'faction':faction})['damage'],28)

    def test_collapse_ignores_incoming_boss_hooks_and_final_overrides(self):
        for key in ('mechanism','magic_shield','collapse','waste_of_time','training','simple'):
            result=self.hit('shadow_collapse',3,boss={'ability_key':key,'ability_state_json':'{"shield_active":true}'})
            self.assertEqual(result['damage'],28,key)
            self.assertEqual(result['boss_resolution']['boss_changes'],{})

    def test_collapse_ignores_armored_resistance(self):
        self.assertEqual(self.hit('shadow_collapse',3,boss={'features_json':'["armored"]'},hero={'attack_range':'melee'})['damage'],28)

    def test_collapse_ignores_damage_potion(self):
        result=self.hit('shadow_collapse',3,potion=999)
        self.assertEqual(result['damage'],28)
        self.assertFalse(any(e['type']=='item_damage_boost' for e in result['passive_events']))

    def test_collapse_ignores_class_party_and_beast_amplification(self):
        warrior=dict(hero_snapshot_json=json.dumps({'class_tag':'warrior'}))
        result=self.hit('shadow_collapse',3,participants=[warrior]*10,
            hero={'class_tag':'beast'},state={'class_successful_hits':100})
        self.assertEqual(result['damage'],28)

    def test_collapse_preserves_fundamental_reachability_even_against_simple(self):
        for key in ('none','simple','training'):
            result=self.hit('shadow_collapse',3,boss={'ability_key':key,'features_json':'["flying"]'},hero={'attack_range':'melee'})
            self.assertFalse(result['reachable']);self.assertEqual(result['damage'],0)

    def test_collapse_extra_hit_uses_normal_lifecycle_without_collapse(self):
        self.assertEqual(self.hit('shadow_collapse',3,extra=100)['damage'],70)
        self.assertEqual(self.hit('shadow_collapse',3,extra=50)['damage'],35)

    def test_waste_sequence_true_zero_and_pending_double_consumption(self):
        state={};sequence=[];doubles=[]
        for n in range(1,8):
            result=self.hit('shadow_waste_of_time',n,state=state)
            plan=result['attack_resolution'];state=plan['hero_state']
            sequence.append(result['damage']);doubles.append(plan['extra_attacks'])
        self.assertEqual(sequence,[70,70,0,70,70,0,70])
        self.assertEqual(doubles,[0,0,0,1,0,0,1]);self.assertEqual(state,{})

    def test_waste_zero_is_not_raised_by_min_one_or_boss_overrides(self):
        for key in ('collapse','training','simple','mechanism','magic_shield'):
            result=self.hit('shadow_waste_of_time',3,attack=1,potion=100,boss={'ability_key':key})
            self.assertEqual(result['damage'],0,key)

    def test_waste_extra_hit_cannot_create_new_pending_state(self):
        result=self.hit('shadow_waste_of_time',3,extra=100)
        self.assertEqual(result['damage'],70);self.assertEqual(result['attack_resolution']['extra_attacks'],0)
        self.assertNotIn('hero_state',result['attack_resolution'])

    def test_oneshot_uses_generic_declarative_multiplier(self):
        effects=ability_catalog.get_ability('shadow_oneshot')['effects']
        self.assertEqual(effects[0]['type'],'every_n_damage_multiplier')
        self.assertEqual([self.hit('shadow_oneshot',n)['damage'] for n in range(1,11)],
                         [70,70,70,70,210,70,70,70,70,210])

    def test_oneshot_extra_hit_is_normal_on_fifth_and_tenth(self):
        for n in (5,10):self.assertEqual(self.hit('shadow_oneshot',n,extra=100)['damage'],70)

    def test_simple_ignores_faction_modifiers(self):
        for faction in ('beasts','dark'):
            self.assertEqual(self.hit('shadow_simple',1,attack=64,boss={'faction':faction})['damage'],64)

    def test_simple_ignores_armored_and_boss_incoming_modifiers(self):
        for key in ('none','mechanism','magic_shield','collapse','waste_of_time','training'):
            result=self.hit('shadow_simple',1,attack=64,boss={'ability_key':key,'features_json':'["armored"]',
                'ability_state_json':'{"shield_active":true}'},hero={'attack_range':'melee'})
            self.assertEqual(result['damage'],64,key)

    def test_simple_uses_current_attack_including_stars(self):
        for attack in (14,64,999):
            self.assertEqual(self.hit('shadow_simple',1,attack=attack)['damage'],attack)

    def test_simple_purity_does_not_spread_to_extra_hits(self):
        result=self.hit('shadow_simple',1,attack=64,boss={'faction':'dark','features_json':'["armored"]'},
            hero={'attack_range':'melee'},extra=100)
        self.assertEqual(result['damage'],12)

    def test_simple_preserves_outgoing_potion_only_incoming_modifiers_disabled(self):
        self.assertEqual(self.hit('shadow_simple',1,attack=64,potion=50,boss={'faction':'dark'})['damage'],96)

    def test_transformation_filters_open_tags_none_and_boss_abilities(self):
        b=boss_state('oneshot');b['features_json']='["none","unknown","oneshot","flying","armored","demon"]'
        h=snapshot('shadow_transformation');h.update(base_attack=14,attack_range='melee')
        seen=[]
        result=heroes.resolve_battle_start(h,b,chooser=lambda traits:seen.extend(traits) or 'flying')
        self.assertEqual(seen,['armored','demonic','flying'])
        self.assertEqual(result['hero']['faction'],b['faction'])
        self.assertEqual(result['hero']['special_trait'],'flying')
        self.assertTrue(creatures.can_reach({**b,'features_json':'["flying"]'},result['hero']))
        self.assertEqual(result['hero']['passive_key'],'shadow_transformation')
        self.assertEqual(result['hero']['base_attack'],14)
        self.assertEqual(h['special_trait'],'none')
        self.assertNotIn('attack',result['state']['shadow_form'])

    def test_transformation_no_executable_trait_uses_none(self):
        b=boss_state('oneshot');b['features_json']='["none","oneshot","open_tag"]'
        result=heroes.resolve_battle_start(snapshot('shadow_transformation'),b,chooser=lambda _:self.fail('no roll'))
        self.assertEqual(result['hero']['special_trait'],'none')

    def test_transformation_copies_once_and_new_battle_chooses_again(self):
        b=boss_state('none');b['features_json']='["armored","undead"]';h=snapshot('shadow_transformation')
        first=heroes.resolve_battle_start(h,b,chooser=lambda _: 'armored')
        replay=heroes.resolve_battle_start(h,{**b,'faction':'dark'},state=first['state'],chooser=lambda _:self.fail('replay roll'))
        self.assertEqual(replay,first)
        second=heroes.resolve_battle_start(h,b,chooser=lambda _: 'undead')
        self.assertEqual(second['hero']['special_trait'],'undead')

    def test_new_effect_configuration_and_persisted_state_validation(self):
        data=ability_catalog.load_ability_catalog()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'abilities.json'
            with patch.object(ability_catalog,'ABILITIES_JSON',path):
                for key,field,value in (('shadow_collapse','every',0),('shadow_collapse','multiplier_percent',True),
                        ('shadow_waste_of_time','every',1),('shadow_simple','trigger','kill'),
                        ('shadow_transformation','trigger','attack')):
                    bad=copy.deepcopy(data);bad['abilities'][key]['effects'][0][field]=value
                    path.write_text(json.dumps(bad),encoding='utf-8')
                    with self.assertRaises(ValueError):ability_catalog.load_ability_catalog()
        for state in ({'shadow_pending_double':1},{'shadow_form':{'faction':'unknown','special_trait':'none'}},
                {'shadow_form':{'faction':'dark','special_trait':'unknown'}},{'shadow_form':{'attack':999}}):
            with self.assertRaises(ValueError):heroes.hero_state(json.dumps(state))


class ShadowMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'bosses.json'
        self.boss=copy.deepcopy(boss_catalog.get_boss_template('training_dummy'))
        self.patch=patch.object(boss_catalog,'BOSSES_JSON',self.path);self.patch.start();self.addCleanup(self.patch.stop)

    def load(self,mapping=None,*,rarity='shadow',extractable=True,exists=True):
        boss={**self.boss,'shadow_extractable':extractable,'shadow_hero_code':mapping}
        self.path.write_text(json.dumps({'bosses':[boss]}),encoding='utf-8')
        catalog=load_hero_catalog()
        if exists:
            hero=copy.deepcopy(catalog['heroes'][0]);hero.update(code='fixture_shadow',rarity=rarity)
            catalog['heroes'].append(hero)
        with patch('app.mini.catalog.load_hero_catalog',return_value=catalog):return boss_catalog.load_boss_catalog()

    def test_missing_mapping_and_empty_shadow_content_are_valid(self):
        self.assertEqual(len(self.load(exists=False)['bosses']),1)

    def test_existing_shadow_mapping_is_canonical_without_shadow_of(self):
        self.assertEqual(self.load('fixture_shadow')['bosses'][0]['shadow_hero_code'],'fixture_shadow')

    def test_unknown_mapping_fails_validation(self):
        with self.assertRaisesRegex(boss_catalog.BossCatalogError,'existing shadow hero'):
            self.load('missing')

    def test_other_rarities_fail_mapping_validation(self):
        for rarity in ('common','uncommon','rare','legendary','mythic'):
            with self.subTest(rarity=rarity), self.assertRaises(boss_catalog.BossCatalogError):
                self.load('fixture_shadow',rarity=rarity)

    def test_nonextractable_without_mapping_is_valid(self):
        self.assertFalse(self.load(extractable=False)['bosses'][0]['shadow_extractable'])

    def test_nonextractable_invalid_explicit_mapping_still_fails(self):
        with self.assertRaises(boss_catalog.BossCatalogError):self.load('unknown',extractable=False)

    def test_startup_rejects_invalid_mapping_before_db_and_telegram(self):
        import asyncio
        import bot
        with patch.object(bot,'load_boss_catalog',side_effect=boss_catalog.BossCatalogError('invalid mapping')) as validate, \
             patch.object(bot,'init_db') as init, patch.object(bot,'Bot') as client:
            with self.assertRaisesRegex(boss_catalog.BossCatalogError,'invalid mapping'):
                asyncio.run(bot.main())
            validate.assert_called_once_with();init.assert_not_called();client.assert_not_called()

    def test_training_dummy_remains_nonextractable(self):
        self.assertFalse(self.boss['shadow_extractable']);self.assertIsNone(self.boss.get('shadow_hero_code'))


class ShadowBattleTests(MiniCase):
    def setUp(self):
        super().setUp();self.now=datetime(2026,10,7,10,tzinfo=timezone.utc)

    def battle(self,key='none',*,stars=0,base_attack=14,features=(),boss_key='none',shields=100,**changes):
        hero=self.hero('Villager',stars)
        self.sql("UPDATE mini_heroes SET passive_key=?, attack=?, class_tag='none', faction='commoners', rarity='shadow', attack_range='ranged', special_trait='none' WHERE id=?",(key,base_attack,hero['id']))
        template=boss_catalog.get_boss_template('training_dummy')
        template.update(min_players=1,max_hp=100000,reward_coins=1000,ability_key=boss_key,
            faction='commoners',features=list(features),reward_decay_percent=100 if boss_key=='oneshot' else 10,
            reward_shields=shields)
        template.update(changes)
        with patch('app.mini.boss.service.get_boss_template',return_value=template):
            boss=create_boss_event(self.world['id'],'training_dummy',1,self.db)
        register_player(boss['id'],self.pid,self.db);close_registration(boss['id'],self.db)
        start_battle(boss['id'],now=self.now,db_path=self.db)
        return boss['id']

    def participant(self,b):
        return self.sql('SELECT * FROM mini_boss_participants WHERE boss_id=? AND player_id=?',(b,self.pid))[0]

    def hit(self,b,n=0):return hit_boss(b,self.pid,now=self.now+timedelta(seconds=n),db_path=self.db)

    def turn(self,b):
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
            return boss_turn(conn,refresh_boss(conn,b),self.now)

    def seal(self,b):self.sql('UPDATE mini_bosses SET ability_state_json=? WHERE id=?',('{"grave_seal":true}',b))

    def test_new_snapshot_base_attack_is_unstarred_and_survives_restart(self):
        b=self.battle('shadow_collapse',stars=4)
        p=self.participant(b);h=battle_loadout(p['hero_snapshot_json'])
        self.assertEqual(h['base_attack'],14);self.assertEqual(p['attack'],calculate_attack(14,4))
        init_boss_db(self.db);state=get_combat_state(b,db_path=self.db)
        self.assertEqual(state['boss']['content_version'],14)
        self.assertEqual(self.participant(b)['hero_snapshot_json'],p['hero_snapshot_json'])
        self.assertEqual(self.participant(b)['attack'],p['attack'])

    def test_live_catalog_and_db_attack_changes_never_change_active_collapse(self):
        b=self.battle('shadow_collapse',stars=4)
        self.sql("UPDATE mini_heroes SET attack=99999, passive_key='none' WHERE code='Villager'")
        with patch('app.mini.catalog.load_hero_catalog',side_effect=AssertionError('live catalog read')):
            results=[self.hit(b,n)['damage'] for n in range(6)]
        self.assertEqual(results,[69,69,28,69,69,28])

    def test_legacy_snapshot_without_base_uses_frozen_attack_and_does_not_fall_through(self):
        b=self.battle('shadow_collapse',stars=4);p=self.participant(b)
        h=json.loads(p['hero_snapshot_json']);h.pop('base_attack')
        self.sql('UPDATE mini_boss_participants SET hero_snapshot_json=? WHERE boss_id=?',(json.dumps(h),b))
        self.sql("UPDATE mini_heroes SET attack=99999 WHERE code='Villager'")
        init_boss_db(self.db)
        self.assertEqual(snapshot_base_attack(h,p),69)
        self.hit(b);self.hit(b,1);self.assertEqual(self.hit(b,2)['damage'],138)
        self.assertNotIn('base_attack',json.loads(self.participant(b)['hero_snapshot_json']))

    def test_old_battle_without_base_retains_normal_damage_and_recovery(self):
        b=self.battle();p=self.participant(b);h=json.loads(p['hero_snapshot_json']);h.pop('base_attack')
        self.sql('UPDATE mini_boss_participants SET hero_snapshot_json=? WHERE boss_id=?',(json.dumps(h),b))
        self.sql('UPDATE mini_bosses SET content_version=0,trait_rules_version=0,class_rules_version=0 WHERE id=?',(b,))
        init_boss_db(self.db);self.assertEqual(self.hit(b)['damage'],14)

    def test_collapse_extra_attacks_do_not_increment_primary_counter(self):
        b=self.battle('shadow_collapse');self.hit(b);self.hit(b,1)
        original=heroes.get_ability
        def ability(key):
            result=original(key)
            if key=='shadow_collapse':
                result['effects']=[*result['effects'],dict(trigger='attack',type='every_n_extra_hits',every=3,extra_attacks=1,damage_percent=100,message='fixture')]
            return result
        with patch.object(heroes,'get_ability',side_effect=ability):third=self.hit(b,2)
        self.assertEqual((third['damage'],third['extra_damage']),(28,14))
        self.assertEqual(self.participant(b)['hit_count'],3)
        self.hit(b,3);self.hit(b,4);self.assertEqual(self.hit(b,5)['damage'],28)

    def test_waste_persisted_pending_double_restart_and_counter(self):
        b=self.battle('shadow_waste_of_time');self.hit(b);self.hit(b,1);zero=self.hit(b,2)
        self.assertEqual(zero['damage'],0);self.assertEqual(self.participant(b)['hit_count'],3)
        state=json.loads(self.participant(b)['hero_state_json']);self.assertTrue(state['shadow_pending_double'])
        init_boss_db(self.db);double=self.hit(b,3)
        self.assertEqual((double['damage'],double['extra_damage']),(14,14))
        self.assertEqual(self.participant(b)['hit_count'],4)
        self.assertNotIn('shadow_pending_double',json.loads(self.participant(b)['hero_state_json']))
        self.assertEqual(len(self.sql("SELECT * FROM mini_boss_actions WHERE boss_id=? AND action_type='extra_attack'",(b,))),1)

    def test_waste_double_uses_same_current_attack_damage_lifecycle(self):
        b=self.battle('shadow_waste_of_time',features=['armored'],faction='dark')
        self.sql("UPDATE mini_boss_participants SET hit_count=3,hero_state_json=?,damage_bonus_percent=50 WHERE boss_id=?",
            (json.dumps({'shadow_pending_double':True,'class_successful_hits':2}),b))
        h=json.loads(self.participant(b)['hero_snapshot_json']);h.update(class_tag='beast',attack_range='melee')
        self.sql('UPDATE mini_boss_participants SET hero_snapshot_json=? WHERE boss_id=?',(json.dumps(h),b))
        result=self.hit(b)
        self.assertEqual(result['damage'],result['extra_damage']);self.assertEqual(result['damage'],4)

    def test_waste_stops_extra_when_first_double_hit_kills_boss(self):
        b=self.battle('shadow_waste_of_time')
        self.sql('UPDATE mini_boss_participants SET hit_count=3,hero_state_json=? WHERE boss_id=?',('{"shadow_pending_double":true}',b))
        self.sql('UPDATE mini_bosses SET current_hp=14 WHERE id=?',(b,))
        result=self.hit(b);self.assertTrue(result['battle_ended']);self.assertEqual(result['extra_damage'],0)
        self.assertEqual(self.sql("SELECT * FROM mini_boss_actions WHERE boss_id=? AND action_type='extra_attack'",(b,)),[])

    def test_transformation_persisted_effective_snapshot_seen_by_shared_systems(self):
        with patch.object(heroes.secrets,'choice',return_value='armored'):
            b=self.battle('shadow_transformation',features=['unknown','armored','undead'],faction='dark',shields=3)
        p=self.participant(b);h=json.loads(p['hero_snapshot_json']);state=json.loads(p['hero_state_json'])
        self.assertEqual((h['faction'],h['special_trait']),('dark','armored'))
        self.assertEqual(state['shadow_form'],{'faction':'dark','special_trait':'armored'})
        self.assertEqual(get_boss(b,self.db)['reward_shields'],4)
        self.assertEqual(self.sql("SELECT special_trait,faction FROM mini_heroes WHERE code='Villager'")[0],{'special_trait':'none','faction':'commoners'})
        init_boss_db(self.db)
        self.assertEqual(self.participant(b)['hero_snapshot_json'],p['hero_snapshot_json'])
        self.assertEqual(self.participant(b)['hero_state_json'],p['hero_state_json'])
        with patch.object(heroes.secrets,'choice',side_effect=AssertionError('new form during active battle')):
            self.hit(b)

    def test_transformation_new_battle_rerolls_form(self):
        with patch.object(heroes.secrets,'choice',return_value='armored'):first=self.battle('shadow_transformation',features=['armored','undead'])
        self.sql("UPDATE mini_bosses SET status='defeated' WHERE id=?",(first,))
        with patch.object(heroes.secrets,'choice',return_value='undead'):second=self.battle('shadow_transformation',features=['armored','undead'])
        self.assertEqual(json.loads(self.participant(first)['hero_snapshot_json'])['special_trait'],'armored')
        self.assertEqual(json.loads(self.participant(second)['hero_snapshot_json'])['special_trait'],'undead')
        self.assertEqual(get_boss(second,self.db)['reward_temp_hp'],15)

    def test_oneshot_primary_counter_is_unaffected_by_real_extra_attacks(self):
        b=self.battle('shadow_oneshot')
        for n in range(10):
            result=self.hit(b,n)
            self.assertEqual(result['damage'],42 if (n+1)%5==0 else 14)
            with connect_mini_db(self.db) as conn:
                conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
                p=self.participant(b);extra_attack(conn,refresh_boss(conn,b),p,battle_loadout(p['hero_snapshot_json']),self.now,'{damage}')
            self.assertEqual(self.participant(b)['hit_count'],n+1)

    def test_simple_does_not_recalculate_echo_or_poison_as_pure_primary(self):
        b=self.battle('shadow_simple',features=['armored'],faction='dark')
        self.sql('UPDATE mini_boss_participants SET hero_state_json=? WHERE boss_id=?',('{"pending_echo_damage":7}',b))
        with connect_mini_db(self.db) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE');p={**self.participant(b),'hero_name':'Fixture'}
            result=apply_turn_start(conn,refresh_boss(conn,b),p,self.now)
        self.assertEqual(result['hero_events'][0]['damage'],7)
        self.sql('UPDATE mini_bosses SET feature_state_json=? WHERE id=?',('{"boss_poisoned":true}',b))
        tick=creatures.poison_tick(get_boss(b,self.db))
        self.assertEqual(tick['events'][0]['damage'],2000)

    def test_unsealed_oneshot_still_destroys_reward_and_temporary_hp(self):
        b=self.battle(boss_key='oneshot',shields=0)
        self.sql('UPDATE mini_bosses SET reward_temp_hp=150 WHERE id=?',(b,))
        self.turn(b);fresh=get_boss(b,self.db)
        self.assertEqual((fresh['reward_percent'],fresh['reward_temp_hp'],fresh['status']),(0,0,'failed'))

    def test_unsealed_oneshot_shield_lifecycle_unchanged(self):
        b=self.battle(boss_key='oneshot',shields=1);self.turn(b);fresh=get_boss(b,self.db)
        self.assertEqual((fresh['reward_shields'],fresh['reward_percent'],fresh['status']),(0,100,'fighting'))
        self.turn(b);self.assertEqual(get_boss(b,self.db)['status'],'failed')

    def test_sealed_oneshot_no_shield_ordinary_ten_percent_and_next_turn_van_shot(self):
        b=self.battle(boss_key='oneshot',shields=0);self.seal(b);turn=self.turn(b);fresh=get_boss(b,self.db)
        self.assertEqual((fresh['reward_percent'],fresh['boss_damage'],fresh['status']),(90,100,'fighting'))
        self.assertEqual(fresh['reward_decay_percent'],100)
        self.assertNotIn('grave_seal',json.loads(fresh['ability_state_json']))
        self.assertTrue(any(e['type']=='grave_seal' for e in turn['boss_events']))
        init_boss_db(self.db);self.turn(b);self.assertEqual(get_boss(b,self.db)['status'],'failed')

    def test_sealed_oneshot_shield_absorbs_ordinary_attack(self):
        b=self.battle(boss_key='oneshot',shields=1);self.seal(b);self.turn(b);fresh=get_boss(b,self.db)
        self.assertEqual((fresh['reward_shields'],fresh['reward_percent'],fresh['boss_damage']),(0,100,0))
        self.assertNotIn('grave_seal',json.loads(fresh['ability_state_json']))
        self.turn(b);self.assertEqual(get_boss(b,self.db)['status'],'failed')

    def test_grave_seal_real_fourth_mummy_primary_suppresses_oneshot(self):
        b=self.battle('grave_seal',boss_key='oneshot',shields=3)
        for n in range(4):result=self.hit(b,n)
        self.assertEqual(self.participant(b)['hit_count'],4)
        self.assertEqual(get_boss(b,self.db)['reward_percent'],90)
        self.assertTrue(any(e['type']=='grave_seal' for e in result['reward_event']['boss_events']))
        self.hit(b,4);self.assertEqual(get_boss(b,self.db)['status'],'failed')

    def test_seal_preserves_passive_creature_regeneration(self):
        b=self.battle(boss_key='hydra_regeneration',features=['holy'],shields=1)
        self.sql('UPDATE mini_bosses SET current_hp=50000 WHERE id=?',(b,));self.seal(b)
        result=self.turn(b)
        kinds={e['type'] for e in result['boss_events']}
        self.assertIn('grave_seal',kinds);self.assertIn('holy_regeneration',kinds)
        self.assertNotIn('hydra_regeneration',kinds)

    def test_seal_regression_for_turn_and_after_turn_active_abilities(self):
        for key in ('critical_strike','banishment','rapier','hydra_regeneration','transformation'):
            with self.subTest(key=key):
                b=boss_state(key);b['ability_state_json']='{"grave_seal":true,"boss_turns":2}'
                b['reward_shields']=2;b['reward_percent']=100
                turn=bosses.boss_turn(b,[{'player_id':1},{'player_id':2}],roller=lambda _:True)
                self.assertEqual(turn['reward_attacks'],1);self.assertFalse(turn['ignore_shields']);self.assertEqual(turn['participant_changes'],[])
                b.update(turn['boss_changes'])
                after=bosses.after_boss_turn(b,[{'player_id':1,'hit_count':1,'hero_snapshot_json':json.dumps(snapshot())}])
                b.update(after['boss_changes'])
                self.assertNotIn('grave_seal',json.loads(b['ability_state_json']))
                self.assertEqual(sum(e['type']=='grave_seal' for e in [*turn['events'],*after['events']]),1)
                self.assertNotIn('current_hp',after['boss_changes'])
                self.assertNotIn('form',json.loads(b['ability_state_json']))
