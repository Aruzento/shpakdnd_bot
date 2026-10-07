"""Additive UX state. Existing players are marked as already onboarded."""


def apply(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_onboarding_claims (
        world_id INTEGER NOT NULL,
        telegram_user_id INTEGER NOT NULL,
        player_id INTEGER REFERENCES mini_players(id) ON DELETE SET NULL,
        tickets INTEGER NOT NULL DEFAULT 0 CHECK(tickets IN (0,3)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(world_id,telegram_user_id)
    )""")
    conn.execute("""INSERT OR IGNORE INTO mini_onboarding_claims
        (world_id,telegram_user_id,player_id,tickets)
        SELECT world_id,telegram_user_id,id,0 FROM mini_players""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_titles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        text TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 48),
        expires_at INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','replaced','expired')),
        granted_by INTEGER NOT NULL,
        operation_key TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mini_title_active ON mini_titles(player_id) WHERE status='active'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mini_title_expiry ON mini_titles(status,expires_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_public_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL UNIQUE,
        world_id INTEGER NOT NULL REFERENCES mini_worlds(id) ON DELETE CASCADE,
        player_id INTEGER REFERENCES mini_players(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('welcome','title_expired')),
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','sending','sent','uncertain','cancelled')),
        message_id INTEGER,
        last_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
