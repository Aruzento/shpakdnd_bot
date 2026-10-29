import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from tests.v1_3.support import MiniCase
from app.mini.combat.modes import freeze_hero, mode_property, ordinary_damage, hp_event
from app.mini.mythic import pets
from app.mini.mythic.wallet import transaction, change
from app.mini.mythic.raid import service as raid
from app.mini.mythic.raid.effects import resolve
from app.mini.mythic.raid.rewards import allocate
from app.mini.mythic.upgrades import upgrade


class ArithmeticTests(unittest.TestCase):
    def test_additive_percentages_and_zero(self):
        self.assertEqual(ordinary_damage(100, bonus_bps=(7000, 2000)), 190)
        self.assertEqual(ordinary_damage(0, additive=50, protected_zero=True), 0)
        self.assertEqual(ordinary_damage(10, additive=-50), 0)

    def test_best_element_and_event_clamp(self):
        self.assertEqual(ordinary_damage(100, element_percents=(200, 150, 200)), 200)
        self.assertEqual(hp_event(3, 100, 7)["actual_hp_damage"], 3)
        self.assertEqual(hp_event(3, -100, 7)["actual_hp_damage"], 0)

    def test_fallback_never_copies_holy_smite(self):
        hero = {"passive_key": "holy_smite", "attack": 100, "stars": 0}
        self.assertEqual(
            mode_property(hero, "raid"), {"key": "none", "configured": False}
        )
        frozen = freeze_hero(hero)
        hero["attack"] = 2
        self.assertEqual(frozen["attack"], 100)

    def test_echoes_before_stars(self):
        hero = {
            "attack": 38,
            "copies": 11,
            "stars": 1,
            "passive_key": "duplicate_echoes",
        }
        self.assertEqual(freeze_hero(hero)["attack"], 67)

    def test_mythic_attack_does_not_grow(self):
        for stars in range(1, 7):
            self.assertEqual(
                freeze_hero({"attack": 3, "stars": stars, "rarity": "mythic"})[
                    "attack"
                ],
                3,
            )

    def test_r09_example_and_conservation(self):
        values = allocate(
            100000,
            dict(enumerate((40000, 25000, 15000, 10000, 6000, 4000), 1)),
            chooser=lambda x: x[0],
        )
        self.assertEqual([x["amount"] for x in values], [24, 16, 9, 3, 2, 2])
        self.assertEqual(sum(x["share"] for x in values), 20)

    def test_ties_single_winner_and_empty(self):
        results = allocate(100001, {1: 1, 2: 1}, chooser=lambda x: x[-1])
        self.assertEqual(results[0]["player_id"], 2)
        self.assertEqual(sum(x["share"] for x in results), 21)
        self.assertEqual(allocate(100000, {1: 0}), [])


class SnapshotSafetyTests(unittest.TestCase):
    def test_explicit_disabled_property_is_inert_and_physical_type_is_retained(self):
        value = freeze_hero(
            {
                "attack": 100,
                "passive_key": "strong_start",
                "passives": {"normal": {"key": "strong_start", "configured": False}},
                "damage_type": "slashing",
            }
        )
        self.assertEqual(value["passive_key"], "none")
        self.assertFalse(value["passives"]["normal"]["configured"])
        self.assertEqual(value["damage_type"], "slashing")

    def test_best_matching_element_can_reduce_and_never_multiplies_twice(self):
        self.assertEqual(ordinary_damage(100, element_percents=[50]), 50)
        self.assertEqual(ordinary_damage(100, element_percents=[50, 200, 150]), 200)


