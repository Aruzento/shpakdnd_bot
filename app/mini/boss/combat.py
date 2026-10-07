from app.mini.presentation import format_player_mention
from app.mini.boss.repository import (
    start_participants,
    active_loadouts,
    save_start_loadout,
    mark_battle_started,
    participant_for_hit,
    record_primary_attack,
    save_attack_events,
    queue_boss_skips,
    record_timeout_skip,
    set_turn_started,
    add_kill_shards,
)
from app.mini.boss.calculations import calculate_hit
from app.mini.boss.runtime import (
    finish_boss_death,
    apply_turn_start,
    extra_attack,
    consume_forced_skip,
    advance_after_turn,
)
from app.mini.boss.errors import BossCombatError, BossNotYourTurn, BossNotParticipant
from app.mini.boss.clock import coerce_utc, parse_db_time
from app.mini.boss.repository import (
    current_participant,
    refresh_boss,
    apply_boss_effects,
    save_hero_state,
)
from app.mini.boss.rewards import reward_items, hydrate_reward_snapshot, finish_admin_victory
from app.mini.combat import creatures, classes
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from app.config import DB_PATH
from app.mini.combat.hero_abilities import resolve_kill
from app.mini.combat.hero_abilities import engine as hero_abilities
from app.mini.boss.catalog import sync_boss_reward_items
from app.mini.boss.boss_abilities import engine as boss_abilities
from app.mini.boss.loadouts import battle_loadout
from app.mini.boss.schema import init_boss_db
from app.mini.boss.service import get_boss, list_participants
from app.mini.db import connect_mini_db
from app.mini.hero_upgrades import calculate_attack
from app.mini.items import EFFECT_BOSS_DAMAGE, EFFECT_BOSS_PHANTOM, consume_effect_charge


