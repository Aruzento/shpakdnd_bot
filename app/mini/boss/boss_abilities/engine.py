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
    if 'grave_seal' in state and type(state['grave_seal']) is not bool:
        raise ValueError('Invalid grave_seal state.')
    if "form" in state:
        form=state["form"]
        if not isinstance(form,dict) or set(form)!={"faction","special_trait","passive_key"}:
            raise ValueError("Invalid transformation form state.")
        if not all(isinstance(value,str) for value in form.values()):
            raise ValueError("Invalid transformation form value types.")
        from app.mini.combat.tags import is_open_tag
        from app.mini.combat.hero_abilities.catalog import configured_ability_keys
        from app.mini.combat.matchups import FACTIONS
        if form["faction"] not in FACTIONS or not is_open_tag(form["special_trait"]) or form["passive_key"] not in configured_ability_keys():
            raise ValueError("Invalid transformation form values.")
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
    from app.mini.combat.classes import can_remove_magic_shield
    magical = can_remove_magic_shield(hero)
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
    if boss["ability_key"] == "transformation" and state.get("form"):
        from app.mini.combat.hero_abilities.engine import resolve_attack
        copied=resolve_attack(state["form"]["passive_key"],base_damage=int(boss["reward_decay_percent"]),
            hit_number=state["boss_turns"],boss_hp_before=100,boss_max_hp=100,roller=roller or _roll_success)
        result["reward_attacks"] += copied["extra_attacks"]
        result["copied_decay_percent"]=min(100,copied["damage"])
        extra_percent=copied.get('extra_attack_percent',100)
        result['attack_decay_percents']=[result['copied_decay_percent']]+[
            int(boss['reward_decay_percent'])*extra_percent//100
        ]*copied['extra_attacks']
        if copied["boss_skip_turns"] and active:
            target=(chooser or _choose_participant)(active)
            result["participant_changes"].append({"player_id":target["player_id"],
                "forced_skip_turns":int(target.get("forced_skip_turns",0))+copied["boss_skip_turns"]})
        result["events"].extend(copied["events"])
    hook = _TURN_HOOKS.get(boss["ability_key"])
    if hook and boss["ability_key"] != "training" and state.pop("grave_seal", False):
        hook = None
        result["events"].append(_event("grave_seal", message="⚰️ Гробовая печать подавила активную способность."))
    if hook and not (attack_missed and boss["ability_key"] in {"critical_strike", "rapier"}):
        hook(boss, active, state, config, roller or _roll_success, chooser or _choose_participant, result)
    result.setdefault("boss_changes", {}).update(ability_state_json=json.dumps(state))
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


def after_boss_turn(boss: dict, participants=None, *, chooser=None) -> dict:
    config = _config(boss)
    state = _state(boss)
    if state.get("grave_seal", False) and boss["ability_key"] in {*_AFTER_TURN_HOOKS, "transformation"}:
        state.pop("grave_seal")
        return {"boss_changes": {"ability_state_json": json.dumps(state)},
                "events": [_event("grave_seal", message="⚰️ Гробовая печать подавила активную способность.")]}
    if boss["ability_key"] == "transformation":
        result = {"boss_changes": {}, "events": []}
        _transformation_turn(boss, participants or [], state, config, _roll_success,
                             chooser or _choose_participant, result)
        result["boss_changes"]["ability_state_json"] = json.dumps(state)
        return result
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


def final_hero_damage(boss, hero, damage, *, base_attack, raw_damage=None):
    """Final HP overrides: true zero and healing never pass a min-one helper."""
    key = boss.get("ability_key", "none")
    value = max(0, int(damage))
    raw = value if raw_damage is None else max(0, int(raw_damage))
    healed = 0
    events = []
    if key == "collapse":
        rarity = hero.get("rarity", "common")
        if rarity == "common": value = raw + 100
        elif rarity == "uncommon": value = raw + 50
        elif rarity == "rare": value = raw * 25 // 100
        elif rarity == "legendary": value = raw // 100
        elif rarity == "shadow": value = 0
        elif rarity == "mythic":
            value = 0
            healed = min(raw, max(0, int(boss["max_hp"])-int(boss["current_hp"])))
            events.append(_event("collapse_heal", healed_hp=healed, message=f"💚 Инверсия восстановила {healed} HP."))
    elif key == "waste_of_time" and hero.get("rarity") in {"rare", "legendary", "mythic"}:
        value = 0
    elif key == "training":
        value = 1
    elif key == "simple":
        value = raw
    return {"damage": value, "healed_hp": healed, "boss_changes": {}, "events": events}


def _training_turn(boss, participants, state, config, roller, chooser, result):
    result["reward_attacks"] = 0


def _transformation_turn(boss, participants, state, config, roller, chooser, result):
    """The new form replaces the old; only an actual attacker can supply it."""
    attacked = [p for p in participants if int(p.get("hit_count",0)) > 0]
    if not attacked:
        return
    chosen = chooser(attacked)
    hero = json.loads(chosen["hero_snapshot_json"])
    form = {field: hero.get(field, default) for field, default in (
        ("faction","commoners"), ("special_trait","none"), ("passive_key","none"))}
    state["form"] = form
    result.setdefault("boss_changes", {}).update(faction=form["faction"])
    result["events"].append(_event("transformation", form=form, message="🎭 Дуппельгангер принял новую форму."))


_TURN_HOOKS.update(training=_training_turn)
