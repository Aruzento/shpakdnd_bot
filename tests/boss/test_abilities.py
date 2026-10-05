import os
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.abilities.catalog import configured_ability_keys
from app.mini.boss.abilities.engine import resolve_attack, resolve_kill


CURRENT_HERO_PASSIVE_KEYS = {
    "none",
    "lucky_strike",
    "precise_strike",
    "combat_training",
    "strong_start",
    "double_strike",
    "silver_edge",
    "hunters_mark",
    "leaping_thrust",
    "execution_protocol",
    "mockery",
}


class BossAbilityEngineTests(unittest.TestCase):
    def test_all_current_hero_passives_are_configured(self):
        self.assertTrue(
            CURRENT_HERO_PASSIVE_KEYS.issubset(configured_ability_keys())
        )

    def test_none_does_not_change_damage(self):
        result = resolve_attack(
            "none",
            base_damage=10,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertEqual(result["damage"], 10)
        self.assertEqual(result["events"], [])

    def test_lucky_strike_success_is_plus_50_percent(self):
        result = resolve_attack(
            "lucky_strike",
            base_damage=10,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
            roller=lambda chance: True,
        )
        self.assertEqual(result["damage"], 15)

    def test_precise_strike_success_is_double(self):
        result = resolve_attack(
            "precise_strike",
            base_damage=8,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
            roller=lambda chance: True,
        )
        self.assertEqual(result["damage"], 16)

    def test_combat_training_every_third_hit(self):
        second = resolve_attack(
            "combat_training",
            base_damage=10,
            hit_number=2,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        third = resolve_attack(
            "combat_training",
            base_damage=10,
            hit_number=3,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertEqual(second["damage"], 10)
        self.assertEqual(third["damage"], 15)

    def test_strong_start_only_first_hit(self):
        first = resolve_attack(
            "strong_start",
            base_damage=9,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        second = resolve_attack(
            "strong_start",
            base_damage=9,
            hit_number=2,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertEqual(first["damage"], 18)
        self.assertEqual(second["damage"], 9)

    def test_double_strike_success_is_two_attacks_total(self):
        result = resolve_attack(
            "double_strike",
            base_damage=7,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
            roller=lambda chance: True,
        )
        self.assertEqual(result["damage"], 14)

    def test_silver_edge_every_third_is_double(self):
        result = resolve_attack(
            "silver_edge",
            base_damage=40,
            hit_number=3,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertEqual(result["damage"], 80)

    def test_hunters_mark_marks_first_and_buffs_later_hits(self):
        first = resolve_attack(
            "hunters_mark",
            base_damage=32,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        second = resolve_attack(
            "hunters_mark",
            base_damage=32,
            hit_number=2,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertEqual(first["damage"], 32)
        self.assertTrue(first["events"])
        self.assertEqual(second["damage"], 40)

    def test_leaping_thrust_every_third_is_double(self):
        result = resolve_attack(
            "leaping_thrust",
            base_damage=35,
            hit_number=3,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertEqual(result["damage"], 70)

    def test_execution_protocol_requires_strictly_below_20_percent(self):
        at_twenty = resolve_attack(
            "execution_protocol",
            base_damage=100,
            hit_number=1,
            boss_hp_before=200,
            boss_max_hp=1000,
        )
        below_twenty = resolve_attack(
            "execution_protocol",
            base_damage=100,
            hit_number=1,
            boss_hp_before=199,
            boss_max_hp=1000,
        )
        self.assertEqual(at_twenty["damage"], 100)
        self.assertEqual(below_twenty["damage"], 150)

    def test_execution_protocol_kill_proc_grants_30_shards(self):
        success = resolve_kill(
            "execution_protocol",
            roller=lambda chance: True,
        )
        fail = resolve_kill(
            "execution_protocol",
            roller=lambda chance: False,
        )
        self.assertEqual(success["bonus_shards"], 30)
        self.assertEqual(fail["bonus_shards"], 0)


    def test_mockery_every_third_hit_queues_boss_skip_without_damage_bonus(self):
        second = resolve_attack(
            "mockery",
            base_damage=150,
            hit_number=2,
            boss_hp_before=500,
            boss_max_hp=500,
        )
        third = resolve_attack(
            "mockery",
            base_damage=150,
            hit_number=3,
            boss_hp_before=500,
            boss_max_hp=500,
        )
        self.assertEqual(second["damage"], 150)
        self.assertEqual(second["boss_skip_turns"], 0)
        self.assertEqual(third["damage"], 150)
        self.assertEqual(third["boss_skip_turns"], 1)
        self.assertTrue(third["events"])

    def test_unknown_passive_fails_safe(self):
        result = resolve_attack(
            "future_unknown_passive",
            base_damage=11,
            hit_number=1,
            boss_hp_before=100,
            boss_max_hp=100,
        )
        self.assertFalse(result["configured"])
        self.assertEqual(result["damage"], 11)


if __name__ == "__main__":
    unittest.main()
