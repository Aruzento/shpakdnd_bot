"""Approved O01–O10: synthetic data only, real services and disposable SQLite."""

import copy
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, Mock
from app.mini.players import create_mini_player
from app.mini.mythic.wallet import transaction
from app.mini.mythic.raid import service as raids
from app.mini.mythic.raid.rewards import compensation
from app.mini.mythic.raid.catalog import COUNTER_POLICY, LEGACY_COUNTER_POLICY
from app.mini.mythic.policies import APPROVED, LEGACY, load as policies
from tests.v1_3.support import MiniCase


class CompensationArithmetic(unittest.TestCase):
    def test_partial_largest_remainder_stable_tie_and_zero(self):
        result = compensation(100000, {3: 10000, 1: 10000, 2: 10000, 4: 0})
        self.assertEqual(
            (result["M"], result["D"], result["F"], result["P"]), (100000, 30000, 20, 6)
        )
        self.assertEqual([v["amount"] for v in result["rewards"]], [2, 2, 2])
        tied = compensation(100000, {2: 3333, 1: 3333})
        self.assertEqual(tied["P"], 1)
        self.assertEqual(
            {r["player_id"]: r["amount"] for r in tied["rewards"]}, {1: 1, 2: 0}
        )
        self.assertEqual(compensation(100000, {1: 1})["P"], 0)
        self.assertEqual(compensation(100000, {})["P"], 0)

    def test_regeneration_saturates_and_large_integer_math(self):
        for maximum, damage in [
            (100000, {1: 150000, 2: 50000}),
            (2**63 - 1, {1: 2**63 - 1, 2: 2**63 - 1}),
        ]:
            result = compensation(maximum, damage)
            self.assertEqual(result["P"], result["F"])
            self.assertEqual(sum(r["amount"] for r in result["rewards"]), result["P"])
            self.assertEqual(
                result, compensation(maximum, dict(reversed(list(damage.items()))))
            )
        for bad in (True, 1.5, 2**63):
            with self.assertRaises(ValueError):
                compensation(100000, {1: bad})


