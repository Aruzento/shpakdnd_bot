"""Four approved raid mechanics; all boss records/PNG fixtures are temporary."""

import copy
import json
import sqlite3
import struct
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.mini.combat.modes import freeze_hero
from app.mini.mythic.policies import APPROVED
from app.mini.mythic.raid import catalog, mechanics, service as raids
from app.mini.mythic.raid.effects import resolve
from app.mini.players import create_mini_player
from tests.v1_3.support import MiniCase
from tests.v1_5 import test_completion_audit as audit_support


def fixture_png():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * 6) * 2))
        + chunk(b"IEND", b"")
    )


def fixture_boss(kind, index=0):
    hp, element, counter, features = mechanics.PROFILES[kind]
    return {
        "code": f"fixture_{index}",
        "name": f"Temporary fixture {index}",
        "image": f"fixture_{index}.png",
        "max_hp": hp,
        "element": element,
        "counter_elements": [counter],
        "features": list(features),
        "regeneration_percent": 0,
        "shadow_extractable": False,
        "counter_policy": catalog.COUNTER_POLICY,
        "mechanic": copy.deepcopy(mechanics.SPECS[kind]),
    }


def pure_raid(kind, *, count=0, hp=None):
    snapshot = fixture_boss(kind)
    snapshot["runtime_policies"] = copy.deepcopy(APPROVED)
    maximum = snapshot["max_hp"]
    state = mechanics.initialize(snapshot, maximum)
    state["mechanic"]["confirmed_attacks"] = count
    return {
        "snapshot": snapshot,
        "state": state,
        "original_max_hp": maximum,
        "current_hp": maximum if hp is None else hp,
        "max_hp": maximum,
    }


def pure_hero(
    *,
    element=None,
    attack=100,
    damage_type="slashing",
    reach="melee",
    trait=None,
    overlays=(),
):
    key = "mythic_" + element if element else "none"
    return freeze_hero(
        {
            "id": 1,
            "attack": attack,
            "stars": 1 if element else 0,
            "rarity": "mythic" if element else "common",
            "passive_key": key,
            "passives": {mode: {"key": key} for mode in ("normal", "raid")},
            "attack_range": reach,
            "damage_type": damage_type,
            "special_trait": trait,
            "elements": list(overlays),
        },
        mode="raid",
    )


def advance(raid, hero, **kwargs):
    result = resolve(raid, hero, 1, **kwargs)
    raid.update({key: result[key] for key in ("state", "current_hp", "max_hp")})
    return result


class RaidMechanicValidation(unittest.TestCase):
    def test_approved_specs_are_deep_copied_and_exact(self):
        for kind, spec in mechanics.SPECS.items():
            with self.subTest(kind=kind):
                result = mechanics.validate(spec)
                self.assertEqual(result, spec)
                self.assertIsNot(result, spec)
                boss = fixture_boss(kind)
                self.assertEqual(mechanics.validate_profile(boss), spec)
                for key, value in (
                    ("max_hp", boss["max_hp"] + 1),
                    ("counter_elements", ["fire", "ice"]),
                    ("element", "earth"),
                    ("regeneration_percent", 1),
                ):
                    changed = copy.deepcopy(boss)
                    changed[key] = value
                    with self.assertRaises(ValueError):
                        mechanics.validate_profile(changed)

    def test_unknown_types_extra_missing_fields_and_wrong_types(self):
        invalid = [None, [], True, {}, {"type": []}, {"type": "fixture_unknown"}]
        for kind, spec in mechanics.SPECS.items():
            invalid += [
                dict(spec, surprise=True),
                {k: v for k, v in spec.items() if k != "type"},
            ]
            for key, value in spec.items():
                if type(value) is int:
                    for wrong in (True, 0, -1, 1.5, "10", 2**63, value + 1):
                        invalid.append({**copy.deepcopy(spec), key: wrong})
                if type(value) is bool:
                    for wrong in (0, 1, "true", False):
                        invalid.append({**copy.deepcopy(spec), key: wrong})
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                mechanics.validate(spec)

    def test_thresholds_and_phase_features_strict(self):
        spec = mechanics.SPECS["hp_defense_phases"]
        for key, bad in [
            ("threshold_percent", [75, 25, 50]),
            ("threshold_percent", [75, 50, 50]),
            ("threshold_percent", [75, 50, True]),
            ("threshold_percent", [100, 50, 25]),
            ("threshold_percent", [75, 50]),
            ("threshold_percent", [76, 50, 25]),
            ("phase_features", [["armored", "armored"], [], [], []]),
            ("phase_features", [["magic"], [], [], []]),
            ("phase_features", [[True], [], [], []]),
            ("phase_features", [["armored"], [], []]),
            ("threshold_base", "max_hp"),
        ]:
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                mechanics.validate({**copy.deepcopy(spec), key: bad})

    def test_legacy_missing_mechanic_state_and_result_unchanged(self):
        raid = pure_raid("armor_cycle")
        del raid["snapshot"]["mechanic"]
        raid["state"] = {"counter_active": True}
        result = resolve(raid, pure_hero(), 1)
        self.assertEqual(result["actual_hp_damage"], 80)
        self.assertNotIn("mechanic", result)
        self.assertNotIn("mechanic", result["state"])
        self.assertIsNone(mechanics.prepare(raid, raid["state"]))
        self.assertEqual(mechanics.lines(raid), [])


