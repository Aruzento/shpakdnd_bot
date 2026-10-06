import json
import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.schema import init_boss_db
from app.mini.db import connect_mini_db


ACTIVE_STATUSES = ("announced", "ready", "fighting")


class BossError(ValueError):
    pass


class BossRegistrationClosed(BossError):
    pass


class BossNotEnoughPlayers(BossError):
    pass


def _boss_row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def get_boss(
    boss_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
    return _boss_row_to_dict(row)


def get_active_boss(
    world_id: int,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    init_boss_db(db_path)
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            SELECT *
            FROM mini_bosses
            WHERE world_id = ?
              AND status IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(world_id), *ACTIVE_STATUSES),
        ).fetchone()
    return _boss_row_to_dict(row)


def create_boss_event(
    world_id: int,
    template_code: str,
    created_by_user_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    init_boss_db(db_path)
    template = get_boss_template(template_code)
    if template is None or not bool(template.get("active", True)):
        raise BossError("Этот босс отсутствует или отключён в каталоге.")

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            """
            SELECT id, name
            FROM mini_bosses
            WHERE world_id = ?
              AND status IN ('announced', 'ready', 'fighting')
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(world_id),),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            raise BossError(
                f"Сначала заверши текущего босса: {existing['name']}."
            )

        cursor = conn.execute(
            """
            INSERT INTO mini_bosses (
                world_id,
                template_code,
                name,
                description,
                image_path,
                max_hp,
                current_hp,
                min_players,
                status,
                signup_opens_at,
                skip_after_hours,
                reward_coins,
                reward_items_json,
                reward_shields,
                reward_shields_max,
                reward_percent,
                reward_decay_percent,
                created_by_user_id
            )
            SELECT
                ?, ?, ?, ?, ?, ?, ?, ?, 'announced', CURRENT_TIMESTAMP,
                boss_skip_hours, ?, ?, ?, ?, 100, ?, ?
            FROM mini_worlds
            WHERE id = ?
            """,
            (
                int(world_id),
                str(template["code"]),
                str(template["name"]),
                str(template.get("description", "")),
                str(template.get("image", "")),
                int(template["max_hp"]),
                int(template["max_hp"]),
                int(template["min_players"]),
                int(template.get("reward_coins", 0)),
                json.dumps(template.get("reward_items", []), ensure_ascii=False),
                int(template.get("reward_shields", 3)),
                int(template.get("reward_shields", 3)),
                int(template.get("reward_decay_percent", 10)),
                int(created_by_user_id),
                int(world_id),
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise BossError("D&D Mini-мир не найден.")
        boss_id = int(cursor.lastrowid)
        conn.execute(
            """UPDATE mini_bosses SET faction = ?, ability_key = ?, ability_text = ?,
               features_json = ?, ability_config_json = ? WHERE id = ?""",
            (template["faction"], template["ability_key"], template["ability_text"],
             json.dumps(template["features"], ensure_ascii=False),
             json.dumps(template.get("ability_config", {})), boss_id),
        )
        conn.commit()

    boss = get_boss(boss_id, db_path)
    if boss is None:
        raise BossError("Не удалось создать босса.")
    return boss


def set_signup_message(
    boss_id: int,
    message_id: int,
    kind: str,
    db_path: str | Path = DB_PATH,
) -> None:
    kind = "photo" if kind == "photo" else "text"
    with connect_mini_db(db_path) as conn:
        conn.execute(
            """
            UPDATE mini_bosses
            SET signup_message_id = ?, signup_message_kind = ?
            WHERE id = ?
            """,
            (int(message_id), kind, int(boss_id)),
        )
        conn.commit()


def set_turn_message(
    boss_id: int,
    message_id: int | None,
    db_path: str | Path = DB_PATH,
    *,
    kind: str = "text",
    notice_json: str | None = None,
) -> None:
    """Сохраняет ID и тип текущего публичного сообщения хода боя."""
    with connect_mini_db(db_path) as conn:
        conn.execute(
            """UPDATE mini_bosses SET turn_message_id = ?, turn_message_kind = ?,
               turn_notice_json = COALESCE(?, turn_notice_json) WHERE id = ?""",
            (int(message_id) if message_id is not None else None,
             "photo" if kind == "photo" else "text", notice_json, int(boss_id)),
        )
        conn.commit()


def list_participants(
    boss_id: int,
    db_path: str | Path = DB_PATH,
) -> list[dict]:
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                bp.queue_position,
                bp.joined_at,
                p.id AS player_id,
                p.telegram_user_id,
                p.username,
                p.character_name,
                p.active_hero_id,
                bp.hero_id AS battle_hero_id,
                bp.attack AS battle_attack,
                bp.damage_bonus_percent,
                bp.phantom_reward,
                bp.forced_skip_turns,
                bp.banished,
                h.name AS hero_name,
                h.rarity AS hero_rarity,
                h.image_path AS hero_image_path,
                bp.hero_snapshot_json
            FROM mini_boss_participants bp
            JOIN mini_players p ON p.id = bp.player_id
            JOIN mini_bosses b ON b.id = bp.boss_id
            LEFT JOIN mini_heroes h ON h.id = CASE
                WHEN b.status IN ('announced', 'ready') THEN COALESCE(bp.hero_id, p.active_hero_id)
                ELSE bp.hero_id END
            WHERE bp.boss_id = ?
            ORDER BY bp.queue_position, bp.joined_at, p.id
            """,
            (int(boss_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def register_player(
    boss_id: int,
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        boss = conn.execute(
            "SELECT id, world_id, status FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
        if boss is None:
            conn.rollback()
            raise BossError("Босс не найден.")
        if boss["status"] != "announced":
            conn.rollback()
            raise BossRegistrationClosed("Регистрация на этого босса закрыта.")

        player = conn.execute(
            """
            SELECT id, world_id, active_hero_id
            FROM mini_players
            WHERE id = ?
            """,
            (int(player_id),),
        ).fetchone()
        if player is None or int(player["world_id"]) != int(boss["world_id"]):
            conn.rollback()
            raise BossError("Mini-персонаж не найден в этом мире.")
        if player["active_hero_id"] is None:
            conn.rollback()
            raise BossError("Сначала выбери активного героя в коллекции.")

        existing = conn.execute(
            """
            SELECT queue_position
            FROM mini_boss_participants
            WHERE boss_id = ? AND player_id = ?
            """,
            (int(boss_id), int(player_id)),
        ).fetchone()
        if existing is not None:
            conn.commit()
            return {
                "applied": False,
                "queue_position": int(existing["queue_position"]),
            }

        next_position = int(conn.execute(
            """
            SELECT COALESCE(MAX(queue_position), 0) + 1
            FROM mini_boss_participants
            WHERE boss_id = ?
            """,
            (int(boss_id),),
        ).fetchone()[0])

        conn.execute(
            """
            INSERT INTO mini_boss_participants (
                boss_id, player_id, queue_position, hero_id
            ) VALUES (?, ?, ?, ?)
            """,
            (int(boss_id), int(player_id), next_position, int(player["active_hero_id"])),
        )
        conn.commit()

    return {"applied": True, "queue_position": next_position}


def unregister_player(
    boss_id: int,
    player_id: int,
    db_path: str | Path = DB_PATH,
) -> bool:
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        boss = conn.execute(
            "SELECT status FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
        if boss is None:
            conn.rollback()
            raise BossError("Босс не найден.")
        if boss["status"] != "announced":
            conn.rollback()
            raise BossRegistrationClosed("Регистрация на этого босса закрыта.")

        cursor = conn.execute(
            """
            DELETE FROM mini_boss_participants
            WHERE boss_id = ? AND player_id = ?
            """,
            (int(boss_id), int(player_id)),
        )

        if cursor.rowcount > 0:
            remaining = conn.execute(
                """
                SELECT player_id
                FROM mini_boss_participants
                WHERE boss_id = ?
                ORDER BY queue_position, joined_at, player_id
                """,
                (int(boss_id),),
            ).fetchall()
            for position, row in enumerate(remaining, start=1):
                conn.execute(
                    """
                    UPDATE mini_boss_participants
                    SET queue_position = ?
                    WHERE boss_id = ? AND player_id = ?
                    """,
                    (position, int(boss_id), int(row["player_id"])),
                )

        conn.commit()
    return cursor.rowcount > 0


def close_registration(
    boss_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
        if boss is None:
            conn.rollback()
            raise BossError("Босс не найден.")
        if boss["status"] != "announced":
            conn.rollback()
            raise BossError("Регистрация уже не открыта.")

        participants = conn.execute(
            """
            SELECT player_id
            FROM mini_boss_participants
            WHERE boss_id = ?
            ORDER BY queue_position, joined_at, player_id
            """,
            (int(boss_id),),
        ).fetchall()
        count = len(participants)
        minimum = int(boss["min_players"])
        if count < minimum:
            conn.rollback()
            raise BossNotEnoughPlayers(
                f"Нужно минимум {minimum} игроков. Сейчас: {count}."
            )

        # Фиксируем непрерывный порядок перед будущим боем.
        for position, row in enumerate(participants, start=1):
            conn.execute(
                """
                UPDATE mini_boss_participants
                SET queue_position = ?
                WHERE boss_id = ? AND player_id = ?
                """,
                (position, int(boss_id), int(row["player_id"])),
            )

        conn.execute(
            """
            UPDATE mini_bosses
            SET status = 'ready', signup_closes_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(boss_id),),
        )
        conn.commit()

    result = get_boss(boss_id, db_path)
    if result is None:
        raise BossError("Босс не найден после закрытия регистрации.")
    return result


def reopen_registration(
    boss_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise BossError("Босс не найден.")
        if row[0] != "ready":
            conn.rollback()
            raise BossError("Открыть снова можно только закрытую регистрацию.")
        conn.execute(
            """
            UPDATE mini_bosses
            SET status = 'announced', signup_closes_at = NULL
            WHERE id = ?
            """,
            (int(boss_id),),
        )
        conn.commit()
    result = get_boss(boss_id, db_path)
    if result is None:
        raise BossError("Босс не найден после открытия регистрации.")
    return result


def cancel_boss(
    boss_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    with connect_mini_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise BossError("Босс не найден.")
        if row[0] not in ACTIVE_STATUSES:
            conn.rollback()
            raise BossError("Этот босс уже завершён или отменён.")
        conn.execute(
            """
            UPDATE mini_bosses
            SET status = 'cancelled', ended_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(boss_id),),
        )
        conn.commit()
    result = get_boss(boss_id, db_path)
    if result is None:
        raise BossError("Босс не найден после отмены.")
    return result


def select_battle_hero(
    boss_id: int,
    player_id: int,
    hero_id: int,
    db_path: str | Path = DB_PATH,
) -> dict:
    """Atomically change a registered player's hero before battle starts."""
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        participant = conn.execute(
            """SELECT b.status FROM mini_boss_participants bp
               JOIN mini_bosses b ON b.id = bp.boss_id
               WHERE bp.boss_id = ? AND bp.player_id = ?""",
            (int(boss_id), int(player_id)),
        ).fetchone()
        if participant is None:
            raise BossError("Ты не зарегистрирован на этого босса.")
        if participant["status"] not in ("announced", "ready"):
            raise BossRegistrationClosed("Героя можно выбрать только до начала боя.")
        hero = conn.execute(
            """SELECT h.* FROM mini_player_heroes ph
               JOIN mini_heroes h ON h.id = ph.hero_id
               WHERE ph.player_id = ? AND ph.hero_id = ?""",
            (int(player_id), int(hero_id)),
        ).fetchone()
        if hero is None:
            raise BossError("Этого героя нет в твоей коллекции.")
        conn.execute(
            "UPDATE mini_boss_participants SET hero_id = ? WHERE boss_id = ? AND player_id = ?",
            (int(hero_id), int(boss_id), int(player_id)),
        )
        return dict(hero)