class ApprovedRaid(MiniCase):
    def setUp(self):
        super().setUp()
        self.other = create_mini_player(
            self.world["id"], 900124, "@other", "Other", self.db
        )["id"]
        self.h = self.hero()
        self.sql("UPDATE mini_heroes SET attack=100 WHERE id=?", (self.h["id"],))
        self.ally = self.sql(
            "SELECT * FROM mini_heroes WHERE rarity='common' AND code!='Villager' ORDER BY id LIMIT 1"
        )[0]
        self.sql(
            "UPDATE mini_heroes SET attack=100,attack_range='ranged',passive_key='none',passives_json='{}' WHERE id=?",
            (self.ally["id"],),
        )
        self.sql(
            "INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
            (self.other, self.ally["id"]),
        )
        self.template = {
            "code": "fixture",
            "name": "Fixture only",
            "max_hp": 100000,
            "element": "ice",
            "counter_elements": ["fire"],
            "features": [],
            "regeneration_percent": 0,
            "shadow_extractable": False,
        }
        self.enterContext(
            patch("app.mini.mythic.raid.service.load", return_value=[self.template])
        )
        self.raid = raids.start(self.pid, "fixture", "start", self.db, now=100)

    def damage(self, values):
        for pid, damage in values.items():
            self.sql(
                "INSERT OR REPLACE INTO mini_mythic_raid_damage VALUES(?,?,?)",
                (self.raid["id"], pid, damage),
            )

    def cancel(self, key="cancel", **kwargs):
        return raids.cancel(
            self.raid["id"],
            self.world["id"],
            self.owner,
            key,
            self.db,
            confirmed=True,
            expected_revision=raids.state(self.world["id"], self.db)["revision"],
            reason="Fixture reason",
            **kwargs,
        )

    def mythic(self, element, stars=1):
        props = {m: {"key": "mythic_" + element} for m in ("normal", "raid")}
        self.sql(
            "UPDATE mini_heroes SET rarity='mythic',passive_key=?,passives_json=?,mythic_element=? WHERE id=?",
            ("mythic_" + element, json.dumps(props), element, self.h["id"]),
        )
        self.sql(
            "UPDATE mini_player_heroes SET stars=? WHERE player_id=? AND hero_id=?",
            (stars, self.pid, self.h["id"]),
        )

    def snapshot(self, **changes):
        snap = raids.state(self.world["id"], self.db)["snapshot"]
        snap.update(changes)
        self.sql(
            "UPDATE mini_mythic_raids SET snapshot_json=? WHERE id=?",
            (json.dumps(snap), self.raid["id"]),
        )

    def test_cancel_compensation_exact_payout_replay_no_victory_bonuses(self):
        raids.attack(self.pid, self.raid["id"], self.h["id"], "hit", self.db, now=100)
        self.damage({self.pid: 6000, self.other: 4000})
        self.sql(
            "UPDATE mini_mythic_raids SET state_json=?",
            (json.dumps({"lunar": {"arcana": 0, "level": 5}}),),
        )
        result = self.cancel(with_reward=True)
        self.assertEqual(result["result"]["P"], 2)
        self.assertEqual([r["amount"] for r in result["result"]["rewards"]], [1, 1])
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "cancelled")
        self.assertEqual(
            {r["source"] for r in self.sql("SELECT * FROM mini_mythic_ledger")},
            {"raid_cancel_compensation"},
        )
        self.assertEqual(len(self.sql("SELECT * FROM mini_combat_arise")), 0)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_pets")), 1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")), 1)
        old = raids.cancel(
            self.raid["id"],
            self.world["id"],
            self.owner,
            "cancel",
            self.db,
            confirmed=True,
            expected_revision=1,
            reason="Fixture reason",
            with_reward=True,
        )
        self.assertFalse(old["applied"])
        self.assertEqual(old["result"], result["result"])
        with self.assertRaises(ValueError):
            self.cancel(key="another", with_reward=True)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")), 2)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_ledger")), 2)
        self.assertEqual(
            [r["amount"] for r in raids.state(self.world["id"], self.db)["ranking"]],
            [1, 1],
        )
        self.assertEqual(
            raids.history(self.world["id"], db_path=self.db)[0]["status"], "cancelled"
        )

    def test_cancel_without_reward_keeps_egg_attempts_and_reason(self):
        raids.attack(self.pid, self.raid["id"], self.h["id"], "hit", self.db, now=100)
        result = self.cancel(with_reward=False)
        self.assertEqual(result["result"]["cancel_mode"], "without_reward")
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_pets")), 1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")), 1)

    def test_zero_damage_or_zero_pool_is_audited_refusal_not_final(self):
        for index, damage in enumerate((0, 1)):
            self.damage({self.pid: damage})
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, "равен нулю"):
                    self.cancel(key=f"refused-{index}", with_reward=True)
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "active")
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM mini_mythic_raid_audit WHERE action='cancel_reward_refused'"
                )
            ),
            2,
        )

    def test_preview_revision_reason_permissions_and_zero_share_not_paid(self):
        self.damage({self.pid: 3333, self.other: 3333})
        request = raids.request_cancel(
            self.raid["id"], self.world["id"], self.owner, True, 0, self.db
        )
        self.assertEqual(request["preview"]["P"], 1)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
        with self.assertRaises(ValueError):
            raids.confirm_cancel(request["token"], self.world["id"], 99, "bad", self.db)
        with self.assertRaises(ValueError):
            raids.cancel_request(
                request["token"],
                self.world["id"],
                self.owner,
                self.db,
                reason="x" * 501,
            )
        edited = raids.cancel_request(
            request["token"], self.world["id"], self.owner, self.db, reason="x" * 500
        )
        self.assertEqual(len(edited["reason"]), 500)
        raids.attack(
            self.pid, self.raid["id"], self.h["id"], "revision", self.db, now=100
        )
        with self.assertRaisesRegex(ValueError, "изменился"):
            raids.confirm_cancel(
                request["token"], self.world["id"], self.owner, "stale", self.db
            )
        self.damage({self.pid: 3333, self.other: 3333})
        result = self.cancel(with_reward=True)
        self.assertEqual(result["result"]["P"], 1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_ledger")), 1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")), 1)

    def test_compensation_failure_rolls_back_all_state_and_can_retry(self):
        self.damage({self.pid: 20000, self.other: 10000})
        self.sql(
            "CREATE TRIGGER deny_cancel BEFORE INSERT ON mini_mythic_raid_rewards BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.cancel(with_reward=True)
        for table in ("mini_mythic_ledger", "mini_mythic_raid_rewards"):
            self.assertEqual(self.sql("SELECT * FROM " + table), [])
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "active")
        self.sql("DROP TRIGGER deny_cancel")
        self.assertEqual(self.cancel(with_reward=True)["result"]["P"], 6)

    def test_last_hit_races_with_cancel_only_one_final(self):
        self.damage({self.pid: 50000, self.other: 50000})
        self.sql("UPDATE mini_mythic_raids SET current_hp=1")

        def run(index):
            try:
                return (
                    self.cancel(with_reward=True)
                    if index == 0
                    else raids.attack(
                        self.other,
                        self.raid["id"],
                        self.ally["id"],
                        "last",
                        self.db,
                        now=100,
                    )
                )
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (0, 1)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertIn(
            raids.state(self.world["id"], self.db)["status"], ("rewarded", "cancelled")
        )
        self.assertEqual(
            len({r["source"] for r in self.sql("SELECT * FROM mini_mythic_ledger")}), 1
        )
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")), 2)

    def test_two_admin_operations_serialize(self):
        self.damage({self.pid: 100000})

        def run(key):
            try:
                return raids.cancel(
                    self.raid["id"],
                    self.world["id"],
                    self.owner if key == "a" else 900123,
                    key,
                    self.db,
                    confirmed=True,
                    expected_revision=0,
                    reason="Two admins",
                    with_reward=True,
                    actor_username=None if key == "a" else "@tester",
                )
            except ValueError:
                return None

        with patch("app.context.get_topic_admin", return_value="@tester"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, ("a", "b")))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(
            self.sql("SELECT SUM(amount) total FROM mini_mythic_ledger")[0]["total"], 20
        )

    def test_counter_zero_attack_is_confirmed_persisted_and_first_is_full(self):
        self.snapshot(features=["flying"])
        before = raids.attack(
            self.other, self.raid["id"], self.ally["id"], "before", self.db, now=100
        )
        self.assertEqual(before["actual_hp_damage"], 10)
        self.mythic("fire")
        zero = raids.attack(
            self.pid, self.raid["id"], self.h["id"], "zero", self.db, now=100
        )
        self.assertEqual(zero["actual_hp_damage"], 0)
        self.assertTrue(zero["state"]["counter_active"])
        self.assertEqual(zero["state"]["counter_source"]["actor_id"], self.pid)
        self.assertEqual(
            raids.attack(
                self.pid, self.raid["id"], self.h["id"], "zero", self.db, now=100
            ),
            zero,
        )
        from app.mini.schema import init_mini_db

        init_mini_db(self.db)
        later = raids.attack(
            self.other, self.raid["id"], self.ally["id"], "next-day", self.db, now=86500
        )
        self.assertEqual(later["actual_hp_damage"], 100)
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM mini_mythic_raid_audit WHERE action='counter_activated'"
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM mini_mythic_pets WHERE player_id=?", (self.pid,)
                )
            ),
            0,
        )

    def test_counter_rollback_and_legacy_zero_policy(self):
        self.mythic("fire")
        self.snapshot(features=["flying"])
        self.sql(
            "CREATE TRIGGER deny_attack BEFORE INSERT ON mini_mythic_raid_attacks BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            raids.attack(
                self.pid, self.raid["id"], self.h["id"], "zero", self.db, now=100
            )
        self.assertNotIn(
            "counter_active", raids.state(self.world["id"], self.db)["state"]
        )
        self.assertEqual(
            self.sql(
                "SELECT * FROM mini_mythic_raid_audit WHERE action='counter_activated'"
            ),
            [],
        )
        self.sql("DROP TRIGGER deny_attack")
        self.snapshot(counter_policy=LEGACY_COUNTER_POLICY)
        zero = raids.attack(
            self.pid, self.raid["id"], self.h["id"], "zero", self.db, now=100
        )
        self.assertNotIn("counter_active", zero["state"])
        other = raids.attack(
            self.other, self.raid["id"], self.ally["id"], "normal", self.db, now=100
        )
        self.assertEqual(other["actual_hp_damage"], 10)

    def test_damage_integer_overflow_rejects_entire_attack(self):
        self.damage({self.pid: 2**63 - 1})
        with self.assertRaisesRegex(ValueError, "SQLite"):
            raids.attack(
                self.pid, self.raid["id"], self.h["id"], "overflow", self.db, now=100
            )
        self.assertEqual(raids.state(self.world["id"], self.db)["current_hp"], 100000)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_attacks"), [])
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_pets"), [])

    def test_eclipse_all_six_stars_saved_once_original_pool(self):
        self.mythic("moon", 6)
        result = raids.attack(
            self.pid,
            self.raid["id"],
            self.h["id"],
            "moon",
            self.db,
            now=100,
            chooser=lambda values: 3 if isinstance(values[0], int) else values[0],
        )
        self.assertEqual(result["max_hp"], 115000)
        self.assertEqual(result["state"]["lunar"]["gain"], 15000)
        self.assertEqual(result["state"]["lunar"]["percent"], 15)
        later = raids.attack(
            self.pid,
            self.raid["id"],
            self.h["id"],
            "moon-next",
            self.db,
            now=86500,
            chooser=Mock(side_effect=AssertionError),
        )
        self.assertEqual(later["max_hp"], 115000)
        self.damage({self.pid: 200000})
        self.assertEqual(self.cancel(with_reward=True)["result"]["P"], 20)
        from app.mini.mythic.raid.effects import resolve
        from tests.v1_5.test_effect_matrix import hero, raid

        for stars in range(1, 7):
            value = resolve(
                raid(runtime_policies=copy.deepcopy(APPROVED)),
                hero("moon", stars),
                1,
                chooser=lambda _: 3,
            )
            self.assertEqual(value["max_hp"], 100000 + 1000 * (9 + stars))
        with self.assertRaises(ValueError):
            resolve(
                {
                    "state": {},
                    "current_hp": 2**63 - 1,
                    "max_hp": 2**63 - 1,
                    "original_max_hp": 2**63 - 1,
                    "snapshot": {"counter_elements": ["moon"]},
                },
                hero("moon"),
                1,
                chooser=lambda _: 3,
            )

    def test_all_eight_distinct_eggs_no_ninth_and_explicit_reserve_restart(self):
        from app.mini.mythic import pets

        with transaction(self.db) as conn:
            first = pets.first_egg(
                conn, self.pid, 100, chooser=lambda values: values[0]
            )
        self.sql("UPDATE mini_players SET mythic_shards=1000 WHERE id=?", (self.pid,))
        pets.activate(self.pid, first["id"], "active", self.db, now=100)
        for index in range(7):
            pets.buy(
                self.pid,
                f"buy-{index}",
                self.db,
                now=100,
                chooser=lambda values: values[0],
            )
        with self.assertRaises(ValueError):
            pets.buy(self.pid, "ninth", self.db, now=100)
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players")[0]["mythic_shards"], 475
        )
        self.assertEqual(
            len({r["element"] for r in self.sql("SELECT * FROM mini_mythic_pets")}), 8
        )
        pets.care(self.pid, first["id"], "sleep", "sleep", self.db, now=100)
        saved = pets.reserve(self.pid, first["id"], "reserve", self.db, now=3700)
        from app.mini.schema import init_mini_db

        for _ in range(3):
            init_mini_db(self.db)
        later = pets.activate(self.pid, first["id"], "again", self.db, now=864000)
        for key in (
            "active_seconds",
            "stage_seconds",
            "mood",
            "protect_until",
            "cooldowns_json",
            "next_decay",
        ):
            self.assertEqual(later[key], saved[key])
        with transaction(self.db) as conn:
            self.assertIsNone(pets.first_egg(conn, self.pid, 864000))
        with transaction(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_populated_new_journals_requests_and_legacy_survive_three_migrations(self):
        from app.mini.combat.star_rewards import award
        from app.mini.schema import init_mini_db

        self.damage({self.pid: 100000})
        request = raids.request_cancel(
            self.raid["id"], self.world["id"], self.owner, True, 0, self.db
        )
        self.cancel(with_reward=True)
        with transaction(self.db) as conn:
            award(
                conn,
                "tower",
                199,
                self.pid,
                6,
                APPROVED["normal_rewards"],
                roller=lambda _: True,
            )
        self.sql(
            "INSERT INTO mini_mythic_fragments VALUES(?,'fixture_legacy',7)",
            (self.pid,),
        )
        tables = (
            "mini_star_rewards",
            "mini_mythic_raid_cancel_requests",
            "mini_mythic_raid_rewards",
            "mini_mythic_ledger",
            "mini_mythic_raids",
            "mini_mythic_fragments",
        )
        before = {table: self.sql("SELECT * FROM " + table) for table in tables}
        self.assertTrue(all(before.values()))
        for _ in range(3):
            init_mini_db(self.db)
            self.assertEqual(
                {table: self.sql("SELECT * FROM " + table) for table in tables}, before
            )
        with transaction(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


class ApprovedNormal(MiniCase):
    def setUp(self):
        super().setUp()
        self.other = create_mini_player(
            self.world["id"], 900124, "@other", "Other", self.db
        )["id"]
        self.h = self.hero()
        self.ally = self.sql(
            "SELECT * FROM mini_heroes WHERE rarity='common' AND code!='Villager' ORDER BY id LIMIT 1"
        )[0]
        self.sql(
            "INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
            (self.other, self.ally["id"]),
        )
        self.sql(
            "UPDATE mini_players SET active_hero_id=? WHERE id=?",
            (self.ally["id"], self.other),
        )
        self.sql(
            "UPDATE mini_heroes SET passive_key='none',passives_json='{}',attack=100 WHERE id=?",
            (self.ally["id"],),
        )
        self.configure(self.h["id"], "star", 6, self.pid)
        from datetime import datetime, timezone

        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)

    def configure(self, hid, element, stars, pid):
        self.sql(
            "UPDATE mini_heroes SET rarity='mythic',passive_key=?,passives_json=?,mythic_element=?,attack=100 WHERE id=?",
            (
                "mythic_" + element,
                json.dumps(
                    {m: {"key": "mythic_" + element} for m in ("normal", "raid")}
                ),
                element,
                hid,
            ),
        )
        self.sql(
            "UPDATE mini_player_heroes SET stars=? WHERE player_id=? AND hero_id=?",
            (stars, pid, hid),
        )

    def battle(self, *, arcana=None):
        from app.mini.boss.catalog import get_boss_template
        from app.mini.boss.service import (
            create_boss_event,
            register_player,
            close_registration,
        )
        from app.mini.boss.combat import start_battle

        template = get_boss_template("simple_village_guy")
        template.update(
            max_hp=100000, ability_key="none", features=[], shadow_extractable=False
        )
        with patch("app.mini.boss.service.get_boss_template", return_value=template):
            boss = create_boss_event(
                self.world["id"], "simple_village_guy", 900123, self.db
            )
        for pid in (self.pid, self.other):
            register_player(boss["id"], pid, self.db)
        close_registration(boss["id"], self.db)
        with patch(
            "app.mini.combat.team.secrets.choice",
            return_value=arcana if arcana is not None else 0,
        ):
            start_battle(boss["id"], now=self.now, db_path=self.db)
        return boss

    def win(self, boss, rolls):
        from app.mini.boss.combat import hit_boss

        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        self.sql("UPDATE mini_bosses SET current_hp=1 WHERE id=?", (boss["id"],))
        with patch(
            "app.mini.boss.rewards.hero_abilities._roll_success", side_effect=rolls
        ) as roller:
            result = hit_boss(boss["id"], self.other, now=self.now, db_path=self.db)
        return result, roller

    def test_star_independent_eligible_rolls_highest_frozen_stars_and_replay(self):
        boss = self.battle()
        self.sql("UPDATE mini_player_heroes SET stars=1 WHERE player_id=?", (self.pid,))
        result, roller = self.win(boss, [True, False])
        self.assertEqual(
            roller.call_args_list, [unittest.mock.call(35), unittest.mock.call(35)]
        )
        self.assertEqual(
            [
                r["shards"]
                for r in self.sql("SELECT shards FROM mini_players ORDER BY id")
            ],
            [300, 0],
        )
        saved = self.sql("SELECT * FROM mini_star_rewards ORDER BY player_id")
        self.assertEqual([r["success"] for r in saved], [1, 0])
        from app.mini.boss.rewards import grant_victory_rewards

        with transaction(self.db) as conn, patch(
            "app.mini.boss.rewards.hero_abilities._roll_success",
            side_effect=AssertionError,
        ):
            row = conn.execute(
                "SELECT * FROM mini_bosses WHERE id=?", (boss["id"],)
            ).fetchone()
            grant_victory_rewards(conn, row, when=self.now)
        self.assertEqual(
            self.sql("SELECT * FROM mini_star_rewards ORDER BY player_id"), saved
        )

    def test_star_blessing_independent_source_not_replaced_or_duplicated(self):
        self.configure(self.ally["id"], "moon", 1, self.other)
        boss = self.battle(arcana=10)
        before = self.sql("SELECT * FROM mini_inventory ORDER BY player_id,item_id")
        _, roller = self.win(boss, [True, False])
        self.assertEqual(roller.call_count, 2)
        self.assertEqual(
            [
                r["mythic_shards"]
                for r in self.sql("SELECT mythic_shards FROM mini_players ORDER BY id")
            ],
            [5, 5],
        )
        self.assertEqual(
            [
                r["coins"]
                for r in self.sql("SELECT coins FROM mini_players ORDER BY id")
            ],
            [0, 0],
        )
        self.assertEqual(
            self.sql("SELECT * FROM mini_inventory ORDER BY player_id,item_id"), before
        )
        self.assertEqual(
            [
                r["shards"]
                for r in self.sql("SELECT shards FROM mini_players ORDER BY id")
            ],
            [300, 0],
        )
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_ledger")), 2)

    def test_star_no_roll_admin_banish_loss(self):
        from app.mini.boss.rewards import (
            finish_admin_victory,
            finish_banishment,
            finish_failure,
        )

        for index, finish in enumerate(
            (finish_admin_victory, finish_banishment, finish_failure)
        ):
            boss = self.battle()
            with transaction(self.db) as conn, patch(
                "app.mini.boss.rewards.hero_abilities._roll_success",
                side_effect=AssertionError,
            ):
                finish(
                    conn,
                    conn.execute(
                        "SELECT * FROM mini_bosses WHERE id=?", (boss["id"],)
                    ).fetchone(),
                    self.now,
                )
            self.assertEqual(self.sql("SELECT * FROM mini_star_rewards"), [])

    def test_old_active_boss_keeps_shared_star_roll_policy(self):
        boss = self.battle()
        values = json.loads(
            self.sql("SELECT team_state_json FROM mini_bosses")[0]["team_state_json"]
        )
        values["reward_policy"] = copy.deepcopy(LEGACY["normal_rewards"])
        self.sql("UPDATE mini_bosses SET team_state_json=?", (json.dumps(values),))
        _, roller = self.win(boss, [True])
        self.assertEqual(roller.call_count, 1)
        self.assertEqual(
            [
                r["shards"]
                for r in self.sql("SELECT shards FROM mini_players ORDER BY id")
            ],
            [300, 300],
        )
        self.assertEqual(self.sql("SELECT * FROM mini_star_rewards"), [])

    def test_star_ledger_failure_rolls_back_all_rewards_and_hit(self):
        boss = self.battle()
        self.sql(
            "CREATE TRIGGER deny_star BEFORE INSERT ON mini_star_rewards BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.win(boss, [True])
        self.assertEqual(self.sql("SELECT * FROM mini_star_rewards"), [])
        self.assertEqual(self.sql("SELECT SUM(shards) n FROM mini_players")[0]["n"], 0)
        self.assertEqual(
            self.sql("SELECT status FROM mini_bosses")[0]["status"], "fighting"
        )
        self.assertEqual(
            self.sql("SELECT SUM(reward_granted) n FROM mini_boss_participants")[0][
                "n"
            ],
            0,
        )

    def test_star_uses_existing_eligible_rules_including_admin_no_hit(self):
        from app.mini.boss.rewards import finish_victory

        for skipped, decisions in ((0, 2), (1, 1)):
            boss = self.battle()
            self.sql("UPDATE mini_bosses SET current_hp=0 WHERE id=?", (boss["id"],))
            self.sql(
                "UPDATE mini_boss_participants SET hit_count=1,total_damage=100000 WHERE boss_id=? AND player_id=?",
                (boss["id"], self.pid),
            )
            self.sql(
                "UPDATE mini_boss_participants SET skipped_turns=? WHERE boss_id=? AND player_id=?",
                (skipped, boss["id"], self.other),
            )
            with transaction(self.db) as conn, patch(
                "app.mini.boss.rewards.hero_abilities._roll_success", return_value=False
            ) as roll:
                finish_victory(
                    conn,
                    conn.execute(
                        "SELECT * FROM mini_bosses WHERE id=?", (boss["id"],)
                    ).fetchone(),
                    self.now,
                )
            self.assertEqual(roll.call_count, decisions)
            self.assertEqual(
                len(
                    self.sql(
                        "SELECT * FROM mini_star_rewards WHERE battle_id=?",
                        (boss["id"],),
                    )
                ),
                decisions,
            )


class ApprovedTower(MiniCase):
    def setUp(self):
        super().setUp()
        self.h = self.hero()
        self.sql(
            "UPDATE mini_heroes SET attack=100,passive_key='none',passives_json='{}' WHERE id=?",
            (self.h["id"],),
        )

    def attempt(
        self, pid=None, *, element=None, arcana=None, level=0, floor=5, bonus=0
    ):
        from app.mini.tower import repository
        from app.mini.tower.catalog import get_floor

        pid = self.pid if pid is None else pid
        row = self.sql("SELECT * FROM mini_heroes WHERE id=?", (self.h["id"],))[0]
        row.update(stars=1, copies=1)
        if element:
            row.update(
                rarity="mythic",
                stars=level + 1,
                passive_key="mythic_" + element,
                passives_json=json.dumps(
                    {m: {"key": "mythic_" + element} for m in ("normal", "raid")}
                ),
            )
        enemy = get_floor(floor)
        enemy.update(max_hp=1, features=[], faction="neutral", response="prepare")
        with transaction(self.db) as conn, patch(
            "app.mini.combat.team.secrets.choice",
            return_value=arcana if arcana is not None else 0,
        ):
            value = repository.create_attempt(conn, pid, row, enemy, 0)
        runtime = json.loads(value["runtime_json"])
        runtime["bonus_shards"] = bonus
        if arcana is not None:
            runtime["team"]["lunar"] = {"arcana": arcana, "level": level}
        self.sql(
            "UPDATE mini_tower_attempts SET runtime_json=? WHERE id=?",
            (json.dumps(runtime), value["id"]),
        )
        self.sql(
            "INSERT OR REPLACE INTO mini_tower_progress(player_id,highest_cleared) VALUES(?,?)",
            (pid, floor - 1),
        )
        return value

    def test_star_success_once_floor_chest_and_restart(self):
        from app.mini.tower import service

        value = self.attempt(element="star", level=5)
        result = service.attack(
            self.pid, value["id"], 0, self.db, roller=lambda _: True
        )
        self.assertEqual(result["reward"]["star"]["chance"], 35)
        self.assertEqual(result["reward"]["star"]["amount"], 300)
        self.assertIsNotNone(result["reward"]["equipment"])
        with transaction(self.db) as conn:
            replay = service.grant_reward(
                conn, result["attempt"], roller=Mock(side_effect=AssertionError)
            )
        self.assertEqual(replay, result["reward"])
        from app.mini.schema import init_mini_db

        init_mini_db(self.db)
        self.assertEqual(len(self.sql("SELECT * FROM mini_star_rewards")), 1)
        self.assertEqual(
            self.sql("SELECT SUM(quantity) n FROM mini_equipment_owned")[0]["n"], 1
        )
        with self.assertRaises(ValueError):
            service.attack(self.pid, value["id"], 0, self.db, roller=lambda _: True)

    def test_star_failure_persists_no_reroll_and_blessing_retains_chest(self):
        from app.mini.tower import service

        value = self.attempt(element="star", arcana=10)
        result = service.attack(
            self.pid, value["id"], 0, self.db, roller=lambda _: False
        )
        self.assertEqual(result["reward"]["shards"], 0)
        self.assertEqual(result["reward"]["mythic_shards"], 5)
        self.assertIsNotNone(result["reward"]["equipment"])
        with transaction(self.db) as conn:
            service.grant_reward(
                conn, result["attempt"], roller=Mock(side_effect=AssertionError)
            )
        self.assertEqual(
            self.sql("SELECT success FROM mini_star_rewards")[0]["success"], 0
        )
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players")[0]["mythic_shards"], 5
        )

    def test_golden_thief_blessing_modify_standard_only_all_levels(self):
        from app.mini.tower import service
        from app.mini.tower.balance import reward_band

        base = reward_band(5)["shards"]
        for index, (arcana, level) in enumerate(
            [(2, l) for l in range(6)] + [(9, 0), (10, 0)]
        ):
            pid = create_mini_player(
                self.world["id"], 910000 + index, "", "Fixture", self.db
            )["id"]
            self.sql(
                "INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
                (pid, self.h["id"]),
            )
            value = self.attempt(pid, arcana=arcana, level=level, bonus=7)
            result = service.attack(
                pid, value["id"], 0, self.db, roller=lambda _: False
            )["reward"]
            expected = (
                base * (300 + 20 * level) // 100
                if arcana == 2
                else (base // 10 if arcana == 9 else 0)
            )
            self.assertEqual(result["shards"], expected + 7)
            self.assertIsNotNone(result["equipment"])
            self.assertEqual(result["mythic_shards"], 5 if arcana == 10 else 0)

    def test_first_entry_choice_does_not_reroll_after_hero_switch(self):
        from app.mini.tower import repository
        from app.mini.tower.catalog import get_floor

        first = self.attempt(arcana=None, floor=1)
        saved = self.sql("SELECT arcana_json FROM mini_tower_lunar_choices")[0][
            "arcana_json"
        ]
        self.sql(
            "UPDATE mini_tower_attempts SET status='lost' WHERE id=?", (first["id"],)
        )
        hero = self.sql("SELECT * FROM mini_heroes WHERE id=?", (self.h["id"],))[0]
        hero.update(
            rarity="mythic",
            stars=3,
            passive_key="mythic_moon",
            passives_json=json.dumps(
                {m: {"key": "mythic_moon"} for m in ("normal", "raid")}
            ),
        )
        with transaction(self.db) as conn, patch(
            "app.mini.combat.team.secrets.choice", side_effect=AssertionError
        ):
            second = repository.create_attempt(conn, self.pid, hero, get_floor(1), 0)
        self.assertEqual(
            json.loads(second["runtime_json"])["team"]["lunar"]["arcana"],
            json.loads(saved)["arcana"],
        )
        self.assertEqual(
            json.loads(second["runtime_json"])["team"]["lunar"]["level"], 2
        )

    def test_shield_order_all_combinations_exactly_one_source(self):
        from app.mini.combat import team

        for seal in (False, True):
            for blind in (False, True):
                for ice in (0, 2):
                    for lunar in (0, 2):
                        state = {
                            "soul_seal": seal,
                            "blind_round": 1 if blind else -1,
                            "blind_chance": 100,
                            "ice_shields": ice,
                            "ice_level": 0,
                            "lunar_shields": lunar,
                        }
                        result = team.reward_attack(
                            state, 1000, 1, roller=lambda _: True
                        )
                        expected = (
                            "soul_seal"
                            if seal
                            else (
                                "sun_miss"
                                if blind
                                else (
                                    "ice_shield"
                                    if ice
                                    else ("lunar_shield" if lunar else None)
                                )
                            )
                        )
                        self.assertEqual(result["reason"], expected)
                        consumed = (
                            ice
                            + lunar
                            - result["state"]["ice_shields"]
                            - result["state"]["lunar_shields"]
                        )
                        self.assertEqual(
                            consumed, int(expected in ("ice_shield", "lunar_shield"))
                        )
                        self.assertEqual(
                            result["hp_damage"], 100 if expected == "ice_shield" else 0
                        )

    def test_soul_seal_is_preserved_in_new_tower_snapshot(self):
        from app.mini.tower import repository
        from app.mini.tower.catalog import get_floor

        hero = self.sql("SELECT * FROM mini_heroes WHERE id=?", (self.h["id"],))[0]
        hero["passive_key"] = "soul_seal"
        with transaction(self.db) as conn:
            value = repository.create_attempt(conn, self.pid, hero, get_floor(1), 0)
        self.assertTrue(json.loads(value["runtime_json"])["team"]["soul_seal"])

    def test_star_all_six_chances_persist_and_failed_attempt_never_rolls(self):
        from app.mini.combat.star_rewards import award

        for stars in range(1, 7):
            roll = Mock(return_value=False)
            with transaction(self.db) as conn:
                result = award(
                    conn,
                    "tower",
                    100 + stars,
                    self.pid,
                    stars,
                    APPROVED["normal_rewards"],
                    roller=roll,
                )
            self.assertEqual(result["chance"], 17 + 3 * stars)
            self.assertEqual(roll.call_count, 1)
        value = self.attempt(element="star", floor=1)
        from app.mini.tower.service import grant_reward

        with transaction(self.db) as conn, self.assertRaises(ValueError):
            grant_reward(conn, value, roller=Mock(side_effect=AssertionError))

    def test_star_blessing_success_is_300_independent_plus_five_not_multiplied(self):
        from app.mini.tower import service

        value = self.attempt(element="star", arcana=10)
        result = service.attack(
            self.pid, value["id"], 0, self.db, roller=lambda _: True
        )["reward"]
        self.assertEqual((result["shards"], result["mythic_shards"]), (300, 5))
        self.assertEqual(self.sql("SELECT shards FROM mini_players")[0]["shards"], 300)
        self.assertEqual(len(self.sql("SELECT * FROM mini_star_rewards")), 1)

    def test_old_active_tower_preserves_legacy_star_economy(self):
        from app.mini.tower import service

        value = self.attempt(element="star")
        runtime = json.loads(
            self.sql(
                "SELECT runtime_json FROM mini_tower_attempts WHERE id=?",
                (value["id"],),
            )[0]["runtime_json"]
        )
        runtime["team"]["reward_policy"] = copy.deepcopy(LEGACY["normal_rewards"])
        self.sql(
            "UPDATE mini_tower_attempts SET runtime_json=? WHERE id=?",
            (json.dumps(runtime), value["id"]),
        )
        result = service.attack(
            self.pid, value["id"], 0, self.db, roller=lambda _: True
        )["reward"]
        self.assertGreaterEqual(result["shards"], 300)
        self.assertEqual(self.sql("SELECT * FROM mini_star_rewards"), [])

    def test_tower_arise_explicit_target_positive_only(self):
        from app.mini.tower import service

        shadow = self.sql("SELECT * FROM mini_heroes WHERE rarity='shadow' LIMIT 1")[0]
        value = self.attempt(floor=1)
        row = self.sql("SELECT * FROM mini_tower_attempts WHERE id=?", (value["id"],))[
            0
        ]
        hero = json.loads(row["hero_json"])
        hero["spirit_lantern"] = True
        enemy = json.loads(row["enemy_json"])
        enemy.update(shadow_extractable=True, shadow_hero_code=shadow["code"])
        self.sql(
            "UPDATE mini_tower_attempts SET hero_json=?,enemy_json=? WHERE id=?",
            (json.dumps(hero), json.dumps(enemy), value["id"]),
        )
        result = service.attack(self.pid, value["id"], 0, self.db)["reward"]
        self.assertTrue(result["arise"][0]["success"])
        with transaction(self.db) as conn:
            service.grant_reward(
                conn,
                self.sql(
                    "SELECT * FROM mini_tower_attempts WHERE id=?", (value["id"],)
                )[0],
            )
        self.assertEqual(
            self.sql(
                "SELECT copies FROM mini_player_heroes WHERE hero_id=?", (shadow["id"],)
            )[0]["copies"],
            1,
        )
        # An explicit target plus attempted hit still cannot award on actual zero.
        value = self.attempt(floor=2)
        runtime = json.loads(value["runtime_json"])
        runtime.update(hit_count=1, actual_hit_count=0)
        value.update(
            status="won",
            current_hp=0,
            hero_json=json.dumps(hero),
            enemy_json=json.dumps(enemy),
            runtime_json=json.dumps(runtime),
        )
        with transaction(self.db) as conn:
            empty = service.grant_reward(conn, value)
        self.assertEqual(empty["arise"], [])
        self.assertEqual(
            self.sql(
                "SELECT copies FROM mini_player_heroes WHERE hero_id=?", (shadow["id"],)
            )[0]["copies"],
            1,
        )


class ApprovedContracts(unittest.TestCase):
    def test_counter_multielement_first_attack_full_and_no_normal_leak(self):
        from app.mini.mythic.raid.effects import resolve
        from tests.v1_5.test_effect_matrix import raid, hero
        from app.mini.combat.modes import freeze_hero

        for passive in ("duplicate_echoes", "holy_smite", "arise_common", "holy_relic"):
            h = freeze_hero(
                {
                    "attack": 100,
                    "rarity": "common",
                    "stars": 0,
                    "copies": 99,
                    "passive_key": passive,
                    "faction": "neutral",
                    "attack_range": "ranged",
                    "damage_type": "physical",
                    "elements": ["ice", "fire"],
                },
                mode="raid",
            )
            result = resolve(raid(counter_policy=COUNTER_POLICY), h, 1)
            self.assertEqual(result["actual_hp_damage"], 100)
            self.assertTrue(result["state"]["counter_active"])
            self.assertEqual(h["damage_type"], "physical")
            self.assertFalse(result["passive_configured"])
        from app.mini.mythic.raid.contracts import validate

        for definition in (
            {"effects": [{"type": "unknown"}]},
            {"effects": [{"type": "flat_damage", "trigger": "attack", "value": True}]},
            {"effects": [], "code": "executable"},
        ):
            with self.assertRaises(ValueError):
                validate(definition)

    def test_approved_policy_load_and_frozen_legacy_constants(self):
        self.assertEqual(policies()["status"], "APPROVED")
        self.assertEqual(
            policies()["normal_rewards"]["star_distribution"], "individual"
        )
        self.assertEqual(LEGACY["status"], "PROVISIONAL")
        self.assertNotIn("version", LEGACY["normal_rewards"])

    def test_four_boss_contract_strict_missing_bad_png_and_unknown_fields(self):
        import tempfile, struct, zlib
        from app.mini.mythic.raid.catalog import validate

        def image():
            def chunk(k, d):
                return (
                    struct.pack(">I", len(d))
                    + k
                    + d
                    + struct.pack(">I", zlib.crc32(k + d) & 0xFFFFFFFF)
                )

            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * 6) * 2))
                + chunk(b"IEND", b"")
            )

        with tempfile.TemporaryDirectory() as directory:
            images = Path(directory)
            entries = []
            for i in range(4):
                (images / f"fixture{i}.png").write_bytes(image())
                entries.append(
                    {
                        "code": f"fixture{i}",
                        "name": "Fixture only",
                        "element": "ice",
                        "image": f"fixture{i}.png",
                        "max_hp": 100000,
                        "counter_elements": ["fire"],
                        "counter_policy": COUNTER_POLICY,
                    }
                )
            data = {"schema_version": 1, "bosses": entries}
            self.assertEqual(len(validate(data, images=images)), 4)
            for changed in (
                {**data, "bosses": entries[:3]},
                {
                    "schema_version": 1,
                    "bosses": [dict(x, unknown=True) for x in entries],
                },
                {"schema_version": 1, "bosses": [entries[0]] * 4},
            ):
                with self.assertRaises(ValueError):
                    validate(changed, images=images)
            (images / "fixture0.png").write_bytes(b"bad")
            with self.assertRaises(ValueError):
                validate(data, images=images)
            with self.assertRaises(ValueError):
                validate(
                    {
                        "schema_version": 1,
                        "bosses": [
                            dict(
                                x,
                                shadow_extractable=True,
                                shadow_hero_code="missing_fixture",
                            )
                            for x in entries
                        ],
                    },
                    images=images,
                    require_images=False,
                )

    def test_strict_cli_full_missing_invalid_temporary_content(self):
        import tempfile, struct, zlib, io
        from contextlib import redirect_stdout
        from app.mini.mythic import content
        from app.mini import catalog
        from app.mini.mythic.raid import catalog as bosses
        from app.stories.catalog import discover

        def png(rgb):
            def chunk(k, d):
                return (
                    struct.pack(">I", len(d))
                    + k
                    + d
                    + struct.pack(">I", zlib.crc32(k + d) & 0xFFFFFFFF)
                )

            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + bytes(rgb) * 2) * 2))
                + chunk(b"IEND", b"")
            )

        with tempfile.TemporaryDirectory() as directory:
            images = Path(directory)
            real = catalog.load_hero_catalog()
            data = copy.deepcopy(real)
            manifest = json.loads(
                Path("docs/v1.5/S5_IMPORT_MANIFEST.json").read_text("utf-8")
            )["heroes"]
            data["heroes"] += copy.deepcopy(manifest)
            for h in manifest:
                (images / h["image"]).write_bytes(png((255, 255, 255)))
            for element, attack in content.BASE_ATTACK.items():
                h = copy.deepcopy(real["heroes"][0])
                h.update(
                    code="fixture_" + element,
                    name="Fixture only",
                    rarity="mythic",
                    attack=attack,
                    mythic_element=element,
                    image=element + "_adult.png",
                    passive_key="mythic_" + element,
                    passives={
                        m: {"key": "mythic_" + element} for m in ("normal", "raid")
                    },
                )
                data["heroes"].append(h)
                for stage in content.STAGES:
                    (images / f"{element}_{stage}.png").write_bytes(png((0, 0, 0)))
            entries = [
                {
                    "code": f"fixture{i}",
                    "name": "Fixture only",
                    "element": "ice",
                    "image": f"raid{i}.png",
                    "max_hp": 100000,
                    "counter_elements": ["fire"],
                }
                for i in range(4)
            ]
            for e in entries:
                (images / e["image"]).write_bytes(png((0, 0, 0)))
            stories = images / "stories"
            stories.mkdir()
            (stories / "fixture.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "code": "fixture",
                        "title": "Fixture",
                        "description": "Fixture only",
                        "start_node": "end",
                        "nodes": {"end": {"text": "End", "end": True}},
                    }
                ),
                encoding="utf-8",
            )
            with patch("app.mini.catalog._read_hero_catalog", return_value=data):
                validated = catalog.load_hero_catalog()
            with patch.object(
                content, "load_hero_catalog", return_value=validated
            ), patch.object(content, "HERO_IMAGES_DIR", images), patch.object(
                bosses, "IMAGES", images
            ), patch.object(
                bosses,
                "load",
                side_effect=lambda **kw: bosses.validate(
                    {"schema_version": 1, "bosses": entries},
                    images=images,
                    require_images=kw.get("require_images", True),
                ),
            ), patch(
                "app.stories.catalog.discover", side_effect=lambda: discover(stories)
            ), patch(
                "sys.argv", ["content", "--strict-content"]
            ), redirect_stdout(
                io.StringIO()
            ):
                self.assertEqual(content.main(), 0)
                missing = images / manifest[0]["image"]
                missing.unlink()
                self.assertEqual(content.main(), 2)
                missing.write_bytes(b"bad")
                self.assertEqual(content.main(), 1)


