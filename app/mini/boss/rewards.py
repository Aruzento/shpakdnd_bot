from app.mini.wallet import change_balance_in_transaction
import json
import sqlite3
from datetime import datetime
from app.mini.combat.hero_abilities import engine as hero_abilities
from app.mini.boss.catalog import get_boss_template
from app.mini.boss.loadouts import battle_loadout
from app.mini.boss.errors import BossCombatError
from app.mini.combat import classes
from app.mini.boss.clock import utcnow, db_time
from app.mini.boss.repository import log_hero_event

def reward_items(raw: str | None) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        quantity = int(item.get("quantity", 1) or 1)
        if code and quantity > 0:
            result.append({"code": code, "quantity": quantity})
    return result


def hydrate_reward_snapshot(conn: sqlite3.Connection, boss: sqlite3.Row) -> sqlite3.Row:
    template = get_boss_template(str(boss["template_code"]))
    if template is None:
        return boss

    current_items = reward_items(boss["reward_items_json"])
    shields_max = int(boss["reward_shields_max"])
    decay = int(boss["reward_decay_percent"])

    updates = []
    params = []
    if not current_items and template.get("reward_items"):
        updates.append("reward_items_json = ?")
        params.append(json.dumps(template.get("reward_items", []), ensure_ascii=False))
    if shields_max < 0:
        updates.extend(["reward_shields = ?", "reward_shields_max = ?"])
        value = int(template.get("reward_shields", 3))
        params.extend([value, value])
    if decay <= 0:
        updates.append("reward_decay_percent = ?")
        params.append(int(template.get("reward_decay_percent", 10)))

    if updates:
        params.append(int(boss["id"]))
        conn.execute(
            f"UPDATE mini_bosses SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?", (int(boss["id"]),)
        ).fetchone()
    return boss


def reward_eligible(row: sqlite3.Row) -> bool:
    return int(row["hit_count"] or 0) > 0 or bool(int(row["phantom_reward"] or 0))


def grant_victory_rewards(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    *,
    include_all_registered: bool = False,
    when: datetime | None = None,
) -> dict:
    reward_percent = max(0, min(100, int(boss["reward_percent"])))
    coins_each = classes.real_reward(dict(boss))
    items = reward_items(boss["reward_items_json"])

    item_ids = {}
    for item in items:
        row = conn.execute(
            "SELECT id FROM mini_items WHERE code = ? AND active = 1",
            (item["code"],),
        ).fetchone()
        if row is None:
            raise BossCombatError(
                f"Предмет награды {item['code']} не найден в mini_items."
            )
        item_ids[item["code"]] = int(row[0])

    participants = conn.execute(
        """
        SELECT bp.*, p.coins, h.name AS hero_name
        FROM mini_boss_participants bp
        JOIN mini_players p ON p.id = bp.player_id
        LEFT JOIN mini_heroes h ON h.id = bp.hero_id
        WHERE bp.boss_id = ?
        ORDER BY bp.queue_position
        """,
        (int(boss["id"]),),
    ).fetchall()

    granted = 0
    missed = 0
    victory_events = []
    for row in participants:
        if int(row["reward_granted"]):
            continue
        player_id = int(row["player_id"])
        if not include_all_registered and not reward_eligible(row):
            conn.execute(
                """
                UPDATE mini_boss_participants
                SET reward_granted = 1
                WHERE boss_id = ? AND player_id = ?
                """,
                (int(boss["id"]), player_id),
            )
            missed += 1
            continue
        if coins_each > 0:
            change_balance_in_transaction(
                conn, player_id, coins_each, f"Победа над боссом: {boss['name']}",
                "boss", int(boss["id"]), f"boss:{boss['id']}:victory:{player_id}:coins",
            )

        for item in items:
            conn.execute(
                """
                INSERT INTO mini_inventory (player_id, item_id, quantity, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(player_id, item_id) DO UPDATE SET
                    quantity = mini_inventory.quantity + excluded.quantity,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (player_id, item_ids[item["code"]], int(item["quantity"])),
            )

        if not include_all_registered and int(row["hit_count"]) > 0:
            passive = battle_loadout(row["hero_snapshot_json"]).get("passive_key", "none")
            victory = hero_abilities.resolve_victory(passive)
            if victory["bonus_shards"]:
                conn.execute("UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                             (victory["bonus_shards"], player_id))
            for event in victory["events"]:
                victory_events.append(log_hero_event(conn, boss, row, event, when or utcnow()))

        conn.execute(
            """
            UPDATE mini_boss_participants
            SET reward_granted = 1
            WHERE boss_id = ? AND player_id = ?
            """,
            (int(boss["id"]), player_id),
        )
        granted += 1

    return {
        "players": granted,
        "missed_players": missed,
        "coins_each": coins_each,
        "items": items,
        "shards_each": 0,
        "passive_events": victory_events,
    }


def grant_failure_rewards(conn: sqlite3.Connection, boss: sqlite3.Row) -> dict:
    shards_each = max(0, int(boss["reward_coins"]) // 10)
    participants = conn.execute(
        """
        SELECT bp.player_id, bp.reward_granted, bp.hit_count, bp.phantom_reward
        FROM mini_boss_participants bp
        WHERE bp.boss_id = ?
        ORDER BY bp.queue_position
        """,
        (int(boss["id"]),),
    ).fetchall()

    granted = 0
    missed = 0
    for row in participants:
        if int(row["reward_granted"]):
            continue
        player_id = int(row["player_id"])
        if not reward_eligible(row):
            conn.execute(
                """
                UPDATE mini_boss_participants
                SET reward_granted = 1
                WHERE boss_id = ? AND player_id = ?
                """,
                (int(boss["id"]), player_id),
            )
            missed += 1
            continue
        if shards_each > 0:
            conn.execute(
                "UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                (shards_each, player_id),
            )
        conn.execute(
            """
            UPDATE mini_boss_participants
            SET reward_granted = 1
            WHERE boss_id = ? AND player_id = ?
            """,
            (int(boss["id"]), player_id),
        )
        granted += 1

    return {
        "players": granted,
        "missed_players": missed,
        "coins_each": 0,
        "items": [],
        "shards_each": shards_each,
    }


def finish_victory(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    rewards = grant_victory_rewards(conn, boss, when=when)
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'defeated', battle_result = 'victory', ended_at = ?,
            current_hp = 0, turn_started_at = NULL, reward_corruption = 0, reward_temp_hp = 0
        WHERE id = ?
        """,
        (db_time(when), int(boss["id"])),
    )
    return rewards


def finish_admin_victory(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    when: datetime,
) -> dict:
    """Аварийно завершает бой победой и награждает всех зарегистрированных."""
    rewards = grant_victory_rewards(
        conn,
        boss,
        include_all_registered=True,
    )
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'defeated', battle_result = 'admin_victory', ended_at = ?,
            current_hp = 0, turn_started_at = NULL, turn_message_id = NULL, reward_corruption = 0, reward_temp_hp = 0
        WHERE id = ?
        """,
        (db_time(when), int(boss["id"])),
    )
    return rewards


def finish_failure(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    rewards = grant_failure_rewards(conn, boss)
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'failed', battle_result = 'reward_destroyed', ended_at = ?,
            reward_percent = 0, turn_started_at = NULL
        WHERE id = ?
        """,
        (db_time(when), int(boss["id"])),
    )
    return rewards

