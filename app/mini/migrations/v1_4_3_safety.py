"""Additive publication leases/retries and explicit maintenance; preserve escrow."""
import time

def apply(conn):
    columns={r[1] for r in conn.execute("PRAGMA table_info(mini_event_pvp)")}
    fields={
        "retry_count":"INTEGER NOT NULL DEFAULT 0",
        "next_retry_at":"INTEGER NOT NULL DEFAULT 0",
        "edit_status":"TEXT NOT NULL DEFAULT 'idle'",
        "edit_token":"TEXT NOT NULL DEFAULT ''",
        "edit_revision":"INTEGER NOT NULL DEFAULT 0",
        "edit_attempt_at":"INTEGER NOT NULL DEFAULT 0",
        "send_attempt_at":"INTEGER NOT NULL DEFAULT 0",
        "send_until":"INTEGER NOT NULL DEFAULT 0",
        "send_token":"TEXT NOT NULL DEFAULT ''",
        "last_error_type":"TEXT NOT NULL DEFAULT ''",
        "last_error_at":"INTEGER NOT NULL DEFAULT 0",
        "delivery_uncertain":"INTEGER NOT NULL DEFAULT 0",
        "publication_reviewed_at":"INTEGER NOT NULL DEFAULT 0",
        "publication_review_note":"TEXT NOT NULL DEFAULT ''",
        "economy_version":"INTEGER NOT NULL DEFAULT 0",
        "legacy_shards_reviewed_at":"INTEGER NOT NULL DEFAULT 0",
        "legacy_shards_review_note":"TEXT NOT NULL DEFAULT ''",
    }
    for name,definition in fields.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE mini_event_pvp ADD COLUMN {name} {definition}")
    if "edit_status" not in columns:
        now=int(time.time())
        # An old in-flight edit is an unknown failed attempt. Preserve its lease
        # deadline and add backoff; repeated init must never reset these values.
        conn.execute("""UPDATE mini_event_pvp SET retry_count=1,
            next_retry_at=MAX(editing_until,?),editing_until=0,edit_status='retrying',
            last_error='Legacy edit attempt; retry scheduled',last_error_type='LegacyEdit',
            last_error_at=? WHERE publication='sent' AND
            (editing_until>0 OR (last_error!='' AND published_revision<revision))""",(now+10,now))
        conn.execute("""UPDATE mini_event_pvp SET delivery_uncertain=1,
            last_error='Legacy publication delivery unknown',last_error_type='LegacySend',
            last_error_at=? WHERE publication='uncertain'""",(now,))
        # Previously stored exception text may contain a URL or personal data.
        conn.execute("""UPDATE mini_event_pvp SET last_error='Legacy publication error',
            last_error_type='LegacyError',last_error_at=?
            WHERE last_error!='' AND last_error_type=''""",(now,))
        conn.execute("""UPDATE mini_event_pvp SET edit_status='synced'
            WHERE publication='sent' AND published_revision>=revision AND edit_status='idle'""")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_event_pvp_control(
        id INTEGER PRIMARY KEY CHECK(id=1),
        paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
        blocked_create INTEGER NOT NULL DEFAULT 0,
        blocked_accept INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("INSERT OR IGNORE INTO mini_event_pvp_control(id) VALUES(1)")
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_event_pvp_shard_awards(
        session_id INTEGER PRIMARY KEY REFERENCES mini_event_pvp(id),
        player_id INTEGER NOT NULL REFERENCES mini_players(id),
        amount INTEGER NOT NULL CHECK(amount=2),
        created_at INTEGER NOT NULL)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_pvp_edit_retry
        ON mini_event_pvp(publication,edit_status,next_retry_at)""")
