"""Stable faction codes and integer damage arithmetic for Combat v2."""

FACTION_CYCLE = ("commoners", "beasts", "monsters", "warriors", "dark")
FACTIONS = frozenset((*FACTION_CYCLE, "neutral"))
DAMAGE_TYPES = frozenset(("slashing", "piercing", "bludgeoning", "magic"))
ATTACK_RANGES = frozenset(("melee", "ranged"))




def is_open_tag(value) -> bool:
    """Class, special and feature tags remain open, nonempty strings."""
    return isinstance(value, str) and bool(value.strip())

LEGACY_HERO_TRAITS = {
    "faction": "commoners", "damage_type": "slashing", "class_tag": "none",
    "attack_range": "melee", "special_trait": "none",
}
