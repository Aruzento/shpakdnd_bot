import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.combat import hit_boss
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import get_boss
from app.mini.db import connect_mini_db
from app.mini.heroes import sync_hero_catalog
from app.mini.schema import init_mini_db


# Complete pre-v2 tables, including live turn/message/reward/hero snapshots.
LEGACY_HERO_SQL = """
CREATE TABLE mini_heroes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, rarity TEXT NOT NULL, race TEXT NOT NULL,
    class_name TEXT NOT NULL, attack INTEGER NOT NULL DEFAULT 1,
    passive_key TEXT NOT NULL DEFAULT '', passive_text TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', image_path TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
LEGACY_BOSS_SQL = """
CREATE TABLE mini_bosses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, world_id INTEGER NOT NULL,
    template_code TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', image_path TEXT NOT NULL DEFAULT '',
    max_hp INTEGER NOT NULL, current_hp INTEGER NOT NULL,
    min_players INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'announced',
    signup_opens_at TEXT, signup_closes_at TEXT, signup_message_id INTEGER,
    signup_message_kind TEXT NOT NULL DEFAULT 'text', turn_message_id INTEGER,
    starts_at TEXT, current_round INTEGER NOT NULL DEFAULT 1,
    current_turn_position INTEGER NOT NULL DEFAULT 0, turn_started_at TEXT,
    skip_after_hours INTEGER NOT NULL DEFAULT 4, reward_coins INTEGER NOT NULL DEFAULT 0,
    reward_item_id INTEGER, created_by_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT,
    reward_items_json TEXT NOT NULL DEFAULT '[]',
    reward_shields INTEGER NOT NULL DEFAULT 3, reward_shields_max INTEGER NOT NULL DEFAULT 3,
    reward_percent INTEGER NOT NULL DEFAULT 100,
    reward_decay_percent INTEGER NOT NULL DEFAULT 10,
    battle_result TEXT NOT NULL DEFAULT ''
)
"""
LEGACY_PARTICIPANT_SQL = """
CREATE TABLE mini_boss_participants (
    boss_id INTEGER NOT NULL, player_id INTEGER NOT NULL, queue_position INTEGER NOT NULL,
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hero_id INTEGER, attack INTEGER NOT NULL DEFAULT 0,
    hit_count INTEGER NOT NULL DEFAULT 0, total_damage INTEGER NOT NULL DEFAULT 0,
    skipped_turns INTEGER NOT NULL DEFAULT 0, damage_bonus_percent INTEGER NOT NULL DEFAULT 0,
    phantom_reward INTEGER NOT NULL DEFAULT 0, reward_granted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (boss_id, player_id), UNIQUE (boss_id, queue_position)
)
"""
LEGACY_ACTION_SQL = """
CREATE TABLE mini_boss_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, boss_id INTEGER NOT NULL, player_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL, action_type TEXT NOT NULL,
    damage INTEGER NOT NULL DEFAULT 0, boss_hp_after INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class CombatV2MigrationTests(unittest.TestCase):
    def test_migrate_live_old_battle_preserves_every_existing_column_and_can_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "legacy.db"
            with connect_mini_db(db) as conn:
                conn.execute(LEGACY_HERO_SQL)
                conn.execute(
                    """INSERT INTO mini_heroes (id, code, name, rarity, race, class_name, attack, passive_key)
                       VALUES (1, 'Villager', 'Legacy', 'common', 'human', 'none', 3, 'none')"""
                )
            init_mini_db(db)
            with connect_mini_db(db) as conn:
                conn.execute("INSERT INTO mini_worlds (id, chat_id) VALUES (1, -100)")
                conn.execute(
                    """INSERT INTO mini_players (id, world_id, telegram_user_id, character_name, active_hero_id)
                       VALUES (1, 1, 101, 'Legacy', NULL)"""
                )
                conn.execute("INSERT INTO mini_player_heroes (player_id, hero_id, stars) VALUES (1, 1, 4)")
                for sql in (LEGACY_BOSS_SQL, LEGACY_PARTICIPANT_SQL, LEGACY_ACTION_SQL):
                    conn.execute(sql)
                conn.execute(
                    """INSERT INTO mini_bosses (
                        id, world_id, name, max_hp, current_hp, status, current_round,
                        current_turn_position, turn_started_at, reward_percent,
                        reward_shields, reward_coins, turn_message_id, signup_message_id,
                        created_by_user_id
                    ) VALUES (1, 1, 'Legacy boss', 1000, 712, 'fighting', 7,
                              1, '2026-10-06 10:00:00', 60, 1, 100, 555, 444, 999)"""
                )
                conn.execute(
                    """INSERT INTO mini_boss_participants (
                        boss_id, player_id, queue_position, hero_id, attack, hit_count,
                        total_damage, damage_bonus_percent, skipped_turns, phantom_reward
                    ) VALUES (1, 1, 1, 1, 17, 9, 288, 10, 2, 1)"""
                )
                conn.execute(
                    """INSERT INTO mini_boss_actions (
                        boss_id, player_id, round_number, action_type, damage, boss_hp_after
                    ) VALUES (1, 1, 7, 'attack', 19, 712)"""
                )
                conn.row_factory = sqlite3.Row
                before = {
                    table: [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
                    for table in ("mini_bosses", "mini_boss_participants", "mini_boss_actions")
                }
            for _ in range(2):
                init_mini_db(db)
                init_boss_db(db)
                sync_hero_catalog(db)
            with connect_mini_db(db) as conn:
                conn.row_factory = sqlite3.Row
                for table, rows in before.items():
                    migrated = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
                    self.assertEqual(len(migrated), len(rows))
                    for original, updated in zip(rows, migrated):
                        self.assertEqual({key: updated[key] for key in original}, original)
                hero = conn.execute("SELECT * FROM mini_heroes WHERE id = 1").fetchone()
                self.assertEqual(hero["faction"], "commoners")
                self.assertEqual(hero["damage_type"], "slashing")
                participant = conn.execute("SELECT * FROM mini_boss_participants").fetchone()
                self.assertEqual(participant["forced_skip_turns"], 0)
                self.assertEqual(participant["banished"], 0)
                self.assertEqual(participant["hero_snapshot_json"], "{}")
            boss = get_boss(1, db)
            self.assertEqual(boss["ability_key"], "none")
            self.assertEqual(boss["ability_state_json"], "{}")
            result = hit_boss(1, 1, now=datetime(2026, 10, 6, 10, tzinfo=timezone.utc), db_path=db)
            self.assertEqual(result["base_damage"], 17)  # Do not recalculate the 4 stars.
            self.assertEqual(result["damage"], 19)
            self.assertEqual(result["state"]["boss"]["current_hp"], 693)
            self.assertEqual(result["state"]["boss"]["current_round"], 8)
            self.assertEqual(result["state"]["boss"]["turn_message_id"], 555)

    def test_old_hero_table_migrates_without_changing_existing_stats(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "heroes.db"
            with connect_mini_db(db) as conn:
                conn.execute(LEGACY_HERO_SQL)
                conn.execute(
                    """INSERT INTO mini_heroes (code, name, rarity, race, class_name, attack, passive_key)
                       VALUES ('legacy_custom', 'Custom', 'rare', 'human', 'old', 73, 'none')"""
                )
            init_mini_db(db)
            init_mini_db(db)
            with connect_mini_db(db) as conn:
                row = conn.execute(
                    "SELECT attack, rarity, passive_key, faction, damage_type, class_tag, attack_range, special_trait FROM mini_heroes"
                ).fetchone()
            self.assertEqual(row, (73, "rare", "none", "commoners", "slashing", "none", "melee", "none"))
