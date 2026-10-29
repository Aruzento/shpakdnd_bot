"""Completion audit regressions: real adapters, saved snapshots, and Telegram paths."""

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch, Mock, AsyncMock
from app.mini.players import create_mini_player
from app.mini.combat import team
from app.mini.combat.modes import freeze_hero
from app.mini.combat.arise import shared_sources
from app.mini.combat.hero_abilities.catalog import get_ability
from app.mini.mythic.raid import service as raids
from app.mini.mythic.wallet import transaction
from tests.v1_3.support import MiniCase
from tests.v1_5.test_effect_matrix import hero as mythic, raid as raid_value


class TeamAriseAudit(MiniCase):
    def setUp(self):
        super().setUp()
        self.other = create_mini_player(
            self.world["id"], 900124, "other", "Other", self.db
        )["id"]
        self.dark = self.hero()
        props = {mode: {"key": "mythic_dark"} for mode in ("normal", "raid")}
        self.sql(
            "UPDATE mini_heroes SET rarity='mythic',attack=2,passive_key='mythic_dark',passives_json=?,mythic_element='dark' WHERE id=?",
            (json.dumps(props), self.dark["id"]),
        )
        self.sql(
            "UPDATE mini_player_heroes SET stars=1 WHERE player_id=? AND hero_id=?",
            (self.pid, self.dark["id"]),
        )
        self.ally = self.sql(
            "SELECT * FROM mini_heroes WHERE code='drow_shadow_necromancer'"
        )[0]
        self.sql(
            "INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
            (self.other, self.ally["id"]),
        )
        self.sql(
            "UPDATE mini_players SET active_hero_id=? WHERE id=?",
            (self.ally["id"], self.other),
        )
        self.shadow = self.sql(
            "SELECT * FROM mini_heroes WHERE rarity='shadow' ORDER BY id LIMIT 1"
        )[0]
        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)

    def raid(self):
        template = {
            "code": "fixture",
            "name": "Fixture only",
            "max_hp": 100000,
            "element": "ice",
            "counter_elements": ["dark"],
            "features": [],
            "regeneration_percent": 0,
            "shadow_extractable": True,
            "shadow_hero_code": self.shadow["code"],
        }
        self.enterContext(
            patch("app.mini.mythic.raid.service.load", return_value=[template])
        )
        return raids.start(self.pid, "fixture", "start", self.db, now=100)

    def own_shadows(self):
        return self.sql(
            "SELECT player_id,copies,stars FROM mini_player_heroes WHERE hero_id=? ORDER BY player_id",
            (self.shadow["id"],),
        )

    def test_raid_dark_grants_ally_independent_roll_once_across_days_and_catalog_edit(
        self,
    ):
        raid = self.raid()
        raids.attack(
            self.pid, raid["id"], self.dark["id"], "dark-first", self.db, now=100
        )
        self.sql(
            "UPDATE mini_player_heroes SET stars=6 WHERE player_id=? AND hero_id=?",
            (self.pid, self.dark["id"]),
        )
        raids.attack(
            self.pid, raid["id"], self.dark["id"], "dark-second-day", self.db, now=86500
        )
        # The first actual frozen instance defines Arise; future live JSON cannot erase it.
        self.sql(
            "UPDATE mini_heroes SET passive_key='none',passives_json='{}' WHERE id=?",
            (self.dark["id"],),
        )
        self.sql("UPDATE mini_mythic_raids SET current_hp=1 WHERE id=?", (raid["id"],))
        roller = Mock(return_value=True)
        result = raids.attack(
            self.other,
            raid["id"],
            self.ally["id"],
            "last",
            self.db,
            now=86500,
            roller=roller,
        )
        self.assertEqual(
            roller.call_args_list, [unittest.mock.call(40), unittest.mock.call(40)]
        )
        self.assertEqual(
            [r["player_id"] for r in self.own_shadows()], [self.pid, self.other]
        )
        self.assertTrue(all(r["copies"] == 1 for r in self.own_shadows()))
        saved = result["final"]
        self.assertEqual(
            raids.attack(
                self.other, raid["id"], self.ally["id"], "last", self.db, now=86501
            )["final"],
            saved,
        )
        self.assertEqual(len(self.sql("SELECT * FROM mini_combat_arise")), 2)

    def test_raid_personal_and_team_are_independent_and_last_callback_is_atomic(self):
        raid = self.raid()
        # Explicit raid extraction belongs only to this authored fixture hero.
        props = {
            "normal": {"key": "shadow_necromancer"},
            "raid": {
                "key": "fixture_raid",
                "configured": True,
                "definition": {
                    "effects": [
                        {
                            "type": "shadow_extraction",
                            "trigger": "victory",
                            "chance_percent": 5,
                        }
                    ]
                },
            },
        }
        # Frozen SQL source accepts definitions only after freeze, not catalog input.
        from app.mini.combat.hero_abilities import catalog

        original = catalog.get_ability

        def definition(key, *, mode="normal"):
            if key == "fixture_raid" and mode == "raid":
                return {
                    "key": key,
                    "configured": True,
                    "effects": props["raid"]["definition"]["effects"],
                }
            return original(key, mode=mode)

        self.sql(
            "UPDATE mini_heroes SET passives_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "normal": {"key": self.ally["passive_key"]},
                        "raid": {"key": "fixture_raid"},
                    }
                ),
                self.ally["id"],
            ),
        )
        raids.attack(self.pid, raid["id"], self.dark["id"], "dark", self.db, now=100)
        self.sql("UPDATE mini_mythic_raids SET current_hp=1 WHERE id=?", (raid["id"],))
        calls = []

        def roll(chance):
            calls.append(chance)
            return chance == 40

        with patch.object(catalog, "get_ability", side_effect=definition):

            def last(_):
                return raids.attack(
                    self.other,
                    raid["id"],
                    self.ally["id"],
                    "last",
                    self.db,
                    now=100,
                    roller=roll,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(last, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(calls, [40, 5, 40])
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")), 2)
        self.assertEqual(len(self.own_shadows()), 2)

    def test_zero_contribution_and_cancel_never_extract(self):
        raid = self.raid()
        raids.attack(self.pid, raid["id"], self.dark["id"], "dark", self.db, now=100)
        with transaction(self.db) as conn:
            row = raids._raid(conn, raid["id"])
            roll = Mock(return_value=True)
            self.assertEqual(raids._arise(conn, row, self.other, roller=roll), [])
            roll.assert_not_called()
        raids.cancel(
            raid["id"],
            self.world["id"],
            self.owner,
            "cancel",
            self.db,
            confirmed=True,
            expected_revision=1,
        )
        with transaction(self.db) as conn:
            row = raids._raid(conn, raid["id"])
            row["current_hp"] = 0
            self.assertEqual(
                raids._arise(
                    conn, row, self.pid, roller=Mock(side_effect=AssertionError)
                ),
                [],
            )
        self.assertEqual(self.own_shadows(), [])

    def normal_boss(self):
        from app.mini.boss.catalog import get_boss_template
        from app.mini.boss.service import (
            create_boss_event,
            register_player,
            close_registration,
        )
        from app.mini.boss.combat import start_battle

        template = get_boss_template("simple_village_guy")
        template.update(
            max_hp=10000,
            ability_key="none",
            features=[],
            shadow_hero_code=self.shadow["code"],
            shadow_extractable=True,
        )
        with patch("app.mini.boss.service.get_boss_template", return_value=template):
            boss = create_boss_event(
                self.world["id"], "simple_village_guy", 900123, self.db
            )
        for pid in (self.pid, self.other):
            register_player(boss["id"], pid, self.db)
        close_registration(boss["id"], self.db)
        start_battle(boss["id"], now=self.now, db_path=self.db)
        return boss

    def test_normal_dark_lunar_personal_frozen_team_and_max_one_shadow(self):
        from app.mini.boss.combat import hit_boss

        boss = self.normal_boss()
        values = json.loads(
            self.sql(
                "SELECT team_state_json FROM mini_bosses WHERE id=?", (boss["id"],)
            )[0]["team_state_json"]
        )
        values["arise_team_sources"].append(["team:lunar_shadows", 25])
        self.sql(
            "UPDATE mini_bosses SET team_state_json=? WHERE id=?",
            (json.dumps(values), boss["id"]),
        )
        # A also has a personal 1% effect; the shared Dark chance is still only one.
        snap = json.loads(
            self.sql(
                "SELECT hero_snapshot_json FROM mini_boss_participants WHERE boss_id=? AND player_id=?",
                (boss["id"], self.pid),
            )[0]["hero_snapshot_json"]
        )
        snap["passives"]["normal"]["definition"]["effects"] = [
            {"type": "shadow_extraction", "trigger": "victory", "chance_percent": 1}
        ]
        self.sql(
            "UPDATE mini_boss_participants SET hero_snapshot_json=? WHERE boss_id=? AND player_id=?",
            (json.dumps(snap), boss["id"], self.pid),
        )
        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        self.sql("UPDATE mini_bosses SET current_hp=1 WHERE id=?", (boss["id"],))
        calls = []

        def roll(chance):
            calls.append(chance)
            return chance == 25

        with patch("app.mini.shadow._roll_success", side_effect=roll):
            hit_boss(boss["id"], self.other, now=self.now, db_path=self.db)
        self.assertEqual(
            calls,
            [1, 40, 25, 5, 40, 25],
            self.sql("SELECT * FROM mini_combat_arise")
            + self.sql("SELECT rules_version,team_state_json FROM mini_bosses"),
        )
        self.assertEqual(len(self.own_shadows()), 2)
        self.assertTrue(all(r["copies"] == 1 for r in self.own_shadows()))

    def test_normal_admin_finalization_never_rolls_team_arise(self):
        from app.mini.boss.combat import hit_boss, force_finish_battle

        boss = self.normal_boss()
        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        with patch("app.mini.shadow._roll_success", side_effect=AssertionError):
            force_finish_battle(boss["id"], now=self.now, db_path=self.db)
        self.assertEqual(self.own_shadows(), [])

    def test_raid_shadow_failure_rolls_back_final_hit_ledger_and_reward(self):
        import sqlite3

        raid = self.raid()
        raids.attack(self.pid, raid["id"], self.dark["id"], "first", self.db, now=100)
        self.sql("UPDATE mini_mythic_raids SET current_hp=1 WHERE id=?", (raid["id"],))
        self.sql(
            "CREATE TRIGGER reject_shadow BEFORE INSERT ON mini_player_heroes WHEN NEW.hero_id=%d BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
            % self.shadow["id"]
        )
        before = self.sql("SELECT * FROM mini_mythic_ledger")
        with self.assertRaises(sqlite3.IntegrityError):
            raids.attack(
                self.other,
                raid["id"],
                self.ally["id"],
                "last",
                self.db,
                now=100,
                roller=lambda _: True,
            )
        self.assertEqual(raids.state(self.world["id"], self.db)["current_hp"], 1)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), before)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_rewards"), [])
        self.assertEqual(self.sql("SELECT * FROM mini_combat_arise"), [])
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")), 1)
        self.sql("DROP TRIGGER reject_shadow")
        raids.attack(
            self.other,
            raid["id"],
            self.ally["id"],
            "last",
            self.db,
            now=100,
            roller=lambda _: True,
        )
        self.assertEqual(len(self.own_shadows()), 2)

    def test_raid_missing_shadow_keeps_r09_and_never_rolls(self):
        raid = self.raid()
        snapshot = raid["snapshot"]
        snapshot["shadow_hero_code"] = "missing_fixture_only"
        self.sql(
            "UPDATE mini_mythic_raids SET current_hp=1,snapshot_json=? WHERE id=?",
            (json.dumps(snapshot), raid["id"]),
        )
        result = raids.attack(
            self.pid,
            raid["id"],
            self.dark["id"],
            "last",
            self.db,
            now=100,
            roller=Mock(side_effect=AssertionError),
        )
        self.assertEqual(result["final"]["rewards"][0]["amount"], 36)
        self.assertEqual(self.own_shadows(), [])

    def test_normal_shared_dark_without_personal_and_old_snapshot_fallback(self):
        from app.mini.boss.combat import hit_boss

        boss = self.normal_boss()
        # Simulate an already running V15 battle before shared-source persistence.
        values = json.loads(
            self.sql("SELECT team_state_json FROM mini_bosses")[0]["team_state_json"]
        )
        values.pop("arise_team_sources")
        self.sql("UPDATE mini_bosses SET team_state_json=?", (json.dumps(values),))
        self.sql(
            "UPDATE mini_heroes SET passive_key='none',passives_json='{}' WHERE id=?",
            (self.dark["id"],),
        )
        snap = json.loads(
            self.sql(
                "SELECT hero_snapshot_json FROM mini_boss_participants WHERE player_id=?",
                (self.other,),
            )[0]["hero_snapshot_json"]
        )
        snap["passives"]["normal"] = {
            "key": "none",
            "definition": get_ability("none"),
        }
        snap["passive_key"] = "none"
        self.sql(
            "UPDATE mini_boss_participants SET hero_snapshot_json=? WHERE player_id=?",
            (json.dumps(snap), self.other),
        )
        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        self.sql("UPDATE mini_bosses SET current_hp=1")
        with patch("app.mini.shadow._roll_success", return_value=True) as roll:
            hit_boss(boss["id"], self.other, now=self.now, db_path=self.db)
        self.assertEqual(
            roll.call_args_list, [unittest.mock.call(40), unittest.mock.call(40)]
        )
        self.assertEqual(len(self.own_shadows()), 2)

    def test_normal_zero_actual_hit_is_not_eligible_for_shared_arise(self):
        from app.mini.boss.combat import hit_boss
        from app.mini.shadow import extract_in_transaction

        boss = self.normal_boss()
        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        # Old attempted hit_count remains distinct from actual HP participation.
        self.sql(
            "UPDATE mini_boss_participants SET hit_count=1,total_damage=0 WHERE player_id=?",
            (self.other,),
        )
        with transaction(self.db) as conn:
            enemy = conn.execute(
                "SELECT * FROM mini_bosses WHERE id=?", (boss["id"],)
            ).fetchone()
            participant = conn.execute(
                "SELECT * FROM mini_boss_participants WHERE player_id=?", (self.other,)
            ).fetchone()
            self.assertIsNone(
                extract_in_transaction(
                    conn, enemy, participant, roller=Mock(side_effect=AssertionError)
                )
            )
        self.assertEqual(self.own_shadows(), [])

    def test_banishment_never_rolls_star_reward(self):
        from app.mini.boss.rewards import finish_banishment
        from app.mini.boss.repository import refresh_boss

        boss = self.normal_boss()
        snap = json.loads(
            self.sql(
                "SELECT hero_snapshot_json FROM mini_boss_participants WHERE player_id=?",
                (self.pid,),
            )[0]["hero_snapshot_json"]
        )
        snap["passive_key"] = "mythic_star"
        self.sql(
            "UPDATE mini_boss_participants SET hero_snapshot_json=? WHERE player_id=?",
            (json.dumps(snap), self.pid),
        )
        before = self.sql("SELECT shards FROM mini_players ORDER BY id")
        with transaction(self.db) as conn, patch(
            "app.mini.boss.rewards.hero_abilities._roll_success",
            side_effect=AssertionError,
        ):
            finish_banishment(conn, refresh_boss(conn, boss["id"]), self.now)
        self.assertEqual(
            self.sql("SELECT shards FROM mini_players ORDER BY id"), before
        )


