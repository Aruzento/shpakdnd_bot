import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.catalog import hero_image_path, load_hero_catalog


def sync_hero_catalog(db_path: str | Path = DB_PATH) -> int:
    catalog = load_hero_catalog()
    heroes = catalog["heroes"]

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
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
                    active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    active = excluded.active
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
    with sqlite3.connect(db_path) as conn:
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
