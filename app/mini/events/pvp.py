"""Durable RPS escrow. BEGIN IMMEDIATE serializes every financial transition."""
import sqlite3
from collections import Counter
from contextlib import contextmanager
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.editorial import text
from app.mini.events.service import RPS_CHOICES, rps_outcome
from app.mini.shards import change_shards_in_transaction
from app.mini.wallet import change_balance_in_transaction
from app.mini.village.service import timestamp

STAKE = 5
WIN_COINS = 8
LOSS_SHARDS = 2
ACCEPT_SECONDS = 120
CHOICE_SECONDS = 120
ACTIVE = ("open", "choosing")

class PvpError(ValueError):
    pass

class PvpLedgerError(RuntimeError):
    pass

def _connection(db_path):
    return connect_mini_db(db_path)

def _begin(conn):
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")

@contextmanager
def _entry_connection(db_path,player_id,world_id,action):
    from app.mini.features import require_enabled
    require_enabled("pvp")
    blocked=False
    with _connection(db_path) as conn:
        _begin(conn)
        _player(conn,player_id,world_id)
        control=conn.execute("SELECT paused FROM mini_event_pvp_control WHERE id=1").fetchone()
        if control is None:raise RuntimeError("PvP maintenance control is missing")
        if control["paused"]:
            column={"create":"blocked_create","accept":"blocked_accept"}[action]
            conn.execute(f"UPDATE mini_event_pvp_control SET {column}={column}+1 WHERE id=1")
            blocked=True
        else:
            yield conn
    # Commit the blocked-request counter, without running any caller operation.
    if blocked:raise PvpError(text("pvp.error.paused"))

def _player(conn, player_id, world_id):
    row = conn.execute("SELECT * FROM mini_players WHERE id=? AND world_id=?",
                       (player_id, world_id)).fetchone()
    if row is None:
        raise PvpError(text("pvp.error.player"))
    return row

def _row(conn, session_id, world_id):
    row = conn.execute("SELECT * FROM mini_event_pvp WHERE id=? AND world_id=?",
                       (session_id, world_id)).fetchone()
    if row is None:
        raise PvpError(text("pvp.error.session"))
    return row

def _snapshot(conn, row):
    result = dict(row)
    result["members"] = [dict(r) for r in conn.execute(
        """SELECT m.*,p.character_name,p.telegram_user_id FROM mini_event_pvp_members m
           JOIN mini_players p ON p.id=m.player_id WHERE session_id=? ORDER BY p.id""",
        (row["id"],))]
    return result

def assert_no_active_pvp(conn, player_id):
    # Historical schema fixtures and a pre-migration installation have no PvP.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mini_event_pvp_members'").fetchone():
        return
    if conn.execute("SELECT 1 FROM mini_event_pvp_members WHERE player_id=? AND active=1",
                    (player_id,)).fetchone():
        raise PvpError(text("pvp.error.busy"))

def _available(conn, player_id):
    assert_no_active_pvp(conn, player_id)
    if conn.execute("SELECT 1 FROM mini_event_sessions WHERE player_id=? AND status='active'",
                    (player_id,)).fetchone():
        raise PvpError(text("pvp.error.solo"))
    # Hero Duels have an independent lock and no Events session. They can coexist:
    # PvP never selects/reserves a hero; both spend through the same SQLite wallet.

def _money(conn, sid, pid, amount, transition):
    return change_balance_in_transaction(conn, pid, amount, "Mini Events PvP",
        "mini_event_pvp", sid, f"pvp:{sid}:{transition}:{pid}")

