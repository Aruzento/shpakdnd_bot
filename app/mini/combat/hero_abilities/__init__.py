"""Изолированный движок пассивных способностей для боёв с боссами."""

from app.mini.combat.hero_abilities.catalog import (
    AbilityCatalogError,
    configured_ability_keys,
    get_ability,
    load_ability_catalog,
)
from app.mini.combat.hero_abilities.engine import (
    resolve_attack,
    resolve_boss_attack,
    resolve_kill,
)

__all__ = [
    "AbilityCatalogError",
    "configured_ability_keys",
    "get_ability",
    "load_ability_catalog",
    "resolve_attack",
    "resolve_boss_attack",
    "resolve_kill",
]
