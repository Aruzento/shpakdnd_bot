"""Pure creature hooks. Caller persists plans inside its combat transaction.

Reward HP and corruption use integer points of the ORIGINAL reward (100 = full).
Temporary HP absorbs permanent decay first; corruption is subtracted afterwards.
"""
import json
import secrets
from app.mini.combat import classes
from app.mini.combat.tags import LEGACY_TRAIT_ALIASES, CREATURE_TRAITS

def roll_success(chance_percent: int) -> bool:
    return secrets.randbelow(100) < chance_percent

def canonical_trait(value: str) -> str:
    return LEGACY_TRAIT_ALIASES.get(value, value)

def rules_enabled(boss: dict) -> bool:
    return int(boss.get("trait_rules_version", 0)) >= 1

def features(boss: dict) -> frozenset[str]:
    if not rules_enabled(boss):
        return frozenset()
    raw = boss.get("features_json", "[]")
    values = json.loads(raw) if isinstance(raw, str) else raw
    return frozenset(canonical_trait(value) for value in values if canonical_trait(value) in CREATURE_TRAITS - {"none"})

def state(boss: dict) -> dict:
    values = json.loads(boss.get("feature_state_json") or "{}")
    if not isinstance(values, dict):
        raise ValueError("feature_state_json must be an object")
    return values

def can_reach(boss: dict, hero: dict) -> bool:
    return ("flying" not in features(boss) or hero.get("attack_range") == "ranged"
            or canonical_trait(hero.get("special_trait", "none")) == "flying")