def _close(conn, row, status, *, outcome=None, winner=None):
    sid = row["id"]
    if row["status"] not in ACTIVE:
        return
    members = conn.execute("SELECT * FROM mini_event_pvp_members WHERE session_id=?", (sid,)).fetchall()
    expected={row["creator_id"]}
    if row["acceptor_id"] is not None:expected.add(row["acceptor_id"])
    if ({m["player_id"] for m in members}!=expected or len(members)!=len(expected)
            or any(m["active"]!=1 for m in members)):
        raise PvpLedgerError("PvP membership/stake mismatch; operator review required")
    # Active escrow must contain exactly its stakes. A partial historical refund,
    # stray payout or shard receipt is ambiguous; never silently complete/repair it.
    entries=conn.execute("""SELECT player_id,amount,reference_type,reference_id,operation_key
        FROM mini_wallet_transactions WHERE (reference_type='mini_event_pvp' AND reference_id=?)
        OR operation_key LIKE ?""",(sid,f"pvp:{sid}:%")).fetchall()
    wanted=[(pid,-STAKE,"mini_event_pvp",sid,f"pvp:{sid}:stake:{pid}") for pid in expected]
    if (Counter(tuple(r) for r in entries)!=Counter(wanted)
            or conn.execute("SELECT 1 FROM mini_event_pvp_shard_awards WHERE session_id=?",(sid,)).fetchone()):
        raise PvpLedgerError("PvP stake ledger mismatch; operator review required")
    if winner is None:
        for member in members:
            _money(conn, sid, member["player_id"], STAKE, "refund")
    else:
        _money(conn, sid, winner, WIN_COINS, "winner")
        loser = next(m["player_id"] for m in members if m["player_id"] != winner)
        # Member uniqueness + active state under this transaction are the shard
        # idempotency guard; a terminal session never enters this branch again.
        change_shards_in_transaction(conn, loser, LOSS_SHARDS)
        conn.execute("""INSERT INTO mini_event_pvp_shard_awards
            (session_id,player_id,amount,created_at) VALUES(?,?,?,?)""",
            (sid,loser,LOSS_SHARDS,timestamp()))
    conn.execute("""UPDATE mini_event_pvp SET status=?,outcome=?,winner_id=?,
                    revision=revision+1,economy_version=1,
                    publication=CASE WHEN publication='pending' THEN 'failed' ELSE publication END
                    WHERE id=? AND status IN ('open','choosing')""",
                 (status, outcome, winner, sid))
    conn.execute("UPDATE mini_event_pvp_members SET active=0 WHERE session_id=?", (sid,))

def expire_in_transaction(conn, now):
    rows = conn.execute("""SELECT * FROM mini_event_pvp WHERE
        (status='open' AND accept_until<=?) OR (status='choosing' AND choose_until<=?)""",
        (now, now)).fetchall()
    for row in rows:
        _close(conn, row, "expired", outcome="timeout")
    return len(rows)

def expire(db_path=DB_PATH, *, now=None):
    with _connection(db_path) as conn:
        _begin(conn)
        return expire_in_transaction(conn, timestamp(now))

def create(player_id, world_id, operation_key, db_path=DB_PATH, *, now=None):
    if not isinstance(operation_key, str) or not operation_key.strip() or len(operation_key)>128:
        raise PvpError(text("pvp.error.key"))
    now = timestamp(now)
    with _entry_connection(db_path,player_id,world_id,"create") as conn:
        expire_in_transaction(conn, now)
        old = conn.execute("SELECT * FROM mini_event_pvp WHERE creator_id=? AND operation_key=?",
                           (player_id, operation_key)).fetchone()
        if old:
            if old["world_id"] != world_id:
                raise PvpError(text("pvp.error.session"))
            return _snapshot(conn, old)
        _available(conn, player_id)
        cursor = conn.execute("""INSERT INTO mini_event_pvp
            (world_id,creator_id,operation_key,created_at,accept_until,economy_version) VALUES(?,?,?,?,?,1)""",
            (world_id, player_id, operation_key, now, now+ACCEPT_SECONDS))
        sid = cursor.lastrowid
        conn.execute("INSERT INTO mini_event_pvp_members(session_id,player_id) VALUES(?,?)", (sid,player_id))
        _money(conn,sid,player_id,-STAKE,"stake")
        return _snapshot(conn,_row(conn,sid,world_id))

def accept(player_id, world_id, session_id, db_path=DB_PATH, *, now=None):
    now=timestamp(now)
    with _entry_connection(db_path,player_id,world_id,"accept") as conn:
        expire_in_transaction(conn,now)
        row=_row(conn,session_id,world_id)
        if player_id==row["creator_id"]:
            raise PvpError(text("pvp.error.self"))
        if row["acceptor_id"]==player_id:
            return _snapshot(conn,row)
        if row["status"]!="open":
            raise PvpError(text("pvp.error.closed"))
        if row["publication"]!="sent" or not row["message_id"]:
            raise PvpError(text("pvp.error.publication_pending"))
        # Acceptance is allowed only after the public send is acknowledged.
        _available(conn,player_id)
        _money(conn,session_id,player_id,-STAKE,"stake")
        conn.execute("INSERT INTO mini_event_pvp_members(session_id,player_id) VALUES(?,?)",(session_id,player_id))
        changed=conn.execute("""UPDATE mini_event_pvp SET status='choosing',acceptor_id=?,
            choose_until=?,revision=revision+1 WHERE id=? AND status='open' AND acceptor_id IS NULL""",
            (player_id,now+CHOICE_SECONDS,session_id)).rowcount
        if changed!=1:
            raise PvpError(text("pvp.error.closed"))
        return _snapshot(conn,_row(conn,session_id,world_id))

