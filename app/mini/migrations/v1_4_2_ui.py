"""Additive UI completion state. No existing rows or combat snapshots change."""


def apply(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_gacha_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        operation_key TEXT NOT NULL CHECK(length(operation_key)>0),
        pull_price INTEGER NOT NULL CHECK(pull_price>=0),
        cost_coins INTEGER NOT NULL CHECK(cost_coins=10*pull_price),
        result_json TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(player_id,operation_key)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_gacha_batch_pulls (
        batch_id INTEGER NOT NULL REFERENCES mini_gacha_batches(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 9),
        pull_id INTEGER NOT NULL UNIQUE REFERENCES mini_gacha_pulls(id),
        PRIMARY KEY(batch_id,position)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_registration_requests (
        token TEXT PRIMARY KEY,
        world_id INTEGER NOT NULL REFERENCES mini_worlds(id) ON DELETE CASCADE,
        telegram_user_id INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        waiting INTEGER NOT NULL DEFAULT 0 CHECK(waiting IN (0,1)),
        player_id INTEGER REFERENCES mini_players(id) ON DELETE SET NULL
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_mini_registration_owner
        ON mini_registration_requests(telegram_user_id,waiting,expires_at)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_ui_navigation (
        token TEXT PRIMARY KEY,
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        screen TEXT NOT NULL,
        context_json TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_ui_publications (
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        operation_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'sending',
        PRIMARY KEY(player_id,operation_key)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_tower_retries (
        source_attempt_id INTEGER PRIMARY KEY REFERENCES mini_tower_attempts(id) ON DELETE CASCADE,
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        new_attempt_id INTEGER NOT NULL UNIQUE REFERENCES mini_tower_attempts(id)
    )""")
