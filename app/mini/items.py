from app.mini.effects.contracts import ItemEffectContext, ItemUseError
from app.mini.effects.registry import EFFECT_GACHA_TICKET, EFFECT_GACHA_LUCK, EFFECT_BOSS_DAMAGE, EFFECT_BOSS_PHANTOM, EFFECT_COIN_POUCH, EFFECT_SHARD_CASKET, get_effect, effect_description, active_effect_title
from app.mini.wallet import change_balance_in_transaction
import json
import secrets
import sqlite3
from collections.abc import Callable
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


AmountPicker = Callable[[int, int], int]


def _default_amount_picker(low: int, high: int) -> int:
    return int(low) + secrets.randbelow(int(high) - int(low) + 1)


def get_player_effects(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT effect_key, charges, updated_at
            FROM mini_player_effects
            WHERE player_id = ? AND charges > 0
            ORDER BY updated_at, effect_key
            """,
            (int(player_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_effect_charges(
    player_id: int,
    effect_key: str,
    db_path: str | Path = DB_PATH,
) -> int:
    with connect_mini_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT charges
            FROM mini_player_effects
            WHERE player_id = ? AND effect_key = ?
            """,
            (int(player_id), str(effect_key)),
        ).fetchone()
    return int(row[0]) if row else 0


def consume_effect_charge(
    conn: sqlite3.Connection,
    player_id: int,
    effect_key: str,
) -> bool:
    """Списывает один заряд внутри уже открытой транзакции вызывающего кода."""
    row = conn.execute(
        """
        SELECT charges
        FROM mini_player_effects
        WHERE player_id = ? AND effect_key = ?
        """,
        (int(player_id), str(effect_key)),
    ).fetchone()
    if row is None or int(row[0]) <= 0:
        return False

    conn.execute(
        """
        UPDATE mini_player_effects
        SET charges = charges - 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE player_id = ? AND effect_key = ?
        """,
        (int(player_id), str(effect_key)),
    )
    return True


def add_effect_charge_in_transaction(
    conn: sqlite3.Connection,
    player_id: int,
    effect_key: str,
    quantity: int = 1,
) -> int:
    quantity = max(1, int(quantity))
    conn.execute(
        """
        INSERT INTO mini_player_effects (
            player_id, effect_key, charges, updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(player_id, effect_key) DO UPDATE SET
            charges = mini_player_effects.charges + excluded.charges,
            updated_at = CURRENT_TIMESTAMP
        """,
        (int(player_id), str(effect_key), quantity),
    )
    row = conn.execute(
        """
        SELECT charges
        FROM mini_player_effects
        WHERE player_id = ? AND effect_key = ?
        """,
        (int(player_id), str(effect_key)),
    ).fetchone()
    return int(row[0])


def get_inventory_item(
    player_id: int,
    item_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                i.id AS item_id,
                i.code,
                i.name,
                i.category,
                i.description,
                i.effect_key,
                i.stackable,
                i.active,
                inv.quantity
            FROM mini_inventory inv
            JOIN mini_items i ON i.id = inv.item_id
            WHERE inv.player_id = ? AND inv.item_id = ? AND inv.quantity > 0
            """,
            (int(player_id), int(item_id)),
        ).fetchone()
    return dict(row) if row else None


def get_certificate_for_use(
    player_id: int,
    purchase_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                p.id AS purchase_id,
                p.status,
                p.created_at,
                o.code,
                o.title,
                o.description,
                o.offer_type
            FROM mini_purchases p
            JOIN mini_shop_offers o ON o.id = p.offer_id
            WHERE p.id = ?
              AND p.player_id = ?
              AND p.status = 'active'
              AND o.offer_type = 'certificate'
            """,
            (int(purchase_id), int(player_id)),
        ).fetchone()
    return dict(row) if row else None


