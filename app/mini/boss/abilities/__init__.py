"""Compatibility path for pre-V1.2.5 hero hook callers and patch points."""
import sys
from app.mini.combat.hero_abilities import (
    AbilityCatalogError, configured_ability_keys, get_ability, load_ability_catalog,
    resolve_attack, resolve_boss_attack, resolve_kill,
)
from app.mini.combat.hero_abilities import catalog, engine, validate
sys.modules[__name__ + ".catalog"] = catalog
sys.modules[__name__ + ".engine"] = engine
sys.modules[__name__ + ".validate"] = validate
