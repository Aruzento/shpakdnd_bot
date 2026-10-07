import json
from functools import lru_cache
from pathlib import Path

SLOTS = ("helmet", "ring", "cloak")
CATALOG_PATH = Path(__file__).with_name("items.json")


def validate_catalog(data: dict) -> dict:
    if not isinstance(data,dict):
        raise ValueError("Catalog must be an object.")
    items = data.get("items", [])
    if len(items) != 300:
        raise ValueError("Equipment requires exactly 300 items.")
    codes, names = set(), set()
    bonuses = {slot: set() for slot in SLOTS}
    for item in items:
        if set(item) != {"code", "name", "slot", "attack_bonus"}:
            raise ValueError("Equipment has only code/name/slot/attack_bonus.")
        slot, bonus = item["slot"], item["attack_bonus"]
        if not isinstance(slot,str) or slot not in SLOTS or type(bonus) is not int or not 1 <= bonus <= 100:
            raise ValueError("Invalid equipment slot/bonus.")
        code, name = item["code"], item["name"]
        if not isinstance(code, str) or not code or not isinstance(name, str) or not name.strip():
            raise ValueError("Equipment code and name are required.")
        if code.casefold() in codes or name.casefold() in names or bonus in bonuses[slot]:
            raise ValueError("Duplicate equipment code/name/slot bonus.")
        codes.add(code.casefold()); names.add(name.casefold()); bonuses[slot].add(bonus)
    if any(values != set(range(1, 101)) for values in bonuses.values()):
        raise ValueError("Each slot requires bonuses 1..100.")
    return data


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    return validate_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
