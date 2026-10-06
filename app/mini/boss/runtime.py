from app.mini.boss.errors import BossCombatError
from app.mini.boss.clock import db_time
from app.mini.boss.repository import (
    current_participant,
    consume_pending_boss_skip,
    refresh_boss,
    apply_boss_effects,
    save_hero_state,
    log_hero_event,
)
from app.mini.boss.rewards import finish_victory, finish_failure
import json
import sqlite3
from datetime import datetime
from app.mini.combat.hero_abilities import resolve_boss_attack
from app.mini.combat.hero_abilities import engine as hero_abilities
from app.mini.boss.boss_abilities import engine as boss_abilities
from app.mini.combat.matchups import faction_multiplier_percent, modify_damage
from app.mini.boss.loadouts import battle_loadout


def apply_boss_attack_passives(
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


def boss_hits_reward(
    conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime,
    *, ignore_shields: bool = False,
) -> dict:
    shields = int(boss["reward_shields"])
    reward_percent = int(boss["reward_percent"])
    decay = int(boss["reward_decay_percent"])
    passive_events = apply_boss_attack_passives(conn, boss)

    guarded = consume_reward_guard(conn, boss, when)
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
        result["failure_rewards"] = finish_failure(conn, refreshed, when)
        result["battle_ended"] = True
    return result


def boss_turn(conn: sqlite3.Connection, boss: sqlite3.Row, when: datetime) -> dict:
    """One boss turn: skip -> ability -> reward attack(s) -> after-turn hook."""
    skipped = consume_pending_boss_skip(conn, boss, when)
    if skipped is not None:
        return skipped
    participants = [
        dict(row) for row in conn.execute(
            "SELECT * FROM mini_boss_participants WHERE boss_id = ? ORDER BY queue_position",
            (int(boss["id"]),),
        ).fetchall()
    ]
    resolution = boss_abilities.boss_turn(dict(boss), participants)
    events = apply_boss_effects(conn, boss, resolution, when)
    attacks = []
    for _ in range(resolution["reward_attacks"]):
        fresh = refresh_boss(conn, int(boss["id"]))
        if fresh["status"] != "fighting":
            break
        attack = boss_hits_reward(
            conn, fresh, when, ignore_shields=resolution["ignore_shields"],
        )
        attacks.append(attack)
        if attack.get("battle_ended"):
            break
    fresh = refresh_boss(conn, int(boss["id"]))
    if fresh["status"] == "fighting":
        after = boss_abilities.after_boss_turn(dict(fresh))
        events.extend(apply_boss_effects(conn, fresh, after, when))
    # Preserve the existing reward_event contract for ordinary turns.
    result = dict(attacks[-1])
    result["attacks"] = attacks
    result["boss_events"] = events
    result["passive_events"] = [
        event for attack in attacks for event in attack.get("passive_events", [])
    ]
    return result


def consume_reward_guard(conn, boss, when) -> dict | None:
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
        save_hero_state(conn, int(boss["id"]), int(row["player_id"]), state)
        who = row["username"] or row["character_name"] or row["hero_name"] or "Игрок"
        return log_hero_event(conn, boss, row, {
            "type": "reward_guard_absorbed", "remaining_charges": charges - 1,
            "message": f"🛡 {who} защитил награду: атака босса полностью поглощена.",
        }, when)
    return None


def finish_boss_death(conn, boss, when, *, actor_id=None) -> dict:
    death = boss_abilities.boss_death(dict(boss))
    events = apply_boss_effects(conn, boss, death, when, actor_id=actor_id)
    fresh = refresh_boss(conn, int(boss["id"]))
    rewards = (finish_failure(conn, fresh, when) if death["destroyed_reward"]
               else finish_victory(conn, fresh, when))
    return {"rewards": rewards, "events": events}


def apply_turn_start(conn, boss, current, when) -> dict:
    result = {"hero_events": [], "battle_ended": False, "rewards": None}
    if current["banished"] or int(current["forced_skip_turns"]) > 0:
        return result
    resolution = hero_abilities.resolve_turn_start(state=hero_abilities.hero_state(current["hero_state_json"]))
    if not resolution["damage"]:
        return result
    damage = min(int(boss["current_hp"]), resolution["damage"])
    save_hero_state(conn, int(boss["id"]), int(current["player_id"]), resolution["state"])
    conn.execute("UPDATE mini_bosses SET current_hp = current_hp - ? WHERE id = ?", (damage, int(boss["id"])))
    conn.execute("UPDATE mini_boss_participants SET total_damage = total_damage + ? WHERE boss_id = ? AND player_id = ?",
                 (damage, int(boss["id"]), int(current["player_id"])))
    fresh = refresh_boss(conn, int(boss["id"]))
    event = log_hero_event(conn, fresh, current, {
        "type": "battle_echo", "damage": damage, "stored_damage": resolution["damage"],
        "message": f"⚔️ Эхо боя {current['hero_name'] or 'героя'} наносит {damage} урона.",
    }, when)
    result["hero_events"].append(event)
    if int(fresh["current_hp"]) <= 0:
        death = finish_boss_death(conn, fresh, when, actor_id=int(current["player_id"]))
        result.update(battle_ended=True, rewards=death["rewards"])
        result["hero_events"].extend(death["events"])
        fresh = refresh_boss(conn, int(boss["id"]))
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


def extra_attack(conn, boss, participant, hero, when, message: str) -> dict:
    faction = faction_multiplier_percent(hero["faction"], boss["faction"])
    after_faction = modify_damage(max(1, int(participant["attack"])), faction)
    resolution = boss_abilities.modify_hero_damage(dict(boss), hero, after_faction)
    events = apply_boss_effects(conn, boss, resolution, when, actor_id=int(participant["player_id"]))
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
         int(boss["current_hp"]) - actual, db_time(when), json.dumps(event, ensure_ascii=False)),
    )
    return {"damage": actual, "event": event, "boss_events": events}


def consume_forced_skip(
    conn: sqlite3.Connection, boss: sqlite3.Row, current: sqlite3.Row, when: datetime,
) -> dict:
    player_id = int(current["player_id"])
    conn.execute(
        """UPDATE mini_boss_participants SET forced_skip_turns = forced_skip_turns - 1
           WHERE boss_id = ? AND player_id = ?""",
        (int(boss["id"]), player_id),
    )
    event = {"type": "paralysis_skip", "player_id": player_id}
    apply_boss_effects(conn, boss, {"events": [event]}, when, actor_id=player_id)
    return event


def advance_after_turn(
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
            reward_event = boss_turn(conn, boss, when)
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
            (int(next_row["queue_position"]), db_time(when), int(boss["id"])),
        )
        boss = refresh_boss(conn, int(boss["id"]))
        current = current_participant(conn, boss)
        if int(current["forced_skip_turns"]) <= 0:
            automatic = apply_turn_start(conn, boss, current, when)
            hero_events.extend(automatic["hero_events"])
            return {
                "round_ended": round_ended, "battle_ended": automatic["battle_ended"],
                "hero_events": hero_events, "rewards": automatic["rewards"],
                "reward_event": reward_events[-1] if reward_events else None,
                "reward_events": reward_events, "forced_skip_events": forced_events,
            }
        forced_events.append(consume_forced_skip(conn, boss, current, when))