def start_battle(
    boss_id: int,
    *,
    now: datetime | None = None,
    db_path: str | Path = DB_PATH,
) -> dict:
    init_boss_db(db_path)
    sync_boss_reward_items(db_path)
    now = coerce_utc(now)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        boss = refresh_boss(conn, int(boss_id))
        if boss is None:
            conn.rollback()
            raise BossCombatError("Босс не найден.")
        if boss["status"] != "ready":
            conn.rollback()
            raise BossCombatError("Начать бой можно только после закрытия регистрации.")

        boss = hydrate_reward_snapshot(conn, boss)
        participants = start_participants(conn, boss_id)

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
            save_start_loadout(conn, boss_id, row, attack, damage_potion, phantom_potion)

        mark_battle_started(conn, boss_id, now)
        fresh = refresh_boss(conn, int(boss_id))
        start_events = apply_boss_effects(conn, fresh, boss_abilities.battle_start(dict(fresh)), now)
        fresh = refresh_boss(conn, int(boss_id))
        # The snapshots just persisted, rather than the live catalog, drive bonuses.
        start_events.extend(apply_boss_effects(
            conn, fresh, creatures.battle_start(dict(fresh), active_loadouts(conn, boss_id)), now))
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
        "reward_items": reward_items(boss.get("reward_items_json")),
        "current_reward_coins": classes.effective_reward(boss) if classes.enabled(boss) else int(boss["reward_coins"]) * creatures.effective_reward_percent(boss) // 100,
        "real_reward_coins": classes.real_reward(boss),
        "effective_reward_percent": creatures.effective_reward_percent(boss),
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
    now = coerce_utc(now)

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        boss = refresh_boss(conn, int(boss_id))
        if boss is None:
            conn.rollback()
            raise BossCombatError("Босс не найден.")
        if boss["status"] != "fighting":
            conn.rollback()
            raise BossCombatError("Аварийно завершить можно только идущий бой.")

        old_turn_message_id = boss["turn_message_id"]
        boss = hydrate_reward_snapshot(conn, boss)
        rewards = finish_admin_victory(conn, boss, now)
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
    now = coerce_utc(now)
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
            boss = refresh_boss(conn, int(boss_id))
            if boss is None:
                conn.rollback()
                raise BossCombatError("Босс не найден.")
            if boss["status"] != "fighting":
                break

            current = current_participant(conn, boss)
            if current is None:
                raise BossCombatError("Не удалось определить текущего участника.")
            if int(current["banished"]) or int(current["forced_skip_turns"]) > 0:
                if not int(current["banished"]):
                    forced_skip_events.append(consume_forced_skip(conn, boss, current, now))
                advance = advance_after_turn(conn, boss, now)
                forced_skip_events.extend(advance.get("forced_skip_events", []))
                reward_events.extend(advance.get("reward_events", []))
                hero_events.extend(advance.get("hero_events", []))
                rewards = advance.get("rewards")
                changed = True
                if advance.get("battle_ended"):
                    break
                continue

            automatic = apply_turn_start(conn, boss, current, now)
            if automatic["hero_events"]:
                changed = True
                hero_events.extend(automatic["hero_events"])
                rewards = automatic["rewards"]
                boss = refresh_boss(conn, int(boss_id))
                if automatic["battle_ended"]:
                    break

            started = parse_db_time(boss["turn_started_at"])
            if started is None:
                started = now
                set_turn_started(conn, boss_id, started)
            deadline = started + timedelta(hours=int(boss["skip_after_hours"]))
            if now < deadline:
                break

            current = current_participant(conn, boss)
            if current is None:
                conn.rollback()
                raise BossCombatError("Не удалось определить текущего участника.")

            record_timeout_skip(conn, boss, current, deadline)
            skipped.append(dict(current))
            changed = True

            advance = advance_after_turn(conn, boss, deadline)
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
    now = coerce_utc(now)
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
        boss = refresh_boss(conn, int(boss_id))
        if boss is None or boss["status"] != "fighting":
            conn.rollback()
            raise BossCombatError("Бой уже завершён.")

        if (
            (expected_round is not None and int(boss["current_round"]) != expected_round)
            or (expected_position is not None and int(boss["current_turn_position"]) != expected_position)
        ):
            raise BossNotYourTurn("Этот ход уже завершён. Используй актуальное сообщение.")

        participant = participant_for_hit(conn, boss_id, player_id)
        if participant is None:
            conn.rollback()
            raise BossNotParticipant("Ты не участвуешь в этом бою.")

        current = current_participant(conn, boss)
        if current is None:
            conn.rollback()
            raise BossCombatError("Не удалось определить текущий ход.")
        if int(current["player_id"]) != int(player_id):
            who = format_player_mention(current,conn=conn)
            conn.rollback()
            raise BossNotYourTurn(f"Сейчас ход {who}.")

        if int(participant["banished"]) or int(participant["forced_skip_turns"]) > 0:
            raise BossNotYourTurn("Этот участник сейчас не может ходить.")
        hero = battle_loadout(participant["hero_snapshot_json"])
        calculation = calculate_hit(dict(boss), dict(participant), hero, active_loadouts(conn,int(boss_id)))
        passive_key = calculation["passive_key"]
        attack_resolution = calculation["attack_resolution"]
        ability_damage = calculation["ability_damage"]
        damage_bonus_percent = calculation["damage_bonus_percent"]
        faction_percent = calculation["faction_percent"]
        damage_after_faction = calculation["damage_after_faction"]
        boss_resolution = calculation["boss_resolution"]
        boss_modifier_percent = calculation["boss_modifier_percent"]
        damage_before_external_bonus = calculation["damage_before_external_bonus"]
        damage = calculation["damage"]
        passive_events = calculation["passive_events"]
        boss_skip_turns = calculation["boss_skip_turns"]
        hp_after = calculation["hp_after"]
        boss_events = apply_boss_effects(conn, boss, boss_resolution, now, actor_id=int(player_id))
        record_primary_attack(conn, boss, player_id, damage, hp_after, now)
        after_attack = hero_abilities.resolve_after_attack(
            passive_key if calculation["reachable"] else "none", hit_number=int(participant["hit_count"]) + 1,
            actual_hp_damage=min(damage, int(boss["current_hp"])),
            state=hero_abilities.hero_state(participant["hero_state_json"]),
        )
        if not calculation["reachable"]:
            after_attack = {"state": hero_abilities.hero_state(participant["hero_state_json"]), "events": []}
        fresh=refresh_boss(conn,int(boss_id))
        class_plan=classes.on_hit(dict(fresh),hero,after_attack['state'],successful=damage>0)
        save_hero_state(conn,int(boss_id),int(player_id),class_plan['state'])
        boss_events.extend(apply_boss_effects(conn,fresh,class_plan,now,actor_id=int(player_id)))
        if class_plan['stolen']:
            from app.mini.wallet import change_balance_in_transaction
            action_id=conn.execute('SELECT MAX(id) FROM mini_boss_actions WHERE boss_id=? AND player_id=?',(boss_id,player_id)).fetchone()[0]
            change_balance_in_transaction(conn,int(player_id),class_plan['stolen'],'Кража награды плутом','boss',int(boss_id),f'boss:{boss_id}:sneaky:{action_id}')
        passive_events.extend(after_attack["events"])
        fresh = refresh_boss(conn, int(boss_id))
        boss_events.extend(apply_boss_effects(
            conn, fresh, creatures.on_hit(dict(fresh), hero, successful=damage > 0),
            now, actor_id=int(player_id)))
        primary_killed = hp_after <= 0
        extra_damage = 0
        if not primary_killed:
            for _ in range(attack_resolution["extra_attacks"]):
                extra = extra_attack(conn, refresh_boss(conn, int(boss_id)), participant, hero, now,
                                      attack_resolution["extra_attack_message"])
                extra_damage += extra["damage"]
                passive_events.append(extra["event"])
                boss_events.extend(extra["boss_events"])
                hp_after = int(refresh_boss(conn, int(boss_id))["current_hp"])
                if hp_after <= 0:
                    break
        save_attack_events(conn, boss_id, player_id, passive_events)
        queue_boss_skips(conn, boss, player_id, hp_after, boss_skip_turns, now)
        reward_event = None
        reward_events = []
        forced_skip_events = []
        rewards = None
        hero_events = []
        battle_ended = False
        bonus_shards = 0
        if hp_after <= 0:
            refreshed = refresh_boss(conn, int(boss_id))
            death = finish_boss_death(conn, refreshed, now, actor_id=int(player_id))
            boss_events.extend(death["events"])
            rewards = death["rewards"]
            battle_ended = death["battle_ended"]
            hp_after = int(refresh_boss(conn, int(boss_id))["current_hp"])
            if battle_ended and primary_killed:
                kill_resolution = resolve_kill(passive_key)
                passive_events.extend(kill_resolution["events"])
                bonus_shards = int(kill_resolution["bonus_shards"])
                if bonus_shards > 0:
                    add_kill_shards(conn, player_id, bonus_shards)
        if not battle_ended:
            refreshed = refresh_boss(conn, int(boss_id))
            boss_events.extend(apply_boss_effects(
                conn, refreshed, creatures.after_hero_turn(dict(refreshed), hero),
                now, actor_id=int(player_id)))
            refreshed = refresh_boss(conn, int(boss_id))
            advance = advance_after_turn(conn, refreshed, now)
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
        "hero_final_damage": calculation["hero_final_damage"],
        "reachable": calculation["reachable"],
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
        "boss_hp_after": int(state["boss"]["current_hp"]),
        "battle_ended": battle_ended,
        "reward_event": reward_event,
        "rewards": rewards,
        "timeout_result": timeout_result,
        "state": state,
    }

# Regression tests and existing administrative callers use this helper.

# Retained direct alias for the existing reward regression caller.
from app.mini.boss.rewards import finish_victory as _finish_victory
