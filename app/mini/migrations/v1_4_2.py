"""Additive V1.4.2 state; old pulls and administrative guarantees stay intact."""


def apply(conn):
    columns={row[1] for row in conn.execute('PRAGMA table_info(mini_purchases)')}
    for field in ('operation_key','result_json'):
        if field not in columns:
            conn.execute(f"ALTER TABLE mini_purchases ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mini_purchase_operation ON mini_purchases(player_id,operation_key) WHERE operation_key!=''")
    conn.execute('''CREATE TABLE IF NOT EXISTS mini_gacha_pity (
        player_id INTEGER PRIMARY KEY REFERENCES mini_players(id) ON DELETE CASCADE,
        uncommon_count INTEGER NOT NULL DEFAULT 0 CHECK(typeof(uncommon_count)='integer' AND uncommon_count BETWEEN 0 AND 10),
        rare_count INTEGER NOT NULL DEFAULT 0 CHECK(typeof(rare_count)='integer' AND rare_count BETWEEN 0 AND 25),
        legendary_count INTEGER NOT NULL DEFAULT 0 CHECK(typeof(legendary_count)='integer' AND legendary_count BETWEEN 0 AND 85),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS mini_gacha_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        operation_key TEXT NOT NULL,
        cost_coins INTEGER NOT NULL CHECK(cost_coins>=0),
        result_json TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(player_id,operation_key)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS mini_registration_sessions (
        token TEXT PRIMARY KEY,
        world_id INTEGER NOT NULL REFERENCES mini_worlds(id) ON DELETE CASCADE,
        telegram_user_id INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
        completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_mini_registration_owner ON mini_registration_sessions(telegram_user_id,active,expires_at)')
    conn.execute('''CREATE TABLE IF NOT EXISTS mini_public_shares (
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        operation_key TEXT NOT NULL,
        PRIMARY KEY(player_id,operation_key)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS mini_tower_retries (
        previous_attempt_id INTEGER PRIMARY KEY REFERENCES mini_tower_attempts(id),
        new_attempt_id INTEGER NOT NULL REFERENCES mini_tower_attempts(id)
    )''')
