import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.wallet import add_coins


def _normalize_username(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value:
        return ""
    return value if value.startswith("@") else "@" + value


def get_mini_player_by_username(
    world_id: int,
    username: str,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    username = _normalize_username(username)

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
                active_hero_id,
                created_at,
                last_seen_at
            FROM mini_players
            WHERE world_id = ?
              AND LOWER(username) = LOWER(?)
            LIMIT 1
            """,
            (
                int(world_id),
                username,
            ),
        ).fetchone()

    return dict(row) if row else None


def grant_mini_coins(
    player_id: int,
    amount: int,
    admin_username: str,
    db_path: str | Path = DB_PATH,
) -> dict:
    amount = int(amount)

    if amount <= 0:
        raise ValueError("Количество монет должно быть больше 0.")

    admin_username = _normalize_username(admin_username)

    return add_coins(
        player_id=player_id,
        amount=amount,
        reason=f"Админ-выдача от {admin_username}",
        reference_type="admin_grant",
        db_path=db_path,
    )


def grant_mini_item(
    player_id: int,
    item_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    item_id = int(item_id)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        player = conn.execute(
            """
            SELECT id, world_id
            FROM mini_players
            WHERE id = ?
            """,
            (int(player_id),),
        ).fetchone()

        if player is None:
            conn.rollback()
            raise ValueError("Mini-игрок не найден.")

        item = conn.execute(
            """
            SELECT
                id,
                code,
                name,
                category,
                description,
                effect_key,
                stackable,
                active
            FROM mini_items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()

        if item is None:
            conn.rollback()
            raise ValueError(f"Предмет с ID {item_id} не найден.")

        existing = conn.execute(
            """
            SELECT quantity
            FROM mini_inventory
            WHERE player_id = ?
              AND item_id = ?
            """,
            (
                int(player_id),
                item_id,
            ),
        ).fetchone()

        if existing is not None and not bool(item["stackable"]):
            conn.rollback()
            raise ValueError(
                f"Предмет «{item['name']}» не складывается и уже есть у игрока."
            )

        conn.execute(
            """
            INSERT INTO mini_inventory (
                player_id,
                item_id,
                quantity,
                updated_at
            )
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = mini_inventory.quantity + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(player_id),
                item_id,
            ),
        )

        quantity = conn.execute(
            """
            SELECT quantity
            FROM mini_inventory
            WHERE player_id = ?
              AND item_id = ?
            """,
            (
                int(player_id),
                item_id,
            ),
        ).fetchone()[0]

        conn.commit()

    result = dict(item)
    result["quantity"] = int(quantity)
    return result


def list_mini_items(
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id,
                code,
                name,
                category,
                description,
                effect_key,
                stackable,
                active
            FROM mini_items
            ORDER BY id
            """
        ).fetchall()

    return [dict(row) for row in rows]
