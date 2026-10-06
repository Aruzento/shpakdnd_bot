from app.mini.combat.tags import LEGACY_HERO_TRAITS


def apply(conn) -> None:
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

    combat_defaults = LEGACY_HERO_TRAITS

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