def feature_damage(boss: dict, hero: dict, hero_final_damage: int) -> int:
    if not can_reach(boss, hero):
        return 0
    reduction = 0
    if "armored" in features(boss):
        if hero.get("damage_type") in {"slashing", "piercing", "bludgeoning"}:
            reduction += 10
        if hero.get("attack_range") == "melee":
            reduction += 10
    damage = max(0, int(hero_final_damage))
    return max(1, damage * (100 - reduction) // 100) if damage else 0

def active_heroes(participants: list[dict]) -> list[dict]:
    # Forced skips are temporary: these heroes still provide counters.
    return [json.loads(p.get("hero_snapshot_json") or "{}")
            for p in participants if not p.get("banished", 0)]

def effective_reward_percent(boss: dict) -> int:
    if classes.enabled(boss) and int(boss.get('reward_coins',0))>0:
        base=int(boss['reward_coins'])
        return (classes.effective_reward(boss)*100+base-1)//base
    return max(0, int(boss.get("reward_percent", 100))
               + int(boss.get("reward_temp_hp", 0)) - int(boss.get("reward_corruption", 0)))

def reward_decay(boss: dict) -> dict:
    decay = int(boss["reward_decay_percent"])
    temporary = int(boss.get("reward_temp_hp", 0))
    absorbed = min(temporary, decay)
    percent=max(0,int(boss['reward_percent'])-decay+absorbed)
    changes={"reward_temp_hp":temporary-absorbed,"reward_percent":percent}
    if classes.enabled(boss):
        base=int(boss.get('reward_coins',0))
        loss=base*int(boss['reward_percent'])//100-base*percent//100
        changes['boss_damage']=classes.permanent_damage(boss)+min(classes.real_reward(boss),max(0,loss))
        if base:
            changes['reward_percent']=max(percent,((base-changes['boss_damage'])*100+base-1)//base)
    return changes

def _plan(changes=None, events=None) -> dict:
    return {"boss_changes": changes or {}, "events": events or []}

def _save_state(values: dict, events: list[dict]) -> dict:
    return _plan({"feature_state_json": json.dumps(values)}, events)

def battle_start(boss: dict, participants: list[dict]) -> dict:
    if not rules_enabled(boss):
        return _plan()
    traits = {canonical_trait(h.get("special_trait", "none")) for h in active_heroes(participants)}
    shields = int(boss["reward_shields_max"]) + ("armored" in traits)
    temp = 15 if "undead" in traits else 0
    events = []
    if temp:
        events.append({"type": "undead_buffer", "message": "☠️ Нежить защищает награду: +15% временного запаса."})
    if "armored" in traits:
        events.append({"type": "armored_shield", "message": "🛡 Бронированный герой добавил щит награды."})
    return _plan({"feature_state_json": "{}", "reward_temp_hp": temp, "reward_corruption": 0,
                  "reward_shields_max": shields, "reward_shields": shields}, events)

def on_hit(boss: dict, hero: dict, *, successful: bool, roller=None) -> dict:
    if not rules_enabled(boss) or not successful or not can_reach(boss, hero):
        return _plan()
    values = state(boss)
    trait = canonical_trait(hero.get("special_trait", "none"))
    events = []
    if trait == "poisonous" and not values.get("boss_poisoned") and (roller or roll_success)(25):
        values["boss_poisoned"] = True
        events.append({"type": "poison_applied", "message": "☠️ Герой отравил босса."})
    if trait == "holy" and features(boss) & {"undead", "demonic"} and not values.get("holy_miss_active"):
        values["holy_miss_active"] = True
        events.append({"type": "holy_applied", "message": "✨ Священная сила ослепляет босса до следующей атаки."})
    return _save_state(values, events) if events else _plan()

def after_hero_turn(boss: dict, hero: dict, *, roller=None) -> dict:
    if (rules_enabled(boss) and hero.get("special_trait") == "construct"
            and int(boss["reward_shields"]) < int(boss["reward_shields_max"])
            and (roller or roll_success)(10)):
        return _plan({"reward_shields": int(boss["reward_shields"]) + 1}, [{
            "type": "construct_shield", "message": "🛡 Конструкт восстановил один щит награды."}])
    return _plan()

def holy_attack_attempt(boss: dict, *, roller=None) -> dict:
    values = state(boss)
    if not rules_enabled(boss) or not values.get("holy_miss_active"):
        return {**_plan(), "miss": False}
    values["holy_miss_active"] = False
    miss = (roller or roll_success)(25)
    events = [{"type": "holy_miss", "message": "✨ Священная сила ослепляет босса! Босс промахивается."}] if miss else []
    return {**_save_state(values, events), "miss": miss}

def boss_death(boss: dict) -> dict:
    values = state(boss)
    if "undead" not in features(boss) or values.get("boss_undead_revived"):
        return {**_plan(), "revived": False}
    values["boss_undead_revived"] = True
    hp = max(1, int(boss["max_hp"]) * 25 // 100)
    plan = _save_state(values, [{"type": "undead_revived", "healed_hp": hp,
        "message": "☠️ Мёртвое не может умереть! Босс снова поднимается. ❤️ Восстановлено 25% здоровья."}])
    plan["boss_changes"]["current_hp"] = hp
    return {**plan, "revived": True}

def poison_tick(boss: dict) -> dict:
    values = state(boss)
    if not rules_enabled(boss) or not values.get("boss_poisoned"):
        return _plan()
    values["boss_poisoned"] = False
    damage = max(1, int(boss["max_hp"]) * 2 // 100)
    actual = min(int(boss["current_hp"]), damage)
    plan = _save_state(values, [{"type": "poison_tick", "damage": actual,
        "message": f"☠️ Яд наносит {boss['name']} {actual} урона."}])
    plan["boss_changes"]["current_hp"] = int(boss["current_hp"]) - actual
    return plan

def holy_regeneration(boss: dict, participants: list[dict]) -> dict:
    if "holy" not in features(boss) or any(
        canonical_trait(h.get("special_trait", "none")) == "demonic" for h in active_heroes(participants)
    ):
        return _plan()
    return _heal(boss, 25, "holy_regeneration", "✨ Святость")

def demonic_corruption(boss: dict) -> dict:
    if "demonic" not in features(boss):
        return _plan()
    corruption = int(boss.get("reward_corruption", 0)) + 10
    return _plan({"reward_corruption": corruption}, [{"type": "demonic_corruption",
        "message": f"😈 Временная порча награды: {corruption}%."}])

def end_round(boss: dict, participants: list[dict]) -> dict:
    if "construct" not in features(boss) or classes.blocks_construct_regeneration(participants):
        return _plan()
    return _heal(boss, 10, "construct_regeneration", "⚙️ Конструкт")

def _heal(boss: dict, percent: int, kind: str, label: str) -> dict:
    hp = min(int(boss["max_hp"]), int(boss["current_hp"]) + int(boss["max_hp"]) * percent // 100)
    healed = hp - int(boss["current_hp"])
    if not healed:
        return _plan()
    return _plan({"current_hp": hp}, [{"type": kind, "healed_hp": healed,
        "message": f"{label} восстанавливает {healed} HP босса."}])
