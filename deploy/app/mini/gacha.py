import secrets
import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.catalog import load_hero_catalog
from app.mini.heroes import sync_hero_catalog


class GachaError(ValueError):
    pass


class GachaInsufficientFunds(GachaError):
    pass


class GachaNoTicket(GachaError):
    pass


class GachaNoHeroes(GachaError):
    pass


def _settings() -> dict:
    return load_hero_catalog()["settings"]


def _choose_hero_code() -> str:
    catalog = load_hero_catalog()
    settings = catalog["settings"]

    by_rarity: dict[str, list[dict]] = {}
    for hero in catalog["heroes"]:
        if not bool(hero.get("active", True)):
            continue
        by_rarity.setdefault(hero["rarity"], []).append(hero)

    choices = []
    total_weight = 0
    for rarity, raw_weight in settings["rarity_weights"].items():
        heroes = by_rarity.get(rarity, [])
        weight = int(raw_weight)
        if not heroes or weight <= 0:
            continue
        choices.append((rarity, heroes, weight))
        total_weight += weight

    if total_weight <= 0:
        raise GachaNoHeroes("В гаче пока нет активных героев.")

    roll = secrets.randbelow(total_weight)
    cursor = 0
    selected_heroes = None

    for _, heroes, weight in choices:
        cursor += weight
        if roll < cursor:
            selected_heroes = heroes
            break

    if not selected_heroes:
        selected_heroes = choices[-1][1]

    return secrets.choice(selected_heroes)["code"]


