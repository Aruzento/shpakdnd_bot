import json
from functools import lru_cache
from pathlib import Path
from app.mini.combat.tags import FACTIONS, DAMAGE_TYPES, CLASS_TAGS, ATTACK_RANGES, CREATURE_TRAITS

CATALOG_PATH = Path(__file__).with_name("floors.json")


def validate_catalog(data):
    if not isinstance(data,dict):
        raise ValueError("Catalog must be an object.")
    floors = data.get("floors", [])
    if len(floors) != 200:
        raise ValueError("Tower requires exactly 200 floors.")
    numbers, codes = set(), set()
    allowed = {"floor","code","name","max_hp","faction","damage_type","class_tag","attack_range","special_trait","features","response"}
    for record in floors:
        if set(record) != allowed:
            raise ValueError("A floor must contain exactly one enemy record.")
        f, hp = record["floor"], record["max_hp"]
        if type(f) is not int or f not in range(1,201) or f in numbers:
            raise ValueError("Invalid/duplicate floor.")
        if type(hp) is not int or hp <= 0:
            raise ValueError("HP must be a positive integer.")
        if not isinstance(record["name"],str) or not record["name"].strip():
            raise ValueError("Enemy name is required.")
        code = record["code"]
        if not isinstance(code,str) or not code or code.casefold() in codes:
            raise ValueError("Invalid/duplicate floor code.")
        for key, registry in [("faction",FACTIONS),("damage_type",DAMAGE_TYPES),("class_tag",CLASS_TAGS),
                              ("attack_range",ATTACK_RANGES),("special_trait",CREATURE_TRAITS)]:
            if not isinstance(record[key],str) or record[key] not in registry:
                raise ValueError(f"Unknown canonical {key}.")
        if record["response"] not in {"attack", "prepare"}:
            raise ValueError("Unknown Tower response pattern.")
        features = record["features"]
        if not isinstance(features,list) or any(not isinstance(x,str) for x in features) or len(set(features)) != len(features) or any(x not in CREATURE_TRAITS-{"none"} for x in features):
            raise ValueError("Invalid creature features.")
        numbers.add(f); codes.add(code.casefold())
    ordered = sorted(floors,key=lambda x:x["floor"])
    if numbers != set(range(1,201)) or any(a["max_hp"] >= b["max_hp"] for a,b in zip(ordered,ordered[1:])):
        raise ValueError("HP must grow across floors 1..200.")
    return data


@lru_cache(maxsize=1)
def load_catalog():
    return validate_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))


def get_floor(floor):
    if not 1 <= int(floor) <= 200:
        raise ValueError("Этажа не существует.")
    return next(x for x in load_catalog()["floors"] if x["floor"] == floor)
