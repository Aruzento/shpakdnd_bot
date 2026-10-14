from app.mini.editorial import text as editorial_text
import sqlite3
import unicodedata
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


def validate_character_name(value):
    if not isinstance(value,str):
        raise ValueError(editorial_text('errors.players.validate_character_name.01'))
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError(editorial_text('errors.players.validate_character_name.02'))
    name = " ".join(value.strip().split())
    if not name or len(name)>64:
        raise ValueError(editorial_text('errors.players.validate_character_name.03'))
    if name.startswith('/') or any(c in name for c in '<>'):
        raise ValueError(editorial_text('errors.players.validate_character_name.04'))
    return name


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
    character_name = validate_character_name(character_name)
    username = username.strip().lower()

    if not character_name:
        raise ValueError(editorial_text('errors.players.create_mini_player.01'))

    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
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
                editorial_text('errors.players.create_mini_player.03')
            )

        cursor = conn.execute(
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

        from app.mini.onboarding import grant_starter_in_transaction
        granted = grant_starter_in_transaction(conn, int(cursor.lastrowid), world_id,
            telegram_user_id, character_name)
        conn.commit()

    player = get_mini_player(
        world_id,
        telegram_user_id,
        db_path,
    )

    if player is None:
        raise RuntimeError(editorial_text('errors.players.create_mini_player.02'))

    player["onboarding_granted"] = granted
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
