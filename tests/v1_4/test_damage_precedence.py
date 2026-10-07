"""Absolute Boss rules have priority over every hero-side damage mode."""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.mini.boss.boss_abilities import engine as bosses
from app.mini.boss.schema import init_boss_db
from tests.v1_3.support import MiniCase
from tests.v1_4 import test_fixes as fixtures


class DamagePrecedenceTests(unittest.TestCase):
    hit = fixtures.ShadowHitTests.hit

    def test_collapse_regular_boss_third_hit_base_fourteen_times_two(self):
        self.assertEqual(self.hit('shadow_collapse',3)['damage'],28)

    def test_collapse_armored_boss_still_twenty_eight(self):
        self.assertEqual(self.hit('shadow_collapse',3,boss={'features_json':'["armored"]'},
                                  hero={'attack_range':'melee'})['damage'],28)

    def test_collapse_inversion_final_rule_overrides_fixed_hit_with_zero(self):
        result=self.hit('shadow_collapse',3,boss={'ability_key':'collapse'})
        self.assertEqual((result['ability_damage'],result['hero_final_damage'],result['damage']),(28,28,0))
        self.assertEqual(result['hp_after'],10000)

    def test_collapse_dummy_final_rule_overrides_fixed_hit_with_one(self):
        result=self.hit('shadow_collapse',3,boss={'ability_key':'training'})
        self.assertEqual((result['ability_damage'],result['damage']),(28,1))
        self.assertEqual(result['hp_after'],9999)

    def test_collapse_simple_village_final_rule_keeps_raw_passive_damage(self):
        result=self.hit('shadow_collapse',3,potion=999,boss={'ability_key':'simple','faction':'dark',
            'features_json':'["armored"]'},hero={'class_tag':'beast'},state={'class_successful_hits':100})
        self.assertEqual((result['ability_damage'],result['damage']),(28,28))

    def test_shadow_simple_regular_boss_uses_current_attack(self):
        self.assertEqual(self.hit('shadow_simple',1,attack=64)['damage'],64)

    def test_shadow_simple_ignores_armored_and_disadvantage(self):
        self.assertEqual(self.hit('shadow_simple',1,attack=64,boss={'faction':'dark','features_json':'["armored"]'},
                                  hero={'attack_range':'melee'})['damage'],64)

    def test_shadow_simple_inversion_final_rule_is_zero(self):
        result=self.hit('shadow_simple',1,attack=64,boss={'ability_key':'collapse'})
        self.assertEqual((result['hero_final_damage'],result['damage']),(64,0))

    def test_shadow_simple_dummy_final_rule_is_one(self):
        self.assertEqual(self.hit('shadow_simple',1,attack=64,boss={'ability_key':'training'})['damage'],1)

    def test_shadow_simple_simple_village_removes_own_potion_and_class_bonus_at_final_layer(self):
        warrior={'hero_snapshot_json':json.dumps({'class_tag':'warrior'})}
        result=self.hit('shadow_simple',1,attack=64,potion=50,participants=[warrior]*10,
            hero={'class_tag':'beast'},state={'class_successful_hits':50},
            boss={'ability_key':'simple','faction':'dark','features_json':'["armored"]'})
        self.assertEqual(result['ability_damage'],64)
        self.assertEqual(result['hero_final_damage'],192)
        self.assertEqual(result['damage'],64)

    def test_waste_regular_boss_third_primary_is_true_zero(self):
        self.assertEqual(self.hit('shadow_waste_of_time',3)['damage'],0)

    def test_waste_dummy_zero_primary_is_one_actual_hit(self):
        result=self.hit('shadow_waste_of_time',3,boss={'ability_key':'training'})
        self.assertEqual((result['ability_damage'],result['damage']),(0,1))
        self.assertTrue(result['attack_resolution']['hero_state']['shadow_pending_double'])

    def test_oneshot_regular_boss_fifth_primary_triples_own_attack(self):
        self.assertEqual(self.hit('shadow_oneshot',5,attack=14)['damage'],42)

    def test_oneshot_inversion_fifth_primary_is_zero(self):
        self.assertEqual(self.hit('shadow_oneshot',5,attack=14,boss={'ability_key':'collapse'})['damage'],0)

    def test_oneshot_dummy_fifth_primary_is_one(self):
        self.assertEqual(self.hit('shadow_oneshot',5,attack=14,boss={'ability_key':'training'})['damage'],1)

    def inversion(self,rarity,**kwargs):
        return self.hit('none',1,attack=200,potion=100,hero={'rarity':rarity},
            boss={'ability_key':'collapse','features_json':'["armored"]','faction':'dark',**kwargs})

    def test_existing_common_inversion_raw_plus_one_hundred(self):
        self.assertEqual(self.inversion('common')['damage'],300)

    def test_existing_uncommon_inversion_raw_plus_fifty(self):
        self.assertEqual(self.inversion('uncommon')['damage'],250)

    def test_existing_rare_inversion_raw_twenty_five_percent(self):
        self.assertEqual(self.inversion('rare')['damage'],50)

    def test_existing_legendary_inversion_raw_one_percent(self):
        self.assertEqual(self.inversion('legendary')['damage'],2)
        self.assertEqual(self.hit('none',1,attack=20,hero={'rarity':'legendary'},boss={'ability_key':'collapse'})['damage'],0)

    def test_existing_mythic_inversion_heals_by_raw_with_max_hp_cap(self):
        result=self.inversion('mythic',current_hp=9000)
        self.assertEqual((result['damage'],result['hp_after']),(0,9200))
        self.assertEqual(result['boss_resolution']['events'][0]['healed_hp'],200)
        capped=self.inversion('mythic',current_hp=9990)
        self.assertEqual((capped['damage'],capped['hp_after']),(0,10000))
        self.assertEqual(capped['boss_resolution']['events'][0]['healed_hp'],10)

    def test_existing_dummy_ordinary_primary_and_extra_each_one_for_any_rarity(self):
        for rarity in ('common','uncommon','rare','legendary','shadow','mythic'):
            for extra in (None,50,100):
                with self.subTest(rarity=rarity,extra=extra):
                    self.assertEqual(self.hit('none',1,attack=10000,potion=999,extra=extra,
                        hero={'rarity':rarity},boss={'ability_key':'training'})['damage'],1)

    def test_waste_boss_blocks_exact_rarities_in_every_damage_mode(self):
        for rarity in ('rare','legendary','mythic'):
            for passive,n in (('none',1),('shadow_collapse',3),('shadow_simple',1),('shadow_oneshot',5)):
                with self.subTest(rarity=rarity,passive=passive):
                    self.assertEqual(self.hit(passive,n,hero={'rarity':rarity},boss={'ability_key':'waste_of_time'})['damage'],0)
        for passive,n,damage in (('shadow_collapse',3,28),('shadow_simple',1,70),
                                 ('shadow_waste_of_time',3,0),('shadow_oneshot',5,210)):
            with self.subTest(passive=passive):
                self.assertEqual(self.hit(passive,n,boss={'ability_key':'waste_of_time'})['damage'],damage)

    def test_existing_simple_village_own_attack_and_passive_raw_are_preserved(self):
        boss={'ability_key':'simple','faction':'dark','features_json':'["armored","flying"]'}
        self.assertEqual(self.hit('none',1,attack=64,potion=999,boss=boss)['damage'],64)
        self.assertEqual(self.hit('bitter',2,attack=46,potion=999,boss=boss)['damage'],546)
        self.assertEqual(self.hit('none',1,attack=64,potion=999,boss=boss,extra=50)['damage'],32)

    def test_pure_modes_skip_ordinary_pipeline_and_apply_final_rule_exactly_once(self):
        for passive,n in (('shadow_collapse',3),('shadow_simple',1),('shadow_waste_of_time',3)):
            with self.subTest(passive=passive), \
                 patch('app.mini.boss.calculations.faction_multiplier_percent',side_effect=AssertionError('faction reapplied')), \
                 patch.object(bosses,'modify_hero_damage',side_effect=AssertionError('ordinary Boss modifier reapplied')), \
                 patch('app.mini.boss.calculations.creatures.feature_damage',side_effect=AssertionError('armor reapplied')), \
                 patch.object(bosses,'final_hero_damage',wraps=bosses.final_hero_damage) as final:
                result=self.hit(passive,n,boss={'ability_key':'training'})
                self.assertEqual(result['damage'],1);final.assert_called_once()
                args=final.call_args
                self.assertEqual(args.kwargs['raw_damage'],result['ability_damage'])
                self.assertEqual(args.args[2],result['hero_final_damage'])

    def test_pure_mode_mythic_heal_and_events_reach_common_hp_layer(self):
        for passive,n,raw in (('shadow_collapse',3,28),('shadow_simple',1,70)):
            with self.subTest(passive=passive):
                result=self.hit(passive,n,potion=100,hero={'rarity':'mythic'},
                    boss={'ability_key':'collapse','current_hp':9900})
                self.assertEqual((result['damage'],result['hp_after']),(0,9900+raw))
                self.assertEqual(result['boss_resolution']['events'][0]['healed_hp'],raw)


