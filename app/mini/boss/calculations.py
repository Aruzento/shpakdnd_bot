


import json
from app.mini.combat import creatures, classes
from app.mini.combat.hero_abilities import resolve_attack
from app.mini.boss.boss_abilities import engine as boss_abilities
from app.mini.combat.matchups import faction_multiplier_percent, modify_damage


def calculate_hit(boss: dict, participant: dict, hero: dict, participants=None, *, extra_percent=None) -> dict:
    passive_key = str(hero.get("passive_key", "none"))
    reachable = boss.get("ability_key") == "simple" or creatures.can_reach(boss, hero)
    if extra_percent is None:
        attack_resolution = resolve_attack(
            passive_key if reachable and extra_percent is None else "none",
            base_damage=classes.initial_attack(participant["attack"],participants or [],active=classes.enabled(boss) and boss.get("ability_key") != "simple"),
            hit_number=int(participant["hit_count"]) + 1,
            boss_hp_before=int(boss["current_hp"]),
            boss_max_hp=int(boss["max_hp"]),
            boss_ability_key=str(boss["ability_key"]),
            boss_state=json.loads(boss["ability_state_json"] or "{}"),
        )
    else:
        extra_base = max(1,int(participant["attack"])*extra_percent//100)
        attack_resolution = dict(damage=extra_base,base_damage=extra_base,
            events=[],boss_skip_turns=0,remove_magic_shield=False,extra_attacks=0)
    ability_damage = int(attack_resolution["damage"])
    damage_bonus_percent = max(0, int(participant["damage_bonus_percent"] or 0))
    faction_percent = faction_multiplier_percent(hero["faction"], boss["faction"])
    damage_after_faction = modify_damage(ability_damage, faction_percent)
    if not reachable:
        boss_resolution = {"damage": 0, "modifier_percent": 0, "boss_changes": {},
                           "events": [{"type": "unreachable", "message": "🪽 Цель находится вне досягаемости. Атака ближнего боя не достигает босса."}]}
        attack_resolution["extra_attacks"] = 0
    elif attack_resolution["remove_magic_shield"]:
        shield_state = json.loads(boss["ability_state_json"] or "{}")
        shield_state["shield_active"] = False
        boss_resolution = {"damage": 0, "modifier_percent": 0, "events": [],
                           "boss_changes": {"ability_state_json": json.dumps(shield_state)}}
    else:
        boss_resolution = boss_abilities.modify_hero_damage(dict(boss), hero, damage_after_faction)
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
    hero_final_damage = damage
    damage = creatures.feature_damage(boss, hero, hero_final_damage)
    damage = classes.beast_damage(damage,hero,json.loads(participant.get("hero_state_json") or "{}"),active=classes.enabled(boss) and extra_percent is None)
    final = boss_abilities.final_hero_damage(dict(boss), hero, damage,
        base_attack=max(1, int(participant["attack"]) * (extra_percent or 100) // 100), raw_damage=ability_damage)
    damage = final["damage"]
    boss_resolution["boss_changes"].update(final["boss_changes"])
    boss_resolution["events"].extend(final["events"])
    hp_after = min(int(boss["max_hp"]), max(0, int(boss["current_hp"]) + final["healed_hp"] - damage))
    return {
        "reachable": reachable,
        "hero_final_damage": hero_final_damage,
        "passive_key": passive_key,
        "attack_resolution": attack_resolution,
        "ability_damage": ability_damage,
        "damage_bonus_percent": damage_bonus_percent,
        "faction_percent": faction_percent,
        "damage_after_faction": damage_after_faction,
        "boss_resolution": boss_resolution,
        "boss_modifier_percent": boss_modifier_percent,
        "damage_before_external_bonus": damage_before_external_bonus,
        "damage": damage,
        "passive_events": passive_events,
        "boss_skip_turns": boss_skip_turns,
        "hp_after": hp_after,
    }
