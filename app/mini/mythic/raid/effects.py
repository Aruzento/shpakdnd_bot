"""Pure ordered raid effects, committed atomically by the raid service."""

from copy import deepcopy
import secrets
from app.mini.combat.modes import hp_event, ordinary_damage, mode_property

ECLIPSE_POLICY = "original_hp_fixed_fund_APPROVED"
ARCANA_WEIGHTS = (40, 30, 20, 10)


def element(hero):
    key = mode_property(hero, "raid")["key"]
    return key.removeprefix("mythic_") if key.startswith("mythic_") else ""


def resolve(raid, hero, pid, *, pet_delta=0, chooser=None):
    state = deepcopy(raid.get("state", {}))
    snapshot = raid["snapshot"]
    hp = int(raid["current_hp"])
    maximum = int(raid["max_hp"])
    original = int(raid["original_max_hp"])
    stars = int(hero.get("stars", 1))
    level = max(0, min(5, stars - 1))
    kind = element(hero)
    if hero.get("rarity") == "mythic" and not 1 <= stars <= 6:
        raise ValueError("Mythic stars must be 1..6")
    from app.mini.mythic.raid.contracts import authored

    authored_effects = authored(hero)
    events = []
    audit = []
    bonus = [e["value"] for e in authored_effects if e["type"] == "damage_bonus_bps"]
    bypass = False

    def hit(value, actor=pid, source="attack"):
        nonlocal hp
        event = hp_event(hp, value, actor, source)
        hp = event["hp_after"]
        events.append(event)
        return event["actual_hp_damage"]

    if kind == "moon" and "lunar" not in state:
        pool = [
            index for index, weight in enumerate(ARCANA_WEIGHTS) for _ in range(weight)
        ]
        arcana = (chooser or secrets.choice)(pool)
        if arcana not in range(4):
            raise ValueError("Invalid lunar choice")
        state["lunar"] = {
            "arcana": arcana,
            "owner": pid,
            "level": level,
            "stars": stars,
        }
        audit.append(
            {"type": "raid_lunar", "arcana": arcana, "owner": pid, "level": level}
        )
        if arcana == 3:
            from app.mini.mythic.policies import LEGACY

            policy = snapshot.get("runtime_policies", LEGACY)["raid_eclipse"]
            percent = policy["percent"][level]
            gain = original * percent // 100
            state["lunar"].update(percent=percent, gain=gain, original_max_hp=original)
            maximum += gain
            hp += gain
            audit.append(
                {"type": "eclipse", "hp_added": gain, "policy": ECLIPSE_POLICY}
            )
    earth = state.get("earth")
    dark = state.get("dark")
    star = state.get("star")
    sun = state.get("sun")
    earth_bonus = 1500 + earth["level"] * 300 if earth and earth["charges"] else None
    suppressed = bool(sun and sun.get("charges", 0))
    if earth_bonus is not None:
        bonus.append(1500 + earth["level"] * 300)
        bypass = True
    if dark and dark["charges"]:
        bonus.append(
            (maximum - hp) * 10000 // max(1, maximum) * (10 + dark["level"]) // 10
        )
    if star and star["charges"]:
        bonus.append(star["bonus_bps"])
    if sun and sun["charges"]:
        bonus.append(2000 + sun["level"] * 400)
        bypass = True
    overlays = set(hero.get("elements", []))
    if kind:
        overlays.add(kind)
    matching = bool(overlays & set(snapshot["counter_elements"]))
    counter = state.get("counter_active", False) or matching
    lunar = state.get("lunar", {})
    arcana = lunar.get("arcana")
    additive = int(pet_delta) + sum(
        e["value"] for e in authored_effects if e["type"] == "flat_damage"
    )
    if arcana == 1 and kind == "moon":
        additive += 200 + 50 * lunar["level"]
    if arcana == 2 and pid != lunar["owner"]:
        additive += 200 + 50 * lunar["level"]
    features = set(snapshot.get("features", []))
    unreachable = (
        "flying" in features
        and hero.get("attack_range") != "ranged"
        and hero.get("special_trait") != "flying"
    )
    armor = 0
    if not bypass and "armored" in features:
        armor = 10 * int(
            hero.get("damage_type") in ("slashing", "piercing", "bludgeoning")
        ) + 10 * int(hero.get("attack_range") == "melee")
    without_earth = list(bonus)
    if earth_bonus is not None:
        without_earth.remove(earth_bonus)
    unboosted = ordinary_damage(
        hero["attack"],
        bonus_bps=without_earth,
        additive=additive,
        reduction_percent=armor,
        protected_zero=unreachable,
    )
    amount = ordinary_damage(
        hero["attack"],
        bonus_bps=bonus,
        additive=additive,
        reduction_percent=armor,
        protected_zero=unreachable,
    )
    if not counter:
        amount //= 10
        unboosted //= 10
    actual = hit(amount)
    from app.mini.mythic.raid.catalog import COUNTER_POLICY, LEGACY_COUNTER_POLICY

    counter_policy = snapshot.get("counter_policy", LEGACY_COUNTER_POLICY)
    if counter_policy not in (COUNTER_POLICY, LEGACY_COUNTER_POLICY):
        raise ValueError("Unknown frozen counter policy")
    if (
        matching
        and (actual > 0 or counter_policy == COUNTER_POLICY)
        and not state.get("counter_active")
    ):
        state["counter_active"] = True
        state["counter_source"] = {
            "actor_id": pid,
            "hero_id": hero.get("id"),
            "policy": counter_policy,
        }
        audit.append(
            {
                "type": "counter_activated",
                "actor_id": pid,
                "policy": snapshot.get(
                    "counter_policy",
                    "first_successful_counter_hero_attack_on_this_raid_PROVISIONAL",
                ),
            }
        )
    if actual:
        for effect in authored_effects:
            if effect["type"] == "current_hp_percent":
                hit(hp * effect["percent"] // 100, source="authored_current_hp")
            if effect["type"] == "original_hp_percent":
                hit(original * effect["percent"] // 100, source="authored_original_hp")
        if earth and earth["charges"]:
            hit(
                unboosted * (15 + earth["level"] * 3) // 100,
                earth["owner"],
                "earth_extra",
            )
        if kind == "ice" and hp:
            hit(original * (100 + level * 10) // 10000, source="glacier")
        lightning = state.get("lightning")
        if lightning and lightning["charges"] and pid != lightning["owner"]:
            lightning["charges"] -= 1
            hit(hp * (50 + lightning["level"] * 10) // 10000, source="chain_lightning")
        fire = state.get("fire")
        if fire and fire["charges"]:
            fire["damage"] += sum(e["actual_hp_damage"] for e in events)
            fire["charges"] -= 1
            if not fire["charges"]:
                hit(
                    fire["damage"] * (20 + fire["level"] * 4) // 100,
                    source="fire_explosion",
                )
        for effect in (earth, dark, star, sun):
            if effect and effect["charges"]:
                effect["charges"] -= 1
        history = state.setdefault("last_stars", [])
        history.append(stars)
        state["last_stars"] = history[-5:]
        if kind == "fire" and not state.get("fire", {}).get("charges"):
            state["fire"] = {"charges": 5, "damage": 0, "level": level}
        elif kind == "lightning":
            state["lightning"] = {"charges": 3, "owner": pid, "level": level}
        elif kind == "earth":
            state["earth"] = {"charges": 3, "owner": pid, "level": level}
        elif kind == "dark":
            state["dark"] = {"charges": 3, "level": level}
        elif kind == "star":
            state["star"] = {
                "charges": 5,
                "bonus_bps": sum(state["last_stars"]) * (100 + 20 * level),
            }
        elif kind == "sun":
            state["sun"] = {"charges": 3, "level": level}
    regeneration = (
        0
        if suppressed or not hp
        else min(maximum - hp, maximum * snapshot.get("regeneration_percent", 0) // 100)
    )
    hp += regeneration
    if regeneration:
        audit.append({"type": "regeneration", "healed_hp": regeneration})
    if maximum > 2**63 - 1 or hp > 2**63 - 1:
        raise ValueError("HP рейда выходит за пределы целого SQLite; атака отменена.")
    return {
        "state": state,
        "current_hp": hp,
        "max_hp": maximum,
        "events": events,
        "audit": audit,
        "actual_hp_damage": sum(
            e["actual_hp_damage"] for e in events if e["actor_id"] == pid
        ),
        "passive_configured": mode_property(hero, "raid")["configured"],
    }
