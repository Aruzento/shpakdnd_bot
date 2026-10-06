from app.mini.wallet import change_balance_in_transaction
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import DB_PATH
from app.mini.combat.hero_abilities import (
    resolve_attack,
    resolve_boss_attack,
    resolve_kill,
)
from app.mini.combat.hero_abilities import engine as hero_abilities
from app.mini.boss.catalog import get_boss_template, sync_boss_reward_items
from app.mini.boss.boss_abilities import engine as boss_abilities
from app.mini.combat.matchups import faction_multiplier_percent, modify_damage
from app.mini.boss.loadouts import battle_loadout
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


def _reward_eligible(row: sqlite3.Row) -> bool:
    return int(row["hit_count"] or 0) > 0 or bool(int(row["phantom_reward"] or 0))


def _grant_victory_rewards(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    *,
    include_all_registered: bool = False,
    when: datetime | None = None,
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
                victory_events.append(_log_hero_event(conn, boss, row, event, when or _utcnow()))

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
    rewards = _grant_victory_rewards(conn, boss, when=when)
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
            bp.hero_snapshot_json
        FROM mini_boss_participants bp
        JOIN mini_players p ON p.id = bp.player_id
        LEFT JOIN mini_heroes h ON h.id = bp.hero_id
        WHERE bp.boss_id = ? AND bp.banished = 0
        ORDER BY bp.queue_position
        """,
        (int(boss["id"]),),
    ).fetchall()

    events: list[dict] = []
    for row in rows:
        resolution = resolve_boss_attack(str(battle_loadout(row["hero_snapshot_json"]).get("passive_key", "none")))
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

def _boss_hits_reward(
    conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime,
    *, ignore_shields: bool = False,
) -> dict:
    shields = int(boss["reward_shields"])
    reward_percent = int(boss["reward_percent"])
    decay = int(boss["reward_decay_percent"])
    passive_events = _apply_boss_attack_passives(conn, boss)

    guarded = _consume_reward_guard(conn, boss, when)
    if guarded:
        return {
            "type": "reward_guard", "shields": shields, "reward_percent": reward_percent,
            "guard_event": guarded, "passive_events": passive_events,
        }

    if shields > 0 and not ignore_shields:
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
        "shields": shields,
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



def _refresh_boss(conn: sqlite3.Connection, boss_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM mini_bosses WHERE id = ?", (boss_id,)).fetchone()


def _apply_boss_effects(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    resolution: dict,
    when: datetime,
    *,
    actor_id: int | None = None,
) -> list[dict]:
    """Persist hook effects and their structured audit events atomically."""
    allowed = {"faction", "ability_state_json", "current_hp", "reward_percent"}
    changes = resolution.get("boss_changes", {})
    if not set(changes).issubset(allowed):
        raise BossCombatError("Недопустимое изменение состояния способности босса.")
    if changes:
        assignments = ", ".join(f"{field} = ?" for field in changes)
        conn.execute(
            f"UPDATE mini_bosses SET {assignments} WHERE id = ?",
            (*changes.values(), int(boss["id"])),
        )
    for change in resolution.get("participant_changes", []):
        fields = {key: value for key, value in change.items() if key != "player_id"}
        if not fields or not set(fields).issubset({"forced_skip_turns", "banished"}):
            raise BossCombatError("Недопустимое изменение состояния участника.")
        assignments = ", ".join(f"{field} = ?" for field in fields)
        conn.execute(
            f"UPDATE mini_boss_participants SET {assignments} WHERE boss_id = ? AND player_id = ?",
            (*fields.values(), int(boss["id"]), int(change["player_id"])),
        )
    events = resolution.get("events", [])
    for raw_event in events:
        event = dict(raw_event)
        event["actor_kind"] = "boss"
        if actor_id is not None:
            event.setdefault("source_player_id", actor_id)
        conn.execute(
            """INSERT INTO mini_boss_events (
                boss_id, round_number, event_type, target_player_id,
                boss_hp_after, created_at, event_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (int(boss["id"]), int(boss["current_round"]), event["type"],
             event.get("player_id"), int(changes.get("current_hp", boss["current_hp"])),
             _db_time(when), json.dumps(event, ensure_ascii=False)),
        )
    return list(events)


def _boss_turn(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    """One boss turn: skip -> ability -> reward attack(s) -> after-turn hook."""
    skipped = _consume_pending_boss_skip(conn, boss, when)
    if skipped is not None:
        return skipped
    participants = [
        dict(row) for row in conn.execute(
            "SELECT * FROM mini_boss_participants WHERE boss_id = ? ORDER BY queue_position",
            (int(boss["id"]),),
        ).fetchall()
    ]
    resolution = boss_abilities.boss_turn(dict(boss), participants)
    events = _apply_boss_effects(conn, boss, resolution, when)
    attacks = []
    for _ in range(resolution["reward_attacks"]):
        fresh = _refresh_boss(conn, int(boss["id"]))
        if fresh["status"] != "fighting":
            break
        attack = _boss_hits_reward(
            conn, fresh, when, ignore_shields=resolution["ignore_shields"],
        )
        attacks.append(attack)
        if attack.get("battle_ended"):
            break
    fresh = _refresh_boss(conn, int(boss["id"]))
    if fresh["status"] == "fighting":
        after = boss_abilities.after_boss_turn(dict(fresh))
        events.extend(_apply_boss_effects(conn, fresh, after, when))
    # Preserve the existing reward_event contract for ordinary turns.
    result = dict(attacks[-1])
    result["attacks"] = attacks
    result["boss_events"] = events
    result["passive_events"] = [
        event for attack in attacks for event in attack.get("passive_events", [])
    ]
    return result


def _save_hero_state(conn, boss_id: int, player_id: int, state: dict) -> None:
    conn.execute(
        "UPDATE mini_boss_participants SET hero_state_json = ? WHERE boss_id = ? AND player_id = ?",
        (json.dumps(state), boss_id, player_id),
    )


def _log_hero_event(conn, boss, participant, raw: dict, when: datetime) -> dict:
    participant = dict(participant)
    event = {**raw, "actor_kind": "hero", "player_id": int(participant["player_id"]),
             "hero_name": str(participant.get("hero_name") or "герой")}
    conn.execute(
        """INSERT INTO mini_boss_events (boss_id, round_number, event_type,
           target_player_id, event_json, boss_hp_after, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (int(boss["id"]), int(boss["current_round"]), event["type"], event["player_id"],
         json.dumps(event, ensure_ascii=False), int(boss["current_hp"]), _db_time(when)),
    )
    return event


def _consume_reward_guard(conn, boss, when) -> dict | None:
    rows = conn.execute(
        """SELECT bp.*, h.name AS hero_name, p.username, p.character_name
           FROM mini_boss_participants bp JOIN mini_players p ON p.id = bp.player_id
           LEFT JOIN mini_heroes h ON h.id = bp.hero_id
           WHERE bp.boss_id = ? AND bp.banished = 0 ORDER BY bp.queue_position""",
        (int(boss["id"]),),
    ).fetchall()
    for row in rows:
        state = hero_abilities.hero_state(row["hero_state_json"])
        charges = int(state.get("reward_guard_charges", 0))
        if charges <= 0:
            continue
        state["reward_guard_charges"] = charges - 1
        _save_hero_state(conn, int(boss["id"]), int(row["player_id"]), state)
        who = row["username"] or row["character_name"] or row["hero_name"] or "Игрок"
        return _log_hero_event(conn, boss, row, {
            "type": "reward_guard_absorbed", "remaining_charges": charges - 1,
            "message": f"🛡 {who} защитил награду: атака босса полностью поглощена.",
        }, when)
    return None


def _finish_boss_death(conn, boss, when, *, actor_id=None) -> dict:
    death = boss_abilities.boss_death(dict(boss))
    events = _apply_boss_effects(conn, boss, death, when, actor_id=actor_id)
    fresh = _refresh_boss(conn, int(boss["id"]))
    rewards = (_finish_failure(conn, fresh, when) if death["destroyed_reward"]
               else _finish_victory(conn, fresh, when))
    return {"rewards": rewards, "events": events}


def _apply_turn_start(conn, boss, current, when) -> dict:
    result = {"hero_events": [], "battle_ended": False, "rewards": None}
    if current["banished"] or int(current["forced_skip_turns"]) > 0:
        return result
    resolution = hero_abilities.resolve_turn_start(state=hero_abilities.hero_state(current["hero_state_json"]))
    if not resolution["damage"]:
        return result
    damage = min(int(boss["current_hp"]), resolution["damage"])
    _save_hero_state(conn, int(boss["id"]), int(current["player_id"]), resolution["state"])
    conn.execute("UPDATE mini_bosses SET current_hp = current_hp - ? WHERE id = ?", (damage, int(boss["id"])))
    conn.execute("UPDATE mini_boss_participants SET total_damage = total_damage + ? WHERE boss_id = ? AND player_id = ?",
                 (damage, int(boss["id"]), int(current["player_id"])))
    fresh = _refresh_boss(conn, int(boss["id"]))
    event = _log_hero_event(conn, fresh, current, {
        "type": "battle_echo", "damage": damage, "stored_damage": resolution["damage"],
        "message": f"⚔️ Эхо боя {current['hero_name'] or 'героя'} наносит {damage} урона.",
    }, when)
    result["hero_events"].append(event)
    if int(fresh["current_hp"]) <= 0:
        death = _finish_boss_death(conn, fresh, when, actor_id=int(current["player_id"]))
        result.update(battle_ended=True, rewards=death["rewards"])
        result["hero_events"].extend(death["events"])
        fresh = _refresh_boss(conn, int(boss["id"]))
    # Recovery can display the automatic event even if the process stops before publishing.
    notice = json.loads(fresh["turn_notice_json"] or "{}")
    matching = (notice.get("status") == fresh["status"] and notice.get("round") == int(fresh["current_round"])
                and notice.get("position") == int(fresh["current_turn_position"]))
    text = (str(notice.get("text") or "") + "\n") if matching and notice.get("text") else ""
    text += event["message"]
    conn.execute("UPDATE mini_bosses SET turn_notice_json = ? WHERE id = ?", (
        json.dumps({"status": fresh["status"], "round": int(fresh["current_round"]),
                    "position": int(fresh["current_turn_position"]), "text": text}, ensure_ascii=False), int(boss["id"])))
    return result


def _extra_attack(conn, boss, participant, hero, when, message: str) -> dict:
    faction = faction_multiplier_percent(hero["faction"], boss["faction"])
    after_faction = modify_damage(max(1, int(participant["attack"])), faction)
    resolution = boss_abilities.modify_hero_damage(dict(boss), hero, after_faction)
    events = _apply_boss_effects(conn, boss, resolution, when, actor_id=int(participant["player_id"]))
    calculated = int(resolution["damage"])
    bonus = max(0, int(participant["damage_bonus_percent"]))
    if bonus and calculated > 0:
        calculated = max(1, (calculated * (100 + bonus) + 99) // 100)
    actual = min(int(boss["current_hp"]), calculated)
    conn.execute("UPDATE mini_bosses SET current_hp = current_hp - ? WHERE id = ?", (actual, int(boss["id"])))
    conn.execute("UPDATE mini_boss_participants SET total_damage = total_damage + ? WHERE boss_id = ? AND player_id = ?",
                 (actual, int(boss["id"]), int(participant["player_id"])))
    event = {"type": "extra_attack", "damage": actual, "calculated_damage": calculated,
             "message": message.format(damage=actual)}
    conn.execute(
        """INSERT INTO mini_boss_actions (boss_id, player_id, round_number, action_type,
           damage, boss_hp_after, created_at, event_json) VALUES (?, ?, ?, 'extra_attack', ?, ?, ?, ?)""",
        (int(boss["id"]), int(participant["player_id"]), int(boss["current_round"]), actual,
         int(boss["current_hp"]) - actual, _db_time(when), json.dumps(event, ensure_ascii=False)),
    )
    return {"damage": actual, "event": event, "boss_events": events}


def _consume_forced_skip(
    conn: sqlite3.Connection, boss: sqlite3.Row, current: sqlite3.Row, when: datetime,
) -> dict:
    player_id = int(current["player_id"])
    conn.execute(
        """UPDATE mini_boss_participants SET forced_skip_turns = forced_skip_turns - 1
           WHERE boss_id = ? AND player_id = ?""",
        (int(boss["id"]), player_id),
    )
    event = {"type": "paralysis_skip", "player_id": player_id}
    _apply_boss_effects(conn, boss, {"events": [event]}, when, actor_id=player_id)
    return event


def _advance_after_turn(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    when: datetime,
) -> dict:
    """Advance through active participants, immediately consuming forced skips."""
    reward_events = []
    forced_events = []
    hero_events = []
    round_ended = False
    while True:
        next_row = conn.execute(
            """SELECT queue_position FROM mini_boss_participants
               WHERE boss_id = ? AND banished = 0 AND queue_position > ?
               ORDER BY queue_position LIMIT 1""",
            (int(boss["id"]), int(boss["current_turn_position"])),
        ).fetchone()
        if next_row is None:
            round_ended = True
            reward_event = _boss_turn(conn, boss, when)
            reward_events.append(reward_event)
            if reward_event.get("battle_ended"):
                return {
                    "round_ended": True, "battle_ended": True,
                    "reward_event": reward_event, "reward_events": reward_events,
                    "forced_skip_events": forced_events,
                }
            next_row = conn.execute(
                """SELECT queue_position FROM mini_boss_participants
                   WHERE boss_id = ? AND banished = 0 ORDER BY queue_position LIMIT 1""",
                (int(boss["id"]),),
            ).fetchone()
            if next_row is None:
                raise BossCombatError("У босса нет активных участников.")
            conn.execute(
                "UPDATE mini_bosses SET current_round = current_round + 1 WHERE id = ?",
                (int(boss["id"]),),
            )
        conn.execute(
            """UPDATE mini_bosses SET current_turn_position = ?, turn_started_at = ?
               WHERE id = ?""",
            (int(next_row["queue_position"]), _db_time(when), int(boss["id"])),
        )
        boss = _refresh_boss(conn, int(boss["id"]))
        current = _current_participant(conn, boss)
        if int(current["forced_skip_turns"]) <= 0:
            automatic = _apply_turn_start(conn, boss, current, when)
            hero_events.extend(automatic["hero_events"])
            return {
                "round_ended": round_ended, "battle_ended": automatic["battle_ended"],
                "hero_events": hero_events, "rewards": automatic["rewards"],
                "reward_event": reward_events[-1] if reward_events else None,
                "reward_events": reward_events, "forced_skip_events": forced_events,
            }
        forced_events.append(_consume_forced_skip(conn, boss, current, when))


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
                COALESCE(bp.hero_id, p.active_hero_id) AS selected_hero_id,
                h.faction, h.damage_type, h.class_tag, h.attack_range, h.special_trait,
                h.passive_key, h.passive_text,
                h.attack AS base_attack,
                ph.stars
            FROM mini_boss_participants bp
            JOIN mini_players p ON p.id = bp.player_id
            LEFT JOIN mini_heroes h ON h.id = COALESCE(bp.hero_id, p.active_hero_id)
            LEFT JOIN mini_player_heroes ph
              ON ph.player_id = p.id AND ph.hero_id = COALESCE(bp.hero_id, p.active_hero_id)
            WHERE bp.boss_id = ?
            ORDER BY bp.queue_position
            """,
            (int(boss_id),),
        ).fetchall()

        if len(participants) < int(boss["min_players"]):
            conn.rollback()
            raise BossCombatError("Недостаточно участников для старта боя.")

        for row in participants:
            if row["selected_hero_id"] is None or row["base_attack"] is None or row["stars"] is None:
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
                    forced_skip_turns = 0, banished = 0, hero_state_json = '{}', hero_snapshot_json = ?,
                    damage_bonus_percent = ?, phantom_reward = ?
                WHERE boss_id = ? AND player_id = ?
                """,
                (
                    int(row["selected_hero_id"]),
                    max(1, attack),
                    json.dumps({field: row[field] for field in (
                        "faction", "damage_type", "class_tag", "attack_range", "special_trait",
                        "passive_key", "passive_text",
                    )}, ensure_ascii=False),
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
        fresh = _refresh_boss(conn, int(boss_id))
        start_events = _apply_boss_effects(conn, fresh, boss_abilities.battle_start(dict(fresh)), now)
        conn.commit()

    state = get_combat_state(boss_id, db_path=db_path)
    state["boss_events"] = start_events
    return state


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
    forced_skip_events = []
    hero_events = []
    rewards = None

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

            current = _current_participant(conn, boss)
            if current is None:
                raise BossCombatError("Не удалось определить текущего участника.")
            if int(current["banished"]) or int(current["forced_skip_turns"]) > 0:
                if not int(current["banished"]):
                    forced_skip_events.append(_consume_forced_skip(conn, boss, current, now))
                advance = _advance_after_turn(conn, boss, now)
                forced_skip_events.extend(advance.get("forced_skip_events", []))
                reward_events.extend(advance.get("reward_events", []))
                hero_events.extend(advance.get("hero_events", []))
                rewards = advance.get("rewards")
                changed = True
                if advance.get("battle_ended"):
                    break
                continue

            automatic = _apply_turn_start(conn, boss, current, now)
            if automatic["hero_events"]:
                changed = True
                hero_events.extend(automatic["hero_events"])
                rewards = automatic["rewards"]
                boss = _refresh_boss(conn, int(boss_id))
                if automatic["battle_ended"]:
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
            reward_events.extend(advance.get("reward_events", []))
            hero_events.extend(advance.get("hero_events", []))
            rewards = advance.get("rewards")
            forced_skip_events.extend(advance.get("forced_skip_events", []))
            if advance.get("battle_ended"):
                break

        conn.commit()

    state = get_combat_state(boss_id, db_path=db_path)
    return {
        "changed": changed,
        "hero_events": hero_events,
        "rewards": rewards,
        "skipped": skipped,
        "forced_skip_events": forced_skip_events,
        "reward_events": reward_events,
        "state": state,
    }


def hit_boss(
    boss_id: int,
    player_id: int,
    *,
    now: datetime | None = None,
    expected_round: int | None = None,
    expected_position: int | None = None,
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
            "rewards": timeout_result.get("rewards"),
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

        if (
            (expected_round is not None and int(boss["current_round"]) != expected_round)
            or (expected_position is not None and int(boss["current_turn_position"]) != expected_position)
        ):
            raise BossNotYourTurn("Этот ход уже завершён. Используй актуальное сообщение.")

        participant = conn.execute(
            """
            SELECT
                bp.*,
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

        if int(participant["banished"]) or int(participant["forced_skip_turns"]) > 0:
            raise BossNotYourTurn("Этот участник сейчас не может ходить.")
        hero = battle_loadout(participant["hero_snapshot_json"])
        passive_key = str(hero.get("passive_key", "none"))
        attack_resolution = resolve_attack(
            passive_key,
            base_damage=max(1, int(participant["attack"])),
            hit_number=int(participant["hit_count"]) + 1,
            boss_hp_before=int(boss["current_hp"]),
            boss_max_hp=int(boss["max_hp"]),
            boss_ability_key=str(boss["ability_key"]),
            boss_state=json.loads(boss["ability_state_json"] or "{}"),
        )
        ability_damage = int(attack_resolution["damage"])
        damage_bonus_percent = max(0, int(participant["damage_bonus_percent"] or 0))
        faction_percent = faction_multiplier_percent(hero["faction"], boss["faction"])
        damage_after_faction = modify_damage(ability_damage, faction_percent)
        if attack_resolution["remove_magic_shield"]:
            shield_state = json.loads(boss["ability_state_json"] or "{}")
            shield_state["shield_active"] = False
            boss_resolution = {"damage": 0, "modifier_percent": 0, "events": [],
                               "boss_changes": {"ability_state_json": json.dumps(shield_state)}}
        else:
            boss_resolution = boss_abilities.modify_hero_damage(dict(boss), hero, damage_after_faction)
        boss_events = _apply_boss_effects(
            conn, boss, boss_resolution, now, actor_id=int(player_id),
        )
        boss_modifier_percent = int(boss_resolution["modifier_percent"])
        damage_before_external_bonus = int(boss_resolution["damage"])
        damage = damage_before_external_bonus
        passive_events = list(attack_resolution["events"])
        boss_skip_turns = int(attack_resolution.get("boss_skip_turns", 0))
        if damage_bonus_percent > 0 and damage > 0:
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
        after_attack = hero_abilities.resolve_after_attack(
            passive_key, hit_number=int(participant["hit_count"]) + 1,
            actual_hp_damage=min(damage, int(boss["current_hp"])),
            state=hero_abilities.hero_state(participant["hero_state_json"]),
        )
        _save_hero_state(conn, int(boss_id), int(player_id), after_attack["state"])
        passive_events.extend(after_attack["events"])
        primary_killed = hp_after <= 0
        extra_damage = 0
        if not primary_killed:
            for _ in range(attack_resolution["extra_attacks"]):
                extra = _extra_attack(conn, _refresh_boss(conn, int(boss_id)), participant, hero, now,
                                      attack_resolution["extra_attack_message"])
                extra_damage += extra["damage"]
                passive_events.append(extra["event"])
                boss_events.extend(extra["boss_events"])
                hp_after = int(_refresh_boss(conn, int(boss_id))["current_hp"])
                if hp_after <= 0:
                    break
        conn.execute(
            "UPDATE mini_boss_actions SET event_json = ? WHERE id = (SELECT MAX(id) FROM mini_boss_actions WHERE boss_id = ? AND player_id = ? AND action_type = 'attack')",
            (json.dumps({"passive_events": passive_events}, ensure_ascii=False), int(boss_id), int(player_id)),
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
        reward_events = []
        forced_skip_events = []
        rewards = None
        hero_events = []
        battle_ended = False
        bonus_shards = 0
        if hp_after <= 0:
            kill_resolution = resolve_kill(passive_key) if primary_killed else {"events": [], "bonus_shards": 0}
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
            death = _finish_boss_death(conn, refreshed, now, actor_id=int(player_id))
            boss_events.extend(death["events"])
            rewards = death["rewards"]
            battle_ended = True
        else:
            refreshed = conn.execute(
                "SELECT * FROM mini_bosses WHERE id = ?", (int(boss_id),)
            ).fetchone()
            advance = _advance_after_turn(conn, refreshed, now)
            reward_event = advance.get("reward_event")
            reward_events = advance.get("reward_events", [])
            forced_skip_events = advance.get("forced_skip_events", [])
            hero_events = advance.get("hero_events", [])
            rewards = advance.get("rewards")
            battle_ended = bool(advance.get("battle_ended"))
            if battle_ended and reward_event and rewards is None:
                rewards = reward_event.get("failure_rewards")

        conn.commit()

    state = get_combat_state(boss_id, db_path=db_path)
    return {
        "applied": True,
        "damage": damage,
        "extra_damage": extra_damage,
        "hero_events": hero_events,
        "base_damage": int(attack_resolution["base_damage"]),
        "ability_damage": ability_damage,
        "faction_multiplier_percent": faction_percent,
        "damage_after_faction": damage_after_faction,
        "boss_modifier_percent": boss_modifier_percent,
        "damage_before_external_bonus": damage_before_external_bonus,
        "boss_events": boss_events,
        "forced_skip_events": forced_skip_events,
        "reward_events": reward_events,
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
