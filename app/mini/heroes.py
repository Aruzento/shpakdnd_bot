import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.catalog import hero_image_path, load_hero_catalog
from app.mini.hero_upgrades import calculate_attack, hero_upgrade_state


def sync_hero_catalog(db_path: str | Path = DB_PATH) -> int:
    catalog = load_hero_catalog()
    heroes = catalog["heroes"]

    with connect_mini_db(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # Freeze old fighting loadouts before changing any catalog passive/trait.
        from app.mini.boss.loadouts import freeze_legacy_loadouts
        freeze_legacy_loadouts(conn)
        catalog_codes = []

        for hero in heroes:
            catalog_codes.append(hero["code"])
            conn.execute(
                """
                INSERT INTO mini_heroes (
                    code,
                    name,
                    rarity,
                    race,
                    class_name,
                    attack,
                    passive_key,
                    passive_text,
                    description,
                    image_path,
                    active, faction, damage_type, class_tag, attack_range, special_trait
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    rarity = excluded.rarity,
                    race = excluded.race,
                    class_name = excluded.class_name,
                    attack = excluded.attack,
                    passive_key = excluded.passive_key,
                    passive_text = excluded.passive_text,
                    description = excluded.description,
                    image_path = excluded.image_path,
                    active = excluded.active,
                    faction = excluded.faction,
                    damage_type = excluded.damage_type,
                    class_tag = excluded.class_tag,
                    attack_range = excluded.attack_range,
                    special_trait = excluded.special_trait
                """,
                (
                    hero["code"],
                    hero["name"],
                    hero.get("rarity", "common"),
                    hero.get("race", ""),
                    hero.get("class_name", ""),
                    int(hero.get("attack", 1)),
                    hero.get("passive_key", ""),
                    hero.get("passive_text", ""),
                    hero.get("description", ""),
                    hero.get("image", ""),
                    1 if hero.get("active", True) else 0,
                    hero["faction"], hero["damage_type"], hero["class_tag"],
                    hero["attack_range"], hero["special_trait"],
                ),
            )

        if catalog_codes:
            placeholders = ",".join("?" for _ in catalog_codes)
            conn.execute(
                f"""
                UPDATE mini_heroes
                SET active = 0
                WHERE code NOT IN ({placeholders})
                """,
                tuple(catalog_codes),
            )
        else:
            conn.execute("UPDATE mini_heroes SET active = 0")

        conn.commit()

    return len(heroes)


def get_hero_by_code(code: str, db_path: str | Path = DB_PATH) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM mini_heroes
            WHERE code = ?
            """,
            (code,),
        ).fetchone()

    return dict(row) if row else None


def get_hero_image(hero: dict) -> Path | None:
    return hero_image_path(hero.get("image_path", ""))


def _enrich_owned_hero(hero: dict) -> dict:
    result = dict(hero)
    base_attack = int(result.get("attack", 1))
    stars = int(result.get("stars", 0))
    result["base_attack"] = base_attack
    result["attack"] = calculate_attack(base_attack, stars)
    result["upgrade"] = hero_upgrade_state(result)
    return result


RARITY_ORDER = {
    "legendary": 0,
    "rare": 1,
    "uncommon": 2,
    "common": 3,
}


def get_hero_by_id(
    hero_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM mini_heroes WHERE id = ?",
            (int(hero_id),),
        ).fetchone()
    return dict(row) if row else None


def get_player_heroes(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    """Все полученные герои игрока, включая выключенных из текущей гачи."""
    sync_hero_catalog(db_path)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                h.*,
                ph.copies,
                p.shards AS shards,
                ph.stars,
                ph.obtained_at,
                CASE WHEN p.active_hero_id = h.id THEN 1 ELSE 0 END AS is_active
            FROM mini_player_heroes ph
            JOIN mini_heroes h ON h.id = ph.hero_id
            JOIN mini_players p ON p.id = ph.player_id
            WHERE ph.player_id = ?
            """,
            (int(player_id),),
        ).fetchall()

    result = [_enrich_owned_hero(dict(row)) for row in rows]
    result.sort(
        key=lambda hero: (
            RARITY_ORDER.get(hero.get("rarity", "common"), 99),
            str(hero.get("name", "")).casefold(),
        )
    )
    return result


def get_player_hero(
    player_id: int,
    hero_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                h.*,
                ph.copies,
                p.shards AS shards,
                ph.stars,
                ph.obtained_at,
                CASE WHEN p.active_hero_id = h.id THEN 1 ELSE 0 END AS is_active
            FROM mini_player_heroes ph
            JOIN mini_heroes h ON h.id = ph.hero_id
            JOIN mini_players p ON p.id = ph.player_id
            WHERE ph.player_id = ? AND ph.hero_id = ?
            """,
            (int(player_id), int(hero_id)),
        ).fetchone()
    return _enrich_owned_hero(dict(row)) if row else None


def get_active_hero(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT h.*, ph.copies, p.shards AS shards, ph.stars, 1 AS is_active
            FROM mini_players p
            JOIN mini_heroes h ON h.id = p.active_hero_id
            JOIN mini_player_heroes ph
              ON ph.player_id = p.id AND ph.hero_id = h.id
            WHERE p.id = ?
            """,
            (int(player_id),),
        ).fetchone()
    return _enrich_owned_hero(dict(row)) if row else None


def set_active_hero_in_transaction(
    conn: sqlite3.Connection,
    player_id: int,
    hero_id: int,
) -> dict:
    """Shared selection operation; the caller owns the write transaction."""
    if not conn.in_transaction:
        raise RuntimeError("Active hero selection requires a transaction.")
    owned = conn.execute(
        """SELECT h.* FROM mini_player_heroes ph
           JOIN mini_heroes h ON h.id = ph.hero_id
           WHERE ph.player_id = ? AND ph.hero_id = ?""",
        (int(player_id), int(hero_id)),
    ).fetchone()
    if owned is None:
        raise ValueError("Этого героя нет в твоей коллекции.")
    conn.execute(
        "UPDATE mini_players SET active_hero_id = ? WHERE id = ?",
        (int(hero_id), int(player_id)),
    )
    result = dict(owned)
    result["is_active"] = 1
    return result


def set_active_hero(
    player_id: int,
    hero_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        return set_active_hero_in_transaction(conn, player_id, hero_id)


def get_collection_summary(
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    sync_hero_catalog(db_path)

    with connect_mini_db(db_path) as conn:
        owned = int(conn.execute(
            "SELECT COUNT(*) FROM mini_player_heroes WHERE player_id = ?",
            (int(player_id),),
        ).fetchone()[0])
        total_active = int(conn.execute(
            "SELECT COUNT(*) FROM mini_heroes WHERE active = 1",
        ).fetchone()[0])

    return {
        "owned": owned,
        "total_active": total_active,
        "heroes": get_player_heroes(player_id, db_path),
        "active_hero": get_active_hero(player_id, db_path),
    }
