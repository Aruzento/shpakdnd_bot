import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


def get_mini_player(
    world_id: int,
    telegram_user_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT
                id,
                world_id,
                telegram_user_id,
                username,
                character_name,
                coins,
                shards,
                active_hero_id,
                created_at,
                last_seen_at
            FROM mini_players
            WHERE world_id = ?
              AND telegram_user_id = ?
            """,
            (
                world_id,
                telegram_user_id,
            ),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def create_mini_player(
    world_id: int,
    telegram_user_id: int,
    username: str,
    character_name: str,
    db_path: str | Path = DB_PATH,
) -> dict:
    character_name = " ".join(character_name.strip().split())
    username = username.strip().lower()

    if not character_name:
        raise ValueError("Имя персонажа не может быть пустым.")

    with connect_mini_db(db_path) as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM mini_players
            WHERE world_id = ?
              AND telegram_user_id = ?
            """,
            (
                world_id,
                telegram_user_id,
            ),
        ).fetchone()

        if existing is not None:
            raise ValueError(
                "Mini-персонаж для этого игрока уже существует."
            )

        conn.execute(
            """
            INSERT INTO mini_players (
                world_id,
                telegram_user_id,
                username,
                character_name,
                coins,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            """,
            (
                world_id,
                telegram_user_id,
                username,
                character_name,
            ),
        )

        conn.commit()

    player = get_mini_player(
        world_id,
        telegram_user_id,
        db_path,
    )

    if player is None:
        raise RuntimeError("Не удалось создать Mini-персонажа.")

    return player


def touch_mini_player(
    world_id: int,
    telegram_user_id: int,
    username: str,
    db_path: str | Path = DB_PATH,
) -> None:
    with connect_mini_db(db_path) as conn:
        conn.execute(
            """
            UPDATE mini_players
            SET
                username = ?,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE world_id = ?
              AND telegram_user_id = ?
            """,
            (
                username.strip().lower(),
                world_id,
                telegram_user_id,
            ),
        )

        conn.commit()
