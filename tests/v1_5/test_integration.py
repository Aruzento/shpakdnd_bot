"""Cross-service V1.5 integration contracts; fixtures are not production content."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from tests.v1_3.support import MiniCase
from tests.v1_5 import test_boss_stories as fixtures
from app.mini.mythic.raid import service as raids
from app.mini.mythic.wallet import transaction
from app.stories import service as stories, enrollment


class RaidBoundaries(MiniCase):
    def setUp(self):
        super().setUp()
        self.template = {
            "code": "fixture",
            "name": "Fixture",
            "max_hp": 100000,
            "element": "ice",
            "counter_elements": ["fire"],
            "features": [],
            "regeneration_percent": 0,
            "shadow_extractable": False,
        }
        mocked = patch(
            "app.mini.mythic.raid.service.load", return_value=[self.template]
        )
        mocked.start()
        self.addCleanup(mocked.stop)
        self.raid = raids.start(self.pid, "fixture", "start", self.db, now=100)

    def test_daily_limit_five_attempts_then_new_moscow_day(self):
        heroes = self.sql(
            "SELECT id FROM mini_heroes WHERE rarity IN ('common','uncommon','rare','legendary') ORDER BY id LIMIT 11"
        )
        for h in heroes:
            self.sql(
                "INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",
                (self.pid, h["id"]),
            )
        for index, h in enumerate(heroes[:5]):
            result = raids.attack(
                self.pid, self.raid["id"], h["id"], str(index), self.db, now=100
            )
            self.assertEqual(result["attempts_left"], 4 - index)
        with self.assertRaisesRegex(ValueError, "пяти"):
            raids.attack(
                self.pid,
                self.raid["id"],
                heroes[5]["id"],
                "sixth",
                self.db,
                now=100,
            )
        self.assertEqual(
            raids.attack(
                self.pid,
                self.raid["id"],
                heroes[0]["id"],
                "tomorrow",
                self.db,
                now=86500,
            )["attempts_left"],
            4,
        )

    def test_admin_cancel_revision_rechecked_under_write_lock(self):
        h = self.hero()
        raids.attack(self.pid, self.raid["id"], h["id"], "hit", self.db, now=100)
        with self.assertRaisesRegex(ValueError, "изменился"):
            raids.cancel(
                self.raid["id"],
                self.world["id"],
                self.owner,
                "stale-cancel",
                self.db,
                confirmed=True,
                expected_revision=0,
            )
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "active")
        result = raids.cancel(
            self.raid["id"],
            self.world["id"],
            self.owner,
            "cancel",
            self.db,
            confirmed=True,
            expected_revision=1,
        )
        self.assertTrue(result["applied"])
        self.assertFalse(
            raids.cancel(
                self.raid["id"],
                self.world["id"],
                self.owner,
                "cancel",
                self.db,
                confirmed=True,
                expected_revision=1,
            )["applied"]
        )
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_rewards"), [])

    def test_rewarded_cancel_zero_pool_without_financial_changes(self):
        with self.assertRaisesRegex(ValueError, "равен нулю"):
            raids.cancel(
                self.raid["id"],
                self.world["id"],
                self.owner,
                "reward-cancel",
                self.db,
                confirmed=True,
                with_reward=True,
                expected_revision=0,
            )
        self.assertEqual(raids.state(self.world["id"], self.db)["status"], "active")
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_ledger"), [])

    def test_new_feature_data_survives_three_startups(self):
        from app.mini.schema import init_mini_db
        from app.mini.boss.schema import init_boss_db

        h = self.hero()
        raids.attack(self.pid, self.raid["id"], h["id"], "hit", self.db, now=100)
        tables = (
            "mini_mythic_raids",
            "mini_mythic_raid_attacks",
            "mini_mythic_raid_damage",
            "mini_mythic_pets",
            "mini_mythic_operations",
        )
        before = {t: self.sql("SELECT * FROM " + t) for t in tables}
        for _ in range(3):
            init_mini_db(self.db)
            init_boss_db(self.db)
        self.assertEqual(before, {t: self.sql("SELECT * FROM " + t) for t in tables})


class StoryIntegration(MiniCase):
    setUp = fixtures.StoryTests.setUp
    write_story = fixtures.StoryTests.write_story
    join = fixtures.StoryTests.join
    start = fixtures.StoryTests.start

    def test_mixed_real_dnd_and_mini_accounts_vote_without_economy(self):
        self.sql(
            "UPDATE mini_worlds SET chat_id=? WHERE id=?", (self.chat, self.world["id"])
        )
        before = self.sql(
            "SELECT coins,shards,mythic_shards FROM mini_players WHERE id=?",
            (self.pid,),
        )
        self.join(1)
        stories.join(
            self.session,
            self.chat,
            0,
            900123,
            "mini",
            {"id": self.pid},
            "join-mini",
            self.db,
        )
        self.start()
        stories.vote(self.session, self.chat, 0, 1, 1, "a", "dnd-vote", self.db)
        result = stories.vote(
            self.session, self.chat, 0, 900123, 1, "a", "mini-vote", self.db
        )
        self.assertTrue(result["advanced"])
        self.assertEqual(stories.status(self.chat, 0, self.db)["status"], "finished")
        self.assertEqual(
            self.sql(
                "SELECT coins,shards,mythic_shards FROM mini_players WHERE id=?",
                (self.pid,),
            ),
            before,
        )

    def test_quick_creation_is_persistent_real_character_and_level_one(self):
        enrollment.begin(self.session, self.chat, None, 77, "dnd", self.db)
        enrollment.input_text(self.chat, 0, 77, "new_dnd", "Имя", self.db)
        enrollment.input_text(self.chat, None, 77, "new_dnd", "Раса", self.db)
        result = enrollment.input_text(self.chat, 0, 77, "new_dnd", "Класс", self.db)
        self.assertEqual(result["stage"], "done")
        self.assertEqual(
            self.sql("SELECT name FROM characters WHERE username=?", ("@new_dnd",))[0][
                "name"
            ],
            "Имя",
        )
        profile = self.sql(
            "SELECT level,race,class_name FROM character_profiles WHERE username=?",
            ("@new_dnd",),
        )[0]
        self.assertEqual(profile, {"level": 1, "race": "Раса", "class_name": "Класс"})
        stories.join(
            self.session,
            self.chat,
            0,
            77,
            "dnd",
            {"thread_id": 0},
            "real-join",
            self.db,
            username="new_dnd",
        )
        self.assertEqual(len(stories.status(self.chat, 0, self.db)["participants"]), 1)

    def test_parallel_final_votes_advance_one_time(self):
        self.join(1)
        self.join(2)
        self.start()

        def vote(uid):
            return stories.vote(
                self.session, self.chat, 0, uid, 1, "a", "parallel-" + str(uid), self.db
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(vote, (1, 2)))
        self.assertEqual(sum(bool(r["advanced"]) for r in results), 1)
        self.assertEqual(
            len(
                self.sql(
                    "SELECT * FROM story_steps WHERE session_id=?", (self.session,)
                )
            ),
            1,
        )


class PvpBoundary(MiniCase):
    def test_mythic_service_bypass_rejected_before_escrow(self):
        import os
        self.enterContext(patch.dict(os.environ, {"MINI_DUELS_ENABLED":"1"}))
        from app.mini.players import create_mini_player
        from app.mini.duels.service import challenge

        other = create_mini_player(self.world["id"], 900124, "other", "Other", self.db)
        h = self.hero()
        self.sql("UPDATE mini_heroes SET rarity='mythic' WHERE id=?", (h["id"],))
        self.sql("UPDATE mini_players SET coins=100 WHERE id=?", (self.pid,))
        with self.assertRaisesRegex(ValueError, "PvP"):
            challenge(self.pid, other["id"], h["id"], "bypass", self.db, now=100)
        self.assertEqual(
            self.sql("SELECT coins FROM mini_players WHERE id=?", (self.pid,))[0][
                "coins"
            ],
            100,
        )
        self.assertEqual(self.sql("SELECT * FROM mini_duels"), [])


class GachaProgression(MiniCase):
    def test_old_copies_and_sequential_batch_without_retro_compensation(self):
        from app.mini.catalog import load_hero_catalog
        from app.mini.gacha import perform_gacha_pull, perform_gacha_batch

        h = self.hero()
        base = load_hero_catalog()["settings"]["duplicate_shards"]["common"]
        self.sql(
            "UPDATE mini_player_heroes SET copies=5 WHERE player_id=? AND hero_id=?",
            (self.pid, h["id"]),
        )
        self.sql("UPDATE mini_players SET shards=0,coins=10000 WHERE id=?", (self.pid,))
        with patch("app.mini.gacha._choose_hero_code", return_value="Villager"):
            one = perform_gacha_pull(self.pid, "coins", self.db, operation_key="one")
            batch = perform_gacha_batch(self.pid, self.db, operation_key="ten")
        self.assertEqual(one["shards_awarded"], base * 5)
        self.assertEqual(batch["shards_awarded"], base * sum(range(6, 15)))
        self.assertEqual(sum(r["rarity"] == "common" for r in batch["results"]), 9)
        self.assertTrue(any(r["rarity"] != "common" for r in batch["results"]))
        self.assertEqual(
            self.sql("SELECT shards FROM mini_players WHERE id=?", (self.pid,))[0][
                "shards"
            ],
            base * 95,
        )
        self.assertEqual(
            self.sql(
                "SELECT copies FROM mini_player_heroes WHERE player_id=? AND hero_id=?",
                (self.pid, h["id"]),
            )[0]["copies"],
            15,
        )

    def test_large_existing_copy_count_has_no_gameplay_cap(self):
        from app.mini.catalog import load_hero_catalog
        from app.mini.gacha import perform_gacha_pull

        h = self.hero()
        base = load_hero_catalog()["settings"]["duplicate_shards"]["common"]
        self.sql(
            "UPDATE mini_player_heroes SET copies=1000000 WHERE player_id=? AND hero_id=?",
            (self.pid, h["id"]),
        )
        self.sql("UPDATE mini_players SET coins=100 WHERE id=?", (self.pid,))
        with patch("app.mini.gacha._choose_hero_code", return_value="Villager"):
            result = perform_gacha_pull(
                self.pid, "coins", self.db, operation_key="large"
            )
        self.assertEqual(result["shards_awarded"], base * 1000000)