def choose(player_id, world_id, session_id, choice, db_path=DB_PATH, *, now=None):
    if choice not in RPS_CHOICES:
        raise PvpError(text("pvp.error.choice"))
    with _connection(db_path) as conn:
        _begin(conn)
        _player(conn,player_id,world_id)
        expire_in_transaction(conn,timestamp(now))
        row=_row(conn,session_id,world_id)
        member=conn.execute("SELECT * FROM mini_event_pvp_members WHERE session_id=? AND player_id=?",
                            (session_id,player_id)).fetchone()
        if not member:
            raise PvpError(text("pvp.error.member"))
        if row["status"]=="choosing" and member["choice"] is None:
            conn.execute("""UPDATE mini_event_pvp_members SET choice=?
                WHERE session_id=? AND player_id=? AND choice IS NULL""",(choice,session_id,player_id))
            choices={r["player_id"]:r["choice"] for r in conn.execute(
                "SELECT player_id,choice FROM mini_event_pvp_members WHERE session_id=?",(session_id,))}
            if all(choices.values()):
                result=rps_outcome(choices[row["creator_id"]],choices[row["acceptor_id"]])
                winner=None if result=="draw" else row["creator_id"] if result=="win" else row["acceptor_id"]
                _close(conn,row,"finished",outcome="draw" if winner is None else "win",winner=winner)
        return _snapshot(conn,_row(conn,session_id,world_id))

def cancel(player_id, world_id, session_id, db_path=DB_PATH, *, now=None):
    with _connection(db_path) as conn:
        _begin(conn)
        _player(conn,player_id,world_id)
        expire_in_transaction(conn,timestamp(now))
        row=_row(conn,session_id,world_id)
        if row["creator_id"]!=player_id:
            raise PvpError(text("pvp.error.member"))
        if row["status"]=="choosing":
            raise PvpError(text("pvp.error.cancel"))
        if row["status"]=="open":
            _close(conn,row,"cancelled",outcome="cancel")
        return _snapshot(conn,_row(conn,session_id,world_id))

def get_session(world_id, session_id, db_path=DB_PATH, *, now=None):
    with _connection(db_path) as conn:
        _begin(conn)
        expire_in_transaction(conn,timestamp(now))
        return _snapshot(conn,_row(conn,session_id,world_id))

def lobby(player_id,world_id,db_path=DB_PATH,*,now=None):
    with _connection(db_path) as conn:
        _begin(conn)
        _player(conn,player_id,world_id)
        expire_in_transaction(conn,timestamp(now))
        active=conn.execute("""SELECT s.* FROM mini_event_pvp s JOIN mini_event_pvp_members m
            ON m.session_id=s.id WHERE m.player_id=? AND m.active=1""",(player_id,)).fetchone()
        rows=conn.execute("""SELECT * FROM mini_event_pvp WHERE world_id=? AND status='open'
            ORDER BY id DESC LIMIT 10""",(world_id,)).fetchall()
        return (_snapshot(conn,active) if active else None,[_snapshot(conn,r) for r in rows])


def maintenance(paused,db_path=DB_PATH,*,now=None):
    """Explicit operator action. Never called by migration/startup/diagnostics."""
    if type(paused) is not bool:raise ValueError("Expected boolean maintenance mode")
    now=timestamp(now)
    with _connection(db_path) as conn:
        _begin(conn)
        conn.execute("UPDATE mini_event_pvp_control SET paused=?,updated_at=? WHERE id=1",(int(paused),now))
        closed=0;unresolved=[]
        if paused:
            for row in conn.execute("SELECT * FROM mini_event_pvp WHERE status='open'").fetchall():
                try:_close(conn,row,"cancelled",outcome="maintenance")
                except PvpLedgerError:unresolved.append(row["id"])
                else:closed+=1
        return {"paused":paused,"closed_open":closed,"unresolved":unresolved}

def latest_session(player_id,world_id,db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.row_factory=sqlite3.Row
        _player(conn,player_id,world_id)
        row=conn.execute("""SELECT s.id FROM mini_event_pvp s
            JOIN mini_event_pvp_members m ON m.session_id=s.id
            WHERE m.player_id=? AND s.world_id=? ORDER BY s.id DESC LIMIT 1""",
            (player_id,world_id)).fetchone()
        return row["id"] if row else None
