import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


MINI_TABLES = (
    "mini_worlds",
    "mini_players",
    "mini_heroes",
    "mini_player_heroes",
    "mini_wallet_transactions",
    "mini_daily_claims",
    "mini_items",
    "mini_inventory",
    "mini_player_effects",
    "mini_item_uses",
    "mini_shop_offers",
    "mini_purchases",
    "mini_gacha_pulls",
)


def init_mini_db(db_path: str | Path = DB_PATH) -> None:
    """Создаёт отдельный набор таблиц D&D Mini, не трогая обычный D&D."""
    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_worlds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL DEFAULT 'D&D Mini',
                currency_name TEXT NOT NULL DEFAULT 'Монеты Mini',
                boss_skip_hours INTEGER NOT NULL DEFAULT 4,
                launcher_message_id INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (chat_id, thread_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id INTEGER NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                character_name TEXT NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0 CHECK (coins >= 0),
                shards INTEGER NOT NULL DEFAULT 0 CHECK (shards >= 0),
                active_hero_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT,
                UNIQUE (world_id, telegram_user_id),
                FOREIGN KEY (world_id)
                    REFERENCES mini_worlds(id)
                    ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_heroes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                rarity TEXT NOT NULL,
                race TEXT NOT NULL,
                class_name TEXT NOT NULL,
                attack INTEGER NOT NULL DEFAULT 1,
                passive_key TEXT NOT NULL DEFAULT '',
                passive_text TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_player_heroes (
                player_id INTEGER NOT NULL,
                hero_id INTEGER NOT NULL,
                copies INTEGER NOT NULL DEFAULT 1,
                shards INTEGER NOT NULL DEFAULT 0,
                stars INTEGER NOT NULL DEFAULT 0 CHECK (stars >= 0),
                obtained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (player_id, hero_id),
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (hero_id)
                    REFERENCES mini_heroes(id)
                    ON DELETE RESTRICT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL,
                reference_type TEXT NOT NULL DEFAULT '',
                reference_id INTEGER,
                operation_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                effect_key TEXT NOT NULL DEFAULT '',
                stackable INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_inventory (
                player_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (player_id, item_id),
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (item_id)
                    REFERENCES mini_items(id)
                    ON DELETE RESTRICT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_player_effects (
                player_id INTEGER NOT NULL,
                effect_key TEXT NOT NULL,
                charges INTEGER NOT NULL DEFAULT 0 CHECK (charges >= 0),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (player_id, effect_key),
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_item_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                effect_key TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}',
                operation_key TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (item_id)
                    REFERENCES mini_items(id)
                    ON DELETE RESTRICT
            )
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mini_item_uses_operation
            ON mini_item_uses (player_id, operation_key)
            WHERE operation_key <> ''
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_daily_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                claim_date TEXT NOT NULL,
                encounter_key TEXT NOT NULL DEFAULT '',
                story_text TEXT NOT NULL DEFAULT '',
                coins_earned INTEGER NOT NULL DEFAULT 0,
                item_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (player_id, claim_date),
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (item_id)
                    REFERENCES mini_items(id)
                    ON DELETE SET NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_shop_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                offer_type TEXT NOT NULL,
                item_id INTEGER,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL CHECK (price >= 0),
                stock INTEGER,
                max_per_player INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                starts_at TEXT,
                ends_at TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE (world_id, code),
                FOREIGN KEY (world_id)
                    REFERENCES mini_worlds(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (item_id)
                    REFERENCES mini_items(id)
                    ON DELETE SET NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                offer_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                total_price INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                redeemed_at TEXT,
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (offer_id)
                    REFERENCES mini_shop_offers(id)
                    ON DELETE RESTRICT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mini_gacha_pulls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                hero_id INTEGER NOT NULL,
                cost_coins INTEGER NOT NULL DEFAULT 0,
                used_ticket INTEGER NOT NULL DEFAULT 0,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                shards_awarded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_id)
                    REFERENCES mini_players(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (hero_id)
                    REFERENCES mini_heroes(id)
                    ON DELETE RESTRICT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mini_players_world
            ON mini_players (world_id, telegram_user_id)
            """
        )

        wallet_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mini_wallet_transactions)"
            ).fetchall()
        }

        if "operation_key" not in wallet_columns:
            conn.execute(
                """
                ALTER TABLE mini_wallet_transactions
                ADD COLUMN operation_key TEXT NOT NULL DEFAULT ''
                """
            )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mini_wallet_player_history
            ON mini_wallet_transactions (
                player_id,
                id DESC
            )
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mini_wallet_operation_key
            ON mini_wallet_transactions (
                player_id,
                operation_key
            )
            WHERE operation_key <> ''
            """
        )

        player_hero_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mini_player_heroes)"
            ).fetchall()
        }

        if "stars" not in player_hero_columns:
            conn.execute(
                """
                ALTER TABLE mini_player_heroes
                ADD COLUMN stars INTEGER NOT NULL DEFAULT 0
                """
            )

        player_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mini_players)"
            ).fetchall()
        }

        if "shards" not in player_columns:
            legacy_shards = conn.execute(
                """
                SELECT player_id, COALESCE(SUM(shards), 0)
                FROM mini_player_heroes
                GROUP BY player_id
                """
            ).fetchall()

            conn.execute(
                """
                ALTER TABLE mini_players
                ADD COLUMN shards INTEGER NOT NULL DEFAULT 0
                """
            )

            for player_id, total_shards in legacy_shards:
                conn.execute(
                    "UPDATE mini_players SET shards = ? WHERE id = ?",
                    (int(total_shards), int(player_id)),
                )

            # Старое поле оставляем ради безопасной миграции,
            # но после переноса больше не используем.
            conn.execute("UPDATE mini_player_heroes SET shards = 0")

        hero_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mini_heroes)"
            ).fetchall()
        }

        combat_defaults = {
            "faction": "commoners", "damage_type": "slashing",
            "class_tag": "none", "attack_range": "melee", "special_trait": "none",
        }
        for name, default in combat_defaults.items():
            if name not in hero_columns:
                conn.execute(
                    f"ALTER TABLE mini_heroes ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'"
                )

        if "image_path" not in hero_columns:
            conn.execute(
                """
                ALTER TABLE mini_heroes
                ADD COLUMN image_path TEXT NOT NULL DEFAULT ''
                """
            )

        world_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(mini_worlds)"
            ).fetchall()
        }

        if "launcher_message_id" not in world_columns:
            conn.execute(
                """
                ALTER TABLE mini_worlds
                ADD COLUMN launcher_message_id INTEGER
                """
            )

        conn.commit()
