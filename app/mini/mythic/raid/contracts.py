"""Typed extension contract for future ordinary heroes' explicitly authored raid rules.

No production ordinary-hero raid effect is inferred from its normal counterpart.
"""

from app.mini.combat.modes import mode_property

FIELDS = {
    "damage_bonus_bps": ("value", 0, 1000000),
    "flat_damage": ("value", 0, 2**63 - 1),
    "current_hp_percent": ("percent", 0, 100),
    "original_hp_percent": ("percent", 0, 100),
    "shadow_extraction": ("chance_percent", 0, 100),
}


def validate(definition):
    if not isinstance(definition, dict) or not isinstance(
        definition.get("effects", []), list
    ):
        raise ValueError("raid definition needs effects list")
    if set(definition) - {"key", "name", "description", "effects", "configured"}:
        raise ValueError("Unknown raid definition field")
    if "configured" in definition and type(definition["configured"]) is not bool:
        raise ValueError("Invalid raid definition configured flag")
    for key in ("key", "name", "description"):
        if key in definition and not isinstance(definition[key], str):
            raise ValueError("Invalid raid definition text")
    for effect in definition.get("effects", []):
        if not isinstance(effect, dict) or effect.get("type") not in FIELDS:
            raise ValueError("Unsupported raid effect type")
        field, low, high = FIELDS[effect["type"]]
        if set(effect) - {"type", field, "trigger"}:
            raise ValueError("Unknown raid effect field")
        if type(effect.get(field)) is not int or not low <= effect[field] <= high:
            raise ValueError("Invalid raid effect value")
        expected = (
            "victory"
            if effect["type"] == "shadow_extraction"
            else (
                "attack"
                if effect["type"] in ("damage_bonus_bps", "flat_damage")
                else "after_attack"
            )
        )
        if effect.get("trigger") != expected:
            raise ValueError("Invalid raid effect trigger")
    return definition


def authored(hero):
    prop = mode_property(hero, "raid")
    if not prop["configured"] or prop["key"].startswith("mythic_"):
        return []
    return validate(prop.get("definition", {})).get("effects", [])
