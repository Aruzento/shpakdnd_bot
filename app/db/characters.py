import sqlite3

from app.config import DB_PATH
from app.context import normalize_username


def get_character_profile(
    chat_id: int,
    thread_id: int,
    username: str,
) -> tuple[int, str, str] | None:
    username = normalize_username(username)

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT
                level,
                class_name,
                race
            FROM character_profiles
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            """,
            (
                chat_id,
                thread_id,
                username,
            ),
        ).fetchone()

    if row is None:
        return None

    return (
        int(row[0]),
        str(row[1]),
        str(row[2]),
    )


def save_character_profile(
    chat_id: int,
    thread_id: int,
    username: str,
    level: int,
    class_name: str,
    race: str,
):
    username = normalize_username(username)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO character_profiles (
                chat_id,
                thread_id,
                username,
                level,
                class_name,
                race
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                chat_id,
                thread_id,
                username
            )
            DO UPDATE SET
                level = excluded.level,
                class_name = excluded.class_name,
                race = excluded.race
            """,
            (
                chat_id,
                thread_id,
                username,
                level,
                class_name.strip(),
                race.strip(),
            ),
        )

        conn.commit()
