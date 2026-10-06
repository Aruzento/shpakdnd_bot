from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.boss.catalog import sync_boss_reward_items


BOSS_TABLES = (
    "mini_bosses",
    "mini_boss_participants",
    "mini_boss_actions",
)


def init_boss_db(db_path: str | Path = DB_PATH) -> None:
    """Создаёт и мигрирует только таблицы модуля боссов."""
    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_bosses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id INTEGER NOT NULL,
                template_code TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                max_hp INTEGER NOT NULL CHECK (max_hp > 0),
                current_hp INTEGER NOT NULL CHECK (current_hp >= 0),
                min_players INTEGER NOT NULL DEFAULT 1 CHECK (min_players > 0),
                status TEXT NOT NULL DEFAULT 'announced',
                signup_opens_at TEXT,
                signup_closes_at TEXT,
                signup_message_id INTEGER,
                signup_message_kind TEXT NOT NULL DEFAULT 'text',
                turn_message_id INTEGER,
                starts_at TEXT,
                current_round INTEGER NOT NULL DEFAULT 1,
                current_turn_position INTEGER NOT NULL DEFAULT 0,
                turn_started_at TEXT,
                skip_after_hours INTEGER NOT NULL DEFAULT 4,
                reward_coins INTEGER NOT NULL DEFAULT 0,
                reward_item_id INTEGER,
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT,
                FOREIGN KEY (world_id)
                    REFERENCES mini_worlds(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (reward_item_id)
                    REFERENCES mini_items(id)
                    ON DELETE SET NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_boss_participants (
                boss_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                queue_position INTEGER NOT NULL,
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                hit_count INTEGER NOT NULL DEFAULT 0,
                total_damage INTEGER NOT NULL DEFAULT 0,
                skipped_turns INTEGER NOT NULL DEFAULT 0,
                damage_bonus_percent INTEGER NOT NULL DEFAULT 0,
                phantom_reward INTEGER NOT NULL DEFAULT 0,
                reward_granted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (boss_id, player_id),
                UNIQUE (boss_id, queue_position),
                FOREIGN KEY (boss_id)
                    REFERENCES mini_bosses(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_boss_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                boss_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                round_number INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                damage INTEGER NOT NULL DEFAULT 0,
                boss_hp_after INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (boss_id)
                    REFERENCES mini_bosses(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE
            )
            """
        )

        # Миграция старой boss-схемы, которая раньше жила в app/mini/schema.py.
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(mini_bosses)").fetchall()
        }
        additions = {
            "faction": "TEXT NOT NULL DEFAULT 'commoners'",
            "ability_key": "TEXT NOT NULL DEFAULT 'none'",
            "ability_text": "TEXT NOT NULL DEFAULT 'Нет особой способности.'",
            "features_json": "TEXT NOT NULL DEFAULT '[]'",
            "ability_config_json": "TEXT NOT NULL DEFAULT '{}'",
            "ability_state_json": "TEXT NOT NULL DEFAULT '{}'",
            "template_code": "TEXT NOT NULL DEFAULT ''",
            "image_path": "TEXT NOT NULL DEFAULT ''",
            "signup_message_id": "INTEGER",
            "signup_message_kind": "TEXT NOT NULL DEFAULT 'text'",
            "turn_message_id": "INTEGER",
            "reward_items_json": "TEXT NOT NULL DEFAULT '[]'",
            "reward_shields": "INTEGER NOT NULL DEFAULT 3",
            "reward_shields_max": "INTEGER NOT NULL DEFAULT 3",
            "reward_percent": "INTEGER NOT NULL DEFAULT 100",
            "reward_decay_percent": "INTEGER NOT NULL DEFAULT 10",
            "battle_result": "TEXT NOT NULL DEFAULT ''",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE mini_bosses ADD COLUMN {name} {sql_type}"
                )

        participant_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mini_boss_participants)"
            ).fetchall()
        }
        participant_additions = {
            "hero_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
            "forced_skip_turns": "INTEGER NOT NULL DEFAULT 0",
            "banished": "INTEGER NOT NULL DEFAULT 0",
            "hero_id": "INTEGER",
            "attack": "INTEGER NOT NULL DEFAULT 0",
            "damage_bonus_percent": "INTEGER NOT NULL DEFAULT 0",
            "phantom_reward": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, sql_type in participant_additions.items():
            if name not in participant_columns:
                conn.execute(
                    f"ALTER TABLE mini_boss_participants ADD COLUMN {name} {sql_type}"
                )

        action_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(mini_boss_actions)").fetchall()
        }
        if "event_json" not in action_columns:
            conn.execute(
                "ALTER TABLE mini_boss_actions ADD COLUMN event_json TEXT NOT NULL DEFAULT '{}'"
            )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mini_bosses_world_status
            ON mini_bosses (world_id, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mini_boss_actions_boss
            ON mini_boss_actions (boss_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mini_boss_participants_player
            ON mini_boss_participants (player_id, boss_id)
            """
        )
        conn.commit()

    sync_boss_reward_items(db_path)
