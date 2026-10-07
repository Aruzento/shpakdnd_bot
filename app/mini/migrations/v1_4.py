"""V1.4 additive state. Caller owns the transaction; historical tables stay intact."""

def boss_state(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='mini_bosses'").fetchone():
        return
    columns={r[1] for r in conn.execute('PRAGMA table_info(mini_bosses)')}
    for name, definition in {
        'content_version': 'INTEGER NOT NULL DEFAULT 0',
        'shadow_extractable': 'INTEGER NOT NULL DEFAULT 1 CHECK(shadow_extractable IN (0,1))',
        'shadow_hero_code': 'TEXT',
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE mini_bosses ADD COLUMN {name} {definition}')


def apply(conn):
    statements = [
        """CREATE TABLE IF NOT EXISTS mini_villages (
            player_id INTEGER PRIMARY KEY REFERENCES mini_players(id),
            houses INTEGER NOT NULL DEFAULT 0 CHECK(houses BETWEEN 0 AND 5),
            cycle_started INTEGER NOT NULL, settled_at INTEGER NOT NULL,
            coins_units INTEGER NOT NULL DEFAULT 0 CHECK(coins_units>=0),
            shards_units INTEGER NOT NULL DEFAULT 0 CHECK(shards_units>=0),
            revision INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS mini_village_residents (
            player_id INTEGER NOT NULL, hero_id INTEGER NOT NULL,
            building TEXT CHECK(building IN ('mine','market','hunt')),
            PRIMARY KEY(player_id,hero_id),
            FOREIGN KEY(player_id,hero_id) REFERENCES mini_player_heroes(player_id,hero_id))""",
        """CREATE TABLE IF NOT EXISTS mini_village_boosts (
            player_id INTEGER NOT NULL REFERENCES mini_players(id),
            resource TEXT NOT NULL CHECK(resource IN ('coins','shards')),
            starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL CHECK(ends_at>starts_at),
            PRIMARY KEY(player_id,resource))""",
        """CREATE TABLE IF NOT EXISTS mini_village_operations (
            player_id INTEGER NOT NULL REFERENCES mini_players(id), operation_key TEXT NOT NULL,
            payload TEXT NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY(player_id,operation_key))""",
        """CREATE TABLE IF NOT EXISTS mini_daily_streak (
            player_id INTEGER PRIMARY KEY REFERENCES mini_players(id), last_day TEXT NOT NULL,
            streak INTEGER NOT NULL CHECK(streak>0))""",
        """CREATE TABLE IF NOT EXISTS mini_daily_chests (
            player_id INTEGER NOT NULL REFERENCES mini_players(id), claim_date TEXT NOT NULL,
            item_code TEXT NOT NULL, PRIMARY KEY(player_id,claim_date))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_fragments (
            player_id INTEGER NOT NULL REFERENCES mini_players(id), hero_code TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0 CHECK(amount>=0), PRIMARY KEY(player_id,hero_code))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_grants (
            player_id INTEGER NOT NULL REFERENCES mini_players(id), operation_key TEXT NOT NULL,
            hero_code TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), source TEXT NOT NULL,
            PRIMARY KEY(player_id,operation_key))""",
        """CREATE TABLE IF NOT EXISTS mini_shadow_rolls (
            boss_id INTEGER NOT NULL REFERENCES mini_bosses(id), player_id INTEGER NOT NULL,
            hero_id INTEGER NOT NULL, chance_percent INTEGER NOT NULL CHECK(chance_percent BETWEEN 0 AND 100),
            shadow_code TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0,1)),
            PRIMARY KEY(boss_id,player_id,hero_id))""",
        """CREATE TABLE IF NOT EXISTS mini_duels (
            id INTEGER PRIMARY KEY, challenger_id INTEGER NOT NULL REFERENCES mini_players(id),
            defender_id INTEGER NOT NULL REFERENCES mini_players(id),
            challenger_hero_id INTEGER NOT NULL REFERENCES mini_heroes(id),
            defender_hero_id INTEGER REFERENCES mini_heroes(id),
            challenger_snapshot TEXT NOT NULL, defender_snapshot TEXT,
            expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','finished','refused','expired','cancelled','insufficient')),
            result_json TEXT, operation_key TEXT NOT NULL,
            CHECK(challenger_id!=defender_id), UNIQUE(challenger_id,operation_key))""",
        """CREATE TABLE IF NOT EXISTS mini_duel_locks (
            player_id INTEGER PRIMARY KEY REFERENCES mini_players(id),
            duel_id INTEGER NOT NULL REFERENCES mini_duels(id))""",
    ]
    for sql in statements:
        conn.execute(sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mini_duel_expiry ON mini_duels(status,expires_at)")
    daily_columns={r[1] for r in conn.execute('PRAGMA table_info(mini_daily_claims)')}
    for field,definition in {'streak_count':'INTEGER NOT NULL DEFAULT 1','cycle_day':'INTEGER NOT NULL DEFAULT 1','chest_code':'TEXT'}.items():
        if field not in daily_columns:
            conn.execute(f'ALTER TABLE mini_daily_claims ADD COLUMN {field} {definition}')
    pull_columns={r[1] for r in conn.execute('PRAGMA table_info(mini_gacha_pulls)')}
    for field in ('operation_key','result_json'):
        if field not in pull_columns:
            conn.execute(f'ALTER TABLE mini_gacha_pulls ADD COLUMN {field} TEXT')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_mini_gacha_operation ON mini_gacha_pulls(player_id,operation_key) WHERE operation_key IS NOT NULL')
    boss_state(conn)
