import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import DB_PATH
from app.mini.boss.abilities import (
    resolve_attack,
    resolve_boss_attack,
    resolve_kill,
)
from app.mini.boss.catalog import get_boss_template, sync_boss_reward_items
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import BossError, get_boss, list_participants
from app.mini.db import connect_mini_db
from app.mini.hero_upgrades import calculate_attack
from app.mini.items import (
    EFFECT_BOSS_DAMAGE,
    EFFECT_BOSS_PHANTOM,
    consume_effect_charge,
)


class BossCombatError(BossError):
    pass


class BossNotYourTurn(BossCombatError):
    pass


class BossNotParticipant(BossCombatError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_time(value: datetime) -> str:
    return _coerce_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            result = datetime.strptime(text, fmt)
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _reward_items(raw: str | None) -> list[dict]:
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


def _hydrate_reward_snapshot(conn: sqlite3.Connection, boss: sqlite3.Row) -> sqlite3.Row:
    template = get_boss_template(str(boss["template_code"]))
    if template is None:
        return boss

    current_items = _reward_items(boss["reward_items_json"])
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


def _current_participant(conn: sqlite3.Connection, boss: sqlite3.Row) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
            bp.*,
            p.telegram_user_id,
            p.username,
            p.character_name,
            h.name AS hero_name,
            h.rarity AS hero_rarity
        FROM mini_boss_participants bp
        JOIN mini_players p ON p.id = bp.player_id
        LEFT JOIN mini_heroes h ON h.id = bp.hero_id
        WHERE bp.boss_id = ? AND bp.queue_position = ?
        """,
        (int(boss["id"]), int(boss["current_turn_position"])),
    ).fetchone()


def _participant_count(conn: sqlite3.Connection, boss_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM mini_boss_participants WHERE boss_id = ?",
            (int(boss_id),),
        ).fetchone()[0]
    )


def _reward_eligible(row: sqlite3.Row) -> bool:
    return int(row["hit_count"] or 0) > 0 or bool(int(row["phantom_reward"] or 0))


def _grant_victory_rewards(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    *,
    include_all_registered: bool = False,
) -> dict:
    reward_percent = max(0, min(100, int(boss["reward_percent"])))
    coins_each = int(boss["reward_coins"]) * reward_percent // 100
    items = _reward_items(boss["reward_items_json"])

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
        SELECT bp.player_id, bp.reward_granted, bp.hit_count, bp.phantom_reward, p.coins
        FROM mini_boss_participants bp
        JOIN mini_players p ON p.id = bp.player_id
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
        if not include_all_registered and not _reward_eligible(row):
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
        current_coins = int(row["coins"])
        new_balance = current_coins + coins_each

        if coins_each > 0:
            conn.execute(
                "UPDATE mini_players SET coins = ? WHERE id = ?",
                (new_balance, player_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO mini_wallet_transactions (
                    player_id,
                    amount,
                    balance_after,
                    reason,
                    reference_type,
                    reference_id,
                    operation_key
                ) VALUES (?, ?, ?, ?, 'boss', ?, ?)
                """,
                (
                    player_id,
                    coins_each,
                    new_balance,
                    f"Победа над боссом: {boss['name']}",
                    int(boss["id"]),
                    f"boss:{boss['id']}:victory:{player_id}:coins",
                ),
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
    }


def _grant_failure_rewards(conn: sqlite3.Connection, boss: sqlite3.Row) -> dict:
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
        if not _reward_eligible(row):
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


def _finish_victory(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    rewards = _grant_victory_rewards(conn, boss)
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'defeated', battle_result = 'victory', ended_at = ?,
            current_hp = 0, turn_started_at = NULL
        WHERE id = ?
        """,
        (_db_time(when), int(boss["id"])),
    )
    return rewards


def _finish_admin_victory(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    when: datetime,
) -> dict:
    """Аварийно завершает бой победой и награждает всех зарегистрированных."""
    rewards = _grant_victory_rewards(
        conn,
        boss,
        include_all_registered=True,
    )
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'defeated', battle_result = 'admin_victory', ended_at = ?,
            current_hp = 0, turn_started_at = NULL, turn_message_id = NULL
        WHERE id = ?
        """,
        (_db_time(when), int(boss["id"])),
    )
    return rewards


