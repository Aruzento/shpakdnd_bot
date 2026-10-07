"""Additive favorites and versioned class reward state; no live data conversion."""

def boss_state(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='mini_bosses'").fetchone():
        return
    columns={r[1] for r in conn.execute('PRAGMA table_info(mini_bosses)')}
    for name in ('class_rules_version','boss_damage','sneaky_stolen'):
        if name not in columns:
            conn.execute(f'ALTER TABLE mini_bosses ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0 CHECK({name}>=0)')


def apply(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_hero_favorites (
        player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
        hero_id INTEGER NOT NULL REFERENCES mini_heroes(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 2),
        PRIMARY KEY(player_id,hero_id), UNIQUE(player_id,position),
        FOREIGN KEY(player_id,hero_id) REFERENCES mini_player_heroes(player_id,hero_id) ON DELETE CASCADE)""")
    boss_state(conn)
