"""Additive V1.3 schema. Caller owns DDL and data transaction."""
def apply(conn):
    statements = [
        """CREATE TABLE IF NOT EXISTS mini_equipment (
            code TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            slot TEXT NOT NULL CHECK(slot IN ('helmet','ring','cloak')),
            attack_bonus INTEGER NOT NULL CHECK(attack_bonus BETWEEN 1 AND 100),
            UNIQUE(slot,attack_bonus))""",
        """CREATE TABLE IF NOT EXISTS mini_equipment_owned (
            player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
            code TEXT NOT NULL REFERENCES mini_equipment(code),
            quantity INTEGER NOT NULL CHECK(quantity > 0), PRIMARY KEY(player_id,code))""",
        """CREATE TABLE IF NOT EXISTS mini_equipment_slots (
            player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
            slot TEXT NOT NULL CHECK(slot IN ('helmet','ring','cloak')),
            code TEXT NOT NULL REFERENCES mini_equipment(code), PRIMARY KEY(player_id,slot),
            FOREIGN KEY(player_id,code) REFERENCES mini_equipment_owned(player_id,code)
                ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS mini_tower_progress (
            player_id INTEGER PRIMARY KEY REFERENCES mini_players(id) ON DELETE CASCADE,
            highest_cleared INTEGER NOT NULL DEFAULT 0 CHECK(highest_cleared BETWEEN 0 AND 200))""",
        """CREATE TABLE IF NOT EXISTS mini_tower_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
            floor INTEGER NOT NULL CHECK(floor BETWEEN 1 AND 200),
            hero_id INTEGER NOT NULL REFERENCES mini_heroes(id),
            hero_json TEXT NOT NULL, enemy_json TEXT NOT NULL,
            equipment_bonus INTEGER NOT NULL CHECK(equipment_bonus BETWEEN 0 AND 300),
            current_hp INTEGER NOT NULL CHECK(current_hp >= 0),
            shields INTEGER NOT NULL DEFAULT 3 CHECK(shields BETWEEN 0 AND 3),
            turn INTEGER NOT NULL DEFAULT 0 CHECK(turn >= 0),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','won','lost')),
            runtime_json TEXT NOT NULL DEFAULT '{}', events_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_tower_active
            ON mini_tower_attempts(player_id) WHERE status='active'""",
        """CREATE TABLE IF NOT EXISTS mini_tower_rewards (
            player_id INTEGER NOT NULL REFERENCES mini_players(id) ON DELETE CASCADE,
            floor INTEGER NOT NULL CHECK(floor BETWEEN 1 AND 200),
            attempt_id INTEGER NOT NULL UNIQUE REFERENCES mini_tower_attempts(id),
            shards INTEGER NOT NULL CHECK(shards >= 0),
            equipment_code TEXT REFERENCES mini_equipment(code),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(player_id,floor))""",
        """CREATE TABLE IF NOT EXISTS mini_gacha_guarantees (
            player_id INTEGER PRIMARY KEY REFERENCES mini_players(id) ON DELETE CASCADE,
            forced_legendary INTEGER NOT NULL DEFAULT 1 CHECK(forced_legendary IN (0,1)))""",
        """CREATE TABLE IF NOT EXISTS mini_superadmin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER NOT NULL,
            action TEXT NOT NULL, world_id INTEGER NOT NULL, target_user_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL, resource TEXT NOT NULL, entity_code TEXT NOT NULL DEFAULT '',
            amount INTEGER, operation_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    ]
    for sql in statements:
        conn.execute(sql)