class PureHpBoundaryTests(unittest.TestCase):
    def test_mana_aura_does_not_amplify_special_hp_damage(self):
        from app.mini.boss.calculations import calculate_hit

        boss = {
            "ability_key": "none",
            "ability_config_json": "{}",
            "ability_state_json": "{}",
            "current_hp": 10000,
            "max_hp": 10000,
            "faction": "neutral",
            "rules_version": 15,
            "current_round": 1,
            "team_state_json": json.dumps({"mana_aura": True}),
        }
        participant = {
            "attack": 100,
            "hit_count": 0,
            "damage_bonus_percent": 0,
            "player_id": 1,
            "hero_state_json": "{}",
        }
        h = {
            "class_tag": "mage",
            "faction": "neutral",
            "base_attack": 100,
            "passive_key": "none",
            "attack_range": "ranged",
            "damage_type": "magic",
        }
        plan = {
            "damage": 100,
            "base_damage": 100,
            "pure_bonus": 1000,
            "events": [],
            "boss_skip_turns": 0,
            "remove_magic_shield": False,
            "extra_attacks": 0,
        }
        with patch("app.mini.boss.calculations.resolve_attack", return_value=plan):
            result = calculate_hit(boss, participant, h)
        self.assertEqual(result["damage"], 1120)


