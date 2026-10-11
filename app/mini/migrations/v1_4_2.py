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
