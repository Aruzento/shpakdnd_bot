import sqlite3
from pathlib import Path

from app.config import DB_PATH


def register_mini_world(
    chat_id: int,
    thread_id: int,
    name: str = "D&D Mini",
    db_path: str | Path = DB_PATH,
) -> int:
    """Создаёт или включает Mini-мир для конкретного Telegram-чата/темы."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO mini_worlds (
                chat_id,
                thread_id,
                name,
                enabled
            )
            VALUES (?, ?, ?, 1)
            ON CONFLICT (chat_id, thread_id)
            DO UPDATE SET
                name = excluded.name,
                enabled = 1
            """,
            (chat_id, thread_id, name.strip() or "D&D Mini"),
        )

        row = conn.execute(
            """
            SELECT id
            FROM mini_worlds
            WHERE chat_id = ?
              AND thread_id = ?
            """,
            (chat_id, thread_id),
        ).fetchone()

        conn.commit()

    if row is None:
        raise RuntimeError("Не удалось создать D&D Mini-мир")

    return int(row[0])


def get_mini_world(
    chat_id: int,
    thread_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT
                id,
                chat_id,
                thread_id,
                name,
                currency_name,
                boss_skip_hours,
                launcher_message_id,
                enabled
            FROM mini_worlds
            WHERE chat_id = ?
              AND thread_id = ?
            """,
            (chat_id, thread_id),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def is_mini_world(
    chat_id: int,
    thread_id: int,
    db_path: str | Path = DB_PATH,
) -> bool:
    world = get_mini_world(chat_id, thread_id, db_path)
    return bool(world and world["enabled"])



def get_launcher_message_id(
    world_id: int,
    db_path: str | Path = DB_PATH,
) -> int | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT launcher_message_id
            FROM mini_worlds
            WHERE id = ?
            """,
            (world_id,),
        ).fetchone()

    if row is None or row[0] is None:
        return None

    return int(row[0])


def set_launcher_message_id(
    world_id: int,
    message_id: int,
    db_path: str | Path = DB_PATH,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE mini_worlds
            SET launcher_message_id = ?
            WHERE id = ?
            """,
            (
                message_id,
                world_id,
            ),
        )
        conn.commit()