class PipelineAudit(unittest.TestCase):
    def test_raid_none_does_not_apply_normal_duplicate_echoes(self):
        raw = {
            "attack": 100,
            "rarity": "common",
            "stars": 0,
            "copies": 51,
            "passive_key": "duplicate_echoes",
        }
        self.assertEqual(freeze_hero(raw)["attack"], 200)
        raid = freeze_hero(raw, mode="raid")
        self.assertEqual(raid["attack"], 100)
        self.assertEqual(raid["passives"]["raid"]["key"], "none")

    def test_team_overlays_exist_before_owner_first_turn_without_type_replacement(self):
        state = team.begin(
            [mythic(e) for e in ("dark", "star", "moon", "sun")],
            10000,
            100,
            chooser=lambda _: 0,
        )
        self.assertEqual(set(state["elements"]), {"dark", "star", "moon", "sun"})
        self.assertEqual(mythic("dark")["damage_type"], "magic")

    def test_draghar_cap_is_present_at_start_without_rescue_and_is_frozen(self):
        state = team.begin(
            [
                {"passive_key": "bloodthirsty_fight"},
                {"passive_key": "soul_seal"},
                mythic("moon"),
            ],
            10000,
            100,
            lunar={"arcana": 2, "level": 0},
        )
        self.assertFalse(state["last_stand_active"])
        self.assertEqual(team.coins(state, 200, stolen=10), 75)

    def test_distinct_dark_instances_and_repeated_snapshot_do_not_stack_per_day(self):
        first = mythic("dark", 1)
        later = mythic("dark", 6)
        entries = [(1, 7, first), (1, 7, later), (2, 7, first)]
        self.assertEqual(
            [chance for _, chance in shared_sources(entries, mode="raid")], [40, 40]
        )

    def test_malformed_story_file_does_not_crash_catalog(self):
        from app.stories.catalog import validate

        data = {
            "schema_version": 1,
            "code": "x",
            "title": "x",
            "description": "x",
            "start_node": [],
            "nodes": {"end": {"text": "End", "end": True}},
        }
        with self.assertRaises(ValueError):
            validate(data)

    def test_utf16_message_and_caption_limits(self):
        from app.mini.ui.limits import clip, units
        from app.mini.mythic.raid.handlers import public_text

        value = {
            "snapshot": {"name": "😀" * 3000},
            "current_hp": 1,
            "max_hp": 100000,
            "status": "active",
            "ranking": [],
        }
        self.assertLessEqual(units(public_text(value)), 3500)
        self.assertLessEqual(units(clip("😀" * 1000, 1024)), 1024)
        self.assertNotIn("\ud83d", clip("😀" * 1000, 1024))

    def test_policy_validation_rejects_bool_and_unapproved_selectors(self):
        from app.mini.mythic.policies import load, APPROVED
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for key, value in [
                ("schema_version", True),
                ("normal_rewards", []),
                ("normal_rewards.star_chance", [True] * 6),
                ("normal_rewards.tower_chest_preserved", 1),
                ("normal_rewards.star_outcome", "all_outcomes"),
            ]:
                data = copy.deepcopy(APPROVED)
                parts = key.split(".")
                if len(parts) == 1:
                    data[key] = value
                else:
                    data[parts[0]][parts[1]] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load(path)

    def test_eclipse_and_normal_rewards_use_frozen_policy(self):
        from app.mini.mythic.policies import LEGACY
        from app.mini.mythic.raid.effects import resolve

        values = team.begin(
            [mythic("moon")], 100000, 100, lunar={"arcana": 2, "level": 0}
        )
        values["reward_policy"] = copy.deepcopy(LEGACY["normal_rewards"])
        live = copy.deepcopy(LEGACY)
        live["normal_rewards"]["gold_percent"] = [900] * 6
        live["raid_eclipse"]["percent"] = [90] * 6
        with patch("app.mini.mythic.policies.load", return_value=live):
            self.assertEqual(team.coins(values, 100), 300)
            raid = raid_value(runtime_policies=copy.deepcopy(LEGACY))
            result = resolve(raid, mythic("moon"), 1, chooser=lambda _: 3)
        self.assertEqual(result["max_hp"], 110000)
        self.assertEqual(
            (
                result["audit"][0]["hp_added"]
                if result["audit"][0].get("type") == "eclipse"
                else next(
                    v["hp_added"] for v in result["audit"] if v["type"] == "eclipse"
                )
            ),
            10000,
        )

    def test_tower_zero_hit_and_separate_ice_lunar_shield_ui(self):
        from app.mini.tower.combat import resolve_turn
        from app.mini.tower.catalog import get_floor
        from app.mini.tower.handlers import render_state

        hero = mythic("ice")
        hero.update(name="Fixture", attack_range="melee")
        enemy = get_floor(1)
        enemy.update(features=["flying"], response="prepare")
        values = team.begin([hero], enemy["max_hp"], 0)
        values["lunar_shields"] = 1
        attempt = {
            "id": 1,
            "floor": 1,
            "player_id": 1,
            "hero_json": json.dumps(hero),
            "enemy_json": json.dumps(enemy),
            "runtime_json": json.dumps(
                {
                    "rules_version": 15,
                    "class_rules_version": 1,
                    "actual_hit_count": 0,
                    "team": values,
                }
            ),
            "events_json": "[]",
            "equipment_bonus": 0,
            "current_hp": enemy["max_hp"],
            "shields": 3,
            "turn": 0,
            "status": "active",
        }
        result = resolve_turn(attempt, roller=lambda _: False)
        self.assertEqual(result["damage"], 0)
        runtime = json.loads(result["runtime_json"])
        self.assertEqual(runtime["hit_count"], 1)
        self.assertEqual(runtime["actual_hit_count"], 0)
        text, _ = render_state(
            {"attempt": attempt, "completed": False, "floor": enemy}, 1, 1
        )
        self.assertIn("❄️ Ледяные щиты: 2", text)
        self.assertIn("🌙 Лунные щиты: 1", text)

    def test_tower_extra_hits_apply_warrior_bonus_without_recursion(self):
        from app.mini.tower.combat import resolve_turn
        from app.mini.tower.catalog import get_floor

        hero = freeze_hero(
            {
                "attack": 100,
                "rarity": "common",
                "stars": 0,
                "passive_key": "blood_frenzy",
                "faction": "neutral",
                "class_tag": "warrior",
                "attack_range": "ranged",
                "special_trait": "none",
                "damage_type": "physical",
            }
        )
        enemy = get_floor(1)
        enemy.update(max_hp=10000, features=[], response="prepare", faction="neutral")
        values = team.begin([hero], 10000, 0)
        attempt = {
            "player_id": 1,
            "hero_json": json.dumps(hero),
            "enemy_json": json.dumps(enemy),
            "runtime_json": json.dumps(
                {
                    "rules_version": 15,
                    "class_rules_version": 1,
                    "actual_hit_count": 0,
                    "hit_count": 3,
                    "team": values,
                }
            ),
            "equipment_bonus": 0,
            "current_hp": 10000,
            "shields": 3,
            "turn": 3,
        }
        result = resolve_turn(attempt, roller=lambda _: False)
        hits = [
            e["damage"]
            for e in json.loads(result["events_json"])
            if e["type"] in ("attack", "extra_attack")
        ]
        self.assertEqual(hits, [150, 150])
        self.assertEqual(json.loads(result["runtime_json"])["actual_hit_count"], 2)

    def test_tower_sun_neutralization_precedes_faction_calculation(self):
        from app.mini.tower.combat import resolve_turn
        from app.mini.tower.catalog import get_floor
        from app.mini.combat.tags import FACTION_CYCLE

        hero = mythic("sun")
        hero["faction"] = FACTION_CYCLE[0]
        enemy = get_floor(1)
        enemy.update(
            max_hp=10000, features=[], response="prepare", faction=FACTION_CYCLE[1]
        )
        values = team.begin([hero], 10000, 0)
        values["neutralized"] = True
        attempt = {
            "player_id": 1,
            "hero_json": json.dumps(hero),
            "enemy_json": json.dumps(enemy),
            "runtime_json": json.dumps(
                {
                    "rules_version": 15,
                    "class_rules_version": 1,
                    "actual_hit_count": 0,
                    "team": values,
                }
            ),
            "equipment_bonus": 0,
            "current_hp": 10000,
            "shields": 3,
            "turn": 0,
        }
        result = resolve_turn(attempt, roller=lambda _: False)
        self.assertEqual(result["faction_percent"], 100)
        self.assertEqual(result["damage"], 100)