class PetTests(MiniCase):
    def egg(self, element="ice"):
        with transaction(self.db) as conn:
            return pets.first_egg(conn, self.pid, 100, chooser=lambda choices: element)[
                "id"
            ]

    def fund(self, value=1000):
        with transaction(self.db) as conn:
            change(conn, self.pid, value, "test", "test-fund")

    def fixture_hero(self, element="ice"):
        ready = patch("app.mini.mythic.content.ready_hero", return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        hero = self.hero()
        self.sql(
            "UPDATE mini_heroes SET rarity='mythic',mythic_element=? WHERE id=?",
            (element, hero["id"]),
        )
        self.sql("DELETE FROM mini_player_heroes WHERE player_id=?", (self.pid,))
        return hero

    def test_free_egg_permanent_and_purchase_idempotent(self):
        first = self.egg()
        self.fund()
        result = pets.buy(
            self.pid, "buy", self.db, now=101, chooser=lambda choices: choices[0]
        )
        self.assertNotEqual(result["element"], "ice")
        self.assertEqual(pets.buy(self.pid, "buy", self.db, now=102), result)
        with transaction(self.db) as conn:
            self.assertIsNone(pets.first_egg(conn, self.pid, 103))
        self.assertEqual(len(pets.state(self.pid, self.db, now=104)["pets"]), 2)

    def test_reserve_freezes_growth_decay_protection_cooldown(self):
        a = self.egg()
        self.fund()
        b = pets.buy(self.pid, "b", self.db, now=100, chooser=lambda c: c[0])["id"]
        pets.activate(self.pid, a, "a", self.db, now=100)
        pets.care(self.pid, a, "sleep", "sleep", self.db, now=100)
        pets.activate(self.pid, b, "b-active", self.db, now=100 + 3600)
        pets.activate(self.pid, a, "a-again", self.db, now=100 + 86400 * 9)
        state = pets.state(self.pid, self.db, now=100 + 86400 * 9)["pets"][0]
        self.assertEqual(state["active_seconds"], 3600)
        self.assertEqual(state["mood"], 70)
        with self.assertRaises(ValueError):
            pets.care(self.pid, a, "sleep", "too-soon", self.db, now=100 + 86400 * 9)

    def test_growth_pauses_not_resets_and_unlock_is_once(self):
        hero = self.fixture_hero()
        a = self.egg()
        pets.activate(self.pid, a, "active", self.db, now=100)
        self.sql(
            "UPDATE mini_mythic_pets SET mood=100,protect_until=9999999 WHERE id=?",
            (a,),
        )
        state = pets.state(self.pid, self.db, now=100 + 72 * 3600)["pets"][0]
        self.assertEqual(state["stage"], "wonder")
        self.sql("UPDATE mini_mythic_pets SET mood=79 WHERE id=?", (a,))
        state = pets.state(self.pid, self.db, now=100 + 80 * 3600)["pets"][0]
        self.assertEqual(state["stage_seconds"], 0)
        self.sql("UPDATE mini_mythic_pets SET mood=100 WHERE id=?", (a,))
        state = pets.state(self.pid, self.db, now=100 + 176 * 3600)["pets"][0]
        self.assertEqual(state["stage"], "adult")
        self.assertEqual(state["hero_id"], hero["id"])
        self.assertEqual(
            self.sql(
                "SELECT stars FROM mini_player_heroes WHERE player_id=?", (self.pid,)
            )[0]["stars"],
            1,
        )
        pets.state(self.pid, self.db, now=100 + 177 * 3600)
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM mini_player_heroes WHERE player_id=?", (self.pid,)
                )
            ),
            1,
        )

    def test_care_all_stages_and_boundaries(self):
        a = self.egg()
        pets.activate(self.pid, a, "active", self.db, now=100)
        self.assertEqual(
            pets.care(self.pid, a, "feed", "feed", self.db, now=100)["mood"], 80
        )
        self.assertEqual(
            pets.care(self.pid, a, "feed", "feed", self.db, now=101)["mood"], 80
        )
        with self.assertRaises(ValueError):
            pets.care(self.pid, a, "feed", "feed-2", self.db, now=101)
        self.assertEqual(
            pets.state(self.pid, self.db, now=100 + 8 * 3600)["pets"][0]["mood"], 77
        )

    def test_equipment_limit_shared_and_no_mood_gain(self):
        from app.mini.equipment.service import grant_in_transaction, equip

        a = self.egg()
        pets.activate(self.pid, a, "active", self.db, now=100)
        self.sql("UPDATE mini_mythic_pets SET stage='wonder' WHERE id=?", (a,))
        code = self.sql("SELECT code FROM mini_equipment LIMIT 1")[0]["code"]
        with transaction(self.db) as conn:
            for _ in range(6):
                grant_in_transaction(conn, self.pid, code)
        equip(self.pid, code, self.db)
        pets.consume(self.pid, a, code, "eat0", self.db, now=100, confirmed=True)
        self.sql(
            "UPDATE mini_equipment_owned SET quantity=1 WHERE player_id=? AND code=?",
            (self.pid, code),
        )
        with self.assertRaises(ValueError):
            pets.consume(
                self.pid, a, code, "no-equipped", self.db, now=100, confirmed=True
            )
        self.sql(
            "UPDATE mini_equipment_owned SET quantity=5 WHERE player_id=? AND code=?",
            (self.pid, code),
        )
        for i in range(1, 5):
            pets.consume(
                self.pid, a, code, "eat" + str(i), self.db, now=100, confirmed=True
            )
        with self.assertRaises(ValueError):
            pets.consume(self.pid, a, code, "eat6", self.db, now=100, confirmed=True)
        self.assertEqual(pets.state(self.pid, self.db, now=100)["pets"][0]["mood"], 70)
        self.assertEqual(
            self.sql(
                "SELECT quantity FROM mini_equipment_owned WHERE player_id=?",
                (self.pid,),
            )[0]["quantity"],
            1,
        )

    def test_claim_shared_day_and_atomic_upgrade_total(self):
        hero = self.fixture_hero()
        a = self.egg()
        pets.activate(self.pid, a, "active", self.db, now=100)
        self.sql("UPDATE mini_mythic_pets SET stage='adult',mood=100 WHERE id=?", (a,))
        self.sql(
            "INSERT INTO mini_player_heroes(player_id,hero_id,stars) VALUES(?,?,1)",
            (self.pid, hero["id"]),
        )
        pets.claim(self.pid, "claim", self.db, now=100)
        with self.assertRaises(ValueError):
            pets.claim(self.pid, "another", self.db, now=100)
        self.fund(300)
        for star in range(1, 6):
            result = upgrade(self.pid, hero["id"], star, self.db)
            self.assertEqual(result["attack"], hero["attack"])
            self.assertEqual(upgrade(self.pid, hero["id"], star, self.db), result)
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players WHERE id=?", (self.pid,))[
                0
            ]["mythic_shards"],
            1,
        )

    def test_failed_spend_rolls_back(self):
        self.egg()
        self.fund(74)
        with self.assertRaises(ValueError):
            pets.buy(self.pid, "fail", self.db, now=100)
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players WHERE id=?", (self.pid,))[
                0
            ]["mythic_shards"],
            74,
        )


