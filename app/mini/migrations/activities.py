

def apply(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mini_event_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            game_type TEXT NOT NULL CHECK (game_type IN ('rps', 'labyrinth')),
            stake INTEGER NOT NULL CHECK (
                (game_type = 'rps' AND stake IN (1, 5, 10)) OR
                (game_type = 'labyrinth' AND stake = 5)
            ),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'resolved')),
            step INTEGER NOT NULL DEFAULT 0 CHECK (step BETWEEN 0 AND 3),
            payload_json TEXT NOT NULL DEFAULT '{}',
            start_key TEXT NOT NULL CHECK (start_key <> ''),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            UNIQUE (player_id, start_key),
            FOREIGN KEY (player_id) REFERENCES mini_players(id)
                ON DELETE CASCADE
        )
        """
    )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mini_events_active_player
        ON mini_event_sessions (player_id) WHERE status = 'active'
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mini_event_requests (
            player_id INTEGER NOT NULL,
            operation_key TEXT NOT NULL CHECK (operation_key <> ''),
            session_id INTEGER NOT NULL,
            PRIMARY KEY (player_id, operation_key),
            FOREIGN KEY (player_id) REFERENCES mini_players(id)
                ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES mini_event_sessions(id)
                ON DELETE CASCADE
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
