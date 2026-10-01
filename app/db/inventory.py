import sqlite3

from app.config import DB_PATH
from app.context import normalize_username


def add_inventory_item(
    chat_id: int,
    thread_id: int,
    username: str,
    name: str,
    quantity: int,
    description: str = "",
) -> tuple[str, int, str]:
    username = normalize_username(username)
    name = name.strip()
    description = description.strip()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                id,
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (chat_id, thread_id, username),
        )

        for (
            item_id,
            stored_name,
            stored_quantity,
            stored_description,
        ) in cursor.fetchall():
            if stored_name.casefold() == name.casefold():
                new_quantity = stored_quantity + quantity
                new_description = (
                    description
                    if description
                    else stored_description
                )

                conn.execute(
                    """
                    UPDATE inventory
                    SET
                        quantity = ?,
                        description = ?
                    WHERE id = ?
                    """,
                    (
                        new_quantity,
                        new_description,
                        item_id,
                    ),
                )
                conn.commit()

                return (
                    stored_name,
                    new_quantity,
                    new_description,
                )

        conn.execute(
            """
            INSERT INTO inventory (
                chat_id,
                thread_id,
                username,
                name,
                quantity,
                description
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                thread_id,
                username,
                name,
                quantity,
                description,
            ),
        )
        conn.commit()

        return name, quantity, description


def get_inventory(
    chat_id: int,
    thread_id: int,
    username: str,
) -> list[tuple[str, int, str]]:
    username = normalize_username(username)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (chat_id, thread_id, username),
        )

        return cursor.fetchall()


def get_all_inventory(
    chat_id: int,
    thread_id: int,
) -> dict[str, list[tuple[str, int, str]]]:
    inventory = {}

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                username,
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
            ORDER BY id
            """,
            (chat_id, thread_id),
        )

        for username, name, quantity, description in cursor.fetchall():
            username = normalize_username(username)
            inventory.setdefault(username, []).append(
                (name, quantity, description)
            )

    return inventory


def delete_inventory_item(
    chat_id: int,
    thread_id: int,
    username: str,
    name: str,
    quantity: int = 1,
) -> tuple[str, int, int, str] | None:
    username = normalize_username(username)
    wanted_name = name.strip().casefold()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                id,
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (chat_id, thread_id, username),
        )

        for item_id, stored_name, stored_quantity, description in cursor.fetchall():
            if stored_name.casefold() != wanted_name:
                continue

            removed_quantity = min(quantity, stored_quantity)
            remaining_quantity = stored_quantity - removed_quantity

            if remaining_quantity <= 0:
                conn.execute(
                    "DELETE FROM inventory WHERE id = ?",
                    (item_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = ?
                    WHERE id = ?
                    """,
                    (remaining_quantity, item_id),
                )

            conn.commit()

            return (
                stored_name,
                removed_quantity,
                remaining_quantity,
                description,
            )

    return None


def clean_inventory(
    chat_id: int,
    thread_id: int,
    username: str,
):
    username = normalize_username(username)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            """,
            (chat_id, thread_id, username),
        )
        conn.commit()


def clean_all_inventory(
    chat_id: int,
    thread_id: int,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
            """,
            (chat_id, thread_id),
        )
        conn.commit()
