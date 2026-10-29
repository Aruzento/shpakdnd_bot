"""Universal currency, distinct from ordinary shards and legacy fragments."""

import json
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH
from app.mini.db import connect_mini_db


@contextmanager
def transaction(db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def change(conn, player_id, amount, source, key, payload=None):
    if not conn.in_transaction:
        raise RuntimeError("Currency requires a transaction")
    if type(amount) is not int or not key or not source:
        raise ValueError("Invalid Mythic ledger operation")
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    old = conn.execute(
        "SELECT * FROM mini_mythic_ledger WHERE player_id=? AND operation_key=?",
        (player_id, key),
    ).fetchone()
    if old:
        if (old["amount"], old["source"], old["payload"]) != (amount, source, encoded):
            raise ValueError("Operation key already used")
        return old["balance_after"]
    row = conn.execute(
        "SELECT mythic_shards FROM mini_players WHERE id=?", (player_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Игрок не найден.")
    balance = row[0] + amount
    if balance < 0:
        raise ValueError("Недостаточно мифических осколков.")
    if balance > 2**63 - 1:
        raise ValueError("Mythic balance exceeds SQLite integer")
    conn.execute(
        "UPDATE mini_players SET mythic_shards=? WHERE id=?", (balance, player_id)
    )
    conn.execute(
        "INSERT INTO mini_mythic_ledger(player_id,operation_key,amount,balance_after,source,payload) VALUES(?,?,?,?,?,?)",
        (player_id, key, amount, balance, source, encoded),
    )
    return balance


def previous(conn, pid, key, payload):
    if not isinstance(key, str) or not key:
        raise ValueError("Operation key required")
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    row = conn.execute(
        "SELECT * FROM mini_mythic_operations WHERE player_id=? AND operation_key=?",
        (pid, key),
    ).fetchone()
    if row:
        if row["payload"] != encoded:
            raise ValueError("Operation key already used")
        return json.loads(row["result_json"])


def remember(conn, pid, key, payload, result):
    conn.execute(
        "INSERT INTO mini_mythic_operations VALUES(?,?,?,?)",
        (
            pid,
            key,
            json.dumps(payload, sort_keys=True, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
        ),
    )
    return result