class DummyDoublePrecedenceTests(MiniCase):
    battle = fixtures.ShadowBattleTests.battle
    hit = fixtures.ShadowBattleTests.hit
    participant = fixtures.ShadowBattleTests.participant

    def setUp(self):
        super().setUp();self.now=datetime(2026,10,7,10,tzinfo=timezone.utc)

    def test_waste_dummy_zero_and_double_are_three_separate_one_damage_hits_after_restart(self):
        b=self.battle('shadow_waste_of_time',boss_key='training')
        self.hit(b);self.hit(b,1);third=self.hit(b,2)
        self.assertEqual((third['ability_damage'],third['damage']),(0,1))
        p=self.participant(b)
        self.assertEqual((p['hit_count'],p['total_damage']),(3,3))
        self.assertTrue(json.loads(p['hero_state_json'])['shadow_pending_double'])
        init_boss_db(self.db);fourth=self.hit(b,3)
        self.assertEqual((fourth['damage'],fourth['extra_damage']),(1,1))
        p=self.participant(b)
        self.assertEqual((p['hit_count'],p['total_damage']),(4,5))
        self.assertNotIn('shadow_pending_double',json.loads(p['hero_state_json']))
        self.assertEqual(self.sql("SELECT damage FROM mini_boss_actions WHERE boss_id=? AND action_type='extra_attack'",(b,)),[{'damage':1}])