class RaidTests(MiniCase):
    def setUp(self):
        super().setUp()
        self.template = {
            "code": "fixture_raid",
            "name": "Test fixture only",
            "max_hp": 100000,
            "element": "ice",
            "counter_elements": ["fire"],
            "features": [],
            "regeneration_percent": 0,
            "shadow_extractable": False,
        }
        mock = patch("app.mini.mythic.raid.service.load", return_value=[self.template])
        mock.start()
        self.addCleanup(mock.stop)
        self.hero_value = self.hero()
        self.sql(
            "UPDATE mini_heroes SET attack=100 WHERE id=?", (self.hero_value["id"],)
        )
        self.boss = raid.start(self.pid, "fixture_raid", "start", self.db, now=100)

    def test_attack_fallback_limit_and_replay(self):
        result = raid.attack(
            self.pid,
            self.boss["id"],
            self.hero_value["id"],
            "hit",
            self.db,
            now=100,
            chooser=lambda c: c[0],
        )
        self.assertEqual(result["actual_hp_damage"], 10)
        self.assertFalse(result["passive_configured"])
        self.assertEqual(
            raid.attack(
                self.pid,
                self.boss["id"],
                self.hero_value["id"],
                "hit",
                self.db,
                now=101,
            ),
            result,
        )
        with self.assertRaises(ValueError):
            raid.attack(
                self.pid,
                self.boss["id"],
                self.hero_value["id"],
                "new",
                self.db,
                now=101,
            )
        self.assertEqual(result["attempts_left"], 4)
        self.assertIsNotNone(result["egg"])

    def test_one_active_and_parallel_same_attack(self):
        with self.assertRaises(ValueError):
            raid.start(self.pid, "fixture_raid", "start2", self.db, now=100)

        def hit(_):
            return raid.attack(
                self.pid,
                self.boss["id"],
                self.hero_value["id"],
                "parallel",
                self.db,
                now=100,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(hit, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")), 1)

    def test_finalization_once_and_no_overkill(self):
        self.sql(
            "UPDATE mini_mythic_raids SET current_hp=3 WHERE id=?", (self.boss["id"],)
        )
        result = raid.attack(
            self.pid, self.boss["id"], self.hero_value["id"], "last", self.db, now=100
        )
        self.assertEqual(result["actual_hp_damage"], 3)
        self.assertEqual(result["final"]["rewards"][0]["amount"], 36)
        self.assertEqual(
            raid.attack(
                self.pid,
                self.boss["id"],
                self.hero_value["id"],
                "last",
                self.db,
                now=101,
            ),
            result,
        )
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")), 1)

    def test_earth_attributes_115_and_15_separately(self):
        value = {
            **self.boss,
            "state": {
                "counter_active": True,
                "earth": {"charges": 3, "owner": 88, "level": 0},
            },
            "snapshot": self.template,
        }
        hero = {"attack": 100, "stars": 0, "passive_key": "none"}
        result = resolve(value, hero, self.pid)
        self.assertEqual(result["events"][0]["actual_hp_damage"], 115)
        self.assertEqual(result["events"][1]["actual_hp_damage"], 15)
        self.assertEqual(result["events"][1]["actor_id"], 88)

    def test_eclipse_increases_both_once_and_keeps_original(self):
        value = {**self.boss, "state": {}, "snapshot": self.template}
        hero = {
            "rarity": "mythic",
            "stars": 6,
            "attack": 1,
            "passives": {
                "normal": {"key": "mythic_moon"},
                "raid": {"key": "mythic_moon"},
            },
        }
        result = resolve(value, hero, self.pid, chooser=lambda _: 3)
        self.assertEqual(result["max_hp"], 115000)
        self.assertGreater(result["current_hp"], 100000)
        value.update(result)
        again = resolve(value, hero, self.pid, chooser=lambda _: 0)
        self.assertEqual(again["max_hp"], 115000)
