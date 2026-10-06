

def apply(conn) -> None:
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
