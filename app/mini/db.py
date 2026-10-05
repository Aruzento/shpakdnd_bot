from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator

from app.config import DB_PATH


@contextmanager
def connect_mini_db(
    db_path: str | Path = DB_PATH,
) -> Iterator[sqlite3.Connection]:
    """Open a Mini SQLite connection and always close it on exit.

    sqlite3.Connection as a context manager controls transactions but does not
    close the connection. That leaves temporary databases locked on Windows
    (especially Python 3.13) until garbage collection. This wrapper keeps the
    same commit/rollback behavior and guarantees close().
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
