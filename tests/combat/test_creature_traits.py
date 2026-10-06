
import json
import unittest
from unittest.mock import Mock
from app.mini.combat import creatures as rules
from app.mini.combat.matchups import faction_multiplier_percent
from app.mini.boss.calculations import calculate_hit


class CreatureHookTests(unittest.TestCase):
    def boss(self, *features, **values):
        result = dict(name="Босс", trait_rules_version=1, features_json=json.dumps(features),
                      feature_state_json="{}", ability_key="none", ability_state_json="{}",
                      faction="commoners", max_hp=1000, current_hp=500,
                      reward_percent=100, reward_decay_percent=10, reward_temp_hp=0,
                      reward_corruption=0, reward_shields=2, reward_shields_max=3)
        result.update(values)
        return result

    def hero(self, **values):
        return dict(faction="commoners", damage_type="slashing", attack_range="melee",
                    class_tag="none", special_trait="none", passive_key="none", **values)

    def participants(self, *heroes):
        return [dict(hero_snapshot_json=json.dumps(h), banished=0) for h in heroes]

    def apply(self, boss, plan):
        boss.update(plan["boss_changes"])
        return plan

    def test_faction_strong_weak_same_neutral(self):
        for hero, percent in (("warriors",200),("commoners",25),("dark",100),("beasts",100),("monsters",100)):
            self.assertEqual(faction_multiplier_percent(hero,"dark"),percent)

    def test_armor_uses_additive_percentage_of_final_damage(self):
        for damage_type, attack_range, expected in (
            ("slashing","melee",80),("piercing","melee",80),("bludgeoning","melee",80),
            ("slashing","ranged",90),("piercing","ranged",90),("bludgeoning","ranged",90),
            ("magic","melee",90),("magic","ranged",100)):
            with self.subTest(damage_type=damage_type, attack_range=attack_range):
                hero=dict(damage_type=damage_type, attack_range=attack_range, special_trait="none")
                self.assertEqual(rules.feature_damage(self.boss("armored"),hero,100),expected)

    def test_zero_damage_remains_zero_under_armor(self):
        self.assertEqual(rules.feature_damage(self.boss("armored"),self.hero(),0),0)

    def test_first_death_resurrects_second_does_not(self):
        boss=self.boss("undead",current_hp=0)
        first=self.apply(boss,rules.boss_death(boss))
        self.assertTrue(first["revived"]); self.assertEqual(boss["current_hp"],250)
        boss["current_hp"]=0
        self.assertFalse(rules.boss_death(boss)["revived"])

    def test_resurrection_has_minimum_one_hp(self):
        self.assertEqual(rules.boss_death(self.boss("undead",max_hp=3))["boss_changes"]["current_hp"],1)

    def test_construct_regenerates_once_per_round_and_caps(self):
        for hp, expected in ((500,600),(980,1000),(1000,1000)):
            boss=self.boss("construct",current_hp=hp)
            self.apply(boss,rules.end_round(boss,self.participants(self.hero())))
            self.assertEqual(boss["current_hp"],expected)

    def test_technical_counter_includes_forced_skip_but_not_banished(self):
        hero=self.hero(); hero["class_tag"]="technical"
        players=self.participants(hero); players[0]["forced_skip_turns"]=1
        boss=self.boss("construct")
        self.assertEqual(rules.end_round(boss,players)["boss_changes"],{})
        players[0]["banished"]=1
        self.assertEqual(rules.end_round(boss,players)["boss_changes"]["current_hp"],600)

    def test_flying_reachability(self):
        for attack_range, trait, expected in (
            ("melee","none",False),("melee","flying",True),("ranged","none",True)):
            hero=self.hero(); hero.update(attack_range=attack_range,special_trait=trait)
            self.assertEqual(rules.can_reach(self.boss("flying"),hero),expected)

    def test_unreachable_does_not_roll_or_activate_onhit(self):
        for trait in ("poisonous","holy"):
            hero=self.hero(); hero["special_trait"]=trait
            roller=Mock(return_value=True)
            result=rules.on_hit(self.boss("flying","undead"),hero,successful=True,roller=roller)
            self.assertEqual(result["boss_changes"],{}); roller.assert_not_called()

    def test_poisonous_onhit_chance_nonstacking_and_nonproc(self):
        boss=self.boss(); hero=self.hero(); hero["special_trait"]="poisonous"
        roll=Mock(return_value=True)
        self.apply(boss,rules.on_hit(boss,hero,successful=True,roller=roll))
        roll.assert_called_once_with(25)
        self.assertTrue(rules.state(boss)["boss_poisoned"])
        roll.reset_mock()
        self.assertEqual(rules.on_hit(boss,hero,successful=True,roller=roll)["events"],[])
        roll.assert_not_called()
        self.assertEqual(rules.on_hit(self.boss(),hero,successful=True,roller=lambda _:False)["events"],[])

    def test_poison_tick_uses_max_hp_and_is_consumed(self):
        boss=self.boss(current_hp=30,feature_state_json='{"boss_poisoned":true}')
        result=self.apply(boss,rules.poison_tick(boss))
        self.assertEqual(boss["current_hp"],10); self.assertEqual(result["events"][0]["damage"],20)
        self.assertFalse(rules.state(boss)["boss_poisoned"])
        self.assertEqual(rules.poison_tick(boss)["events"],[])

    def test_poison_death_can_resurrect(self):
        boss=self.boss("undead",current_hp=10,feature_state_json='{"boss_poisoned":true}')
        self.apply(boss,rules.poison_tick(boss))
        self.assertEqual(boss["current_hp"],0)
        self.assertTrue(self.apply(boss,rules.boss_death(boss))["revived"])
        self.assertEqual(boss["current_hp"],250)

    def test_holy_regeneration_demonic_alias_counter_and_cap(self):
        for trait, expected in (("none",750),("demonic",500),("demon",500)):
            hero=self.hero(); hero["special_trait"]=trait
            boss=self.boss("holy")
            self.apply(boss,rules.holy_regeneration(boss,self.participants(hero)))
            self.assertEqual(boss["current_hp"],expected)
        boss=self.boss("holy",current_hp=900)
        self.apply(boss,rules.holy_regeneration(boss,[]))
        self.assertEqual(boss["current_hp"],1000)

    def test_demonic_corruption_is_temporary_and_additive(self):
        boss=self.boss("demonic",reward_percent=80)
        self.apply(boss,rules.demonic_corruption(boss)); self.apply(boss,rules.demonic_corruption(boss))
        self.assertEqual(boss["reward_percent"],80)
        self.assertEqual(boss["reward_corruption"],20)
        self.assertEqual(rules.effective_reward_percent(boss),60)

    def test_corruption_zero_effective_reward(self):
        boss=self.boss("demonic",reward_percent=10)
        self.apply(boss,rules.demonic_corruption(boss))
        self.assertEqual(rules.effective_reward_percent(boss),0)

    def test_undead_start_buffer_once_and_no_reward_change(self):
        hero=self.hero(); hero["special_trait"]="undead"
        for count in (1,2,3):
            boss=self.boss()
            self.apply(boss,rules.battle_start(boss,self.participants(*([hero]*count))))
            self.assertEqual(boss["reward_temp_hp"],15); self.assertEqual(boss["reward_percent"],100)
            self.assertEqual(rules.effective_reward_percent(boss),115)

    def test_buffer_absorbs_decay_before_real_reward(self):
        boss=self.boss(reward_temp_hp=15)
        self.apply(boss,{"boss_changes":rules.reward_decay(boss)})
        self.assertEqual((boss["reward_temp_hp"],boss["reward_percent"]),(5,100))
        self.apply(boss,{"boss_changes":rules.reward_decay(boss)})
        self.assertEqual((boss["reward_temp_hp"],boss["reward_percent"]),(0,95))

    def test_armored_start_bonus_is_not_stacked(self):
        hero=self.hero(); hero["special_trait"]="armored"
        for count in (1,2):
            boss=self.boss()
            self.apply(boss,rules.battle_start(boss,self.participants(*([hero]*count))))
            self.assertEqual((boss["reward_shields"],boss["reward_shields_max"]),(4,4))

    def test_construct_roll_and_shield_cap(self):
        hero=self.hero(); hero["special_trait"]="construct"
        boss=self.boss(); roll=Mock(return_value=True)
        self.apply(boss,rules.after_hero_turn(boss,hero,roller=roll))
        roll.assert_called_once_with(10); self.assertEqual(boss["reward_shields"],3)
        roll.reset_mock()
        self.assertEqual(rules.after_hero_turn(boss,hero,roller=roll)["events"],[])
        roll.assert_not_called()

    def test_construct_nonproc(self):
        hero=self.hero(); hero["special_trait"]="construct"
        self.assertEqual(rules.after_hero_turn(self.boss(),hero,roller=lambda _:False)["events"],[])

    def test_holy_only_against_undead_or_demonic_with_success(self):
        hero=self.hero(); hero["special_trait"]="holy"
        for feature, enabled in (("undead",True),("demonic",True),("construct",False)):
            plan=rules.on_hit(self.boss(feature),hero,successful=True)
            self.assertEqual(bool(plan["boss_changes"]),enabled)
        self.assertEqual(rules.on_hit(self.boss("undead"),hero,successful=False)["events"],[])

    def test_holy_ranged_reaches_flying_undead(self):
        hero=self.hero(); hero.update(special_trait="holy",attack_range="ranged")
        self.assertTrue(rules.on_hit(self.boss("flying","undead"),hero,successful=True)["events"])

    def test_holy_miss_chance_consumption_and_no_stack(self):
        boss=self.boss("undead"); hero=self.hero(); hero["special_trait"]="holy"
        self.apply(boss,rules.on_hit(boss,hero,successful=True))
        self.assertEqual(rules.on_hit(boss,hero,successful=True)["events"],[])
        roll=Mock(return_value=True)
        result=self.apply(boss,rules.holy_attack_attempt(boss,roller=roll))
        roll.assert_called_once_with(25); self.assertTrue(result["miss"])
        roll.reset_mock()
        self.assertFalse(rules.holy_attack_attempt(boss,roller=roll)["miss"])
        roll.assert_not_called()

    def test_holy_nonmiss_still_consumes_effect(self):
        boss=self.boss(feature_state_json='{"holy_miss_active":true}')
        self.assertFalse(self.apply(boss,rules.holy_attack_attempt(boss,roller=lambda _:False))["miss"])
        self.assertFalse(rules.state(boss)["holy_miss_active"])

    def test_v0_features_and_traits_are_inert(self):
        boss=self.boss("undead","flying","armored","demonic",trait_rules_version=0)
        hero=self.hero(); hero["special_trait"]="armored"
        self.assertTrue(rules.can_reach(boss,hero))
        self.assertEqual(rules.feature_damage(boss,hero,100),100)
        self.assertEqual(rules.battle_start(boss,self.participants(hero))["events"],[])
        self.assertFalse(rules.boss_death(boss)["revived"])
        self.assertEqual(rules.demonic_corruption(boss)["events"],[])

    def test_armor_after_full_passive_faction_potion_pipeline(self):
        boss=self.boss("armored"); hero=self.hero(); hero["faction"]="dark"
        p=dict(attack=100,hit_count=0,damage_bonus_percent=10)
        result=calculate_hit(boss,p,hero)
        self.assertEqual(result["hero_final_damage"],220)
        self.assertEqual(result["damage"],176)

    def test_unreachable_skips_old_attack_rng_and_magic_shield_removal(self):
        from unittest.mock import patch
        boss=self.boss("flying",ability_key="magic_shield",ability_state_json='{"shield_active":true}')
        hero=self.hero(); hero.update(passive_key="rune_spark",class_tag="mage")
        with patch("app.mini.combat.hero_abilities.engine._roll_success") as roll:
            result=calculate_hit(boss,dict(attack=100,hit_count=0,damage_bonus_percent=10),hero)
        roll.assert_not_called()
        self.assertEqual(result["damage"],0)
        self.assertEqual(result["boss_resolution"]["boss_changes"],{})


    def test_duplicate_features_never_stack(self):
        boss=self.boss("armored","armored")
        self.assertEqual(rules.feature_damage(boss,self.hero(),100),80)

    def test_new_shared_hooks_have_no_boss_telegram_database_dependencies(self):
        import subprocess,sys
        code="import sys; from app.mini.combat import creatures; assert 'app.config' not in sys.modules; assert 'aiogram' not in sys.modules; assert not any(n.startswith('app.mini.boss') for n in sys.modules)"
        result=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
