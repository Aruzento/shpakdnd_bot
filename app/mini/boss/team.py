"""Ordinary Boss persistence adapter for the shared V15 normal property engine."""

import json
from app.mini.combat import team
from app.mini.boss.clock import db_time


def enabled(boss):
    return int(dict(boss).get("rules_version", 0)) >= 15


def state(boss):
    return json.loads(dict(boss).get("team_state_json") or "{}")


def save(conn, boss_id, values):
    conn.execute(
        "UPDATE mini_bosses SET team_state_json=? WHERE id=?",
        (json.dumps(values), boss_id),
    )


def initialize(conn, boss, participants, now):
    if not enabled(boss):
        return []
    heroes = [json.loads(p["hero_snapshot_json"]) for p in participants]
    values = team.begin(heroes, boss["max_hp"], boss["reward_coins"])
    from app.mini.combat.arise import shared_sources

    values["arise_team_sources"] = shared_sources(
        [
            (p["player_id"], p["hero_id"], json.loads(p["hero_snapshot_json"]))
            for p in participants
        ],
        team=values,
    )
    save(conn, boss["id"], values)
    conn.execute(
        "UPDATE mini_bosses SET original_max_hp=?,original_reward_coins=? WHERE id=?",
        (boss["max_hp"], boss["reward_coins"], boss["id"]),
    )
    changes = team.start_changes(values)
    if "max_hp" in changes:
        conn.execute(
            "UPDATE mini_bosses SET max_hp=?,current_hp=? WHERE id=?",
            (changes["max_hp"], changes["current_hp"], boss["id"]),
        )
    if changes.get("extra_shields"):
        conn.execute(
            "UPDATE mini_bosses SET reward_shields=reward_shields+?,reward_shields_max=reward_shields_max+? WHERE id=?",
            (changes["extra_shields"], changes["extra_shields"], boss["id"]),
        )
    if changes.get("instant"):
        from app.mini.boss.repository import refresh_boss
        from app.mini.boss.rewards import finish_victory, finish_failure

        fresh = refresh_boss(conn, boss["id"])
        if changes["instant"] == "victory":
            finish_victory(conn, fresh, now)
        else:
            finish_failure(conn, fresh, now)
    return [
        {"type": "v15_start", "message": "✨ Свойства normal зафиксированы на бой."}
    ]


def after_hit(conn, boss, participant, hero, actual, now, *, is_extra=False):
    if not enabled(boss):
        return []
    from app.mini.boss.repository import active_loadouts, log_hero_event, refresh_boss

    result = team.after_hit(
        state(boss),
        hero,
        participant["player_id"],
        boss["current_hp"],
        boss["max_hp"],
        boss["current_round"],
        actual,
        is_extra=is_extra,
        participants=active_loadouts(conn, boss["id"]),
    )
    values = result["state"]
    if values.get("neutralized"):
        conn.execute(
            "UPDATE mini_bosses SET faction='neutral' WHERE id=?", (boss["id"],)
        )
    if result["extra_players"]:
        credits = values.setdefault("lightning_credits", {})
        for pid in result["extra_players"]:
            credits[str(pid)] = 1
    save(conn, boss["id"], values)
    conn.execute(
        "UPDATE mini_bosses SET current_hp=? WHERE id=?",
        (result["current_hp"], boss["id"]),
    )
    events = []
    for event in result["events"]:
        damage = event["actual_hp_damage"]
        conn.execute(
            "UPDATE mini_boss_participants SET total_damage=total_damage+? WHERE boss_id=? AND player_id=?",
            (damage, boss["id"], participant["player_id"]),
        )
        events.append(
            log_hero_event(
                conn,
                refresh_boss(conn, boss["id"]),
                participant,
                {
                    "type": event["kind"],
                    "damage": damage,
                    "actual_hp_damage": damage,
                    "hero": hero,
                    "message": f"✨ {event['kind']}: {damage} HP",
                },
                now,
            )
        )
    if result["boss_skip"]:
        from app.mini.boss.repository import queue_boss_skips

        queue_boss_skips(
            conn,
            boss,
            participant["player_id"],
            result["current_hp"],
            result["boss_skip"],
            now,
        )
    return events


def reward_guard(conn, boss, when):
    if not enabled(boss):
        return None
    from app.mini.boss.repository import refresh_boss, apply_boss_effects

    values = state(boss)
    plan = team.reward_attack(values, boss["current_hp"], boss["current_round"])
    save(conn, boss["id"], plan["state"])
    if not plan["blocked"]:
        return None
    if plan["hp_damage"]:
        damage = min(boss["current_hp"], plan["hp_damage"])
        conn.execute(
            "UPDATE mini_bosses SET current_hp=current_hp-? WHERE id=?",
            (damage, boss["id"]),
        )
    result = {
        "type": plan["reason"],
        "actual_hp_damage": min(boss["current_hp"], plan["hp_damage"]),
        "passive_events": [],
        "shields": boss["reward_shields"],
        "reward_percent": boss["reward_percent"],
    }
    apply_boss_effects(
        conn,
        refresh_boss(conn, boss["id"]),
        {
            "events": [
                {
                    "type": plan["reason"],
                    "actual_hp_damage": min(boss["current_hp"], plan["hp_damage"]),
                    "actual_hp_damage": min(boss["current_hp"], plan["hp_damage"]),
                    "message": (
                        "❄️ Ледяной щит разрушен."
                        if plan["reason"] == "ice_shield"
                        else "✨ Атака по награде предотвращена."
                    ),
                }
            ]
        },
        when,
    )
    if refresh_boss(conn, boss["id"])["current_hp"] == 0:
        from app.mini.boss.runtime import finish_boss_death

        death = finish_boss_death(conn, refresh_boss(conn, boss["id"]), when)
        result.update(battle_ended=death["battle_ended"], rewards=death["rewards"])
    return result


def rescue(conn, boss, when):
    if not enabled(boss):
        return False
    from app.mini.boss.repository import active_loadouts

    values = state(boss)
    if values.get("last_stand_used"):
        return False
    participants = active_loadouts(conn, boss["id"])
    owners = [
        p
        for p in participants
        if team.key(json.loads(p["hero_snapshot_json"])) == "bloodthirsty_fight"
        and not p.get("banished")
    ]
    if not owners:
        return False
    owner = min(owners, key=lambda p: p["queue_position"])
    values.update(
        last_stand_used=True,
        last_stand_active=True,
        last_stand_owner=owner["player_id"],
    )
    save(conn, boss["id"], values)
    # Reward percent 1 is an explicit live state, even for a 1-coin base.
    conn.execute(
        "UPDATE mini_bosses SET reward_percent=1,boss_damage=?,reward_corruption=0,reward_temp_hp=0 WHERE id=?",
        (
            max(
                0,
                boss["reward_coins"]
                - boss["reward_coins"] // 100
                - boss["sneaky_stolen"],
            ),
            boss["id"],
        ),
    )
    return True


def coin_reward(boss):
    from app.mini.combat import classes

    if not enabled(boss):
        return classes.real_reward(dict(boss))
    return team.coins(
        state(boss),
        boss["reward_coins"],
        stolen=boss["sneaky_stolen"],
        boss_damage=classes.permanent_damage(dict(boss)),
        shields_intact=boss["reward_shields"] == boss["reward_shields_max"],
    )
