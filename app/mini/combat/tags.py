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

# Class semantics currently used by combat/content checks; other tags stay open.
MAGIC_CLASSES = frozenset(("mage", "magical"))
TECHNICAL_CLASS = "technical"


# Registered executable traits. Open legacy metadata tags remain valid.
CREATURE_TRAITS = frozenset(("none", "undead", "construct", "flying", "poisonous", "holy", "demonic", "armored"))
CLASS_TAGS = frozenset(("none", "warrior", "guardian", "sneaky", "healer", "technical", "mage", "beast"))
LEGACY_CLASS_TAGS = frozenset(("magical", "martial", "ranger"))
LEGACY_TRAIT_ALIASES = {"demon": "demonic"}
