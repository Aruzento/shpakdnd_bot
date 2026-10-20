from contextlib import closing
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
    if type(quantity) is not int or not 0 < quantity <= 2**63-1:
        raise ValueError('Количество должно быть положительным целым.')
    username = normalize_username(username)
    name = name.strip()
    description = description.strip()

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        from app.db.dnd_items import register_in_transaction
        from app.db import dnd_items
        existing=conn.execute('SELECT id,category FROM dnd_items WHERE chat_id=? AND thread_id=? AND name_key=?',
                              (chat_id,thread_id,name.casefold())).fetchone()
        definition_id=existing[0] if existing else None
        normalized_coin=False
        if definition_id is not None and existing[1]=='currency':
            from app.dnd_shop.coins import configured_profile
            coin_profile=configured_profile(conn,(chat_id,thread_id),
                                dnd_items.TOPIC_SETTINGS.get(chat_id,{}).get(thread_id),validate_mode=False)
            mapping=coin_profile['bundles'].get(str(definition_id)) if coin_profile else None
            if mapping:
                normalized_coin=True
                definition_id,factor=mapping
                quantity*=factor
                if quantity>2**63-1:
                    raise ValueError('Превышен предел количества монет SQLite INTEGER.')
                name=conn.execute('SELECT name FROM dnd_items WHERE id=?',(definition_id,)).fetchone()[0]
        if definition_id is None:
            definition_id=register_in_transaction(conn,chat_id,thread_id,name,description)
        cursor = conn.execute(
            """
            SELECT
                id,
                name,
                quantity,
                description,
                item_id
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (chat_id, thread_id, username),
        )

        stored_rows=cursor.fetchall()
        if normalized_coin and sum(r[1].strip().casefold()==name.casefold() for r in stored_rows)>1:
            raise ValueError('Неоднозначные записи основных монет; нужна миграция.')
        for (
            item_id,
            stored_name,
            stored_quantity,
            stored_description,
            stored_definition,
        ) in stored_rows:
            if stored_name.casefold() == name.casefold():
                if stored_definition is not None and stored_definition!=definition_id:
                    raise ValueError('Название инвентаря конфликтует с item_id.')
                if type(stored_quantity) is not int or stored_quantity<=0:
                    raise ValueError('Некорректное старое количество предмета.')
                new_quantity = stored_quantity + quantity
                if new_quantity>2**63-1:
                    raise ValueError('Превышен предел количества SQLite INTEGER.')
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
                        description = ?, item_id = ?
                    WHERE id = ?
                    """,
                    (
                        new_quantity,
                        new_description,
                        definition_id,
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
                description,
                item_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                thread_id,
                username,
                name,
                quantity,
                description,
                definition_id,
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

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
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

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
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
    if type(quantity) is not int or quantity <= 0:
        raise ValueError('Количество должно быть положительным целым.')
    username = normalize_username(username)
    wanted_name = name.strip().casefold()

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
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

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
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
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            DELETE FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
            """,
            (chat_id, thread_id),
        )
        conn.commit()