from tests.v1_5 import test_completion_audit as audit_support


class ApprovedTelegram(MiniCase, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = audit_support.TelegramAudit.asyncSetUp
    message = audit_support.TelegramAudit.message
    callback = audit_support.TelegramAudit.callback

    def raid_fixture(self):
        template = {
            "code": "fixture",
            "name": "Fixture only",
            "max_hp": 100000,
            "element": "ice",
            "counter_elements": ["fire"],
            "features": [],
            "regeneration_percent": 0,
            "shadow_extractable": False,
        }
        self.enterContext(
            patch("app.mini.mythic.raid.service.load", return_value=[template])
        )
        self.enterContext(patch("app.context.get_topic_admin", return_value="@tester"))
        self.enterContext(
            patch("app.mini.boss.ui.get_topic_admin", return_value="@tester")
        )
        return raids.start(self.pid, "fixture", "start-ui-approved", self.db)

    async def test_cancel_forecast_reason_scoped_confirm_public_and_history_replay(
        self,
    ):
        from app.mini.mythic.raid import handlers

        raid = self.raid_fixture()
        self.sql(
            "INSERT INTO mini_mythic_raid_damage VALUES(?,?,?)",
            (raid["id"], self.pid, 50000),
        )
        context = self.enterContext(
            patch(
                "app.mini.mythic.raid.handlers.load_extended_context",
                new_callable=unittest.mock.AsyncMock,
                return_value=(self.world, self.player, f"cancelask.{raid['id']}.1.0"),
            )
        )
        cb = self.callback("unused")
        await handlers.action(cb)
        self.assertIn("пул 10", self.sent.call_args.kwargs["text"])
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
        request = self.sql("SELECT * FROM mini_mythic_raid_cancel_requests")[0]
        context.return_value = (
            self.world,
            self.player,
            "cancelreason." + request["token"],
        )
        await handlers.action(cb)
        from aiogram.types import Message

        incoming = self.message("Тестовая причина", mid=9)
        incoming = incoming.model_copy(
            update={"reply_to_message": self.message("prompt", mid=991)}
        )
        filt = handlers.PendingCancelReason()
        matched = await filt(incoming)
        self.assertTrue(matched)
        self.assertFalse(await filt(self.message("Чужая причина", uid=900124)))
        await handlers.cancel_reason(incoming, matched["cancel_prompt"])
        context.return_value = (
            self.world,
            self.player,
            "cancelconfirm." + request["token"],
        )
        await handlers.action(cb)
        await handlers.action(cb)
        self.assertEqual(
            self.sql("SELECT SUM(amount) n FROM mini_mythic_ledger")[0]["n"], 10
        )
        current = raids.state(self.world["id"], self.db)
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual(
            json.loads(current["result_json"])["reason"], "Тестовая причина"
        )
        self.assertIn("не победа", handlers.public_text(current))
        context.return_value = (self.world, self.player, f"past.{raid['id']}.0")
        await handlers.action(cb)
        self.assertIn("+10", self.sent.call_args.kwargs["text"])

    async def test_pet_buy_confirmation_and_explicit_reserve_freeze(self):
        from app.mini.mythic import pets, handlers

        now = pets.timestamp()
        with transaction(self.db) as conn:
            first = pets.first_egg(
                conn, self.pid, now, chooser=lambda values: values[0]
            )
        pets.activate(self.pid, first["id"], "activate", self.db, now=now)
        self.sql("UPDATE mini_players SET mythic_shards=600 WHERE id=?", (self.pid,))
        context = self.enterContext(
            patch(
                "app.mini.mythic.handlers.load_extended_context",
                new_callable=unittest.mock.AsyncMock,
                return_value=(self.world, self.player, "buyask.abcdef"),
            )
        )
        await handlers.action(self.callback("unused"))
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players")[0]["mythic_shards"], 600
        )
        context.return_value = (self.world, self.player, "buy.abcdef")
        await handlers.action(self.callback("unused"))
        await handlers.action(self.callback("unused"))
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players")[0]["mythic_shards"], 525
        )
        self.assertEqual(
            len(self.sql("SELECT * FROM mini_mythic_pets WHERE location='active'")), 1
        )
        context.return_value = (
            self.world,
            self.player,
            f"reserve.{first['id']}.defabc",
        )
        await handlers.action(self.callback("unused"))
        await handlers.action(self.callback("unused"))
        before = self.sql("SELECT * FROM mini_mythic_pets WHERE id=?", (first["id"],))[
            0
        ]
        self.assertEqual(before["location"], "reserve")
        pets.state(self.pid, self.db, now=now + 999999)
        after = self.sql("SELECT * FROM mini_mythic_pets WHERE id=?", (first["id"],))[0]
        for key in (
            "active_seconds",
            "stage_seconds",
            "mood",
            "protect_until",
            "cooldowns_json",
            "next_decay",
        ):
            self.assertEqual(before[key], after[key])

    async def test_forecast_pagination_and_stale_confirmation_rejected(self):
        from app.mini.mythic.raid import handlers

        raid = self.raid_fixture()
        for i in range(12):
            pid = create_mini_player(
                self.world["id"], 920000 + i, "", "Fixture", self.db
            )["id"]
            self.sql(
                "INSERT INTO mini_mythic_raid_damage VALUES(?,?,?)",
                (raid["id"], pid, 5000),
            )
        request = raids.request_cancel(
            raid["id"],
            self.world["id"],
            900123,
            True,
            0,
            self.db,
            actor_username="@tester",
        )
        _, markup = handlers.cancel_preview(self.world["id"], 900123, request)
        self.assertTrue(
            any(
                "cancelpage." in b.callback_data
                for row in markup.inline_keyboard
                for b in row
            )
        )
        self.sql("UPDATE mini_mythic_raids SET revision=1")
        context = self.enterContext(
            patch(
                "app.mini.mythic.raid.handlers.load_extended_context",
                new_callable=unittest.mock.AsyncMock,
                return_value=(
                    self.world,
                    self.player,
                    "cancelconfirm." + request["token"],
                ),
            )
        )
        await handlers.action(self.callback("unused"))
        self.assertIn("изменился", self.answered.call_args.args[0])
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
