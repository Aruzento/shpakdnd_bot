

def apply(conn) -> None:
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