def _finish_failure(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    rewards = _grant_failure_rewards(conn, boss)
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'failed', battle_result = 'reward_destroyed', ended_at = ?,
            reward_percent = 0, turn_started_at = NULL
        WHERE id = ?
        """,
        (_db_time(when), int(boss["id"])),
    )
    return rewards



def _apply_boss_attack_passives(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
) -> list[dict]:
    """Применяет пассивки участников, срабатывающие на атаку босса."""
    rows = conn.execute(
        """
        SELECT
            bp.player_id,
            p.username,
            p.character_name,
            h.name AS hero_name,
            h.passive_key
        FROM mini_boss_participants bp
        JOIN mini_players p ON p.id = bp.player_id
        LEFT JOIN mini_heroes h ON h.id = bp.hero_id
        WHERE bp.boss_id = ?
        ORDER BY bp.queue_position
        """,
        (int(boss["id"]),),
    ).fetchall()

    events: list[dict] = []
    for row in rows:
        resolution = resolve_boss_attack(str(row["passive_key"] or "none"))
        bonus_shards = int(resolution.get("bonus_shards", 0))
        if bonus_shards > 0:
            conn.execute(
                "UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                (bonus_shards, int(row["player_id"])),
            )

        for raw_event in resolution.get("events", []):
            event = dict(raw_event)
            event.update(
                {
                    "player_id": int(row["player_id"]),
                    "username": str(row["username"] or ""),
                    "character_name": str(row["character_name"] or ""),
                    "hero_name": str(row["hero_name"] or ""),
                }
            )
            events.append(event)

    return events

def _boss_hits_reward(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    shields = int(boss["reward_shields"])
    reward_percent = int(boss["reward_percent"])
    decay = int(boss["reward_decay_percent"])
    passive_events = _apply_boss_attack_passives(conn, boss)

    if shields > 0:
        new_shields = shields - 1
        conn.execute(
            "UPDATE mini_bosses SET reward_shields = ? WHERE id = ?",
            (new_shields, int(boss["id"])),
        )
        return {
            "type": "shield",
            "old_shields": shields,
            "shields": new_shields,
            "reward_percent": reward_percent,
            "passive_events": passive_events,
        }

    new_percent = max(0, reward_percent - decay)
    conn.execute(
        "UPDATE mini_bosses SET reward_percent = ? WHERE id = ?",
        (new_percent, int(boss["id"])),
    )
    result = {
        "type": "reward_damage",
        "old_percent": reward_percent,
        "reward_percent": new_percent,
        "shields": 0,
        "passive_events": passive_events,
    }
    if new_percent <= 0:
        refreshed = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?", (int(boss["id"]),)
        ).fetchone()
        result["failure_rewards"] = _finish_failure(conn, refreshed, when)
        result["battle_ended"] = True
    return result


def _consume_pending_boss_skip(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    when: datetime,
) -> dict | None:
    """Тратит один накопленный пропуск хода босса, если такой есть."""
    consumed = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM mini_boss_actions
            WHERE boss_id = ? AND action_type = 'boss_skip_consumed'
            """,
            (int(boss["id"]),),
        ).fetchone()[0]
    )
    queued = conn.execute(
        """
        SELECT player_id
        FROM mini_boss_actions
        WHERE boss_id = ? AND action_type = 'boss_skip_queued'
        ORDER BY id
        LIMIT 1 OFFSET ?
        """,
        (int(boss["id"]), consumed),
    ).fetchone()
    if queued is None:
        return None

    source_player_id = int(queued["player_id"])
    conn.execute(
        """
        INSERT INTO mini_boss_actions (
            boss_id, player_id, round_number, action_type,
            damage, boss_hp_after, created_at
        ) VALUES (?, ?, ?, 'boss_skip_consumed', 0, ?, ?)
        """,
        (
            int(boss["id"]),
            source_player_id,
            int(boss["current_round"]),
            int(boss["current_hp"]),
            _db_time(when),
        ),
    )
    return {
        "type": "boss_skip",
        "source_player_id": source_player_id,
        "shields": int(boss["reward_shields"]),
        "reward_percent": int(boss["reward_percent"]),
    }


