"""Mythic stars 1..6 spend universal shards and never increase base attack."""

from app.config import DB_PATH
from app.mini.mythic.wallet import transaction, change, previous, remember

COSTS = (20, 35, 55, 80, 110)


def upgrade(player_id, hero_id, expected_stars, db_path=DB_PATH, *, now=None):
    if type(expected_stars) is not int or not 1 <= expected_stars <= 5:
        raise ValueError("Для улучшения Mythic требуется актуальная звезда 1..5.")
    target = expected_stars + 1
    key = f"mythic:upgrade:{hero_id}:{target}"
    payload = ["upgrade", hero_id, target]
    with transaction(db_path) as conn:
        prior = previous(conn, player_id, key, payload)
        if prior is not None:
            return prior
        row = conn.execute(
            "SELECT h.*,ph.stars FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND ph.hero_id=? AND h.rarity='mythic'",
            (player_id, hero_id),
        ).fetchone()
        if row is None:
            raise ValueError("Мифический герой не принадлежит игроку.")
        if row["stars"] != expected_stars:
            raise ValueError("Устаревшая кнопка улучшения Mythic.")
        cost = COSTS[expected_stars - 1]
        balance = change(conn, player_id, -cost, "mythic_upgrade", key, payload)
        if conn.execute(
            "SELECT 1 FROM mini_village_residents WHERE player_id=? AND hero_id=?",
            (player_id, hero_id),
        ).fetchone():
            from app.mini.village.service import settle, timestamp

            settle(conn, player_id, timestamp(now))
        conn.execute(
            "UPDATE mini_player_heroes SET stars=? WHERE player_id=? AND hero_id=?",
            (target, player_id, hero_id),
        )
        return remember(
            conn,
            player_id,
            key,
            payload,
            {
                "hero_id": hero_id,
                "code": row["code"],
                "name": row["name"],
                "rarity": "mythic",
                "old_stars": expected_stars,
                "stars": target,
                "shards_spent": cost,
                "shards": balance,
                "mythic_shards": balance,
                "old_attack": row["attack"],
                "attack": row["attack"],
                "max_stars": 6,
            },
        )
