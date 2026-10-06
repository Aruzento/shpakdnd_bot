"""Stable faction codes and integer damage arithmetic for Combat v2."""

FACTION_CYCLE = ("commoners", "beasts", "monsters", "warriors", "dark")
FACTIONS = frozenset((*FACTION_CYCLE, "neutral"))
DAMAGE_TYPES = frozenset(("slashing", "piercing", "bludgeoning", "magic"))
ATTACK_RANGES = frozenset(("melee", "ranged"))


def faction_multiplier_percent(hero_faction: str, boss_faction: str) -> int:
    for faction in (hero_faction, boss_faction):
        if not isinstance(faction, str) or faction not in FACTIONS:
            raise ValueError(f"Unknown faction: {faction!r}")
    if "neutral" in (hero_faction, boss_faction) or hero_faction == boss_faction:
        return 100
    hero_index = FACTION_CYCLE.index(hero_faction)
    boss_index = FACTION_CYCLE.index(boss_faction)
    if (hero_index + 1) % len(FACTION_CYCLE) == boss_index:
        return 200
    if (boss_index + 1) % len(FACTION_CYCLE) == hero_index:
        return 25
    return 100


def modify_damage(damage: int, percent: int) -> int:
    """Floor integer modifiers; ordinary attacks retain at least 1 HP."""
    return max(1, int(damage) * int(percent) // 100)
