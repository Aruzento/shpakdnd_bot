"""Approved O01–O10 configuration, frozen by adapters at battle creation."""

import json
from copy import deepcopy
from pathlib import Path

PATH = Path(__file__).with_suffix(".json")
# Immutable compatibility values for battles that predate policy snapshots.
LEGACY = {
    "schema_version": 1,
    "status": "PROVISIONAL",
    "normal_rewards": {
        "gold_percent": [300, 320, 340, 360, 380, 400],
        "thief_remaining_percent": 10,
        "blessing_mythic_shards": 5,
        "star_chance": [20, 23, 26, 29, 32, 35],
        "star_shards": 300,
        "star_outcome": "normal_victory_only",
        "tower_chest_preserved": True,
        "tower_arcana": "first_player_floor_choice",
    },
    "raid_eclipse": {
        "percent": [10, 11, 12, 13, 14, 15],
        "hp_base": "original_max_hp",
        "fund_base": "original_max_hp",
    },
}


APPROVED = deepcopy(LEGACY)
APPROVED.update(schema_version=2, status="APPROVED")
APPROVED["normal_rewards"].update(
    version=2, star_distribution="individual", star_blessing="independent"
)


def load(path=None):
    data = json.loads((Path(path) if path else PATH).read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != set(APPROVED)
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 2
        or data["status"] != "APPROVED"
    ):
        raise ValueError("Invalid approved policy contract")
    normal = data["normal_rewards"]
    eclipse = data["raid_eclipse"]
    if not isinstance(normal, dict) or set(normal) != set(APPROVED["normal_rewards"]):
        raise ValueError("Invalid normal reward policy")
    if not isinstance(eclipse, dict) or set(eclipse) != set(LEGACY["raid_eclipse"]):
        raise ValueError("Invalid eclipse policy")
    for group, key, high in (
        (normal, "gold_percent", 10000),
        (normal, "star_chance", 100),
        (eclipse, "percent", 100),
    ):
        values = group[key]
        if (
            not isinstance(values, list)
            or len(values) != 6
            or any(type(v) is not int or not 0 <= v <= high for v in values)
        ):
            raise ValueError("Policy needs six valid star values")
    for key in ("thief_remaining_percent", "blessing_mythic_shards", "star_shards"):
        if (
            type(normal[key]) is not int
            or not 0 <= normal[key] <= 1000000
            or key == "thief_remaining_percent"
            and normal[key] > 100
        ):
            raise ValueError("Invalid reward number")
    for key in (
        "star_outcome",
        "tower_chest_preserved",
        "tower_arcana",
        "version",
        "star_distribution",
        "star_blessing",
    ):
        if (
            type(normal[key]) is not type(APPROVED["normal_rewards"][key])
            or normal[key] != APPROVED["normal_rewards"][key]
        ):
            raise ValueError("Unapproved reward policy selector")
    if (
        eclipse["hp_base"] != "original_max_hp"
        or eclipse["fund_base"] != "original_max_hp"
    ):
        raise ValueError("Unapproved eclipse basis")
    return deepcopy(data)


def normal(state):
    return state.get("reward_policy", deepcopy(LEGACY["normal_rewards"]))


if __name__ == "__main__":
    load()
    print("PASS: approved normal rewards and raid Eclipse configuration")
