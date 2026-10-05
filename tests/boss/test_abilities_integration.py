import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.combat import hit_boss, start_battle
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import (
    close_registration,
    create_boss_event,
    register_player,
)
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.players import create_mini_player, get_mini_player
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds


UTC = timezone.utc


class BossAbilityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        init_boss_db(self.db)
        self.world_id = sync_configured_mini_worlds(self.db)[0]["id"]
        sync_hero_catalog(self.db)

        self.player1 = self._create_player(9101, "@passive_one", "Первый")
        self.player2 = self._create_player(9102, "@passive_two", "Второй")

        with connect_mini_db(self.db) as conn:
            cursor = conn.execute(
                """
                INSERT INTO mini_heroes (
                    code, name, rarity, race, class_name, attack,
                    passive_key, passive_text, description, image_path, active
                ) VALUES (
                    'ability_test_execution', 'Тестовая Азраэль', 'legendary',
                    'тифлинг', 'плут', 40, 'execution_protocol',
                    'Тест Ликвидации', '', '', 1
                )
                """
            )
            hero_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO mini_player_heroes (
                    player_id, hero_id, copies, shards, stars
                ) VALUES (?, ?, 1, 0, 0)
                """,
                (self.player1["id"], hero_id),
            )
            conn.execute(
                "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
                (hero_id, self.player1["id"]),
            )
            conn.commit()

        self.boss = create_boss_event(
            self.world_id, "training_golem", 999, self.db
        )
        # Интеграционный тест не зависит от min_players живого bosses.json.
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_bosses SET min_players = 2 WHERE id = ?",
                (self.boss["id"],),
            )
            conn.commit()
        register_player(self.boss["id"], self.player1["id"], self.db)
        register_player(self.boss["id"], self.player2["id"], self.db)
        close_registration(self.boss["id"], self.db)
        self.now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
        start_battle(self.boss["id"], now=self.now, db_path=self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def _create_player(self, user_id: int, username: str, name: str) -> dict:
        player = create_mini_player(
            self.world_id, user_id, username, name, self.db
        )
        with connect_mini_db(self.db) as conn:
            hero_id = int(
                conn.execute(
                    "SELECT id FROM mini_heroes WHERE code = 'Villager'"
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO mini_player_heroes (
                    player_id, hero_id, copies, shards, stars
                ) VALUES (?, ?, 1, 0, 0)
                """,
                (player["id"], hero_id),
            )
            conn.execute(
                "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
                (hero_id, player["id"]),
            )
            conn.commit()
        return player

    def test_combat_applies_execution_damage_and_kill_shards(self):
        # Фиксируем HP тестовой цели: 50 из 300 строго ниже 20%.
        with connect_mini_db(self.db) as conn:
            conn.execute(
                "UPDATE mini_bosses SET max_hp = 300, current_hp = 50 WHERE id = ?",
                (self.boss["id"],),
            )
            conn.commit()

        with patch(
            "app.mini.boss.abilities.engine._roll_success",
            return_value=True,
        ):
            result = hit_boss(
                self.boss["id"],
                self.player1["id"],
                now=self.now,
                db_path=self.db,
            )

        self.assertEqual(result["base_damage"], 40)
        self.assertEqual(result["damage"], 60)
        self.assertEqual(result["bonus_shards"], 30)
        self.assertTrue(result["battle_ended"])
        self.assertEqual(result["state"]["boss"]["status"], "defeated")
        player = get_mini_player(
            self.world_id, self.player1["telegram_user_id"], self.db
        )
        self.assertEqual(player["shards"], 30)


if __name__ == "__main__":
    unittest.main()
