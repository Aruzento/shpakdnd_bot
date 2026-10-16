"""Additive PvP escrow; no historical session or balance conversion."""
def apply(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_event_pvp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        world_id INTEGER NOT NULL REFERENCES mini_worlds(id),
        creator_id INTEGER NOT NULL REFERENCES mini_players(id),
        acceptor_id INTEGER REFERENCES mini_players(id),
        operation_key TEXT NOT NULL,
        stake INTEGER NOT NULL DEFAULT 5 CHECK(stake=5),
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN
            ('open','choosing','finished','cancelled','expired','publication_failed')),
        created_at INTEGER NOT NULL,
        accept_until INTEGER NOT NULL,
        choose_until INTEGER,
        winner_id INTEGER REFERENCES mini_players(id),
        outcome TEXT,
        revision INTEGER NOT NULL DEFAULT 1,
        publication TEXT NOT NULL DEFAULT 'pending' CHECK(publication IN
            ('pending','sending','sent','uncertain','failed')),
        message_id INTEGER,
        published_revision INTEGER NOT NULL DEFAULT 0,
        editing_until INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        UNIQUE(creator_id,operation_key),
        CHECK(acceptor_id IS NULL OR acceptor_id!=creator_id),
        CHECK((status='choosing' AND acceptor_id IS NOT NULL AND choose_until IS NOT NULL)
              OR status!='choosing')
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_event_pvp_members (
        session_id INTEGER NOT NULL REFERENCES mini_event_pvp(id),
        player_id INTEGER NOT NULL REFERENCES mini_players(id),
        choice TEXT CHECK(choice IN ('saint','demon','villager')),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        PRIMARY KEY(session_id,player_id)
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_pvp_active_player
        ON mini_event_pvp_members(player_id) WHERE active=1""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_pvp_deadline
        ON mini_event_pvp(status,accept_until,choose_until)""")
