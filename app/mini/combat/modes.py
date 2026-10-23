"""V15 immutable normal/raid contracts and integer damage primitives.

A missing raid property means no passive, never the normal property. Legacy
snapshots remain on their original pipeline until a new battle is created.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass

VERSION = 15
MODES = frozenset({"normal", "raid"})
ELEMENTS = frozenset(
    {"ice", "lightning", "dark", "fire", "star", "earth", "moon", "sun"}
)


def passive_contract(hero):
    import json

    supplied = hero.get("passives")
    if supplied is None:
        supplied = json.loads(hero.get("passives_json") or "{}")
    if not isinstance(supplied, dict) or set(supplied) - MODES:
        raise ValueError("passives: expected normal/raid objects")
    result = {}
    for mode in ("normal", "raid"):
        value = supplied.get(mode)
        if value is None:
            value = (
                {"key": hero.get("passive_key", "none")}
                if mode == "normal"
                else {"key": "none", "configured": False}
            )
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("key"), str)
            or not value["key"]
        ):
            raise ValueError("passives." + mode + ": key is required")
        if set(value) - (
            {"key", "configured", "params", "definition"}
            if int(hero.get("rules_version", 0)) >= VERSION
            else {"key", "configured", "params"}
        ):
            raise ValueError("passive contract has unknown fields")
        if not isinstance(value.get("params", {}), dict):
            raise ValueError("passive params must be an object")
        result[mode] = deepcopy(value)
        # passive_key is the legacy normal-mode column; never a raid fallback.
        if (
            mode == "normal"
            and "passive_key" in hero
            and hero["passive_key"] != result[mode]["key"]
        ):
            result[mode]["key"] = hero["passive_key"]
            result[mode].pop("definition", None)
            result[mode]["configured"] = hero["passive_key"] != "none"
        result[mode].setdefault("configured", value["key"] != "none")
        if type(result[mode]["configured"]) is not bool:
            raise ValueError("configured must be boolean")
    return result


def mode_property(hero, mode):
    if mode not in MODES:
        raise ValueError("Unknown combat mode")
    return passive_contract(hero)[mode]


def normalized_attack(hero):
    from app.mini.hero_upgrades import calculate_attack

    base = max(0, int(hero.get("base_attack", hero.get("attack", 0))))
    if mode_property(hero, "normal")["key"] == "duplicate_echoes":
        base = base * (100 + 2 * max(0, int(hero.get("copies", 1)) - 1)) // 100
    return calculate_attack(
        base, int(hero.get("stars", 0)), rarity=hero.get("rarity", "common")
    )


def freeze_hero(hero):
    result = deepcopy(dict(hero))
    result["base_attack"] = int(hero.get("base_attack", hero.get("attack", 0)))
    result["copies"] = int(hero.get("copies", 1))
    result["stars"] = int(hero.get("stars", 0))
    result["passives"] = passive_contract(hero)
    result["passive_key"] = result["passives"]["normal"]["key"]
    result["rules_version"] = VERSION
    from app.mini.combat.hero_abilities.catalog import get_ability

    for mode, prop in result["passives"].items():
        if "definition" not in prop:
            prop["definition"] = deepcopy(get_ability(prop["key"], mode=mode))
        prop["configured"] = bool(
            prop.get("configured") and prop["definition"].get("configured")
        )
        if not prop["configured"]:
            prop["key"] = "none"
            prop["definition"] = deepcopy(get_ability("none", mode=mode))
    result["passive_key"] = result["passives"]["normal"]["key"]
    overlays = list(result.get("elements", []))
    if any(e not in ELEMENTS for e in overlays):
        raise ValueError("Unknown elemental overlay")
    own = result["passives"]["normal"]["key"].removeprefix("mythic_")
    if result.get("rarity") == "mythic" and own in ELEMENTS and own not in overlays:
        overlays.append(own)
    result["elements"] = overlays
    result["attack"] = normalized_attack(result)
    return result


@dataclass(frozen=True)
class DamageEvent:
    kind: str
    actor_id: int
    requested: int
    actual_hp_damage: int
    hp_before: int
    hp_after: int


def hp_event(current_hp, damage, actor_id, kind="attack"):
    before = max(0, int(current_hp))
    requested = max(0, int(damage))
    actual = min(before, requested)
    return asdict(
        DamageEvent(kind, int(actor_id), requested, actual, before, before - actual)
    )


def ordinary_damage(
    base,
    *,
    bonus_bps=(),
    additive=0,
    element_percents=(),
    reduction_percent=0,
    multiplier=1,
    protected_zero=False
):
    if protected_zero:
        return 0
    value = max(0, int(base))
    # 100 basis points = 1%; preserve rational star progressions without floats.
    value = max(0, value * (10000 + sum(int(v) for v in bonus_bps)) // 10000)
    value = max(0, value + int(additive)) * int(multiplier)
    value = (
        value * (max(map(int, element_percents)) if element_percents else 100) // 100
    )
    return max(0, value * max(0, 100 - int(reduction_percent)) // 100)
