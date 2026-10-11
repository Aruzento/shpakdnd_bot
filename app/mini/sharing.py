"""Durable at-most-once claim before sending a public hero card."""
from app.config import DB_PATH
from app.mini.db import connect_mini_db


def claim(player_id,operation_key,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        return bool(conn.execute('INSERT OR IGNORE INTO mini_public_shares(player_id,operation_key) VALUES(?,?)',
                                (player_id,operation_key)).rowcount)
