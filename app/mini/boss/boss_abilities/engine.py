from app.mini.combat.tags import MAGIC_CLASSES, TECHNICAL_CLASS
"""Pure hooks return effects; combat persists them in the current transaction."""
import json
import secrets

from app.mini.boss.boss_abilities.catalog import get_ability, validate_config
from app.mini.combat.matchups import modify_damage


def _roll_success(chance_percent: int) -> bool:
    return secrets.randbelow(100) < chance_percent


def _choose_participant(participants: list[dict]) -> dict:
    return secrets.choice(participants)


def _state(boss: dict) -> dict:
    state = json.loads(boss.get("ability_state_json") or "{}")
    if not isinstance(state, dict):
        raise ValueError("Boss ability_state_json must be an object.")
    return state


def _config(boss: dict) -> dict:
    key = boss.get("ability_key", "none")
    config = get_ability(key)
    overrides = json.loads(boss.get("ability_config_json") or "{}")
    validate_config(key, overrides)
    config.update(overrides)
    return config


def _event(kind: str, **values) -> dict:
    return {"type": kind, **values}


def _shapeshifter_start(boss, state, config, roller):
    if roller(config["chance_percent"]):
        return {"faction": config["target_faction"]}, [
            _event("shapeshifter", faction=config["target_faction"])
        ]
    return {}, [_event("shapeshifter_unchanged", faction=boss["faction"])]


def _magic_shield_start(boss, state, config, roller):
    state["shield_active"] = True
    return {}, [_event("magic_shield_activated")]


_START_HOOKS = {
    "shapeshifter": _shapeshifter_start, "magic_shield": _magic_shield_start,
}


def battle_start(boss: dict, *, roller=None) -> dict:
    state = {"boss_turns": 0}
    config = _config(boss)
    hook = _START_HOOKS.get(boss["ability_key"])
    changes, events = hook(boss, state, config, roller or _roll_success) if hook else ({}, [])
    changes["ability_state_json"] = json.dumps(state)
    return {"boss_changes": changes, "events": events}


def _magic_shield_damage(boss, hero, damage, state, config):
    if not state.get("shield_active", False):
        return damage, 100, []
    # mage is the live catalog tag; magical remains valid for older loadouts.
    magical = hero.get("class_tag") in MAGIC_CLASSES
    if magical:
        state["shield_active"] = False
    return 0, 0, [_event("magic_shield_removed" if magical else "magic_shield_blocked")]


def _mechanism_damage(boss, hero, damage, state, config):
    percent = 100 if hero.get("class_tag") == TECHNICAL_CLASS else config["damage_percent"]
    return modify_damage(damage, percent), percent, []


_DAMAGE_HOOKS = {
    "magic_shield": _magic_shield_damage, "mechanism": _mechanism_damage,
}


def modify_hero_damage(boss: dict, hero: dict, damage: int) -> dict:
    state = _state(boss)
    config = _config(boss)
    hook = _DAMAGE_HOOKS.get(boss["ability_key"])
    value, percent, events = hook(boss, hero, damage, state, config) if hook else (damage, 100, [])
    return {
        "damage": value, "modifier_percent": percent, "events": events,
        "boss_changes": {"ability_state_json": json.dumps(state)},
    }


def _paralysis_turn(boss, participants, state, config, roller, chooser, result):
    if participants:
        target = chooser(participants)
        result["participant_changes"].append({
            "player_id": target["player_id"],
            "forced_skip_turns": int(target.get("forced_skip_turns", 0)) + 1,
        })
        result["events"].append(_event("paralysis", player_id=target["player_id"]))


def _critical_turn(boss, participants, state, config, roller, chooser, result):
    if roller(config["chance_percent"]):
        result["reward_attacks"] = 2
        result["events"].append(_event("critical_strike"))


def _banishment_turn(boss, participants, state, config, roller, chooser, result):
    if state["boss_turns"] % config["every"] != 0:
        return
    if len(participants) <= 1:
        result["events"].append(_event("banishment_no_target"))
    elif roller(config["chance_percent"]):
        target = chooser(participants)
        result["participant_changes"].append({"player_id": target["player_id"], "banished": 1})
        result["events"].append(_event("banishment", player_id=target["player_id"]))


def _rapier_turn(boss, participants, state, config, roller, chooser, result):
    if roller(config["chance_percent"]):
        result["ignore_shields"] = True
        result["events"].append(_event("rapier"))


_TURN_HOOKS = {
    "paralysis": _paralysis_turn, "critical_strike": _critical_turn,
    "banishment": _banishment_turn, "rapier": _rapier_turn,
}


def boss_turn(boss: dict, participants: list[dict], *, roller=None, chooser=None, attack_missed=False) -> dict:
    state = _state(boss)
    state["boss_turns"] = int(state.get("boss_turns", 0)) + 1
    config = _config(boss)
    active = [p for p in participants if not p.get("banished", 0)]
    result = {
        "reward_attacks": 1, "ignore_shields": False,
        "participant_changes": [], "events": [],
    }
    hook = _TURN_HOOKS.get(boss["ability_key"])
    if hook and not (attack_missed and boss["ability_key"] in {"critical_strike", "rapier"}):
        hook(boss, active, state, config, roller or _roll_success, chooser or _choose_participant, result)
    result["boss_changes"] = {"ability_state_json": json.dumps(state)}
    return result


def _hydra_after_turn(boss, config):
    if boss["status"] != "fighting" or int(boss["current_hp"]) <= 0:
        return {"boss_changes": {}, "events": []}
    old_hp = int(boss["current_hp"])
    hp = min(int(boss["max_hp"]), old_hp + int(boss["max_hp"]) * config["heal_percent"] // 100)
    return {
        "boss_changes": {"current_hp": hp},
        "events": [_event("hydra_regeneration", healed_hp=hp - old_hp)],
    }


_AFTER_TURN_HOOKS = {"hydra_regeneration": _hydra_after_turn}


def after_boss_turn(boss: dict) -> dict:
    config = _config(boss)
    hook = _AFTER_TURN_HOOKS.get(boss["ability_key"])
    return hook(boss, config) if hook else {"boss_changes": {}, "events": []}


def _kamikaze_death(boss):
    destroyed = int(boss["reward_shields"]) == 0
    return {
        "destroyed_reward": destroyed,
        "boss_changes": {"reward_percent": 0} if destroyed else {},
        "events": [_event("kamikaze_destroyed_reward" if destroyed else "kamikaze_absorbed")],
    }


_DEATH_HOOKS = {"kamikaze": _kamikaze_death}


def boss_death(boss: dict) -> dict:
    _config(boss)
    hook = _DEATH_HOOKS.get(boss["ability_key"])
    return hook(boss) if hook else {
        "destroyed_reward": False, "boss_changes": {}, "events": [],
    }
