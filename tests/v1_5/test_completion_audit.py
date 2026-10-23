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
            raid["id"], self.world["id"], self.owner, "cancel", self.db, confirmed=True
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
        self.assertEqual(calls, [1, 40, 25, 5, 40, 25])
        self.assertEqual(len(self.own_shadows()), 2)
        self.assertTrue(all(r["copies"] == 1 for r in self.own_shadows()))

    def test_normal_admin_finalization_never_rolls_team_arise(self):
        from app.mini.boss.combat import hit_boss, force_finish_battle

        boss = self.normal_boss()
        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        with patch("app.mini.shadow._roll_success", side_effect=AssertionError):
            force_finish_battle(boss["id"], now=self.now, db_path=self.db)
        self.assertEqual(self.own_shadows(), [])


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
