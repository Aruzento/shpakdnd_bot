"""Additive UI completion, including compatibility with the earlier draft schema."""
import json


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
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_mini_registration_request_owner
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
        previous_attempt_id INTEGER PRIMARY KEY REFERENCES mini_tower_attempts(id),
        new_attempt_id INTEGER NOT NULL UNIQUE REFERENCES mini_tower_attempts(id)
    )""")

    # e27153e created batches without pull_price. Keep every old result verbatim.
    columns={r[1] for r in conn.execute('PRAGMA table_info(mini_gacha_batches)')}
    if 'pull_price' not in columns:
        conn.execute('ALTER TABLE mini_gacha_batches ADD COLUMN pull_price INTEGER NOT NULL DEFAULT 0 CHECK(pull_price>=0)')
        conn.execute('UPDATE mini_gacha_batches SET pull_price=cost_coins/10')
    for batch in conn.execute('SELECT id,result_json FROM mini_gacha_batches').fetchall():
        if not batch[1]:continue
        results=json.loads(batch[1])['results']
        for position,result in enumerate(results):
            conn.execute('INSERT OR IGNORE INTO mini_gacha_batch_pulls(batch_id,position,pull_id) VALUES(?,?,?)',
                         (batch[0],position,result['pull_id']))
    # Old draft callbacks/deep links remain usable after a restart. Retain legacy
    # tables for code rollback; the canonical runtime writes requests/publications.
    conn.execute("""INSERT OR IGNORE INTO mini_registration_requests
        (token,world_id,telegram_user_id,expires_at,waiting,player_id)
        SELECT s.token,s.world_id,s.telegram_user_id,s.expires_at,
            CASE WHEN s.active=1 AND s.completed=0 AND p.id IS NULL THEN 1 ELSE 0 END,p.id
        FROM mini_registration_sessions s LEFT JOIN mini_players p
            ON p.world_id=s.world_id AND p.telegram_user_id=s.telegram_user_id""")
    for player,key in conn.execute('SELECT player_id,operation_key FROM mini_public_shares').fetchall():
        keys=[key]
        parts=key.split(':')
        if len(parts)==3 and parts[0]=='share':keys.append('share:'+parts[1]+'.'+parts[2])
        for compatible in keys:
            conn.execute('INSERT OR IGNORE INTO mini_ui_publications(player_id,operation_key) VALUES(?,?)',(player,compatible))