def _advance_after_turn(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    when: datetime,
) -> dict:
    count = _participant_count(conn, int(boss["id"]))
    position = int(boss["current_turn_position"])

    if count <= 0:
        raise BossCombatError("У босса нет участников.")

    if position < count:
        conn.execute(
            """
            UPDATE mini_bosses
            SET current_turn_position = ?, turn_started_at = ?
            WHERE id = ?
            """,
            (position + 1, _db_time(when), int(boss["id"])),
        )
        return {"round_ended": False, "battle_ended": False}

    reward_event = _consume_pending_boss_skip(conn, boss, when)
    if reward_event is None:
        reward_event = _boss_hits_reward(conn, boss, when)
    if reward_event.get("battle_ended"):
        return {
            "round_ended": True,
            "battle_ended": True,
            "reward_event": reward_event,
        }

    conn.execute(
        """
        UPDATE mini_bosses
        SET current_round = current_round + 1,
            current_turn_position = 1,
            turn_started_at = ?
        WHERE id = ?
        """,
        (_db_time(when), int(boss["id"])),
    )
    return {
        "round_ended": True,
        "battle_ended": False,
        "reward_event": reward_event,
    }


def start_battle(
    boss_id: int,
    *,
    now: datetime | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    init_boss_db(db_path)
    sync_boss_reward_items(db_path)
    now = _coerce_utc(now)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?", (int(boss_id),)
        ).fetchone()
        if boss is None:
            conn.rollback()
            raise BossCombatError("Босс не найден.")
        if boss["status"] != "ready":
            conn.rollback()
            raise BossCombatError("Начать бой можно только после закрытия регистрации.")

        boss = _hydrate_reward_snapshot(conn, boss)
        participants = conn.execute(
            """
            SELECT
                bp.player_id,
                bp.queue_position,
                p.active_hero_id,
                h.attack AS base_attack,
                ph.stars
            FROM mini_boss_participants bp
            JOIN mini_players p ON p.id = bp.player_id
            LEFT JOIN mini_heroes h ON h.id = p.active_hero_id
            LEFT JOIN mini_player_heroes ph
              ON ph.player_id = p.id AND ph.hero_id = p.active_hero_id
            WHERE bp.boss_id = ?
            ORDER BY bp.queue_position
            """,
            (int(boss_id),),
        ).fetchall()

        if len(participants) < int(boss["min_players"]):
            conn.rollback()
            raise BossCombatError("Недостаточно участников для старта боя.")

        for row in participants:
            if row["active_hero_id"] is None or row["base_attack"] is None or row["stars"] is None:
                conn.rollback()
                raise BossCombatError(
                    "У одного из участников больше нет выбранного героя. Открой регистрацию и проверь состав."
                )
            attack = calculate_attack(int(row["base_attack"]), int(row["stars"]))
            damage_potion = consume_effect_charge(
                conn, int(row["player_id"]), EFFECT_BOSS_DAMAGE
            )
            phantom_potion = consume_effect_charge(
                conn, int(row["player_id"]), EFFECT_BOSS_PHANTOM
            )
            conn.execute(
                """
                UPDATE mini_boss_participants
                SET hero_id = ?, attack = ?, hit_count = 0, total_damage = 0,
                    skipped_turns = 0, reward_granted = 0,
                    damage_bonus_percent = ?, phantom_reward = ?
                WHERE boss_id = ? AND player_id = ?
                """,
                (
                    int(row["active_hero_id"]),
                    max(1, attack),
                    10 if damage_potion else 0,
                    1 if phantom_potion else 0,
                    int(boss_id),
                    int(row["player_id"]),
                ),
            )

        conn.execute(
            """
            UPDATE mini_bosses
            SET status = 'fighting', starts_at = ?, ended_at = NULL,
                current_round = 1, current_turn_position = 1, turn_started_at = ?,
                current_hp = max_hp, reward_percent = 100, battle_result = ''
            WHERE id = ?
            """,
            (_db_time(now), _db_time(now), int(boss_id)),
        )
        conn.commit()

    return get_combat_state(boss_id, db_path=db_path)


def list_fighting_bosses(db_path: str | Path = DB_PATH) -> list[dict]:
    init_boss_db(db_path)
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM mini_bosses
            WHERE status = 'fighting'
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_combat_state(
    boss_id: int,
    *,
    db_path: str | Path = DB_PATH,
) -> dict:
    boss = get_boss(boss_id, db_path)
    if boss is None:
        raise BossCombatError("Босс не найден.")
    participants = list_participants(boss_id, db_path)
    current = None
    if boss["status"] == "fighting":
        for row in participants:
            if int(row["queue_position"]) == int(boss["current_turn_position"]):
                current = row
                break
    return {
        "boss": boss,
        "participants": participants,
        "current": current,
        "reward_items": _reward_items(boss.get("reward_items_json")),
        "current_reward_coins": int(boss["reward_coins"])
        * max(0, min(100, int(boss.get("reward_percent", 100))))
        // 100,
    }


