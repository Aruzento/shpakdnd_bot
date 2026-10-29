"""Ordinary Boss version 15 fields; legacy timeouts are preserved exactly."""


def apply(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(mini_bosses)")}
    additions = {
        "starts_at": "TEXT",
        "skip_after_hours": "INTEGER NOT NULL DEFAULT 4",
        "rules_version": "INTEGER NOT NULL DEFAULT 0",
        "turn_timeout_seconds": "INTEGER NOT NULL DEFAULT 14400",
        "team_state_json": "TEXT NOT NULL DEFAULT '{}'",
        "original_max_hp": "INTEGER NOT NULL DEFAULT 0",
        "reward_snapshot_version": "INTEGER NOT NULL DEFAULT 0",
        "original_reward_coins": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, kind in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE mini_bosses ADD COLUMN {name} {kind}")
            if name == "turn_timeout_seconds":
                conn.execute(
                    "UPDATE mini_bosses SET turn_timeout_seconds="
                    + (
                        "skip_after_hours*3600"
                        if "skip_after_hours" in columns
                        else "14400"
                    )
                )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS mini_boss_daily_started ON mini_bosses(world_id,starts_at)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_boss_admin_audit (
      id INTEGER PRIMARY KEY,boss_id INTEGER NOT NULL REFERENCES mini_bosses(id),world_id INTEGER NOT NULL,
      actor_user_id INTEGER NOT NULL,operation_key TEXT NOT NULL UNIQUE,action TEXT NOT NULL,
      payload TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT NOT NULL)""")


def timeline(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mini_boss_timeline (sequence INTEGER PRIMARY KEY AUTOINCREMENT,boss_id INTEGER NOT NULL REFERENCES mini_bosses(id) ON DELETE CASCADE,source TEXT NOT NULL,source_id INTEGER NOT NULL,UNIQUE(source,source_id))"
    )
    for source in ("actions", "events"):
        conn.execute(
            f"""CREATE TRIGGER IF NOT EXISTS mini_boss_timeline_{source} AFTER INSERT ON mini_boss_{source}
        WHEN (SELECT rules_version FROM mini_bosses WHERE id=NEW.boss_id)>=15
        BEGIN INSERT INTO mini_boss_timeline(boss_id,source,source_id) VALUES(NEW.boss_id,'{source}',NEW.id); END"""
        )
