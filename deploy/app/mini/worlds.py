import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.topics import TOPIC_SETTINGS


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
            (
                int(chat_id),
                int(thread_id),
                name.strip() or "D&D Mini",
            ),
        )

        row = conn.execute(
            """
            SELECT id
            FROM mini_worlds
            WHERE chat_id = ?
              AND thread_id = ?
            """,
            (
                int(chat_id),
                int(thread_id),
            ),
        ).fetchone()

        conn.commit()

    if row is None:
        raise RuntimeError("Не удалось создать D&D Mini-мир")

    return int(row[0])


def sync_configured_mini_worlds(
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    """
    Синхронизирует Mini-миры из app/topics.py.

    Теперь не нужно отдельно запускать app.mini.setup: достаточно поставить
    `mini: True` у нужной темы и перезапустить бота.
    """
    synced = []

    for chat_id, topics in TOPIC_SETTINGS.items():
        for thread_id, settings in topics.items():
            if not settings.get("mini"):
                continue

            name = str(settings.get("mini_name") or "D&D Mini")
            world_id = register_mini_world(
                chat_id,
                thread_id,
                name,
                db_path,
            )

            synced.append(
                {
                    "id": world_id,
                    "chat_id": int(chat_id),
                    "thread_id": int(thread_id),
                    "name": name,
                }
            )

    return synced


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
            (
                int(chat_id),
                int(thread_id),
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def get_mini_world_by_id(
    world_id: int,
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
            WHERE id = ?
            """,
            (int(world_id),),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def ensure_configured_mini_world(
    chat_id: int,
    thread_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    """Возвращает Mini-мир и при необходимости создаёт его из topics.py."""
    world = get_mini_world(chat_id, thread_id, db_path)

    if world is not None:
        return world

    settings = TOPIC_SETTINGS.get(chat_id, {}).get(thread_id)

    if not settings or not settings.get("mini"):
        return None

    world_id = register_mini_world(
        chat_id,
        thread_id,
        str(settings.get("mini_name") or "D&D Mini"),
        db_path,
    )

    return get_mini_world_by_id(world_id, db_path)


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
            (int(world_id),),
        ).fetchone()

    if row is None or row[0] is None:
        return None

    return int(row[0])


def set_launcher_message_id(
    world_id: int,
    message_id: int | None,
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
                int(world_id),
            ),
        )
        conn.commit()
