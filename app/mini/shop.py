import json
import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.catalog import load_shop_catalog


class ShopError(ValueError):
    pass


class ShopInsufficientFunds(ShopError):
    pass


class ShopLimitReached(ShopError):
    pass


class ShopOutOfStock(ShopError):
    pass


def _category_map(catalog: dict) -> dict[str, dict]:
    return {
        category["code"]: category
        for category in catalog["categories"]
    }


def sync_shop_catalog(
    world_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    catalog = load_shop_catalog()
    categories = _category_map(catalog)

    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        catalog_codes = []
        for product in catalog["products"]:
            product_code = product["code"]
            catalog_codes.append(product_code)
            delivery = product["delivery"]
            item_id = None

            if delivery == "inventory":
                item = product["item"]
                conn.execute(
                    """
                    INSERT INTO mini_items (
                        code,
                        name,
                        category,
                        description,
                        effect_key,
                        stackable,
                        active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code) DO UPDATE SET
                        name = excluded.name,
                        category = excluded.category,
                        description = excluded.description,
                        effect_key = excluded.effect_key,
                        stackable = excluded.stackable,
                        active = excluded.active
                    """,
                    (
                        item["code"],
                        item["name"],
                        product["category"],
                        item.get("description", product.get("description", "")),
                        item.get("effect_key", ""),
                        1 if item.get("stackable", True) else 0,
                        1 if product.get("active", True) else 0,
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM mini_items WHERE code = ?",
                    (item["code"],),
                ).fetchone()
                item_id = int(row[0])

            metadata = {
                "category": product["category"],
                "category_title": categories[product["category"]]["title"],
                "delivery": delivery,
                "item_code": (
                    product.get("item", {}).get("code", "")
                    if delivery == "inventory"
                    else ""
                ),
            }

            conn.execute(
                """
                INSERT INTO mini_shop_offers (
                    world_id,
                    code,
                    offer_type,
                    item_id,
                    title,
                    description,
                    price,
                    stock,
                    max_per_player,
                    active,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_id, code) DO UPDATE SET
                    offer_type = excluded.offer_type,
                    item_id = excluded.item_id,
                    title = excluded.title,
                    description = excluded.description,
                    price = excluded.price,
                    stock = excluded.stock,
                    max_per_player = excluded.max_per_player,
                    active = excluded.active,
                    metadata_json = excluded.metadata_json
                """,
                (
                    int(world_id),
                    product_code,
                    delivery,
                    item_id,
                    product["title"],
                    product.get("description", ""),
                    int(product.get("price", 0)),
                    product.get("stock"),
                    product.get("max_per_player"),
                    1 if product.get("active", True) else 0,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

        # Offers removed from the file are hidden but kept for purchase history.
        if catalog_codes:
            placeholders = ",".join("?" for _ in catalog_codes)
            conn.execute(
                f"""
                UPDATE mini_shop_offers
                SET active = 0
                WHERE world_id = ?
                  AND code NOT IN ({placeholders})
                """,
                (int(world_id), *catalog_codes),
            )
        else:
            conn.execute(
                "UPDATE mini_shop_offers SET active = 0 WHERE world_id = ?",
                (int(world_id),),
            )

        conn.commit()

    return catalog


def get_shop_categories(
    world_id: int,
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    catalog = sync_shop_catalog(world_id, db_path)

    with connect_mini_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT metadata_json, COUNT(*)
            FROM mini_shop_offers
            WHERE world_id = ? AND active = 1
            GROUP BY metadata_json
            """,
            (int(world_id),),
        ).fetchall()

    counts = {}
    for metadata_json, count in rows:
        try:
            category = json.loads(metadata_json).get("category", "")
        except json.JSONDecodeError:
            continue
        counts[category] = counts.get(category, 0) + int(count)

    result = []
    for category in sorted(
        catalog["categories"],
        key=lambda value: int(value.get("sort", 999)),
    ):
        item = dict(category)
        item["product_count"] = counts.get(category["code"], 0)
        result.append(item)
    return result


def _offer_to_dict(row: sqlite3.Row) -> dict:
    offer = dict(row)
    try:
        offer["metadata"] = json.loads(offer.pop("metadata_json"))
    except json.JSONDecodeError:
        offer["metadata"] = {}
    return offer


def get_offers_by_category(
    world_id: int,
    category: str,
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    sync_shop_catalog(world_id, db_path)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM mini_shop_offers
            WHERE world_id = ? AND active = 1
            ORDER BY id
            """,
            (int(world_id),),
        ).fetchall()

    result = []
    for row in rows:
        offer = _offer_to_dict(row)
        if offer["metadata"].get("category") == category:
            result.append(offer)
    return result


def get_offer(
    world_id: int,
    offer_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    sync_shop_catalog(world_id, db_path)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM mini_shop_offers
            WHERE id = ? AND world_id = ?
            """,
            (int(offer_id), int(world_id)),
        ).fetchone()

    return _offer_to_dict(row) if row else None


def get_offer_purchase_count(
    player_id: int,
    offer_id: int,
    db_path: str | Path = DB_PATH,
) -> int:
    with connect_mini_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM mini_purchases
            WHERE player_id = ? AND offer_id = ?
            """,
            (int(player_id), int(offer_id)),
        ).fetchone()
    return int(row[0])


def purchase_offer(
    player_id: int,
    world_id: int,
    offer_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    sync_shop_catalog(world_id, db_path)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        player = conn.execute(
            """
            SELECT id, world_id, coins
            FROM mini_players
            WHERE id = ?
            """,
            (int(player_id),),
        ).fetchone()
        if player is None or int(player["world_id"]) != int(world_id):
            conn.rollback()
            raise ShopError("Mini-игрок не найден в этом мире.")

        offer_row = conn.execute(
            """
            SELECT *
            FROM mini_shop_offers
            WHERE id = ? AND world_id = ? AND active = 1
            """,
            (int(offer_id), int(world_id)),
        ).fetchone()
        if offer_row is None:
            conn.rollback()
            raise ShopError("Товар больше не доступен.")

        offer = _offer_to_dict(offer_row)
        price = int(offer["price"])
        balance = int(player["coins"])

        bought_by_player = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM mini_purchases
            WHERE player_id = ? AND offer_id = ?
            """,
            (int(player_id), int(offer_id)),
        ).fetchone()[0]
        bought_by_player = int(bought_by_player)

        max_per_player = offer["max_per_player"]
        if max_per_player is not None and bought_by_player >= int(max_per_player):
            conn.rollback()
            raise ShopLimitReached("Лимит покупок этого товара уже исчерпан.")

        if offer["stock"] is not None:
            sold = conn.execute(
                """
                SELECT COALESCE(SUM(quantity), 0)
                FROM mini_purchases
                WHERE offer_id = ?
                """,
                (int(offer_id),),
            ).fetchone()[0]
            if int(sold) >= int(offer["stock"]):
                conn.rollback()
                raise ShopOutOfStock("Товар закончился.")

        if balance < price:
            conn.rollback()
            raise ShopInsufficientFunds(
                f"Не хватает {price - balance} монет."
            )

        delivery = offer["metadata"].get("delivery", offer["offer_type"])
        purchase_status = "active" if delivery == "certificate" else "delivered"

        purchase_cursor = conn.execute(
            """
            INSERT INTO mini_purchases (
                player_id,
                offer_id,
                quantity,
                total_price,
                status
            )
            VALUES (?, ?, 1, ?, ?)
            """,
            (
                int(player_id),
                int(offer_id),
                price,
                purchase_status,
            ),
        )
        purchase_id = int(purchase_cursor.lastrowid)

        if delivery == "inventory":
            item_id = offer["item_id"]
            if item_id is None:
                conn.rollback()
                raise ShopError("У товара не настроен предмет выдачи.")

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
                (int(player_id), int(item_id)),
            )

        new_balance = balance - price
        conn.execute(
            "UPDATE mini_players SET coins = ? WHERE id = ?",
            (new_balance, int(player_id)),
        )

        if price > 0:
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
                VALUES (?, ?, ?, ?, 'shop', ?, ?)
                """,
                (
                    int(player_id),
                    -price,
                    new_balance,
                    f"Покупка: {offer['title']}",
                    purchase_id,
                    f"shop:{purchase_id}",
                ),
            )

        conn.commit()

    return {
        "purchase_id": purchase_id,
        "title": offer["title"],
        "description": offer["description"],
        "delivery": delivery,
        "price": price,
        "balance": new_balance,
        "status": purchase_status,
    }


def get_player_goods(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row

        inventory_rows = conn.execute(
            """
            SELECT
                i.code,
                i.name,
                i.description,
                inv.quantity
            FROM mini_inventory inv
            JOIN mini_items i ON i.id = inv.item_id
            WHERE inv.player_id = ? AND inv.quantity > 0
            ORDER BY i.name
            """,
            (int(player_id),),
        ).fetchall()

        certificate_rows = conn.execute(
            """
            SELECT
                p.id AS purchase_id,
                o.code,
                o.title,
                o.description,
                p.status,
                p.created_at
            FROM mini_purchases p
            JOIN mini_shop_offers o ON o.id = p.offer_id
            WHERE p.player_id = ?
              AND p.status = 'active'
              AND o.offer_type = 'certificate'
            ORDER BY p.id DESC
            """,
            (int(player_id),),
        ).fetchall()

    return {
        "inventory": [dict(row) for row in inventory_rows],
        "certificates": [dict(row) for row in certificate_rows],
    }