class RaidMechanicEffects(unittest.TestCase):
    def test_armor_cycle_exact_attack_indices_and_next_tag(self):
        expected = {
            1: True,
            9: True,
            10: True,
            11: False,
            15: False,
            16: True,
            25: True,
            26: False,
        }
        for index, armored in expected.items():
            raid = pure_raid("armor_cycle", count=index - 1)
            result = resolve(raid, pure_hero(overlays=("ice",)), 1)
            self.assertEqual("armored" in result["mechanic"]["features_used"], armored)
            self.assertEqual(result["actual_hp_damage"], 80 if armored else 100)
            self.assertEqual(result["state"]["mechanic"]["confirmed_attacks"], index)
            if index in (10, 15, 25):
                self.assertEqual(
                    len(
                        [
                            e
                            for e in result["audit"]
                            if e["type"] == "raid_mechanic_transition"
                        ]
                    ),
                    1,
                )
            self.assertEqual(raid["snapshot"]["features"], ["armored"])

    def test_armor_reduction_types_and_sun_bypass(self):
        for dtype, reach, damage in (
            ("slashing", "melee", 80),
            ("slashing", "ranged", 90),
            ("magic", "melee", 90),
            ("magic", "ranged", 100),
        ):
            result = resolve(
                pure_raid("armor_cycle"),
                pure_hero(damage_type=dtype, reach=reach, overlays=("ice",)),
                1,
            )
            self.assertEqual(result["actual_hp_damage"], damage)
        raid = pure_raid("armor_cycle")
        raid["state"]["sun"] = {"charges": 1, "level": 0}
        result = resolve(raid, pure_hero(overlays=("ice",)), 1)
        self.assertEqual(result["actual_hp_damage"], 120)
        self.assertIn("armored", result["mechanic"]["features_used"])

    def test_zero_attempts_advance_cycles_but_extra_events_do_not(self):
        raid = pure_raid("armor_cycle", count=9)
        result = resolve(raid, pure_hero(attack=0), 1)
        self.assertEqual(result["actual_hp_damage"], 0)
        self.assertEqual(result["mechanic"]["phase_next"], 2)
        raid = pure_raid("armor_cycle", count=9)
        raid["state"]["fire"] = {"charges": 1, "damage": 200, "level": 0}
        result = resolve(raid, pure_hero(element="ice", reach="ranged"), 1)
        self.assertGreaterEqual(len(result["events"]), 3)
        self.assertEqual(result["state"]["mechanic"]["confirmed_attacks"], 10)
        self.assertEqual(
            result["actual_hp_damage"],
            sum(e["actual_hp_damage"] for e in result["events"]),
        )

    def test_flight_cycle_indices_reachability_and_zero_counter(self):
        for index in (1, 8, 9, 10, 11, 18, 19):
            flying = (index - 1) % 10 < 8
            for reach, trait, can_reach in (
                ("melee", None, not flying),
                ("ranged", None, True),
                ("melee", "flying", True),
            ):
                raid = pure_raid("flight_cycle", count=index - 1)
                result = resolve(
                    raid, pure_hero(element="star", reach=reach, trait=trait), 1
                )
                self.assertEqual(result["actual_hp_damage"], 100 if can_reach else 0)
                self.assertEqual(
                    "flying" in result["mechanic"]["features_used"], flying
                )
                self.assertTrue(result["state"]["counter_active"])
                self.assertEqual(
                    result["state"]["mechanic"]["confirmed_attacks"], index
                )

    def test_counter_is_ten_then_full_before_dynamic_defense(self):
        raid = pure_raid("flight_cycle")
        first = advance(raid, pure_hero(reach="ranged"))
        self.assertEqual(first["actual_hp_damage"], 10)
        second = advance(raid, pure_hero(element="star"))
        self.assertEqual(second["actual_hp_damage"], 0)
        self.assertTrue(raid["state"]["counter_active"])
        self.assertEqual(
            advance(raid, pure_hero(reach="ranged"))["actual_hp_damage"], 100
        )
        raid["snapshot"]["counter_policy"] = catalog.LEGACY_COUNTER_POLICY
        raid["state"].pop("counter_active")
        raid["state"].pop("counter_source")
        self.assertNotIn(
            "counter_active", advance(raid, pure_hero(element="star"))["state"]
        )

    def test_heal_exact_15_30_and_unchanged_positive_contribution(self):
        for count in (13, 14, 15, 28, 29):
            raid = pure_raid("periodic_heal", count=count, hp=120000)
            result = resolve(raid, pure_hero(reach="ranged", overlays=("sun",)), 1)
            healing = (count + 1) % 15 == 0
            self.assertEqual(result["current_hp"], 119900 + (325 if healing else 0))
            self.assertEqual(result["actual_hp_damage"], 100)
            self.assertEqual(
                len([e for e in result["audit"] if e["type"] == "raid_periodic_heal"]),
                int(healing),
            )
            self.assertEqual(result["events"][0]["hp_after"], 119900)

    def test_heal_clamp_no_resurrection_and_original_eclipse_basis(self):
        raid = pure_raid("periodic_heal", count=14, hp=129999)
        result = resolve(raid, pure_hero(attack=0), 1)
        self.assertEqual(result["mechanic"]["heal"]["healed_hp"], 1)
        raid = pure_raid("periodic_heal", count=14, hp=1)
        result = resolve(raid, pure_hero(overlays=("sun",)), 1)
        self.assertEqual(result["current_hp"], 0)
        self.assertTrue(result["mechanic"]["heal"]["fatal_hit"])
        raid = pure_raid("periodic_heal", count=14, hp=100000)
        result = resolve(
            raid, pure_hero(element="moon", attack=0), 1, chooser=lambda _: 3
        )
        self.assertEqual(result["max_hp"], 143000)
        self.assertEqual(result["mechanic"]["heal"]["requested_hp"], 325)
        self.assertEqual(result["current_hp"], 113325)

    def test_sun_suppression_uses_incoming_charge_not_just_acquired_buff(self):
        for incoming, attack in ((0, 100), (1, 100), (1, 0)):
            raid = pure_raid("periodic_heal", count=14, hp=120000)
            if incoming:
                raid["state"]["sun"] = {"charges": incoming, "level": 0}
            result = resolve(raid, pure_hero(element="sun", attack=attack), 1)
            heal = result["mechanic"]["heal"]
            self.assertEqual(heal["sun_suppressed"], bool(incoming))
            self.assertEqual(heal["healed_hp"], 0 if incoming else 325)
            self.assertEqual(result["state"]["mechanic"]["confirmed_attacks"], 15)

    def test_hp_phase_exact_boundaries(self):
        for hp, phase in (
            (150000, 1),
            (112501, 1),
            (112500, 2),
            (112499, 2),
            (75001, 2),
            (75000, 3),
            (74999, 3),
            (37501, 3),
            (37500, 4),
            (37499, 4),
        ):
            raid = pure_raid("hp_defense_phases", hp=hp)
            result = resolve(raid, pure_hero(attack=0, reach="ranged"), 1)
            self.assertEqual(result["mechanic"]["phase_used"], phase)
            self.assertEqual(
                result["mechanic"]["features_used"],
                sorted(
                    mechanics.SPECS["hp_defense_phases"]["phase_features"][phase - 1]
                ),
            )

    def test_hp_jump_uses_old_defense_then_next_phase_once(self):
        raid = pure_raid("hp_defense_phases")
        result = advance(
            raid, pure_hero(attack=100000, reach="ranged", overlays=("moon",))
        )
        self.assertEqual(result["actual_hp_damage"], 90000)
        self.assertEqual(result["mechanic"]["phase_used"], 1)
        self.assertEqual(result["mechanic"]["phase_next"], 3)
        events = [e for e in result["audit"] if e["type"] == "raid_mechanic_transition"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["crossed_thresholds"], [75, 50])
        self.assertEqual(
            advance(raid, pure_hero(reach="ranged"))["actual_hp_damage"], 100
        )
        raid = pure_raid("hp_defense_phases")
        result = resolve(
            raid, pure_hero(attack=140000, reach="ranged", overlays=("moon",)), 1
        )
        self.assertEqual(result["mechanic"]["phase_next"], 4)
        self.assertEqual(
            [e for e in result["audit"] if e["type"] == "raid_mechanic_transition"][0][
                "crossed_thresholds"
            ],
            [75, 50, 25],
        )

    def test_hp_phase_does_not_rewind_after_heal_or_eclipse(self):
        raid = pure_raid("hp_defense_phases", hp=37500)
        advance(raid, pure_hero(attack=0, reach="ranged"))
        raid["current_hp"] = 150000
        result = advance(raid, pure_hero(element="moon", attack=0), chooser=lambda _: 3)
        self.assertEqual(result["max_hp"], 165000)
        self.assertEqual(result["mechanic"]["features_used"], [])
        self.assertEqual(result["mechanic"]["phase_next"], 4)
        self.assertEqual(raid["original_max_hp"], 150000)

    def test_additional_hp_events_and_heal_retain_lowest_phase(self):
        raid = pure_raid("hp_defense_phases")
        h = pure_hero(reach="ranged", overlays=("moon",))
        h["passives"]["raid"] = {
            "key": "fixture_raid",
            "configured": True,
            "definition": {
                "effects": [
                    {
                        "type": "original_hp_percent",
                        "trigger": "after_attack",
                        "percent": 70,
                    }
                ]
            },
        }
        raid["snapshot"][
            "regeneration_percent"
        ] = 40  # Existing static engine; temporary pure fixture only.
        result = resolve(raid, h, 1)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["mechanic"]["phase_used"], 1)
        self.assertEqual(result["mechanic"]["phase_next"], 3)
        self.assertGreater(result["current_hp"], 75000)
        self.assertEqual(
            result["state"]["mechanic"]["last_transition"]["lowest_hp"], 44910
        )

    def test_state_and_original_inputs_are_not_mutated_and_count_overflow_rejected(
        self,
    ):
        raid = pure_raid("armor_cycle", count=9)
        frozen = copy.deepcopy(raid)
        resolve(raid, pure_hero(overlays=("ice",)), 1)
        self.assertEqual(raid, frozen)
        for key, value in (
            ("confirmed_attacks", True),
            ("confirmed_attacks", -1),
            ("confirmed_attacks", 2**63 - 1),
            ("type", "flight_cycle"),
        ):
            changed = copy.deepcopy(raid)
            changed["state"]["mechanic"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve(changed, pure_hero(), 1)

    def test_every_mythic_stays_functional_and_only_its_explicit_counter_activates(
        self,
    ):
        for kind in mechanics.SPECS:
            for element in (
                "ice",
                "lightning",
                "dark",
                "fire",
                "star",
                "earth",
                "moon",
                "sun",
            ):
                with self.subTest(kind=kind, element=element):
                    raid = pure_raid(kind)
                    result = resolve(
                        raid,
                        pure_hero(element=element, reach="ranged"),
                        1,
                        chooser=lambda choices: choices[0],
                    )
                    self.assertGreater(result["actual_hp_damage"], 0)
                    self.assertEqual(
                        result["state"].get("counter_active", False),
                        element == raid["snapshot"]["counter_elements"][0],
                    )
                    self.assertEqual(
                        result["state"]["mechanic"]["confirmed_attacks"], 1
                    )

    def test_suppressed_heal_is_not_carried_to_next_attack(self):
        raid = pure_raid("periodic_heal", count=14, hp=120000)
        raid["state"]["sun"] = {"charges": 1, "level": 0}
        first = advance(raid, pure_hero(reach="ranged", overlays=("sun",)))
        self.assertEqual(first["mechanic"]["heal"]["healed_hp"], 0)
        second = advance(raid, pure_hero(reach="ranged"))
        self.assertNotIn("heal", second["mechanic"])
        self.assertEqual(second["current_hp"], first["current_hp"] - 100)


class RaidFixture:
    def setup_raid_fixtures(self):
        self.fixture_dir = Path(self.tmp.name) / "boss-fixtures-only"
        self.fixture_dir.mkdir()
        self.bosses = [fixture_boss(kind, i) for i, kind in enumerate(mechanics.SPECS)]
        for boss in self.bosses:
            (self.fixture_dir / boss["image"]).write_bytes(fixture_png())
        self.catalog_path = self.fixture_dir / "bosses.json"
        self.write_catalog()
        self.enterContext(patch.object(catalog, "CATALOG", self.catalog_path))
        self.enterContext(patch.object(catalog, "IMAGES", self.fixture_dir))
        self.h = self.hero()
        self.sql(
            "UPDATE mini_heroes SET attack=100,attack_range='ranged',damage_type='magic',passive_key='none',passives_json='{}' WHERE id=?",
            (self.h["id"],),
        )

    def write_catalog(self):
        self.catalog_path.write_text(
            json.dumps({"schema_version": 1, "bosses": self.bosses}), encoding="utf-8"
        )

    def start_raid(self, index, *, key="start"):
        return raids.start(self.pid, self.bosses[index]["code"], key, self.db, now=100)

    def state_update(self, rid, *, count=None, current_hp=None, **extra):
        current = raids.state(self.world["id"], self.db, raid_id=rid)
        state = current["state"]
        if count is not None:
            state["mechanic"]["confirmed_attacks"] = count
        state.update(extra)
        self.sql(
            "UPDATE mini_mythic_raids SET state_json=?,current_hp=? WHERE id=?",
            (
                json.dumps(state),
                current["current_hp"] if current_hp is None else current_hp,
                rid,
            ),
        )

    def grant_other_hero(self):
        other = self.sql(
            "SELECT * FROM mini_heroes WHERE code!='Villager' AND rarity='common' ORDER BY id LIMIT 1"
        )[0]
        self.sql(
            "INSERT OR IGNORE INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
            (self.pid, other["id"]),
        )
        self.sql(
            "UPDATE mini_heroes SET attack=100,attack_range='ranged',damage_type='magic',passive_key='none',passives_json='{}' WHERE id=?",
            (other["id"],),
        )
        return other

    def mechanic_audit(self):
        return self.sql(
            "SELECT * FROM mini_mythic_raid_audit WHERE action IN ('raid_mechanic_transition','raid_periodic_heal') ORDER BY id"
        )

    def hit(self, rid, key, *, hero=None, now=100):
        return raids.attack(
            self.pid, rid, (hero or self.h)["id"], key, self.db, now=now
        )


class RaidMechanicIntegration(RaidFixture, MiniCase):
    def setUp(self):
        super().setUp()
        self.setup_raid_fixtures()

    def test_real_four_record_catalog_contract_and_initial_frozen_states(self):
        loaded = catalog.load()
        self.assertEqual(len(loaded), 4)
        self.assertEqual(
            [b["counter_elements"] for b in loaded],
            [["ice"], ["star"], ["sun"], ["moon"]],
        )
        self.assertEqual(
            [b["max_hp"] for b in loaded], [100000, 115000, 130000, 150000]
        )
        for index in range(4):
            raid = self.start_raid(index, key=f"start-{index}")
            self.assertEqual(raid["state"]["mechanic"]["confirmed_attacks"], 0)
            self.assertEqual(
                raid["snapshot"]["mechanic"], self.bosses[index]["mechanic"]
            )
            raids.cancel(
                raid["id"],
                self.world["id"],
                self.owner,
                f"cancel-{index}",
                self.db,
                confirmed=True,
                expected_revision=0,
                with_reward=False,
            )

    def test_json_catalog_rejects_bad_mechanics_types_profiles_and_duplicate_fields(
        self,
    ):
        for mutate in (
            lambda b: b["mechanic"].update(armored_attacks=True),
            lambda b: b["mechanic"].update(exposed_attacks=0),
            lambda b: b["mechanic"].update(type="unknown"),
            lambda b: b.update(counter_elements=["ice", "sun"]),
            lambda b: b.update(counter_elements=["unknown"]),
            lambda b: b.update(features=["armored", "armored"]),
            lambda b: b.update(regeneration_percent=1),
            lambda b: b.update(
                shadow_extractable=True, shadow_hero_code="unknown_fixture"
            ),
        ):
            changed = copy.deepcopy(self.bosses)
            mutate(changed[0])
            with self.subTest(changed=changed[0]), self.assertRaises(ValueError):
                catalog.validate(
                    {"schema_version": 1, "bosses": changed}, images=self.fixture_dir
                )
        with self.assertRaises(ValueError):
            catalog.validate(
                {"schema_version": 1, "bosses": self.bosses[:3]},
                images=self.fixture_dir,
            )
        self.catalog_path.write_text(
            '{"schema_version":1,"bosses":[],"bosses":[]}', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            catalog.load()
        self.write_catalog()
        (self.fixture_dir / self.bosses[0]["image"]).write_bytes(b"invalid fixture PNG")
        with self.assertRaisesRegex(ValueError, "PNG"):
            catalog.load()

    def test_catalog_requires_all_four_distinct_mechanics_and_images(self):
        for mode in ("duplicate_mechanic", "mixed_legacy", "shared_image"):
            bosses = copy.deepcopy(self.bosses)
            if mode == "duplicate_mechanic":
                bosses[1] = {
                    **copy.deepcopy(bosses[0]),
                    "code": "fixture_distinct_code",
                    "image": bosses[1]["image"],
                }
            elif mode == "mixed_legacy":
                bosses[1].pop("mechanic")
            else:
                bosses[1]["image"] = bosses[0]["image"]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                catalog.validate(
                    {"schema_version": 1, "bosses": bosses}, images=self.fixture_dir
                )
        legacy = copy.deepcopy(self.bosses)
        for boss in legacy:
            boss.pop("mechanic")
        self.assertEqual(
            len(
                catalog.validate(
                    {"schema_version": 1, "bosses": legacy}, images=self.fixture_dir
                )
            ),
            4,
        )

    def test_start_and_attack_replay_survive_invalid_hot_edit_and_restart(self):
        raid = self.start_raid(0)
        self.state_update(raid["id"], count=9, counter_active=True)
        frozen = copy.deepcopy(raid["snapshot"])
        self.catalog_path.write_text("{invalid JSON hot edit", encoding="utf-8")
        self.assertEqual(self.start_raid(0)["id"], raid["id"])
        first = self.hit(raid["id"], "hit")
        for _ in range(3):
            from app.mini.schema import init_mini_db

            init_mini_db(self.db)
            self.assertEqual(self.hit(raid["id"], "hit", now=999999), first)
        current = raids.state(self.world["id"], self.db)
        self.assertEqual(current["snapshot"], frozen)
        self.assertEqual(current["state"]["mechanic"]["confirmed_attacks"], 10)
        self.assertEqual(current["state"]["mechanic"]["phase"], 2)
        self.assertEqual(len(self.mechanic_audit()), 1)
        self.assertEqual(current["revision"], 1)

    def test_armor_and_flight_cycles_survive_moscow_days(self):
        for index, total, boundaries in ((0, 26, {10, 15, 25}), (1, 19, {8, 10, 18})):
            raid = self.start_raid(index, key=f"start-{index}")
            for number in range(1, total + 1):
                outcome = self.hit(
                    raid["id"], f"hit-{index}-{number}", now=100 + 86400 * number
                )
                self.assertEqual(outcome["mechanic"]["attack_number"], number)
            logged = [
                json.loads(r["payload"])["attack_number"]
                for r in self.mechanic_audit()
                if r["raid_id"] == raid["id"]
            ]
            self.assertEqual(set(logged), boundaries)
            current = raids.state(self.world["id"], self.db)
            self.assertEqual(current["state"]["mechanic"]["confirmed_attacks"], total)
            self.assertEqual(
                current["snapshot"]["features"], self.bosses[index]["features"]
            )
            raids.cancel(
                raid["id"],
                self.world["id"],
                self.owner,
                f"cancel-{index}",
                self.db,
                confirmed=True,
                expected_revision=total,
                with_reward=False,
            )

    def test_zero_flight_counter_activation_rollbacks_and_attempt_limits(self):
        raid = self.start_raid(1)
        props = {mode: {"key": "mythic_star"} for mode in ("normal", "raid")}
        self.sql(
            "UPDATE mini_heroes SET rarity='mythic',mythic_element='star',passive_key='mythic_star',passives_json=?,attack_range='melee' WHERE id=?",
            (json.dumps(props), self.h["id"]),
        )
        self.sql(
            "UPDATE mini_player_heroes SET stars=1 WHERE player_id=? AND hero_id=?",
            (self.pid, self.h["id"]),
        )
        self.sql(
            "CREATE TRIGGER fail_counter BEFORE INSERT ON mini_mythic_raid_audit WHEN NEW.action='counter_activated' BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.hit(raid["id"], "zero")
        self.assertEqual(
            raids.state(self.world["id"], self.db)["state"]["mechanic"][
                "confirmed_attacks"
            ],
            0,
        )
        self.sql("DROP TRIGGER fail_counter")
        first = self.hit(raid["id"], "zero")
        self.assertEqual(first["actual_hp_damage"], 0)
        self.assertTrue(first["state"]["counter_active"])
        self.assertIsNone(first["egg"])
        self.assertEqual(first["attempts_left"], 9)
        self.assertEqual(self.hit(raid["id"], "zero"), first)
        with self.assertRaisesRegex(ValueError, "сегодня"):
            self.hit(raid["id"], "another")
        with self.assertRaises(ValueError):
            self.hit(raid["id"], "wrong", hero={"id": 999999})
        self.assertEqual(
            raids.state(self.world["id"], self.db)["state"]["mechanic"][
                "confirmed_attacks"
            ],
            1,
        )
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM mini_mythic_raid_audit WHERE action='counter_activated'"
                )
            ),
            1,
        )

    def test_ten_zero_attempts_limit_and_new_day_no_phase_reset(self):
        raid = self.start_raid(1)
        heroes = self.sql(
            "SELECT * FROM mini_heroes WHERE rarity IN ('common','uncommon','rare','legendary') ORDER BY id LIMIT 11"
        )
        for h in heroes:
            self.sql(
                "INSERT OR IGNORE INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
                (self.pid, h["id"]),
            )
            self.sql(
                "UPDATE mini_heroes SET attack=0,attack_range='melee',passive_key='none',passives_json='{}' WHERE id=?",
                (h["id"],),
            )
        for i, h in enumerate(heroes[:10]):
            result = self.hit(raid["id"], f"zero-{i}", hero=h)
            self.assertEqual(result["actual_hp_damage"], 0)
        with self.assertRaisesRegex(ValueError, "десяти"):
            self.hit(raid["id"], "eleven", hero=heroes[10])
        result = self.hit(raid["id"], "tomorrow", hero=heroes[0], now=86500)
        self.assertEqual(result["mechanic"]["attack_number"], 11)
        self.assertEqual(result["mechanic"]["features_used"], ["flying"])
        self.assertEqual(result["attempts_left"], 9)

    def test_heal_325_is_not_negative_damage_and_repeat_never_heals_twice(self):
        raid = self.start_raid(2)
        self.state_update(raid["id"], count=14, current_hp=120000, counter_active=True)
        result = self.hit(raid["id"], "heal")
        self.assertEqual(result["current_hp"], 120225)
        self.assertEqual(result["actual_hp_damage"], 100)
        self.assertEqual(
            self.sql("SELECT damage FROM mini_mythic_raid_damage")[0]["damage"], 100
        )
        self.assertEqual(len(self.mechanic_audit()), 1)
        self.assertEqual(
            json.loads(self.mechanic_audit()[0]["payload"])["healed_hp"], 325
        )
        for _ in range(3):
            self.assertEqual(self.hit(raid["id"], "heal"), result)
        self.assertEqual(raids.state(self.world["id"], self.db)["current_hp"], 120225)
        self.assertEqual(len(self.mechanic_audit()), 1)

    def test_mechanic_audit_and_attack_ledger_failures_roll_back_everything(self):
        for index, table in enumerate(
            (
                "mini_mythic_raid_audit",
                "mini_mythic_raid_attacks",
                "mini_mythic_raid_damage",
            )
        ):
            raid = self.start_raid(0, key=f"start-{index}")
            self.state_update(raid["id"], count=9, counter_active=True)
            before = raids.state(self.world["id"], self.db)
            self.sql(
                f"CREATE TRIGGER fail_insert BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'fixture'); END"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self.hit(raid["id"], f"hit-{index}")
            self.assertEqual(raids.state(self.world["id"], self.db), before)
            self.assertEqual(
                self.sql(
                    "SELECT * FROM mini_mythic_raid_attacks WHERE raid_id=?",
                    (raid["id"],),
                ),
                [],
            )
            self.assertEqual(self.sql("SELECT * FROM mini_mythic_pets"), [])
            self.sql("DROP TRIGGER fail_insert")
            result = self.hit(raid["id"], f"hit-{index}", now=100 + 86400 * index)
            self.assertEqual(result["state"]["mechanic"]["confirmed_attacks"], 10)
            # The starter legitimately exists after the successful retry; remove
            # only the entire per-test fixture pet to isolate the next subcase.
            self.sql("DELETE FROM mini_mythic_pets WHERE player_id=?", (self.pid,))
            raids.cancel(
                raid["id"],
                self.world["id"],
                self.owner,
                f"cancel-{index}",
                self.db,
                confirmed=True,
                expected_revision=1,
            )

    def test_heal_and_final_reward_failures_do_not_commit_phase_or_egg(self):
        raid = self.start_raid(2)
        self.state_update(raid["id"], count=14, current_hp=1, counter_active=True)
        before = raids.state(self.world["id"], self.db)
        self.sql(
            "CREATE TRIGGER fail_reward BEFORE INSERT ON mini_mythic_raid_rewards BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.hit(raid["id"], "fatal")
        self.assertEqual(raids.state(self.world["id"], self.db), before)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_pets"), [])
        self.assertEqual(self.mechanic_audit(), [])
        self.sql("DROP TRIGGER fail_reward")
        result = self.hit(raid["id"], "fatal")
        self.assertEqual(result["current_hp"], 0)
        self.assertEqual(result["mechanic"]["heal"]["healed_hp"], 0)
        self.assertEqual(result["final"]["fund"], 26)
        self.assertEqual(sum(r["share"] for r in result["final"]["rewards"]), 26)

    def test_original_r09_funds_all_four_and_compensation_after_healing(self):
        for index, fund in enumerate((20, 23, 26, 30)):
            raid = self.start_raid(index, key=f"start-{index}")
            self.state_update(raid["id"], current_hp=1, counter_active=True)
            result = self.hit(raid["id"], f"final-{index}", now=100 + 86400 * index)
            self.assertEqual(result["final"]["fund"], fund)
            self.assertEqual(sum(r["share"] for r in result["final"]["rewards"]), fund)
            self.assertEqual(
                self.hit(raid["id"], f"final-{index}", now=100 + 86400 * index), result
            )
        raid = self.start_raid(2, key="heal-compensation")
        self.sql("UPDATE mini_heroes SET attack=10000 WHERE id=?", (self.h["id"],))
        self.state_update(raid["id"], count=14, current_hp=120000, counter_active=True)
        self.hit(raid["id"], "comp-hit", now=100 + 86400 * 4)
        result = raids.cancel(
            raid["id"],
            self.world["id"],
            self.owner,
            "comp-cancel",
            self.db,
            confirmed=True,
            with_reward=True,
            expected_revision=1,
        )
        self.assertEqual(result["result"]["D"], 10000)
        self.assertEqual(result["result"]["M"], 130000)
        self.assertEqual(result["result"]["F"], 26)
        self.assertEqual(result["result"]["P"], 2)
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "cancelled")

    def test_same_operation_and_two_different_attacks_serialize(self):
        raid = self.start_raid(0)
        self.state_update(raid["id"], count=9, counter_active=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: self.hit(raid["id"], "same"), range(2)))
        self.assertEqual(values[0], values[1])
        self.assertEqual(len(self.mechanic_audit()), 1)
        other = self.grant_other_hero()
        self.state_update(raid["id"], count=14)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.hit, raid["id"], "next-day-a", now=86500),
                pool.submit(self.hit, raid["id"], "next-day-b", hero=other, now=86500),
            ]
            results = [f.result() for f in futures]
        self.assertEqual({r["mechanic"]["attack_number"] for r in results}, {15, 16})
        self.assertEqual(
            {tuple(r["mechanic"]["features_used"]) for r in results}, {(), ("armored",)}
        )
        self.assertEqual(
            raids.state(self.world["id"], self.db)["state"]["mechanic"][
                "confirmed_attacks"
            ],
            16,
        )

    def test_fatal_attack_racing_cancel_has_exactly_one_final(self):
        raid = self.start_raid(2)
        self.state_update(raid["id"], count=14, current_hp=1, counter_active=True)

        def attack():
            try:
                return self.hit(raid["id"], "final-race")
            except ValueError:
                return None

        def cancel():
            try:
                return raids.cancel(
                    raid["id"],
                    self.world["id"],
                    self.owner,
                    "cancel-race",
                    self.db,
                    confirmed=True,
                    expected_revision=0,
                )
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attack), pool.submit(cancel)]
            results = [f.result() for f in futures]
        self.assertEqual(sum(r is not None for r in results), 1)
        current = raids.state(self.world["id"], self.db)
        self.assertIn(current["status"], ("rewarded", "cancelled"))
        self.assertEqual(
            len(self.sql("SELECT * FROM mini_mythic_raid_rewards")),
            int(current["status"] == "rewarded"),
        )
        self.assertEqual(
            len(self.mechanic_audit()), int(current["status"] == "rewarded")
        )

    def test_three_migrations_keep_all_new_states_audits_and_legacy_values(self):
        from app.mini.schema import init_mini_db
        from app.mini.mythic.wallet import change, transaction

        raid = self.start_raid(2)
        self.state_update(raid["id"], count=14, current_hp=120000, counter_active=True)
        self.hit(raid["id"], "heal")
        self.sql(
            "INSERT INTO mini_mythic_fragments(player_id,hero_code,amount) VALUES(?,?,7)",
            (self.pid, "fixture_legacy"),
        )
        with transaction(self.db) as conn:
            change(conn, self.pid, 9, "fixture", "migration-fixture", {})
        tables = (
            "mini_mythic_raids",
            "mini_mythic_raid_attacks",
            "mini_mythic_raid_audit",
            "mini_mythic_raid_damage",
            "mini_mythic_fragments",
            "mini_mythic_ledger",
        )
        before = {t: self.sql(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}
        for _ in range(3):
            init_mini_db(self.db)
            self.assertEqual(
                {t: self.sql(f"SELECT * FROM {t} ORDER BY rowid") for t in tables},
                before,
            )
        self.assertEqual(
            self.sql("PRAGMA integrity_check"), [{"integrity_check": "ok"}]
        )
        self.assertEqual(self.sql("PRAGMA foreign_key_check"), [])

    def test_existing_static_snapshot_preserves_legacy_counter_and_regeneration(self):
        raid = self.start_raid(1)
        snapshot = raid["snapshot"]
        snapshot.pop("mechanic")
        snapshot.pop("counter_policy")
        snapshot["features"] = []
        snapshot["regeneration_percent"] = 1
        self.sql(
            "UPDATE mini_mythic_raids SET snapshot_json=?,state_json='{}',current_hp=100000 WHERE id=?",
            (json.dumps(snapshot), raid["id"]),
        )
        result = self.hit(raid["id"], "legacy")
        self.assertEqual(result["actual_hp_damage"], 10)
        self.assertEqual(result["current_hp"], 101140)
        self.assertNotIn("mechanic", result)
        self.assertNotIn("mechanic", result["state"])
        self.assertEqual(self.mechanic_audit(), [])

    def test_hp_phase_and_earth_attribution_survive_result_replay(self):
        other = create_mini_player(
            self.world["id"], 900124, "@fixture_other", "Other", self.db
        )["id"]
        raid = self.start_raid(3)
        self.state_update(
            raid["id"],
            current_hp=113000,
            counter_active=True,
            earth={"charges": 3, "level": 0, "owner": other},
        )
        self.sql("UPDATE mini_heroes SET attack=10000 WHERE id=?", (self.h["id"],))
        result = self.hit(raid["id"], "phase-earth")
        rows = self.sql("SELECT * FROM mini_mythic_raid_damage")
        self.assertEqual(
            sum(r["damage"] for r in rows),
            sum(e["actual_hp_damage"] for e in result["events"]),
        )
        self.assertEqual({r["player_id"] for r in rows}, {self.pid, other})
        self.assertEqual(result["mechanic"]["phase_used"], 1)
        self.assertEqual(result["mechanic"]["phase_next"], 2)
        self.assertEqual(result["state"]["mechanic"]["confirmed_attacks"], 1)
        self.assertEqual(self.hit(raid["id"], "phase-earth"), result)
        self.assertEqual(len(self.mechanic_audit()), 1)

    def test_counter_overflow_rolls_back_all_and_input_ui_remains_utf16_safe(self):
        from app.mini.mythic.raid.handlers import public_text
        from app.mini.ui.limits import units

        raid = self.start_raid(0)
        self.state_update(raid["id"], count=2**63 - 1, counter_active=True)
        before = raids.state(self.world["id"], self.db)
        with self.assertRaisesRegex(ValueError, "confirmed_attacks"):
            self.hit(raid["id"], "overflow")
        self.assertEqual(raids.state(self.world["id"], self.db), before)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_attacks"), [])
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_pets"), [])
        before["snapshot"]["name"] = "\U0001f525" * 5000
        self.assertLessEqual(units(public_text(before)), 3500)


class RaidMechanicTelegram(RaidFixture, MiniCase, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = audit_support.TelegramAudit.asyncSetUp
    message = audit_support.TelegramAudit.message
    callback = audit_support.TelegramAudit.callback

    def setUp(self):
        super().setUp()
        self.setup_raid_fixtures()

    async def test_all_four_handlers_to_sqlite_photo_state_audit_and_replay(self):
        from app.mini.mythic.raid import handlers
        from app.mini.ui.limits import units

        self.enterContext(patch.object(handlers, "IMAGES", self.fixture_dir))
        self.enterContext(
            patch("app.mini.mythic.raid.service.timestamp", return_value=100)
        )
        with patch.object(
            handlers, "load_personal_context", return_value=(self.world, self.player)
        ):
            text, markup = handlers.screen(self.world, self.player, 900123)
            self.assertEqual(
                sum(
                    1
                    for row in markup.inline_keyboard
                    for b in row
                    if ":start." in b.callback_data
                ),
                4,
            )
        for index in range(4):
            payload = "start." + handlers.short_code(self.bosses[index]["code"])
            with patch.object(
                handlers,
                "load_extended_context",
                return_value=(self.world, self.player, payload),
            ):
                await handlers.action(self.callback("fixture"))
            raid = raids.state(self.world["id"], self.db)
            self.assertEqual(raid["template_code"], self.bosses[index]["code"])
            self.assertEqual(raid["state"]["mechanic"]["confirmed_attacks"], 0)
            self.assertTrue(self.photos.called)
            self.assertEqual(
                Path(self.photos.call_args.kwargs["photo"].path).parent,
                self.fixture_dir,
            )
            self.assertLessEqual(units(self.photos.call_args.kwargs["caption"]), 1024)
            before = (9, 7, 14, 0)[index]
            self.state_update(
                raid["id"],
                count=before,
                current_hp=113000 if index == 3 else None,
                counter_active=True,
            )
            self.sql(
                "UPDATE mini_heroes SET attack=?,attack_range=? WHERE id=?",
                (
                    1000 if index == 3 else 100,
                    "melee" if index == 1 else "ranged",
                    self.h["id"],
                ),
            )
            payload = f"hitask.{raid['id']}.{self.h['id']}.fixture{index}"
            with patch.object(
                handlers,
                "load_extended_context",
                return_value=(self.world, self.player, payload),
            ):
                await handlers.action(self.callback("fixture"))
            self.assertEqual(
                len(
                    self.sql(
                        "SELECT * FROM mini_mythic_raid_attacks WHERE raid_id=?",
                        (raid["id"],),
                    )
                ),
                0,
            )
            payload = f"hit.{raid['id']}.{self.h['id']}.fixture{index}"
            with patch.object(
                handlers,
                "load_extended_context",
                return_value=(self.world, self.player, payload),
            ):
                await handlers.action(self.callback("fixture"))
                await handlers.action(self.callback("fixture"))
            current = raids.state(self.world["id"], self.db)
            self.assertEqual(current["revision"], 1)
            self.assertEqual(
                current["state"]["mechanic"]["confirmed_attacks"], before + 1
            )
            self.assertEqual(
                len(
                    self.sql(
                        "SELECT * FROM mini_mythic_raid_attacks WHERE raid_id=?",
                        (raid["id"],),
                    )
                ),
                1,
            )
            audit = [r for r in self.mechanic_audit() if r["raid_id"] == raid["id"]]
            self.assertEqual(len(audit), 1)
            screen = handlers.public_text(current)
            self.assertIn("Механика:", screen)
            self.assertIn("Активная защита:", screen)
            self.assertLessEqual(units(screen), 3500)
            self.assertLessEqual(units(self.sent.call_args.kwargs["text"]), 4000)
            if index == 1:
                attack = json.loads(
                    self.sql(
                        "SELECT result_json FROM mini_mythic_raid_attacks WHERE raid_id=?",
                        (raid["id"],),
                    )[0]["result_json"]
                )
                self.assertEqual(attack["actual_hp_damage"], 0)
                self.assertIn("На земле", screen)
            if index == 3:
                self.assertIn("Фаза 2/4", screen)
                self.assertIn("75000", screen)
            raids.cancel(
                raid["id"],
                self.world["id"],
                self.owner,
                f"ui-cancel-{index}",
                self.db,
                confirmed=True,
                expected_revision=1,
            )
            # Different raid, same real day: the next confirmation must still
            # respect the global daily cap, but the hero/day key is raid-scoped.

    async def test_fatal_periodic_heal_callback_rewards_once_and_no_resurrection(self):
        from app.mini.mythic.raid import handlers

        self.enterContext(patch.object(handlers, "IMAGES", self.fixture_dir))
        self.enterContext(
            patch("app.mini.mythic.raid.service.timestamp", return_value=100)
        )
        raid = self.start_raid(2)
        self.state_update(raid["id"], count=14, current_hp=1, counter_active=True)
        payload = f"hit.{raid['id']}.{self.h['id']}.final"
        with patch.object(
            handlers,
            "load_extended_context",
            return_value=(self.world, self.player, payload),
        ):
            await handlers.action(self.callback("fixture"))
            ledger = self.sql("SELECT * FROM mini_mythic_ledger")
            await handlers.action(self.callback("fixture"))
            self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), ledger)
        result = raids.state(self.world["id"], self.db)
        self.assertEqual(result["status"], "rewarded")
        self.assertEqual(result["current_hp"], 0)
        self.assertEqual(len(self.mechanic_audit()), 1)
        self.assertEqual(
            json.loads(self.mechanic_audit()[0]["payload"])["healed_hp"], 0
        )
        self.assertEqual(json.loads(result["result_json"])["fund"], 26)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")), 1)
        self.assertIn("Награда:", self.sent.call_args.kwargs["text"])
