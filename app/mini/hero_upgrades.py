import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.catalog import load_hero_catalog


DEFAULT_MAX_STARS = {
    "common": 4,
    "uncommon": 5,
    "rare": 6,
    "legendary": None,
}

DEFAULT_COST_MULTIPLIER = {
    "common": 1,
    "uncommon": 2,
    "rare": 4,
    "legendary": 10,
}


class HeroUpgradeError(ValueError):
    pass


class HeroUpgradeMaxStars(HeroUpgradeError):
    pass


class HeroUpgradeInsufficientShards(HeroUpgradeError):
    pass


class HeroShardSellError(HeroUpgradeError):
    pass


def get_upgrade_settings() -> dict:
    settings = load_hero_catalog().get("settings", {})
    raw = settings.get("upgrade", {})

    max_stars = dict(DEFAULT_MAX_STARS)
    max_stars.update(raw.get("max_stars", {}))

    cost_multiplier = dict(DEFAULT_COST_MULTIPLIER)
    cost_multiplier.update(raw.get("cost_multiplier", {}))

    return {
        "attack_growth_percent": int(raw.get("attack_growth_percent", 50)),
        "shard_sell_price": int(raw.get("shard_sell_price", 1)),
        "star_cost_base": int(raw.get("star_cost_base", 10)),
        "max_stars": max_stars,
        "cost_multiplier": {
            rarity: int(value)
            for rarity, value in cost_multiplier.items()
        },
    }


def _max_stars_from_settings(settings: dict, rarity: str) -> int | None:
    value = settings["max_stars"].get(rarity)
    if value is None:
        return None
    return int(value)


def max_stars_for_rarity(rarity: str) -> int | None:
    return _max_stars_from_settings(get_upgrade_settings(), rarity)


def _calculate_attack(base_attack: int, stars: int, growth: int) -> int:
    attack = max(0, int(base_attack))
    stars = max(0, int(stars))

    for _ in range(stars):
        attack = attack * (100 + int(growth)) // 100

    return attack


def calculate_attack(base_attack: int, stars: int) -> int:
    """Каждая звезда: +50% от ТЕКУЩЕЙ атаки, округление вниз."""
    settings = get_upgrade_settings()
    return _calculate_attack(
        base_attack,
        stars,
        int(settings["attack_growth_percent"]),
    )


def _upgrade_cost_from_settings(
    settings: dict,
    rarity: str,
    target_star: int,
) -> int:
    target_star = int(target_star)
    if target_star < 1:
        raise ValueError("Номер звезды должен быть не меньше 1.")

    multiplier = int(settings["cost_multiplier"].get(rarity, 1))
    return target_star * int(settings["star_cost_base"]) * multiplier


def upgrade_cost(rarity: str, target_star: int) -> int:
    return _upgrade_cost_from_settings(
        get_upgrade_settings(),
        rarity,
        target_star,
    )


def hero_upgrade_state(hero: dict) -> dict:
    settings = get_upgrade_settings()
    rarity = str(hero.get("rarity", "common"))
    base_attack = int(hero.get("base_attack", hero.get("attack", 1)))
    stars = int(hero.get("stars", 0))
    shards = int(hero.get("shards", 0))
    maximum = _max_stars_from_settings(settings, rarity)
    growth = int(settings["attack_growth_percent"])
    current_attack = _calculate_attack(base_attack, stars, growth)
    at_max = maximum is not None and stars >= maximum

    if at_max:
        next_star = None
        next_attack = None
        cost = None
    else:
        next_star = stars + 1
        next_attack = _calculate_attack(base_attack, next_star, growth)
        cost = _upgrade_cost_from_settings(settings, rarity, next_star)

    return {
        "base_attack": base_attack,
        "stars": stars,
        "shards": shards,
        "max_stars": maximum,
        "current_attack": current_attack,
        "at_max": at_max,
        "next_star": next_star,
        "next_attack": next_attack,
        "upgrade_cost": cost,
        "can_upgrade": (not at_max and shards >= int(cost or 0)),
        "shard_sell_price": int(settings["shard_sell_price"]),
    }

