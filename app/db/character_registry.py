import sqlite3

from app.config import DB_PATH
from app.context import normalize_username


def create_character(
    chat_id: int,
    thread_id: int,
    username: str,
    name: str,
):
    username = normalize_username(username)
    name = name.strip()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO characters (
                chat_id,
                thread_id,
                username,
                name
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                chat_id,
                thread_id,
                username,
                name,
            ),
        )

        conn.commit()
