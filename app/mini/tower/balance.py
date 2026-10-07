import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_balance():
    data = json.loads(Path(__file__).with_name("balance.json").read_text(encoding="utf-8"))
    previous = 0
    for band in data["bands"]:
        if not previous < band["max_floor"] <= 200 or not 1 <= band["min_bonus"] <= band["max_bonus"] <= 100 or band["shards"] <= 0:
            raise ValueError("Invalid Tower reward band.")
        previous = band["max_floor"]
    if previous != 200:
        raise ValueError("Reward bands must cover the entire Tower.")
    return data


def reward_band(floor):
    if not 1 <= floor <= 200:
        raise ValueError("Invalid reward floor.")
    return next(x for x in load_balance()["bands"] if floor <= x["max_floor"])
