import json
import secrets
from collections.abc import Callable

from app.mini.combat.hero_abilities.catalog import get_ability


Roller = Callable[[float], bool]


def _roll_success(chance_percent: float) -> bool:
    chance = max(0.0, min(100.0, float(chance_percent)))
    # 0.01% resolution.
    return secrets.randbelow(10_000) < int(round(chance * 100))


def _multiply_damage(damage: int, multiplier_percent: int) -> int:
    return max(1, int(damage) * int(multiplier_percent) // 100)


def _event(effect: dict) -> dict:
    return {
        "type": str(effect.get("type", "")),
        "message": str(effect.get("message", "")).strip(),
    }


def resolve_attack(
    passive_key: str,
    *,
    base_damage: int,
    hit_number: int,
    boss_hp_before: int,
    boss_max_hp: int,
    boss_ability_key: str = "none",
    boss_state: dict | None = None,
    roller: Roller | None = None,
    base_attack: int | None = None,
    state: dict | None = None,
) -> dict:
    """Применяет attack-эффекты пассивки и возвращает итоговый урон."""
    ability = get_ability(passive_key)
    damage = max(1, int(base_damage))
    hit_number = max(1, int(hit_number))
    hp_before = max(0, int(boss_hp_before))
    max_hp = max(1, int(boss_max_hp))
    roller = roller or _roll_success
    events: list[dict] = []
    state = dict(state or {})
    damage_mode = "normal"
    extra_uses_primary_base = False
    boss_skip_turns = 0
    remove_magic_shield = False
    extra_attacks = 0
    extra_attack_percent = 100
    extra_attack_message = "💥 Дополнительная атака — {damage} урона"

    for effect in ability["effects"]:
        if effect.get("trigger") != "attack":
            continue

        effect_type = str(effect.get("type", ""))
        triggered = False

        if effect_type == "every_n_pure_base_hit":
            if hit_number % int(effect["every"]) == 0:
                damage = max(0, int(base_damage if base_attack is None else base_attack)) * int(effect["multiplier_percent"]) // 100
                damage_mode = "fixed"
                events.append(_event(effect))
            continue
        if effect_type == "every_n_zero_then_double_hit":
            if hit_number % int(effect["every"]) == 0:
                damage = 0
                damage_mode = "fixed"
                state["shadow_pending_double"] = True
                events.append(_event(effect))
            elif state.pop("shadow_pending_double", False):
                extra_attacks += 1
                extra_uses_primary_base = True
            continue
        if effect_type == "pure_primary_hit":
            damage_mode = "incoming_pure"
            continue

        if effect_type == "chance_remove_magic_shield":
            if (boss_ability_key == "magic_shield" and (boss_state or {}).get("shield_active")
                    and roller(float(effect["chance_percent"]))):
                remove_magic_shield = True
                events.append(_event(effect))
            continue

        if effect_type == "every_n_max_hp_damage":
            if hit_number % int(effect["every"]) == 0:
                damage += max_hp * int(effect["damage_percent"]) // 100
                events.append(_event(effect))
            continue
        if effect_type == "every_n_extra_hits":
            if hit_number % int(effect["every"]) == 0:
                extra_attacks += int(effect["extra_attacks"])
                extra_attack_percent = int(effect["damage_percent"])
                events.append(_event(effect))
            continue
        if effect_type == "cyclic_damage_bonus":
            every = int(effect["every"])
            step = (hit_number - 1) % every
            bonus = step * int(effect["step_percent"])
            damage = _multiply_damage(damage, 100 + bonus)
            if bonus:
                event = _event(effect)
                event.update(bonus_percent=bonus, message=event["message"].format(bonus_percent=bonus))
                events.append(event)
            if step == every - 1:
                extra_attacks += int(effect["extra_attacks"])
                extra_attack_message = str(effect.get("extra_message", extra_attack_message))
            continue

        if effect_type == "chance_damage_multiplier":
            triggered = roller(float(effect.get("chance_percent", 0)))

        elif effect_type == "every_n_damage_multiplier":
            every = max(1, int(effect.get("every", 1)))
            triggered = hit_number % every == 0

        elif effect_type == "first_hit_damage_multiplier":
            triggered = hit_number == 1

        elif effect_type == "after_first_damage_multiplier":
            if hit_number == 1:
                first_message = str(effect.get("first_hit_message", "")).strip()
                if first_message:
                    events.append(
                        {
                            "type": "mark_applied",
                            "message": first_message,
                        }
                    )
            triggered = hit_number > 1

        elif effect_type == "boss_hp_below_damage_multiplier":
            threshold = float(effect.get("threshold_percent", 0))
            hp_percent = hp_before * 100.0 / max_hp
            if bool(effect.get("strict_below", False)):
                triggered = hp_percent < threshold
            else:
                triggered = hp_percent <= threshold

        elif effect_type == "every_n_boss_skip":
            every = max(1, int(effect.get("every", 1)))
            if hit_number % every == 0:
                turns = max(1, int(effect.get("turns", 1)))
                boss_skip_turns += turns
                event = _event(effect)
                event["turns"] = turns
                events.append(event)
            # Эта пассивка не меняет урон.
            continue

        if triggered:
            damage = _multiply_damage(
                damage,
                int(effect.get("multiplier_percent", 100)),
            )
            events.append(_event(effect))

    return {
        "passive_key": ability["key"],
        "passive_name": ability["name"],
        "configured": bool(ability["configured"]),
        "base_damage": max(1, int(base_damage)),
        "damage": damage,
        "boss_skip_turns": boss_skip_turns,
        "remove_magic_shield": remove_magic_shield,
        "extra_attacks": extra_attacks,
        "extra_attack_percent": extra_attack_percent,
        "extra_attack_message": extra_attack_message,
        "damage_mode": damage_mode,
        "hero_state": state,
        "extra_uses_primary_base": extra_uses_primary_base,
        "events": events,
    }



def resolve_boss_attack(
    passive_key: str,
    *,
    roller: Roller | None = None,
) -> dict:
    """Применяет пассивки, срабатывающие при реальной атаке босса."""
    ability = get_ability(passive_key)
    roller = roller or _roll_success
    events: list[dict] = []
    bonus_shards = 0

    for effect in ability["effects"]:
        if effect.get("trigger") != "boss_attack":
            continue

        effect_type = str(effect.get("type", ""))
        if effect_type == "chance_shards":
            if roller(float(effect.get("chance_percent", 0))):
                shards = max(0, int(effect.get("shards", 0)))
                bonus_shards += shards
                event = _event(effect)
                event["shards"] = shards
                events.append(event)

    return {
        "passive_key": ability["key"],
        "passive_name": ability["name"],
        "configured": bool(ability["configured"]),
        "bonus_shards": bonus_shards,
        "events": events,
    }

def resolve_kill(
    passive_key: str,
    *,
    roller: Roller | None = None,
) -> dict:
    """Применяет эффекты, которые срабатывают только на смертельном ударе."""
    ability = get_ability(passive_key)
    roller = roller or _roll_success
    events: list[dict] = []
    bonus_shards = 0

    for effect in ability["effects"]:
        if effect.get("trigger") != "kill":
            continue

        effect_type = str(effect.get("type", ""))
        if effect_type == "kill_chance_shards":
            if roller(float(effect.get("chance_percent", 0))):
                shards = max(0, int(effect.get("shards", 0)))
                bonus_shards += shards
                event = _event(effect)
                event["shards"] = shards
                events.append(event)

    return {
        "passive_key": ability["key"],
        "passive_name": ability["name"],
        "configured": bool(ability["configured"]),
        "bonus_shards": bonus_shards,
        "events": events,
    }


def hero_state(raw: str) -> dict:
    state = json.loads(raw or "{}")
    if not isinstance(state, dict):
        raise ValueError("Hero runtime state must be an object.")
    for field in ('bone_decoy','grave_seal','last_watch_used','shadow_pending_double'):
        if field in state and type(state[field]) is not bool:
            raise ValueError(f'Invalid hero state: {field}.')
    if "shadow_form" in state:
        from app.mini.combat.tags import CREATURE_TRAITS, FACTIONS
        form = state["shadow_form"]
        if (not isinstance(form, dict) or set(form) != {"faction", "special_trait"}
                or not isinstance(form["faction"], str) or form["faction"] not in FACTIONS
                or not isinstance(form["special_trait"], str) or form["special_trait"] not in CREATURE_TRAITS):
            raise ValueError("Invalid shadow_form state.")
    return state


def resolve_after_attack(passive_key: str, *, hit_number: int, actual_hp_damage: int, state: dict) -> dict:
    """Only ordinary hits create effects; counters use persisted hit_count."""
    state = dict(state)
    events = []
    for effect in get_ability(passive_key)["effects"]:
        if effect.get("trigger") != "after_attack" or hit_number % int(effect["every"]):
            continue
        kind = effect["type"]
        event = _event(effect)
        if kind == "every_n_decoy":
            state["bone_decoy"] = True
        elif kind == "every_n_seal":
            state["grave_seal"] = True
        elif kind == "every_n_reward_guard":
            charges = int(effect["charges"])
            state["reward_guard_charges"] = int(state.get("reward_guard_charges", 0)) + charges
            event["charges"] = charges
        elif kind == "every_n_echo" and actual_hp_damage > 0:
            damage = max(1, actual_hp_damage * int(effect["damage_percent"]) // 100)
            state["pending_echo_damage"] = damage
            event.update(damage=damage, message=event["message"].format(damage=damage))
        else:
            continue
        events.append(event)
    return {"state": state, "events": events}


def resolve_turn_start(*, state: dict) -> dict:
    """Consume stored damage verbatim, without any attack/passive pipeline."""
    state = dict(state)
    damage = max(0, int(state.get("pending_echo_damage", 0)))
    if damage:
        state.pop("pending_echo_damage")
    return {"state": state, "damage": damage}


def resolve_victory(passive_key: str, *, roller: Roller | None = None) -> dict:
    roller = roller or _roll_success
    events = []
    shards = 0
    for effect in get_ability(passive_key)["effects"]:
        if effect.get("trigger") == "victory" and effect["type"] == "chance_shards":
            if roller(float(effect["chance_percent"])):
                amount = int(effect["shards"])
                shards += amount
                events.append({**_event(effect), "shards": amount})
    return {"bonus_shards": shards, "events": events}


def resolve_reward_defense(passive_key, *, state, shields, roller=None):
    """Attempt one decoy/save; failed Last Watch attempts do not consume it."""
    state = dict(state)
    roll = roller or _roll_success
    blocked = False
    kind = None
    if state.pop("bone_decoy", False):
        chance = next((e['chance_percent'] for e in get_ability('bone_decoy')['effects']
                       if e['type']=='every_n_decoy'),25)
        blocked = roll(chance)
        kind = "bone_decoy"
    if not blocked and shields > 0 and not state.get("last_watch_used"):
        for effect in get_ability(passive_key)["effects"]:
            if effect["type"] == "save_shield" and roll(effect["chance_percent"]):
                state["last_watch_used"] = True
                blocked = True
                kind = "last_watch"
                break
    return {"state": state, "blocked": blocked, "type": kind}


def arise_chance(hero):
    effects = get_ability(hero.get("passive_key", "none"))["effects"]
    for effect in effects:
        if effect.get("trigger") == "victory" and effect["type"] == "shadow_extraction":
            return int(hero.get("arise_chance_percent", effect["chance_percent"]))
    return None


def resolve_battle_start(hero: dict, boss: dict, *, state=None, chooser=None) -> dict:
    """Freeze one effective form at battle creation; no live hero catalog."""
    from app.mini.combat import creatures
    from app.mini.combat.tags import CREATURE_TRAITS
    hero = dict(hero)
    state = dict(state or {})
    for effect in get_ability(hero.get("passive_key", "none"))["effects"]:
        if effect["type"] != "copy_boss_form" or effect["trigger"] != "battle_start":
            continue
        if "shadow_form" not in state:
            raw = boss.get("features_json", "[]")
            values = json.loads(raw) if isinstance(raw, str) else list(raw)
            traits = sorted({creatures.canonical_trait(value) for value in values}
                            & (CREATURE_TRAITS - {"none"}))
            state["shadow_form"] = {"faction": boss["faction"],
                "special_trait": (chooser or secrets.choice)(traits) if traits else "none"}
        hero.update(state["shadow_form"])
    return {"hero": hero, "state": state}