def force_finish_battle(
    boss_id: int,
    *,
    now: datetime | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    """Админское аварийное завершение текущего боя победой.

    Награда берётся из текущего состояния босса, включая уже уменьшенный
    процент монет, но право на неё получают все зарегистрированные участники.
    """
    init_boss_db(db_path)
    sync_boss_reward_items(db_path)
    now = _coerce_utc(now)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?",
            (int(boss_id),),
        ).fetchone()
        if boss is None:
            conn.rollback()
            raise BossCombatError("Босс не найден.")
        if boss["status"] != "fighting":
            conn.rollback()
            raise BossCombatError("Аварийно завершить можно только идущий бой.")

        old_turn_message_id = boss["turn_message_id"]
        boss = _hydrate_reward_snapshot(conn, boss)
        rewards = _finish_admin_victory(conn, boss, now)
        conn.commit()

    state = get_combat_state(boss_id, db_path=db_path)
    return {
        "applied": True,
        "battle_ended": True,
        "old_turn_message_id": (
            int(old_turn_message_id)
            if old_turn_message_id is not None
            else None
        ),
        "rewards": rewards,
        "state": state,
    }


def advance_expired_turns(
    boss_id: int,
    *,
    now: datetime | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    init_boss_db(db_path)
    now = _coerce_utc(now)
    changed = False
    skipped = []
    reward_events = []

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")

        while True:
            boss = conn.execute(
                "SELECT * FROM mini_bosses WHERE id = ?", (int(boss_id),)
            ).fetchone()
            if boss is None:
                conn.rollback()
                raise BossCombatError("Босс не найден.")
            if boss["status"] != "fighting":
                break

            started = _parse_db_time(boss["turn_started_at"])
            if started is None:
                started = now
                conn.execute(
                    "UPDATE mini_bosses SET turn_started_at = ? WHERE id = ?",
                    (_db_time(started), int(boss_id)),
                )
            deadline = started + timedelta(hours=int(boss["skip_after_hours"]))
            if now < deadline:
                break

            current = _current_participant(conn, boss)
            if current is None:
                conn.rollback()
                raise BossCombatError("Не удалось определить текущего участника.")

            conn.execute(
                """
                UPDATE mini_boss_participants
                SET skipped_turns = skipped_turns + 1
                WHERE boss_id = ? AND player_id = ?
                """,
                (int(boss_id), int(current["player_id"])),
            )
            conn.execute(
                """
                INSERT INTO mini_boss_actions (
                    boss_id, player_id, round_number, action_type, damage, boss_hp_after, created_at
                ) VALUES (?, ?, ?, 'skip', 0, ?, ?)
                """,
                (
                    int(boss_id),
                    int(current["player_id"]),
                    int(boss["current_round"]),
                    int(boss["current_hp"]),
                    _db_time(deadline),
                ),
            )
            skipped.append(dict(current))
            changed = True

            advance = _advance_after_turn(conn, boss, deadline)
            if advance.get("reward_event"):
                reward_events.append(advance["reward_event"])
            if advance.get("battle_ended"):
                break

        conn.commit()

    state = get_combat_state(boss_id, db_path=db_path)
    return {
        "changed": changed,
        "skipped": skipped,
        "reward_events": reward_events,
        "state": state,
    }


def hit_boss(
    boss_id: int,
    player_id: int,
    *,
    now: datetime | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    now = _coerce_utc(now)
    # Сначала честно пропускаем все просроченные ходы.
    timeout_result = advance_expired_turns(boss_id, now=now, db_path=db_path)
    if timeout_result["state"]["boss"]["status"] != "fighting":
        return {
            "applied": False,
            "battle_ended": True,
            "timeout_result": timeout_result,
            "state": timeout_result["state"],
        }

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        boss = conn.execute(
            "SELECT * FROM mini_bosses WHERE id = ?", (int(boss_id),)
        ).fetchone()
        if boss is None or boss["status"] != "fighting":
            conn.rollback()
            raise BossCombatError("Бой уже завершён.")

        participant = conn.execute(
            """
            SELECT
                bp.*,
                h.passive_key,
                h.passive_text,
                h.name AS hero_name
            FROM mini_boss_participants bp
            LEFT JOIN mini_heroes h ON h.id = bp.hero_id
            WHERE bp.boss_id = ? AND bp.player_id = ?
            """,
            (int(boss_id), int(player_id)),
        ).fetchone()
        if participant is None:
            conn.rollback()
            raise BossNotParticipant("Ты не участвуешь в этом бою.")

        current = _current_participant(conn, boss)
        if current is None:
            conn.rollback()
            raise BossCombatError("Не удалось определить текущий ход.")
        if int(current["player_id"]) != int(player_id):
            who = str(current["username"] or current["character_name"] or "другого игрока")
            conn.rollback()
            raise BossNotYourTurn(f"Сейчас ход {who}.")

        passive_key = str(participant["passive_key"] or "none")
        attack_resolution = resolve_attack(
            passive_key,
            base_damage=max(1, int(participant["attack"])),
            hit_number=int(participant["hit_count"]) + 1,
            boss_hp_before=int(boss["current_hp"]),
            boss_max_hp=int(boss["max_hp"]),
        )
        ability_damage = int(attack_resolution["damage"])
        damage_bonus_percent = max(0, int(participant["damage_bonus_percent"] or 0))
        damage = ability_damage
        passive_events = list(attack_resolution["events"])
        boss_skip_turns = int(attack_resolution.get("boss_skip_turns", 0))
        if damage_bonus_percent > 0:
            damage = max(1, (damage * (100 + damage_bonus_percent) + 99) // 100)
            passive_events.append(
                {
                    "type": "item_damage_boost",
                    "message": f"🧪 Зелье урона: +{damage_bonus_percent}% урона",
                }
            )
        hp_after = max(0, int(boss["current_hp"]) - damage)
        conn.execute(
            """
            UPDATE mini_boss_participants
            SET hit_count = hit_count + 1,
                total_damage = total_damage + ?
            WHERE boss_id = ? AND player_id = ?
            """,
            (damage, int(boss_id), int(player_id)),
        )
        conn.execute(
            "UPDATE mini_bosses SET current_hp = ? WHERE id = ?",
            (hp_after, int(boss_id)),
        )
        conn.execute(
            """
            INSERT INTO mini_boss_actions (
                boss_id, player_id, round_number, action_type, damage, boss_hp_after, created_at
            ) VALUES (?, ?, ?, 'attack', ?, ?, ?)
            """,
            (
                int(boss_id),
                int(player_id),
                int(boss["current_round"]),
                damage,
                hp_after,
                _db_time(now),
            ),
        )
        for _ in range(boss_skip_turns):
            conn.execute(
                """
                INSERT INTO mini_boss_actions (
                    boss_id, player_id, round_number, action_type,
                    damage, boss_hp_after, created_at
                ) VALUES (?, ?, ?, 'boss_skip_queued', 0, ?, ?)
                """,
                (
                    int(boss_id),
                    int(player_id),
                    int(boss["current_round"]),
                    hp_after,
                    _db_time(now),
                ),
            )

        reward_event = None
        rewards = None
        battle_ended = False
        bonus_shards = 0
        if hp_after <= 0:
            kill_resolution = resolve_kill(passive_key)
            passive_events.extend(kill_resolution["events"])
            bonus_shards = int(kill_resolution["bonus_shards"])
            if bonus_shards > 0:
                conn.execute(
                    "UPDATE mini_players SET shards = shards + ? WHERE id = ?",
                    (bonus_shards, int(player_id)),
                )

            refreshed = conn.execute(
                "SELECT * FROM mini_bosses WHERE id = ?", (int(boss_id),)
            ).fetchone()
            rewards = _finish_victory(conn, refreshed, now)
            battle_ended = True
        else:
            refreshed = conn.execute(
                "SELECT * FROM mini_bosses WHERE id = ?", (int(boss_id),)
            ).fetchone()
            advance = _advance_after_turn(conn, refreshed, now)
            reward_event = advance.get("reward_event")
            battle_ended = bool(advance.get("battle_ended"))
            if battle_ended and reward_event:
                rewards = reward_event.get("failure_rewards")

        conn.commit()

    state = get_combat_state(boss_id, db_path=db_path)
    return {
        "applied": True,
        "damage": damage,
        "base_damage": int(attack_resolution["base_damage"]),
        "ability_damage": ability_damage,
        "damage_bonus_percent": damage_bonus_percent,
        "passive_key": passive_key,
        "passive_name": attack_resolution["passive_name"],
        "passive_events": passive_events,
        "boss_skip_turns": boss_skip_turns,
        "bonus_shards": bonus_shards,
        "boss_hp_after": hp_after,
        "battle_ended": battle_ended,
        "reward_event": reward_event,
        "rewards": rewards,
        "timeout_result": timeout_result,
        "state": state,
    }
