import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    create_boss_event,
    register_player,
    close_registration,
    get_boss,
    list_participants,
)
from app.mini.boss.combat import (
    start_battle,
    hit_boss,
    advance_expired_turns,
    force_finish_battle,
)
from app.mini.boss.operations import (
    remaining,
    turn_token,
    admin_skip,
    change_boss,
    history,
    parse_change,
)
from app.mini.combat import team
from app.stories import service as stories
from app.stories.catalog import validate, discover


class BossTests(MiniCase):
    def setUp(self):
        super().setUp()
        init_boss_db(self.db)
        self.hero_value = self.hero()
        self.now = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)

    def begin(self, code="simple_village_guy"):
        boss = create_boss_event(self.world["id"], code, 900123, self.db)
        register_player(boss["id"], self.pid, self.db)
        close_registration(boss["id"], self.db)
        start_battle(boss["id"], now=self.now, db_path=self.db)
        return get_boss(boss["id"], self.db)

    def test_new_timeout_and_legacy_preserved(self):
        boss = self.begin()
        self.assertEqual(boss["turn_timeout_seconds"], 900)
        self.assertFalse(
            advance_expired_turns(
                boss["id"], now=self.now + timedelta(seconds=899), db_path=self.db
            )["changed"]
        )
        self.assertTrue(
            advance_expired_turns(
                boss["id"], now=self.now + timedelta(seconds=900), db_path=self.db
            )["changed"]
        )
        self.sql(
            "UPDATE mini_bosses SET turn_timeout_seconds=18000,skip_after_hours=5 WHERE id=?",
            (boss["id"],),
        )
        init_boss_db(self.db)
        self.assertEqual(get_boss(boss["id"], self.db)["turn_timeout_seconds"], 18000)

    def test_admin_skip_does_not_count_timeout_and_is_stale_safe(self):
        boss = self.begin()
        token = turn_token(boss)
        result = admin_skip(
            boss["id"],
            self.world["id"],
            self.owner,
            "",
            token,
            "skip",
            self.db,
            now=self.now + timedelta(seconds=10),
        )
        self.assertEqual(list_participants(boss["id"], self.db)[0]["skipped_turns"], 0)
        self.assertEqual(
            admin_skip(
                boss["id"],
                self.world["id"],
                self.owner,
                "",
                token,
                "skip",
                self.db,
                now=self.now + timedelta(seconds=11),
            ),
            result,
        )
        with self.assertRaises(ValueError):
            admin_skip(
                boss["id"],
                self.world["id"],
                self.owner,
                "",
                token,
                "other",
                self.db,
                now=self.now + timedelta(seconds=12),
            )

    def test_ten_started_quota_and_moscow_midnight(self):
        for index in range(10):
            boss = self.begin()
            self.sql("UPDATE mini_bosses SET status='failed' WHERE id=?", (boss["id"],))
        self.assertEqual(remaining(self.world["id"], self.db, now=self.now), 0)
        boss = create_boss_event(
            self.world["id"], "simple_village_guy", 900123, self.db
        )
        register_player(boss["id"], self.pid, self.db)
        close_registration(boss["id"], self.db)
        with self.assertRaises(ValueError):
            start_battle(boss["id"], now=self.now, db_path=self.db)
        self.assertEqual(
            remaining(self.world["id"], self.db, now=self.now + timedelta(days=1)), 10
        )

    def test_no_hit_eligible_and_history_frozen(self):
        boss = self.begin()
        rewards = force_finish_battle(boss["id"], now=self.now, db_path=self.db)[
            "rewards"
        ]
        self.assertEqual(rewards["players"], 1)
        self.assertIn(
            "История боя",
            history(boss["id"], self.world["id"], db_path=self.db)["text"],
        )

    def test_admin_parameter_validation_and_frozen_penalty(self):
        boss = self.begin()
        result = change_boss(
            boss["id"],
            self.world["id"],
            self.owner,
            "-drop",
            200,
            "change",
            self.db,
            now=self.now,
        )
        self.assertEqual(result["new"], 200)
        self.assertEqual(
            change_boss(
                boss["id"],
                self.world["id"],
                self.owner,
                "-drop",
                200,
                "change",
                self.db,
                now=self.now,
            ),
            result,
        )
        with self.assertRaises(ValueError):
            change_boss(
                boss["id"], self.world["id"], self.owner, "-hp", 0, "hp", self.db
            )
        with self.assertRaises(ValueError):
            parse_change("/superadminch 1 -drop 1 extra")
        state = {
            "original_coins": 100,
            "draghar_cap": 60,
            "last_stand_active": True,
            "seal_penalty": 25,
            "lunar": {"arcana": 2, "level": 0},
        }
        self.assertEqual(team.coins(state, 200, stolen=10), 75)

    def test_training_percent_and_hp_history(self):
        boss = self.begin("training_dummy")
        result = hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        self.assertEqual(result["damage"], 700)
        action = self.sql(
            "SELECT event_json FROM mini_boss_actions WHERE action_type='attack'"
        )[0]
        self.assertEqual(json.loads(action["event_json"])["actual_hp_damage"], 700)

    def test_holy_smite_real_damage_once(self):
        self.sql(
            "UPDATE mini_heroes SET passive_key='holy_smite',passives_json=? WHERE id=?",
            (json.dumps({"normal": {"key": "holy_smite"}}), self.hero_value["id"]),
        )
        boss = self.begin()
        before = boss["current_hp"]
        first = hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        self.assertLessEqual(first["boss_hp_after"], before - before // 5)
        values = json.loads(get_boss(boss["id"], self.db)["team_state_json"])
        self.assertTrue(values["smite_used"])

    def test_soul_seal_suppresses_reward_attack_and_healer_cannot_repair(self):
        self.sql(
            "UPDATE mini_heroes SET passive_key='soul_seal',passives_json=? WHERE id=?",
            (json.dumps({"normal": {"key": "soul_seal"}}), self.hero_value["id"]),
        )
        boss = self.begin()
        shields = boss["reward_shields"]
        hit_boss(boss["id"], self.pid, now=self.now, db_path=self.db)
        after = get_boss(boss["id"], self.db)
        self.assertEqual(after["reward_shields"], shields)
        self.assertEqual(
            json.loads(after["team_state_json"])["seal_penalty"], 15 * 25 // 100
        )


class HistoryLengthTests(MiniCase):
    def test_history_preserves_long_unicode_events_within_telegram_limit(self):
        init_boss_db(self.db)
        self.hero()
        boss = create_boss_event(
            self.world["id"], "simple_village_guy", 900123, self.db
        )
        register_player(boss["id"], self.pid, self.db)
        close_registration(boss["id"], self.db)
        start_battle(boss["id"], db_path=self.db)
        force_finish_battle(boss["id"], db_path=self.db)
        self.sql(
            "INSERT INTO mini_boss_events(boss_id,round_number,event_type,event_json,boss_hp_after) VALUES(?,1,'fixture',?,0)",
            (boss["id"], json.dumps({"message": "😀" * 5000})),
        )
        first = history(boss["id"], self.world["id"], db_path=self.db)
        pages = [
            history(boss["id"], self.world["id"], page, db_path=self.db)["text"]
            for page in range(first["pages"])
        ]
        self.assertTrue(
            all(len(text.encode("utf-16-le")) // 2 <= 4096 for text in pages)
        )
        self.assertEqual(sum(text.count("😀") for text in pages), 5000)


class StoryTests(MiniCase):
    def setUp(self):
        MiniCase.setUp(self)
        self.storydir = Path(self.tmp.name) / "stories"
        self.storydir.mkdir()
        self.story = {
            "schema_version": 1,
            "code": "fixture_story",
            "title": "Fixture",
            "description": "Test only",
            "start_node": "start",
            "nodes": {
                "start": {
                    "text": "Choice",
                    "choices": [
                        {"id": "a", "text": "A", "next": "a_end"},
                        {"id": "b", "text": "B", "next": "b_end"},
                    ],
                },
                "a_end": {"text": "A ended", "end": True},
                "b_end": {"text": "B ended", "end": True},
            },
        }
        self.write_story()
        admin = patch("app.context.get_topic_admin", return_value="@admin")
        admin.start()
        self.addCleanup(admin.stop)
        self.chat = -111
        self.thread = 0
        with __import__("app.mini.db", fromlist=["connect_mini_db"]).connect_mini_db(
            self.db
        ) as conn:
            from app.db.schema import (
                create_characters_table,
                create_character_profiles_table,
            )

            create_characters_table(conn)
            create_character_profiles_table(conn)
        self.session = stories.create(
            self.chat,
            None,
            1,
            "admin",
            "fixture_story",
            "create",
            self.db,
            catalog_dir=self.storydir,
        )["session_id"]

    def write_story(self):
        (self.storydir / "fixture.json").write_text(
            json.dumps(self.story), encoding="utf-8"
        )

    def join(self, uid):
        self.sql(
            "INSERT OR IGNORE INTO characters VALUES(?,?,?,?)",
            (self.chat, 0, "@u" + str(uid), "Player"),
        )
        stories.join(
            self.session,
            self.chat,
            0,
            uid,
            "dnd",
            {"thread_id": 0},
            "join" + str(uid),
            self.db,
            username="u" + str(uid),
        )

    def start(self):
        return stories.start(
            self.session,
            self.chat,
            0,
            1,
            "admin",
            "start",
            self.db,
            catalog_dir=self.storydir,
        )

    def test_dag_rejects_cycle_missing_and_unreachable(self):
        data = json.loads(json.dumps(self.story))
        data["nodes"]["start"]["choices"][0]["next"] = "start"
        with self.assertRaises(ValueError):
            validate(data)
        data = json.loads(json.dumps(self.story))
        data["nodes"]["ghost"] = {"text": "not reachable", "end": True}
        with self.assertRaises(ValueError):
            validate(data)

    def test_hot_discovery_invalid_isolated_and_snapshot_frozen(self):
        self.join(2)
        self.start()
        self.story["nodes"]["start"]["text"] = "Changed"
        self.write_story()
        (self.storydir / "broken.json").write_text("{", encoding="utf-8")
        found, errors = discover(self.storydir)
        self.assertIn("fixture_story", found)
        self.assertIn("broken.json", errors)
        state = stories.status(self.chat, None, self.db)
        self.assertEqual(
            json.loads(state["snapshot_json"])["nodes"]["start"]["text"], "Choice"
        )

    def test_one_vote_tie_and_repeat_last_vote(self):
        self.join(2)
        self.join(3)
        self.start()
        first = stories.vote(self.session, self.chat, 0, 2, 1, "a", "vote2", self.db)
        self.assertEqual(first["percent"], {"a": 100, "b": 0})
        with self.assertRaises(ValueError):
            stories.vote(self.session, self.chat, 0, 2, 1, "b", "change2", self.db)
        result = stories.vote(
            self.session,
            self.chat,
            0,
            3,
            1,
            "b",
            "vote3",
            self.db,
            chooser=lambda c: c[-1],
        )
        self.assertTrue(result["advanced"])
        self.assertEqual(result["choice"], "b")
        self.assertEqual(
            stories.vote(self.session, self.chat, 0, 3, 1, "b", "vote3", self.db),
            result,
        )

    def test_kick_recalculates_and_zero_waits(self):
        self.join(2)
        self.join(3)
        self.start()
        stories.vote(self.session, self.chat, 0, 2, 1, "a", "vote", self.db)
        result = stories.kick(
            self.session, self.chat, 0, 1, "admin", 3, "kick", self.db
        )
        self.assertTrue(result["advanced"])

    def test_scopes_and_admin_permissions(self):
        self.join(2)
        self.start()
        with self.assertRaises(ValueError):
            stories.vote(self.session, -222, 0, 2, 1, "a", "foreign", self.db)
        with self.assertRaises(ValueError):
            stories.kick(self.session, self.chat, 0, 2, "u2", 2, "kick", self.db)

    def test_replay_after_finish_new_session(self):
        self.join(2)
        self.start()
        stories.vote(self.session, self.chat, 0, 2, 1, "a", "vote", self.db)
        next_session = stories.create(
            self.chat,
            0,
            1,
            "admin",
            "fixture_story",
            "new",
            self.db,
            catalog_dir=self.storydir,
        )
        self.assertNotEqual(next_session["session_id"], self.session)