def _ticket_quantity(
    conn: sqlite3.Connection,
    player_id: int,
    ticket_item_code: str,
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(inv.quantity, 0)
        FROM mini_items i
        LEFT JOIN mini_inventory inv
          ON inv.item_id = i.id AND inv.player_id = ?
        WHERE i.code = ?
        """,
        (int(player_id), ticket_item_code),
    ).fetchone()
    return int(row[0]) if row else 0


def get_gacha_state(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    sync_hero_catalog(db_path)
    settings = _settings()
    ticket_code = str(settings["ticket_item_code"])

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        player = conn.execute(
            "SELECT coins, active_hero_id FROM mini_players WHERE id = ?",
            (int(player_id),),
        ).fetchone()
        if player is None:
            raise GachaError("Mini-игрок не найден.")

        tickets = _ticket_quantity(conn, player_id, ticket_code)
        owned = int(conn.execute(
            "SELECT COUNT(*) FROM mini_player_heroes WHERE player_id = ?",
            (int(player_id),),
        ).fetchone()[0])
        rarity_rows = conn.execute(
            """
            SELECT rarity, COUNT(*)
            FROM mini_heroes
            WHERE active = 1
            GROUP BY rarity
            """
        ).fetchall()

    rarity_counts = {str(rarity): int(count) for rarity, count in rarity_rows}
    rarity_weights = {
        key: int(value)
        for key, value in settings["rarity_weights"].items()
    }
    eligible_total = sum(
        weight
        for rarity, weight in rarity_weights.items()
        if weight > 0 and rarity_counts.get(rarity, 0) > 0
    )
    rarity_chances = {}
    for rarity, weight in rarity_weights.items():
        if eligible_total <= 0 or rarity_counts.get(rarity, 0) <= 0:
            rarity_chances[rarity] = 0.0
        else:
            rarity_chances[rarity] = round(weight * 100 / eligible_total, 1)

    return {
        "pull_price": int(settings["pull_price"]),
        "ticket_item_code": ticket_code,
        "tickets": tickets,
        "coins": int(player["coins"]),
        "owned": owned,
        "total": sum(rarity_counts.values()),
        "rarity_counts": rarity_counts,
        "rarity_weights": rarity_weights,
        "rarity_chances": rarity_chances,
        "active_hero_id": player["active_hero_id"],
    }


def perform_gacha_pull(
    player_id: int,
    payment: str = "coins",
    db_path: str | Path = DB_PATH,
) -> dict:
    """Одна атомарная крутка. payment: coins или ticket."""
    if payment not in {"coins", "ticket"}:
        raise GachaError("Неизвестный способ оплаты призыва.")

    sync_hero_catalog(db_path)
    settings = _settings()
    pull_price = int(settings["pull_price"])
    ticket_code = str(settings["ticket_item_code"])
    duplicate_shards = {
        key: int(value)
        for key, value in settings["duplicate_shards"].items()
    }

    hero_code = _choose_hero_code()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        player = conn.execute(
            """
            SELECT id, world_id, coins, active_hero_id
            FROM mini_players
            WHERE id = ?
            """,
            (int(player_id),),
        ).fetchone()
        if player is None:
            conn.rollback()
            raise GachaError("Mini-игрок не найден.")

        hero = conn.execute(
            "SELECT * FROM mini_heroes WHERE code = ? AND active = 1",
            (hero_code,),
        ).fetchone()
        if hero is None:
            conn.rollback()
            raise GachaNoHeroes("Выбранный герой больше не активен. Повтори крутку.")

        balance = int(player["coins"])
        used_ticket = 0
        cost_coins = 0

        if payment == "ticket":
            item = conn.execute(
                "SELECT id FROM mini_items WHERE code = ?",
                (ticket_code,),
            ).fetchone()
            if item is None:
                conn.rollback()
                raise GachaNoTicket("Билеты призыва ещё не настроены в магазине.")

            item_id = int(item[0])
            ticket_qty = conn.execute(
                """
                SELECT quantity
                FROM mini_inventory
                WHERE player_id = ? AND item_id = ?
                """,
                (int(player_id), item_id),
            ).fetchone()
            if ticket_qty is None or int(ticket_qty[0]) <= 0:
                conn.rollback()
                raise GachaNoTicket("У тебя нет билета призыва.")

            conn.execute(
                """
                UPDATE mini_inventory
                SET quantity = quantity - 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE player_id = ? AND item_id = ?
                """,
                (int(player_id), item_id),
            )
            used_ticket = 1
        else:
            if balance < pull_price:
                conn.rollback()
                raise GachaInsufficientFunds(
                    f"Не хватает {pull_price - balance} монет для призыва."
                )
            cost_coins = pull_price
            balance -= pull_price
            conn.execute(
                "UPDATE mini_players SET coins = ? WHERE id = ?",
                (balance, int(player_id)),
            )

        owned = conn.execute(
            """
            SELECT copies, shards
            FROM mini_player_heroes
            WHERE player_id = ? AND hero_id = ?
            """,
            (int(player_id), int(hero["id"])),
        ).fetchone()

        is_duplicate = owned is not None
        shards_awarded = 0

        if owned is None:
            copies = 1
            shards = 0
            conn.execute(
                """
                INSERT INTO mini_player_heroes (
                    player_id,
                    hero_id,
                    copies,
                    shards
                )
                VALUES (?, ?, 1, 0)
                """,
                (int(player_id), int(hero["id"])),
            )
        else:
            shards_awarded = int(duplicate_shards.get(hero["rarity"], 0))
            copies = int(owned["copies"]) + 1
            shards = int(owned["shards"]) + shards_awarded
            conn.execute(
                """
                UPDATE mini_player_heroes
                SET copies = ?, shards = ?
                WHERE player_id = ? AND hero_id = ?
                """,
                (
                    copies,
                    shards,
                    int(player_id),
                    int(hero["id"]),
                ),
            )

        auto_activated = player["active_hero_id"] is None
        if auto_activated:
            conn.execute(
                "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
                (int(hero["id"]), int(player_id)),
            )

        pull_cursor = conn.execute(
            """
            INSERT INTO mini_gacha_pulls (
                player_id,
                hero_id,
                cost_coins,
                used_ticket,
                is_duplicate,
                shards_awarded
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(player_id),
                int(hero["id"]),
                cost_coins,
                used_ticket,
                1 if is_duplicate else 0,
                shards_awarded,
            ),
        )
        pull_id = int(pull_cursor.lastrowid)

        if cost_coins > 0:
            conn.execute(
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
                VALUES (?, ?, ?, ?, 'gacha', ?, ?)
                """,
                (
                    int(player_id),
                    -cost_coins,
                    balance,
                    f"Призыв героя: {hero['name']}",
                    pull_id,
                    f"gacha:{pull_id}",
                ),
            )

        tickets_after = _ticket_quantity(conn, player_id, ticket_code)
        conn.commit()

    result = dict(hero)
    result.update({
        "pull_id": pull_id,
        "payment": payment,
        "cost_coins": cost_coins,
        "used_ticket": bool(used_ticket),
        "is_duplicate": is_duplicate,
        "shards_awarded": shards_awarded,
        "copies": copies,
        "shards": shards,
        "balance": balance,
        "tickets": tickets_after,
        "pull_price": pull_price,
        "auto_activated": auto_activated,
    })
    return result