def upgrade_hero(
    player_id: int,
    hero_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    """Тратит осколки конкретного героя и повышает его звёзды на 1."""
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT
                h.id,
                h.code,
                h.name,
                h.rarity,
                h.attack AS base_attack,
                ph.stars,
                ph.shards
            FROM mini_player_heroes ph
            JOIN mini_heroes h ON h.id = ph.hero_id
            WHERE ph.player_id = ? AND ph.hero_id = ?
            """,
            (int(player_id), int(hero_id)),
        ).fetchone()

        if row is None:
            conn.rollback()
            raise HeroUpgradeError("Этого героя нет в твоей коллекции.")

        stars = int(row["stars"])
        shards = int(row["shards"])
        rarity = str(row["rarity"])
        settings = get_upgrade_settings()
        maximum = _max_stars_from_settings(settings, rarity)

        if maximum is not None and stars >= maximum:
            conn.rollback()
            raise HeroUpgradeMaxStars("У героя уже максимальное количество звёзд.")

        target_star = stars + 1
        cost = _upgrade_cost_from_settings(settings, rarity, target_star)

        if shards < cost:
            conn.rollback()
            raise HeroUpgradeInsufficientShards(
                f"Не хватает {cost - shards} осколков. Нужно {cost}."
            )

        new_shards = shards - cost
        conn.execute(
            """
            UPDATE mini_player_heroes
            SET stars = ?, shards = ?
            WHERE player_id = ? AND hero_id = ?
            """,
            (target_star, new_shards, int(player_id), int(hero_id)),
        )
        conn.commit()

    base_attack = int(row["base_attack"])
    growth = int(settings["attack_growth_percent"])
    return {
        "hero_id": int(row["id"]),
        "code": str(row["code"]),
        "name": str(row["name"]),
        "rarity": rarity,
        "old_stars": stars,
        "stars": target_star,
        "shards_spent": cost,
        "shards": new_shards,
        "old_attack": _calculate_attack(base_attack, stars, growth),
        "attack": _calculate_attack(base_attack, target_star, growth),
        "max_stars": maximum,
    }


def sell_hero_shards(
    player_id: int,
    hero_id: int,
    quantity: int,
    *,
    operation_key: str = "",
    db_path: str | Path = DB_PATH,
) -> dict:
    """Продаёт осколки героя. Курс берётся из heroes.json."""
    quantity = int(quantity)
    if quantity <= 0:
        raise HeroShardSellError("Количество осколков должно быть больше нуля.")

    settings = get_upgrade_settings()
    sell_price = int(settings["shard_sell_price"])
    if sell_price < 0:
        raise HeroShardSellError("Некорректный курс продажи осколков.")

    operation_key = str(operation_key or "").strip()
    wallet_operation_key = f"shardsell:{operation_key}" if operation_key else ""

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        if wallet_operation_key:
            existing = conn.execute(
                """
                SELECT amount, balance_after
                FROM mini_wallet_transactions
                WHERE player_id = ? AND operation_key = ?
                """,
                (int(player_id), wallet_operation_key),
            ).fetchone()
            if existing is not None:
                hero_row = conn.execute(
                    """
                    SELECT ph.shards, h.name
                    FROM mini_player_heroes ph
                    JOIN mini_heroes h ON h.id = ph.hero_id
                    WHERE ph.player_id = ? AND ph.hero_id = ?
                    """,
                    (int(player_id), int(hero_id)),
                ).fetchone()
                conn.commit()
                return {
                    "applied": False,
                    "hero_id": int(hero_id),
                    "name": str(hero_row["name"]) if hero_row else "Герой",
                    "sold": int(existing["amount"]) // max(1, sell_price),
                    "coins_earned": int(existing["amount"]),
                    "balance": int(existing["balance_after"]),
                    "shards": int(hero_row["shards"]) if hero_row else 0,
                }

        row = conn.execute(
            """
            SELECT ph.shards, h.name, p.coins
            FROM mini_player_heroes ph
            JOIN mini_heroes h ON h.id = ph.hero_id
            JOIN mini_players p ON p.id = ph.player_id
            WHERE ph.player_id = ? AND ph.hero_id = ?
            """,
            (int(player_id), int(hero_id)),
        ).fetchone()

        if row is None:
            conn.rollback()
            raise HeroShardSellError("Этого героя нет в твоей коллекции.")

        current_shards = int(row["shards"])
        if current_shards < quantity:
            conn.rollback()
            raise HeroShardSellError(
                f"Недостаточно осколков. Сейчас: {current_shards}."
            )

        coins_earned = quantity * sell_price
        new_shards = current_shards - quantity
        new_balance = int(row["coins"]) + coins_earned

        conn.execute(
            """
            UPDATE mini_player_heroes
            SET shards = ?
            WHERE player_id = ? AND hero_id = ?
            """,
            (new_shards, int(player_id), int(hero_id)),
        )
        conn.execute(
            "UPDATE mini_players SET coins = ? WHERE id = ?",
            (new_balance, int(player_id)),
        )
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
            VALUES (?, ?, ?, ?, 'hero_shards', ?, ?)
            """,
            (
                int(player_id),
                coins_earned,
                new_balance,
                f"Продажа осколков: {row['name']}",
                int(hero_id),
                wallet_operation_key,
            ),
        )
        conn.commit()

    return {
        "applied": True,
        "hero_id": int(hero_id),
        "name": str(row["name"]),
        "sold": quantity,
        "coins_earned": coins_earned,
        "balance": new_balance,
        "shards": new_shards,
    }
