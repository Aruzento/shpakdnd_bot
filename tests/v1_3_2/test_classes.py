import json
import unittest
from unittest.mock import Mock
from app.mini.combat import classes,creatures


def battle(**changes):
    return dict(class_rules_version=1,trait_rules_version=1,reward_coins=100,reward_percent=100,
        boss_damage=0,sneaky_stolen=0,reward_temp_hp=0,reward_corruption=0,
        reward_shields=1,reward_shields_max=3,features_json='[]',feature_state_json='{}',**changes)


def participant(kind,**extra):return dict(hero_snapshot_json=json.dumps({'class_tag':kind}),**extra)


class ClassDomainTests(unittest.TestCase):
    def test_warrior_linear_stacking_and_no_mutation(self):
        for count in range(8):
            team=[participant('warrior') for _ in range(count)]
            self.assertEqual(classes.initial_attack(40,team),40*(100+count*5)//100)
            self.assertEqual(team,[participant('warrior') for _ in range(count)])

    def test_banished_excluded_forced_skip_still_contributes(self):
        team=[participant('warrior',banished=1),participant('warrior',forced_skip_turns=1)]
        self.assertEqual(classes.initial_attack(40,team),42)

    def test_guardian_deterministic_restores_one(self):
        b=battle();r=Mock(return_value=True)
        result=classes.on_hit(b,{'class_tag':'guardian'},{},successful=True,roller=r)
        self.assertEqual(result['boss_changes']['reward_shields'],2);r.assert_called_once_with(5)
        self.assertEqual(b['reward_shields'],1)

    def test_guardian_full_shields_no_roll_and_failed_attack_no_roll(self):
        for successful,shield in ((True,3),(False,1)):
            r=Mock(return_value=True);b=battle();b['reward_shields']=shield
            result=classes.on_hit(b,{'class_tag':'guardian'},{},successful=successful,roller=r)
            self.assertEqual(result['boss_changes'],{});r.assert_not_called()

    def test_guardian_failed_rng_no_proc(self):
        self.assertEqual(classes.on_hit(battle(),{'class_tag':'guardian'},{},successful=True,roller=lambda p:False)['boss_changes'],{})

    def test_sneaky_uses_current_pool_ceil_and_clamp(self):
        for pool,expected in ((100,5),(57,3),(21,2),(5,1),(1,1),(0,0)):
            b=battle();b['reward_coins']=pool;r=Mock(return_value=True)
            result=classes.on_hit(b,{'class_tag':'sneaky'},{},successful=True,roller=r)
            self.assertEqual(result['stolen'],expected)
            self.assertLessEqual(result['stolen'],pool)
            if pool:r.assert_called_once_with(1)
            else:r.assert_not_called()

    def test_sneaky_stolen_is_separate_from_boss_damage(self):
        b=battle();b.update(boss_damage=20,reward_percent=80,sneaky_stolen=5)
        result=classes.on_hit(b,{'class_tag':'sneaky'},{},successful=True,roller=lambda p:True)
        self.assertEqual(result['stolen'],4)
        self.assertEqual(result['boss_changes']['sneaky_stolen'],9)
        self.assertNotIn('boss_damage',result['boss_changes'])
        self.assertEqual(result['state']['class_stolen_coins'],4)

    def test_healer_only_restores_permanent_boss_damage(self):
        b=battle();b.update(boss_damage=20,reward_percent=80,sneaky_stolen=5,reward_corruption=10)
        result=classes.on_hit(b,{'class_tag':'healer'},{},successful=True)
        b.update(result['boss_changes']);self.assertEqual(b['boss_damage'],15)
        self.assertEqual(classes.real_reward(b),80)
        self.assertEqual(b['sneaky_stolen'],5);self.assertEqual(b['reward_corruption'],10)
        for _ in range(10):b.update(classes.on_hit(b,{'class_tag':'healer'},{},successful=True)['boss_changes'])
        self.assertEqual(classes.real_reward(b),95)

    def test_healer_no_boss_damage_is_noop_even_with_theft_and_corruption(self):
        b=battle();b.update(sneaky_stolen=5,reward_corruption=20)
        self.assertEqual(classes.on_hit(b,{'class_tag':'healer'},{},successful=True)['boss_changes'],{})

    def test_all_on_hit_classes_require_successful_ordinary_hit(self):
        for kind in ('guardian','sneaky','healer','beast'):
            b=battle();b.update(boss_damage=20,reward_percent=80)
            result=classes.on_hit(b,{'class_tag':kind},{},successful=False,roller=Mock(side_effect=AssertionError('roll')))
            self.assertEqual(result,dict(state={},boss_changes={},events=[],stolen=0))

    def test_beast_percentages_are_linear_against_current_damage(self):
        state={};hero={'class_tag':'beast'}
        for n in range(1,11):
            self.assertEqual(classes.beast_damage(100,hero,state),100+2*(n-1))
            state=classes.on_hit(battle(),hero,state,successful=True)['state']
        self.assertEqual(classes.beast_damage(200,hero,{'class_successful_hits':9}),236)

    def test_beast_miss_and_new_battle_counter(self):
        state=classes.on_hit(battle(),{'class_tag':'beast'},{},successful=False)['state']
        self.assertEqual(state,{})
        self.assertEqual(classes.beast_damage(100,{'class_tag':'beast'},{}),100)

    def test_technical_is_binary_and_only_disables_construct_regeneration(self):
        for n in (0,1,2):
            b=battle();b.update(features_json='["construct"]',max_hp=100,current_hp=50)
            team=[participant('technical') for _ in range(n)]
            plan=creatures.end_round(b,team)
            self.assertEqual(plan['boss_changes'].get('current_hp',50),60 if n==0 else 50)
        b.update(features_json='["holy"]')
        self.assertEqual(creatures.holy_regeneration(b,[participant('technical')])['boss_changes']['current_hp'],75)

    def test_mage_predicate_preserves_existing_legacy_magic_class(self):
        for kind,expected in (('mage',True),('magical',True),('warrior',False),('none',False)):
            self.assertEqual(classes.can_remove_magic_shield({'class_tag':kind}),expected)

    def test_none_has_no_effect(self):
        self.assertEqual(classes.beast_damage(100,{'class_tag':'none'},{}),100)
        self.assertEqual(classes.on_hit(battle(),{'class_tag':'none'},{},successful=True)['boss_changes'],{})

    def test_old_rules_do_not_enable_new_classes(self):
        b=battle();b['class_rules_version']=0
        self.assertEqual(classes.on_hit(b,{'class_tag':'beast'},{},successful=True)['state'],{})
        self.assertEqual(classes.initial_attack(40,[participant('warrior')],active=False),40)

    def test_decay_records_only_real_loss_and_leaves_stolen_safe(self):
        b=battle();b.update(sneaky_stolen=95,reward_decay_percent=10)
        b.update(creatures.reward_decay(b))
        self.assertEqual(b['boss_damage'],5)
        self.assertEqual(b['sneaky_stolen'],95)
        self.assertEqual(classes.real_reward(b),0)

    def test_legacy_reward_destruction_never_marks_stolen_coins_as_healable_damage(self):
        b=battle();b.update(reward_percent=0,sneaky_stolen=95)
        self.assertEqual(classes.permanent_damage(b),5)
        plan=classes.on_hit(b,{'class_tag':'healer'},{},successful=True)
        self.assertEqual(plan['boss_changes']['boss_damage'],0)
        b.update(plan['boss_changes'])
        self.assertEqual(classes.real_reward(b),5)
        self.assertEqual(b['sneaky_stolen'],95)