class TelegramAudit(MiniCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiogram import Bot
        from aiogram.types import Message, CallbackQuery
        from aiogram.client.session.aiohttp import AiohttpSession

        self.enterContext(
            patch.object(
                AiohttpSession,
                "make_request",
                new_callable=AsyncMock,
                side_effect=AssertionError("Offline tests must never send HTTP"),
            )
        )
        self.bot = Bot("123456:offline-audit")
        self.addAsyncCleanup(self.bot.session.close)
        returned = Message(
            message_id=991,
            date=datetime.now(timezone.utc),
            chat={"id": self.world["chat_id"], "type": "group"},
        ).as_(self.bot)
        self.sent = self.enterContext(
            patch.object(
                Bot, "send_message", new_callable=AsyncMock, return_value=returned
            )
        )
        self.photos = self.enterContext(
            patch.object(
                Bot, "send_photo", new_callable=AsyncMock, return_value=returned
            )
        )
        self.answered = self.enterContext(
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock)
        )
        self.enterContext(
            patch.object(
                Message, "answer", new_callable=AsyncMock, return_value=returned
            )
        )
        for name in (
            "edit_message_text",
            "edit_message_caption",
            "edit_message_reply_markup",
        ):
            self.enterContext(patch.object(Bot, name, new_callable=AsyncMock))
        self.tx = transaction
        original_selection = raids.selection
        self.enterContext(patch.object(raids,"selection",side_effect=lambda *a,**k: original_selection(*a,**dict(k,db_path=self.db))))
        for name in (
            "app.stories.handlers",
            "app.stories.enrollment",
            "app.stories.service",
            "app.stories.publication",
            "app.mini.mythic.raid.service",
            "app.mini.mythic.raid.handlers",
            "app.mini.mythic.pets",
            "app.mini.mythic.handlers",
        ):
            self.enterContext(
                patch(
                    name + ".transaction", side_effect=lambda *a, **k: self.tx(self.db)
                )
            )

    def message(self, text, mid=1, uid=900123, chat=None, thread=None):
        from aiogram.types import Message, User

        value = uid if chat is None else chat
        return Message(
            message_id=mid,
            date=datetime.now(timezone.utc),
            chat={"id": value, "type": "private" if value == uid else "group"},
            message_thread_id=thread,
            from_user=User(
                id=uid, is_bot=False, first_name="Tester", username="tester"
            ),
            text=text,
        ).as_(self.bot)

    def callback(self, data, uid=900123, chat=None):
        from aiogram.types import CallbackQuery

        message = self.message("menu", uid=uid, chat=chat)
        return CallbackQuery(
            id="audit-query",
            from_user=message.from_user,
            chat_instance="audit",
            message=message,
            data=data,
        ).as_(self.bot)

    async def test_private_story_quick_creation_real_rows_join_publication_and_duplicate_text(
        self,
    ):
        from app.stories import service, enrollment, handlers
        from app.db.schema import (
            create_characters_table,
            create_character_profiles_table,
        )
        from tests.v1_5.test_boss_stories import StoryTests

        with self.tx(self.db) as conn:
            create_characters_table(conn)
            create_character_profiles_table(conn)
        import tempfile
        from pathlib import Path

        directory = Path(self.tmp.name) / "stories"
        directory.mkdir()
        story = {
            "schema_version": 1,
            "code": "fixture",
            "title": "Fixture",
            "description": "Fixture only",
            "start_node": "end",
            "nodes": {"end": {"text": "End", "end": True}},
        }
        (directory / "fixture.json").write_text(json.dumps(story), encoding="utf-8")
        self.enterContext(patch("app.context.get_topic_admin", return_value="@admin"))
        for name in (
            "app.stories.service.discover",
            "app.stories.presentation.discover",
        ):
            self.enterContext(patch(name, return_value=({"fixture": story}, {})))
        sid = service.create(
            -111, 7, 1, "admin", "fixture", "create", self.db, catalog_dir=directory
        )["session_id"]
        enrollment.begin(sid, -111, 7, 77, "choose", self.db)
        original = enrollment.pending
        self.enterContext(
            patch(
                "app.stories.enrollment.pending",
                side_effect=lambda c, t, u: original(c, t, u, self.db),
            )
        )
        await handlers.callback_action(self.callback(f"story:new:{sid}", uid=77))
        filt = handlers.PendingStoryCharacter()
        first = self.message("Имя", mid=11, uid=77)
        self.assertTrue(await filt(first))
        self.assertFalse(await filt(self.message("/mini", uid=77)))
        self.assertFalse(await filt(self.message("не мой ответ", uid=78)))
        self.assertFalse(
            await filt(self.message("другая тема", uid=77, chat=-111, thread=7))
        )
        await handlers.quick_character(first)
        await handlers.quick_character(first)
        self.assertEqual(enrollment.pending(77, 0, 77)["stage"], "race")
        await handlers.quick_character(
            self.message("Произвольная раса", mid=12, uid=77)
        )
        await handlers.quick_character(
            self.message("Произвольный класс", mid=13, uid=77)
        )
        row = self.sql(
            "SELECT c.chat_id,c.thread_id,c.name,p.level,p.race,p.class_name FROM characters c JOIN character_profiles p USING(chat_id,thread_id,username)"
        )[0]
        self.assertEqual(
            (row["chat_id"], row["thread_id"], row["name"], row["level"]),
            (-111, 7, "Имя", 1),
        )
        self.assertEqual(row["race"], "Произвольная раса")
        self.assertEqual(
            len(
                self.sql("SELECT * FROM story_participants WHERE session_id=?", (sid,))
            ),
            1,
        )
        self.assertEqual(
            self.sql(
                "SELECT status FROM story_publications WHERE session_id=?", (sid,)
            )[0]["status"],
            "sent",
        )
        self.assertIsNone(enrollment.pending(77, 0, 77))

    async def test_raid_hit_handler_commits_once_and_displays_egg_and_saved_payout(
        self,
    ):
        from app.mini.mythic.raid import handlers

        h = self.hero()
        self.sql("UPDATE mini_heroes SET attack=100 WHERE id=?", (h["id"],))
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
        self.enterContext(
            patch("app.mini.mythic.raid.handlers.load", return_value=[template])
        )
        raid = raids.start(self.pid, "fixture", "start-ui", self.db)
        self.sql("UPDATE mini_mythic_raids SET current_hp=3 WHERE id=?", (raid["id"],))
        payload = f"hit.{raid['id']}.{h['id']}.abcdef"
        self.enterContext(
            patch(
                "app.mini.mythic.raid.handlers.load_extended_context",
                new_callable=AsyncMock,
                return_value=(self.world, self.player, payload),
            )
        )
        callback = self.callback("unused")
        await handlers.action(callback)
        await handlers.action(callback)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")), 1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_pets")), 1)
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players WHERE id=?", (self.pid,))[
                0
            ]["mythic_shards"],
            36,
        )
        text = self.sent.call_args.kwargs["text"]
        self.assertIn("Снято HP: 3", text)
        self.assertIn("первое яйцо", text)
        self.assertIn("+36", text)
        current = raids.state(self.world["id"], self.db)
        self.assertEqual(current["ranking"][0]["amount"], 36)
        self.assertIn("+36", handlers.public_text(current))

    async def test_pet_equipment_confirmation_handler_and_replay(self):
        from app.mini.mythic import pets, handlers

        now = pets.timestamp()
        with self.tx(self.db) as conn:
            pet = pets.first_egg(conn, self.pid, now, chooser=lambda values: values[0])
        pets.activate(self.pid, pet["id"], "activate", self.db, now=now)
        self.sql(
            "UPDATE mini_mythic_pets SET stage='wonder',mood=90 WHERE id=?",
            (pet["id"],),
        )
        item = self.sql(
            "SELECT rowid AS item_id,code FROM mini_equipment ORDER BY code LIMIT 1"
        )[0]
        self.sql(
            "INSERT INTO mini_equipment_owned(player_id,code,quantity) VALUES(?,?,1)",
            (self.pid, item["code"]),
        )
        context = self.enterContext(
            patch(
                "app.mini.mythic.handlers.load_extended_context", new_callable=AsyncMock
            )
        )
        context.return_value = (
            self.world,
            self.player,
            f"eatask.{pet['id']}.{item['item_id']}.abcdef",
        )
        await handlers.action(self.callback("unused"))
        self.assertEqual(
            self.sql("SELECT quantity FROM mini_equipment_owned")[0]["quantity"], 1
        )
        markup = self.sent.call_args.kwargs["reply_markup"]
        payload = markup.inline_keyboard[0][0].callback_data.split(":", 4)[4]
        context.return_value = (self.world, self.player, payload)
        await handlers.action(self.callback("unused"))
        await handlers.action(self.callback("unused"))
        self.assertEqual(self.sql("SELECT * FROM mini_equipment_owned"), [])
        self.assertEqual(
            self.sql("SELECT consumed FROM mini_mythic_pet_days")[0]["consumed"], 1
        )
        self.assertEqual(self.sql("SELECT mood FROM mini_mythic_pets")[0]["mood"], 90)
        self.assertEqual(
            self.sql("SELECT mythic_shards FROM mini_players WHERE id=?", (self.pid,))[
                0
            ]["mythic_shards"],
            1,
        )

    async def test_cancel_zero_pool_handler_audits_refusal_once_without_state_or_money(
        self,
    ):
        from app.mini.mythic.raid import handlers

        template = {
            "code": "fixture",
            "name": "Fixture",
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
        raid = raids.start(self.pid, "fixture", "start-blocked", self.db)
        request = raids.request_cancel(
            raid["id"],
            self.world["id"],
            900123,
            True,
            0,
            self.db,
            actor_username="@tester",
        )
        payload = f"cancelconfirm.{request['token']}"
        self.enterContext(
            patch(
                "app.mini.mythic.raid.handlers.load_extended_context",
                new_callable=AsyncMock,
                return_value=(self.world, self.player, payload),
            )
        )
        callback = self.callback("unused")
        await handlers.action(callback)
        await handlers.action(callback)
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "active")
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM mini_mythic_raid_audit WHERE action='cancel_reward_refused'"
                )
            ),
            1,
        )
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])
        self.assertIn("равен нулю", self.answered.call_args.args[0])

    async def test_restart_raid_pending_is_sent_once_uncertain_is_not_retried(self):
        from app.mini.mythic.raid import handlers

        template = {
            "code": "fixture",
            "name": "Fixture",
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
        raid = raids.start(self.pid, "fixture", "start-recovery", self.db)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_publications"), [])
        raids.recover_publications(self.db)
        await handlers.recover_pending(self.bot)
        await handlers.recover_pending(self.bot)
        self.assertEqual(self.sent.await_count, 1)
        self.assertEqual(
            self.sql("SELECT status FROM mini_mythic_raid_publications")[0]["status"],
            "sent",
        )
        self.sql(
            "UPDATE mini_mythic_raid_publications SET status='sending',message_id=NULL"
        )
        raids.recover_publications(self.db)
        await handlers.recover_pending(self.bot)
        self.assertEqual(self.sent.await_count, 1)
        self.assertEqual(
            self.sql("SELECT status FROM mini_mythic_raid_publications")[0]["status"],
            "uncertain",
        )
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])

    async def test_restart_story_pending_is_sent_once_uncertain_is_not_retried(self):
        from app.stories import service, publication, handlers
        from pathlib import Path

        directory = Path(self.tmp.name) / "stories"
        directory.mkdir()
        story = {
            "schema_version": 1,
            "code": "fixture",
            "title": "Fixture",
            "description": "Fixture only",
            "start_node": "end",
            "nodes": {"end": {"text": "End", "end": True}},
        }
        (directory / "fixture.json").write_text(json.dumps(story), encoding="utf-8")
        self.enterContext(patch("app.context.get_topic_admin", return_value="@admin"))
        self.enterContext(
            patch(
                "app.stories.presentation.discover",
                return_value=({"fixture": story}, {}),
            )
        )
        sid = service.create(
            -111,
            7,
            1,
            "admin",
            "fixture",
            "create-recovery",
            self.db,
            catalog_dir=directory,
        )["session_id"]
        await handlers.recover_pending(self.bot)
        await handlers.recover_pending(self.bot)
        self.assertEqual(self.sent.await_count, 1)
        self.assertEqual(
            self.sql("SELECT status FROM story_publications")[0]["status"], "sent"
        )
        self.sql("UPDATE story_publications SET status='sending',message_id=NULL")
        publication.recover(self.db)
        await handlers.recover_pending(self.bot)
        self.assertEqual(self.sent.await_count, 1)
        self.assertEqual(
            self.sql("SELECT status FROM story_publications")[0]["status"], "uncertain"
        )

    async def test_recovered_raid_public_button_uses_world_not_raid_id(self):
        from app.mini.mythic.raid import handlers

        template = {
            "code": "fixture",
            "name": "Fixture",
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
        old = raids.start(self.pid, "fixture", "first-recover-world", self.db)
        raids.cancel(
            old["id"],
            self.world["id"],
            self.owner,
            "cancel-first-world",
            self.db,
            confirmed=True,
            expected_revision=0,
        )
        raid = raids.start(self.pid, "fixture", "second-recover-world", self.db)
        self.assertNotEqual(raid["id"], self.world["id"])
        raids.recover_publications(self.db)
        await handlers.recover_pending(self.bot)
        calls = [c for c in self.sent.call_args_list if c.kwargs.get("reply_markup")]
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0].kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            f"mini:raidopen:{self.world['id']}:{raid['id']}",
        )
