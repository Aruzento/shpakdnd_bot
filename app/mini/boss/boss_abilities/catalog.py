import json
from pathlib import Path

from app.mini.combat.matchups import FACTIONS

ABILITIES_JSON = Path(__file__).with_name("abilities.json")
ABILITY_KEYS = frozenset((
    "none", "paralysis", "critical_strike", "banishment", "shapeshifter",
    "rapier", "hydra_regeneration", "kamikaze", "magic_shield", "mechanism",
    "collapse", "waste_of_time", "training", "transformation", "oneshot", "simple",
))


def validate_config(key: str, config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError(f"Boss ability {key}: config must be an object.")
    allowed = {
        "critical_strike": {"chance_percent"},
        "banishment": {"chance_percent", "every"},
        "shapeshifter": {"chance_percent", "target_faction"},
        "rapier": {"chance_percent"},
        "hydra_regeneration": {"heal_percent"},
        "mechanism": {"damage_percent"},
    }.get(key, set())
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"Boss ability {key}: unknown config fields {sorted(unknown)}.")
    for field in ("chance_percent", "heal_percent", "damage_percent"):
        if field in config and (
            type(config[field]) is not int or not 0 <= config[field] <= 100
        ):
            raise ValueError(f"Boss ability {key}: {field} must be an integer 0..100.")
    if "every" in config and (
        type(config["every"]) is not int or config["every"] < 1
    ):
        raise ValueError(f"Boss ability {key}: every must be a positive integer.")
    if "target_faction" in config and (
        not isinstance(config["target_faction"], str)
        or config["target_faction"] not in FACTIONS
    ):
        raise ValueError(f"Boss ability {key}: invalid target_faction.")


def load_ability_catalog() -> dict:
    try:
        data = json.loads(ABILITIES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Boss abilities JSON: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("abilities"), dict):
        raise ValueError("Boss abilities: abilities must be an object.")
    abilities = data["abilities"]
    if set(abilities) != ABILITY_KEYS:
        raise ValueError("Boss abilities: missing or unknown ability keys.")
    required = {
        "critical_strike": {"chance_percent"},
        "banishment": {"chance_percent", "every"},
        "shapeshifter": {"chance_percent", "target_faction"},
        "rapier": {"chance_percent"},
        "hydra_regeneration": {"heal_percent"},
        "mechanism": {"damage_percent"},
    }
    for key, config in abilities.items():
        validate_config(key, config)
        if not required.get(key, set()).issubset(config):
            raise ValueError(f"Boss ability {key}: missing required config fields.")
    return data


def get_ability(key: str) -> dict:
    if not isinstance(key, str) or key not in ABILITY_KEYS:
        raise ValueError(f"Unknown boss ability_key: {key!r}")
    return dict(load_ability_catalog()["abilities"][key])
