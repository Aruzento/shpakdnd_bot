from app.mini.boss.clock import db_time
import json
import sqlite3
from datetime import datetime
from app.mini.boss.errors import BossCombatError

def current_participant(conn: sqlite3.Connection, boss: sqlite3.Row) -> sqlite3.Row | None:
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


def consume_pending_boss_skip(
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
            db_time(when),
        ),
    )
    return {
        "type": "boss_skip",
        "source_player_id": source_player_id,
        "shields": int(boss["reward_shields"]),
        "reward_percent": int(boss["reward_percent"]),
    }


def refresh_boss(conn: sqlite3.Connection, boss_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM mini_bosses WHERE id = ?", (boss_id,)).fetchone()


def apply_boss_effects(
    conn: sqlite3.Connection,
    boss: sqlite3.Row,
    resolution: dict,
    when: datetime,
    *,
    actor_id: int | None = None,
) -> list[dict]:
    """Persist hook effects and their structured audit events atomically."""
    allowed = {"faction", "ability_state_json", "current_hp", "reward_percent",
               "feature_state_json", "reward_temp_hp", "reward_corruption",
               "reward_shields", "reward_shields_max"}
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
             db_time(when), json.dumps(event, ensure_ascii=False)),
        )
    return list(events)


def save_hero_state(conn, boss_id: int, player_id: int, state: dict) -> None:
    conn.execute(
        "UPDATE mini_boss_participants SET hero_state_json = ? WHERE boss_id = ? AND player_id = ?",
        (json.dumps(state), boss_id, player_id),
    )


def log_hero_event(conn, boss, participant, raw: dict, when: datetime) -> dict:
    participant = dict(participant)
    event = {**raw, "actor_kind": "hero", "player_id": int(participant["player_id"]),
             "hero_name": str(participant.get("hero_name") or "герой")}
    conn.execute(
        """INSERT INTO mini_boss_events (boss_id, round_number, event_type,
           target_player_id, event_json, boss_hp_after, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (int(boss["id"]), int(boss["current_round"]), event["type"], event["player_id"],
         json.dumps(event, ensure_ascii=False), int(boss["current_hp"]), db_time(when)),
    )
    return event


def start_participants(conn, boss_id: int):
    return conn.execute(
        """
        SELECT
            bp.player_id,
            bp.queue_position,
            p.active_hero_id AS selected_hero_id,
            h.faction, h.damage_type, h.class_tag, h.attack_range, h.special_trait,
            h.passive_key, h.passive_text,
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


def save_start_loadout(conn, boss_id, row, attack, damage_potion, phantom_potion):
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


def mark_battle_started(conn, boss_id, now):
    conn.execute(
        """
        UPDATE mini_bosses
        SET status = 'fighting', starts_at = ?, ended_at = NULL,
            current_round = 1, current_turn_position = 1, turn_started_at = ?,
            current_hp = max_hp, reward_percent = 100, battle_result = '',
            feature_state_json = '{}', reward_temp_hp = 0, reward_corruption = 0
        WHERE id = ?
        """,
        (db_time(now), db_time(now), int(boss_id)),
    )


def participant_for_hit(conn, boss_id, player_id):
    return conn.execute(
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


def record_primary_attack(conn, boss, player_id, damage, hp_after, now):
    boss_id = int(boss["id"])
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
            db_time(now),
        ),
    )


def save_attack_events(conn, boss_id, player_id, passive_events):
    conn.execute(
        "UPDATE mini_boss_actions SET event_json = ? WHERE id = (SELECT MAX(id) FROM mini_boss_actions WHERE boss_id = ? AND player_id = ? AND action_type = 'attack')",
        (json.dumps({"passive_events": passive_events}, ensure_ascii=False), int(boss_id), int(player_id)),
    )


def queue_boss_skips(conn, boss, player_id, hp_after, boss_skip_turns, now):
    boss_id = int(boss["id"])
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
                db_time(now),
            ),
        )


def record_timeout_skip(conn, boss, current, deadline):
    boss_id = int(boss["id"])
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
            db_time(deadline),
        ),
    )


def set_turn_started(conn, boss_id, started):
    conn.execute("UPDATE mini_bosses SET turn_started_at = ? WHERE id = ?", (db_time(started), int(boss_id)))


def add_kill_shards(conn, player_id, amount):
    conn.execute("UPDATE mini_players SET shards = shards + ? WHERE id = ?", (amount, int(player_id)))


def active_loadouts(conn, boss_id: int) -> list[dict]:
    """Current participants: excludes banished, retains temporarily skipped."""
    return [dict(row) for row in conn.execute(
        "SELECT * FROM mini_boss_participants WHERE boss_id = ? AND banished = 0 ORDER BY queue_position",
        (boss_id,),
    ).fetchall()]
