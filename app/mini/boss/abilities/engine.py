import secrets
from collections.abc import Callable

from app.mini.boss.abilities.catalog import get_ability


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
    roller: Roller | None = None,
) -> dict:
    """Применяет attack-эффекты пассивки и возвращает итоговый урон."""
    ability = get_ability(passive_key)
    damage = max(1, int(base_damage))
    hit_number = max(1, int(hit_number))
    hp_before = max(0, int(boss_hp_before))
    max_hp = max(1, int(boss_max_hp))
    roller = roller or _roll_success
    events: list[dict] = []
    boss_skip_turns = 0

    for effect in ability["effects"]:
        if effect.get("trigger") != "attack":
            continue

        effect_type = str(effect.get("type", ""))
        triggered = False

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
