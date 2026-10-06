from app.mini.wallet import change_balance_in_transaction
import json
import secrets
import sqlite3
from collections.abc import Callable
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


class ItemUseError(ValueError):
    pass


EFFECT_GACHA_TICKET = "gacha_ticket"
EFFECT_GACHA_LUCK = "gacha_luck"
EFFECT_BOSS_DAMAGE = "boss_damage_boost"
EFFECT_BOSS_PHANTOM = "boss_phantom_participation"
EFFECT_COIN_POUCH = "boss_coin_pouch"
EFFECT_SHARD_CASKET = "boss_shard_casket"


EFFECT_LABELS = {
    EFFECT_GACHA_TICKET: "Одна крутка героя без монет.",
    EFFECT_GACHA_LUCK: (
        "Следующая крутка получает усиленные веса редкостей: "
        "легендарные +10%, редкие +30%."
    ),
    EFFECT_BOSS_DAMAGE: (
        "Следующий бой с боссом: весь твой итоговый урон после пассивки героя +10%."
    ),
    EFFECT_BOSS_PHANTOM: (
        "Следующий бой с боссом: получишь награду даже если не нанесёшь ни одного удара."
    ),
    EFFECT_COIN_POUCH: "Открывается и даёт от 10 до 30 монет.",
    EFFECT_SHARD_CASKET: "Открывается и даёт от 10 до 30 осколков.",
}


ACTIVE_EFFECT_TITLES = {
    EFFECT_GACHA_LUCK: "🍀 Удача — следующая крутка",
    EFFECT_BOSS_DAMAGE: "🧪 Урон +10% — следующий бой",
    EFFECT_BOSS_PHANTOM: "👻 Фантомное участие — следующий бой",
}


AmountPicker = Callable[[int, int], int]


def _default_amount_picker(low: int, high: int) -> int:
    return int(low) + secrets.randbelow(int(high) - int(low) + 1)


def effect_description(effect_key: str) -> str:
    return EFFECT_LABELS.get(str(effect_key or ""), "Эффект предмета не настроен.")


def active_effect_title(effect_key: str) -> str:
    return ACTIVE_EFFECT_TITLES.get(str(effect_key or ""), str(effect_key or ""))


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


def _add_effect_charge(
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

        supported = {
            EFFECT_COIN_POUCH,
            EFFECT_SHARD_CASKET,
            EFFECT_GACHA_LUCK,
            EFFECT_BOSS_DAMAGE,
            EFFECT_BOSS_PHANTOM,
        }
        if effect_key not in supported:
            conn.rollback()
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

        if effect_key == EFFECT_COIN_POUCH:
            amount = int(amount_picker(10, 30))
            if amount < 10 or amount > 30:
                conn.rollback()
                raise ItemUseError("Некорректный результат открытия кошеля.")
            new_balance = change_balance_in_transaction(
                conn, int(player_id), amount, f"Использован предмет: {row['name']}",
                "item", int(item_id),
                f"item-use:{operation_key}:coins" if operation_key else "",
            )["balance"]
            result["amount"] = amount
            result["coins"] = new_balance

        elif effect_key == EFFECT_SHARD_CASKET:
            amount = int(amount_picker(10, 30))
            if amount < 10 or amount > 30:
                conn.rollback()
                raise ItemUseError("Некорректный результат открытия шкатулки.")
            new_shards = int(row["shards"]) + amount
            conn.execute(
                "UPDATE mini_players SET shards = ? WHERE id = ?",
                (new_shards, int(player_id)),
            )
            result["amount"] = amount
            result["shards"] = new_shards

        else:
            charges = _add_effect_charge(conn, player_id, effect_key, 1)
            result["charges"] = charges

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
