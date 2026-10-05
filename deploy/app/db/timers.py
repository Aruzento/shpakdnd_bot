import sqlite3
from datetime import datetime

from app.config import DB_PATH


def save_timer(
    chat_id: int,
    message_id: int,
    title: str,
    end_time: datetime,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO timers (
                chat_id,
                message_id,
                title,
                end_time
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                chat_id,
                message_id,
                title,
                end_time.isoformat(),
            ),
        )
        conn.commit()


def delete_timer(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM timers WHERE chat_id = ?",
            (chat_id,),
        )
        conn.commit()


def get_timer(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                chat_id,
                message_id,
                title,
                end_time
            FROM timers
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        return cursor.fetchone()


def get_all_timers():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                chat_id,
                message_id,
                title,
                end_time
            FROM timers
            """
        )
        return cursor.fetchall()
