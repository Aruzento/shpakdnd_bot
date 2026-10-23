"""Shared SQLite transaction boundary; no story operation mutates game currency."""

from contextlib import contextmanager
import sqlite3
from app.config import DB_PATH


@contextmanager
def transaction(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            yield conn
    finally:
        conn.close()
