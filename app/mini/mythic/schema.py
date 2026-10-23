"""Additive V1.5 schema; caller owns the transaction."""


def apply(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(mini_players)")}
    if "mythic_shards" not in columns:
        conn.execute(
            "ALTER TABLE mini_players ADD COLUMN mythic_shards INTEGER NOT NULL DEFAULT 0 CHECK(mythic_shards>=0)"
        )
    columns = {r[1] for r in conn.execute("PRAGMA table_info(mini_heroes)")}
    for name, kind in {
        "passives_json": "TEXT NOT NULL DEFAULT '{}'",
        "mythic_element": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE mini_heroes ADD COLUMN {name} {kind}")
    statements = [
        """CREATE TABLE IF NOT EXISTS mini_mythic_ledger (
      id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
      operation_key TEXT NOT NULL, amount INTEGER NOT NULL, balance_after INTEGER NOT NULL CHECK(balance_after>=0),
      source TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(player_id,operation_key))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_operations (
      player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE, operation_key TEXT NOT NULL,
      payload TEXT NOT NULL,result_json TEXT NOT NULL,PRIMARY KEY(player_id,operation_key))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_pets (
      id INTEGER PRIMARY KEY, player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
      element TEXT NOT NULL CHECK(element IN ('ice','lightning','dark','fire','star','earth','moon','sun')),
      stage TEXT NOT NULL DEFAULT 'egg' CHECK(stage IN ('egg','wonder','adult')),
      location TEXT NOT NULL DEFAULT 'inventory' CHECK(location IN ('inventory','active','reserve')),
      mood INTEGER NOT NULL DEFAULT 70 CHECK(mood BETWEEN 0 AND 100), active_seconds INTEGER NOT NULL DEFAULT 0,
      stage_seconds INTEGER NOT NULL DEFAULT 0, next_decay INTEGER NOT NULL DEFAULT 28800,
      protect_until INTEGER NOT NULL DEFAULT 0, cooldowns_json TEXT NOT NULL DEFAULT '{}',
      settled_at INTEGER NOT NULL, hero_id INTEGER REFERENCES mini_heroes(id), evolution_pending INTEGER NOT NULL DEFAULT 0,
      UNIQUE(player_id,element))""",
        "CREATE UNIQUE INDEX IF NOT EXISTS mini_pet_one_active ON mini_mythic_pets(player_id) WHERE location='active'",
        """CREATE TABLE IF NOT EXISTS mini_mythic_pet_days (
      player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE, game_day TEXT NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed BETWEEN 0 AND 5), claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
      PRIMARY KEY(player_id,game_day))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_raids (
      id INTEGER PRIMARY KEY,world_id INTEGER NOT NULL REFERENCES mini_worlds(id) ON DELETE CASCADE,
      template_code TEXT NOT NULL,snapshot_json TEXT NOT NULL,state_json TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','rewarded','cancelled')),
      original_max_hp INTEGER NOT NULL CHECK(original_max_hp>=100000),max_hp INTEGER NOT NULL,current_hp INTEGER NOT NULL CHECK(current_hp>=0),
      created_by INTEGER NOT NULL,created_at INTEGER NOT NULL,ended_at INTEGER,
      result_json TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 0)""",
        "CREATE UNIQUE INDEX IF NOT EXISTS mini_raid_one_active ON mini_mythic_raids(world_id) WHERE status='active'",
        """CREATE TABLE IF NOT EXISTS mini_mythic_raid_attacks (
      id INTEGER PRIMARY KEY,raid_id INTEGER NOT NULL REFERENCES mini_mythic_raids(id),
      player_id INTEGER NOT NULL REFERENCES mini_players(id),hero_id INTEGER NOT NULL REFERENCES mini_heroes(id),
      game_day TEXT NOT NULL,operation_key TEXT NOT NULL,payload TEXT NOT NULL,
      hero_json TEXT NOT NULL,events_json TEXT NOT NULL,result_json TEXT NOT NULL,created_at INTEGER NOT NULL,
      UNIQUE(player_id,operation_key),UNIQUE(raid_id,player_id,hero_id,game_day))""",
        "CREATE INDEX IF NOT EXISTS mini_raid_daily ON mini_mythic_raid_attacks(player_id,game_day)",
        """CREATE TABLE IF NOT EXISTS mini_mythic_raid_damage (
      raid_id INTEGER NOT NULL REFERENCES mini_mythic_raids(id),player_id INTEGER NOT NULL REFERENCES mini_players(id),
      damage INTEGER NOT NULL DEFAULT 0 CHECK(damage>=0),PRIMARY KEY(raid_id,player_id))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_raid_rewards (
      raid_id INTEGER NOT NULL REFERENCES mini_mythic_raids(id),player_id INTEGER NOT NULL REFERENCES mini_players(id),
      rank INTEGER NOT NULL,damage INTEGER NOT NULL,amount INTEGER NOT NULL,result_json TEXT NOT NULL,
      PRIMARY KEY(raid_id,player_id))""",
        """CREATE TABLE IF NOT EXISTS mini_mythic_raid_audit (
      id INTEGER PRIMARY KEY,raid_id INTEGER NOT NULL REFERENCES mini_mythic_raids(id),actor_id INTEGER NOT NULL,
      operation_key TEXT NOT NULL UNIQUE,action TEXT NOT NULL,payload TEXT NOT NULL,created_at INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS mini_tower_lunar_choices (
      player_id INTEGER NOT NULL REFERENCES mini_players(id),floor INTEGER NOT NULL,arcana_json TEXT NOT NULL,
      PRIMARY KEY(player_id,floor))""",
    ]
    statements.append(
        "CREATE TABLE IF NOT EXISTS mini_combat_arise (mode TEXT NOT NULL,battle_id INTEGER NOT NULL,player_id INTEGER NOT NULL REFERENCES mini_players(id),result_json TEXT NOT NULL,PRIMARY KEY(mode,battle_id,player_id))"
    )
    statements.append(
        "CREATE TABLE IF NOT EXISTS mini_mythic_raid_publications (raid_id INTEGER PRIMARY KEY REFERENCES mini_mythic_raids(id),status TEXT NOT NULL DEFAULT 'pending',message_id INTEGER)"
    )
    for statement in statements:
        conn.execute(statement)
    if "media_type" not in {
        r[1] for r in conn.execute("PRAGMA table_info(mini_mythic_raid_publications)")
    }:
        conn.execute(
            "ALTER TABLE mini_mythic_raid_publications ADD COLUMN media_type TEXT NOT NULL DEFAULT 'text'"
        )


def legacy_nonzero(conn):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='mini_mythic_fragments'"
    ).fetchone()
    return (
        [
            tuple(r)
            for r in conn.execute(
                "SELECT player_id,hero_code,amount FROM mini_mythic_fragments WHERE amount>0"
            )
        ]
        if exists
        else []
    )
