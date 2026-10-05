import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db


class InsufficientFundsError(ValueError):
    pass


def get_balance(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> int:
    with connect_mini_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT coins
            FROM mini_players
            WHERE id = ?
            """,
            (player_id,),
        ).fetchone()

    if row is None:
        raise ValueError("Mini-игрок не найден.")

    return int(row[0])


def get_wallet_history(
    player_id: int,
    limit: int = 10,
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    limit = max(1, min(int(limit), 50))

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT
                id,
                amount,
                balance_after,
                reason,
                reference_type,
                reference_id,
                operation_key,
                created_at
            FROM mini_wallet_transactions
            WHERE player_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                player_id,
                limit,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def change_balance(
    player_id: int,
    amount: int,
    reason: str,
    reference_type: str = "",
    reference_id: int | None = None,
    operation_key: str = "",
    db_path: str | Path = DB_PATH,
) -> dict:
    """
    Атомарно меняет баланс и записывает операцию в историю.

    amount > 0  -> начисление
    amount < 0  -> списание

    operation_key нужен для защиты от повторной выдачи одной награды.
    Если операция с тем же ключом для игрока уже была выполнена,
    баланс повторно не меняется.
    """
    amount = int(amount)
    reason = reason.strip()
    reference_type = reference_type.strip()
    operation_key = operation_key.strip()

    if amount == 0:
        raise ValueError("Изменение баланса не может быть равно 0.")

    if not reason:
        raise ValueError("Не указана причина изменения баланса.")

    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        if operation_key:
            existing = conn.execute(
                """
                SELECT
                    id,
                    balance_after
                FROM mini_wallet_transactions
                WHERE player_id = ?
                  AND operation_key = ?
                """,
                (
                    player_id,
                    operation_key,
                ),
            ).fetchone()

            if existing is not None:
                conn.commit()
                return {
                    "applied": False,
                    "transaction_id": int(existing[0]),
                    "balance": int(existing[1]),
                }

        row = conn.execute(
            """
            SELECT coins
            FROM mini_players
            WHERE id = ?
            """,
            (player_id,),
        ).fetchone()

        if row is None:
            conn.rollback()
            raise ValueError("Mini-игрок не найден.")

        current_balance = int(row[0])
        new_balance = current_balance + amount

        if new_balance < 0:
            conn.rollback()
            raise InsufficientFundsError(
                "Недостаточно монет Mini."
            )

        conn.execute(
            """
            UPDATE mini_players
            SET coins = ?
            WHERE id = ?
            """,
            (
                new_balance,
                player_id,
            ),
        )

        cursor = conn.execute(
            """
            INSERT INTO mini_wallet_transactions (
                player_id,
                amount,
                balance_after,
                reason,
                reference_type,
                reference_id,
                operation_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_id,
                amount,
                new_balance,
                reason,
                reference_type,
                reference_id,
                operation_key,
            ),
        )

        conn.commit()

    return {
        "applied": True,
        "transaction_id": int(cursor.lastrowid),
        "balance": new_balance,
    }


def add_coins(
    player_id: int,
    amount: int,
    reason: str,
    reference_type: str = "",
    reference_id: int | None = None,
    operation_key: str = "",
    db_path: str | Path = DB_PATH,
) -> dict:
    amount = int(amount)

    if amount <= 0:
        raise ValueError(
            "Для начисления количество монет должно быть больше 0."
        )

    return change_balance(
        player_id=player_id,
        amount=amount,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        operation_key=operation_key,
        db_path=db_path,
    )


def spend_coins(
    player_id: int,
    amount: int,
    reason: str,
    reference_type: str = "",
    reference_id: int | None = None,
    operation_key: str = "",
    db_path: str | Path = DB_PATH,
) -> dict:
    amount = int(amount)

    if amount <= 0:
        raise ValueError(
            "Для списания количество монет должно быть больше 0."
        )

    return change_balance(
        player_id=player_id,
        amount=-amount,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        operation_key=operation_key,
        db_path=db_path,
    )