def mark_certificate_requested(
    player_id: int,
    purchase_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT
                p.id AS purchase_id,
                p.status,
                o.code,
                o.title,
                o.description
            FROM mini_purchases p
            JOIN mini_shop_offers o ON o.id = p.offer_id
            WHERE p.id = ?
              AND p.player_id = ?
              AND o.offer_type = 'certificate'
            """,
            (int(purchase_id), int(player_id)),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ItemUseError("Сертификат не найден.")
        if row["status"] != "active":
            conn.rollback()
            raise ItemUseError("Этот сертификат уже использован или отправлен мастеру.")

        conn.execute(
            """
            UPDATE mini_purchases
            SET status = 'requested'
            WHERE id = ? AND player_id = ?
            """,
            (int(purchase_id), int(player_id)),
        )
        conn.commit()

    result = dict(row)
    result["status"] = "requested"
    return result


def _load_previous_use(
    conn: sqlite3.Connection,
    player_id: int,
    operation_key: str,
) -> dict | None:
    if not operation_key:
        return None
    row = conn.execute(
        """
        SELECT result_json
        FROM mini_item_uses
        WHERE player_id = ? AND operation_key = ?
        """,
        (int(player_id), str(operation_key)),
    ).fetchone()
    if row is None:
        return None
    try:
        result = json.loads(row[0] or "{}")
    except json.JSONDecodeError:
        result = {}
    result["repeated"] = True
    return result


def use_inventory_item(
    player_id: int,
    item_id: int,
    *,
    operation_key: str = "",
    amount_picker: AmountPicker | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    """
    Использует обычный предмет из mini_inventory.

    Билет гачи здесь намеренно не списывается: его атомарно поглощает
    perform_gacha_pull(payment='ticket'), чтобы крутка и расход билета были одной транзакцией.
    """
    amount_picker = amount_picker or _default_amount_picker

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        previous = _load_previous_use(conn, player_id, operation_key)
        if previous is not None:
            conn.commit()
            return previous

        row = conn.execute(
            """
            SELECT
                i.id AS item_id,
                i.code,
                i.name,
                i.description,
                i.effect_key,
                inv.quantity,
                p.coins,
                p.shards
            FROM mini_inventory inv
            JOIN mini_items i ON i.id = inv.item_id
            JOIN mini_players p ON p.id = inv.player_id
            WHERE inv.player_id = ?
              AND inv.item_id = ?
              AND inv.quantity > 0
              AND i.active = 1
            """,
            (int(player_id), int(item_id)),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ItemUseError("Этого предмета больше нет в инвентаре.")

        effect_key = str(row["effect_key"] or "").strip()
        if effect_key == EFFECT_GACHA_TICKET:
            conn.rollback()
            raise ItemUseError("Билет призыва используется через крутку героя.")

        definition = get_effect(effect_key)
        if definition is None or definition.handler is None:
            raise ItemUseError("У этого предмета пока нет используемого эффекта.")

        conn.execute(
            """
            UPDATE mini_inventory
            SET quantity = quantity - 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE player_id = ? AND item_id = ? AND quantity > 0
            """,
            (int(player_id), int(item_id)),
        )

        result = {
            "item_id": int(row["item_id"]),
            "code": str(row["code"]),
            "name": str(row["name"]),
            "effect_key": effect_key,
            "amount": 0,
            "coins": int(row["coins"]),
            "shards": int(row["shards"]),
            "charges": 0,
            "repeated": False,
        }

        definition.handler(ItemEffectContext(
            conn=conn, player_id=player_id, item_id=item_id, row=row,
            result=result, operation_key=operation_key, amount_picker=amount_picker,
            add_charge=add_effect_charge_in_transaction,
            change_balance=change_balance_in_transaction,
        ))

        remaining = conn.execute(
            """
            SELECT quantity
            FROM mini_inventory
            WHERE player_id = ? AND item_id = ?
            """,
            (int(player_id), int(item_id)),
        ).fetchone()
        result["remaining"] = int(remaining[0]) if remaining else 0

        conn.execute(
            """
            INSERT INTO mini_item_uses (
                player_id, item_id, effect_key, result_json, operation_key
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(player_id),
                int(item_id),
                effect_key,
                json.dumps(result, ensure_ascii=False),
                str(operation_key or ""),
            ),
        )
        conn.commit()

    return result
