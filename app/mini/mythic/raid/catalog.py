"""Strict raid content. Empty catalog is supported; partial entries are not."""

import json
from pathlib import Path
from app.mini.combat.modes import ELEMENTS

CATALOG = Path(__file__).with_name("bosses.json")
IMAGES = Path(__file__).with_name("images")
LEGACY_COUNTER_POLICY = "first_successful_counter_hero_attack_on_this_raid_PROVISIONAL"
COUNTER_POLICY = "first_confirmed_counter_hero_attack_on_this_raid"


def validate(data, *, images=IMAGES, require_images=True):
    if (
        not isinstance(data, dict)
        or set(data) - {"schema_version", "bosses"}
        or (type(data.get("schema_version")) is not int or data["schema_version"] != 1)
        or not isinstance(data.get("bosses"), list)
    ):
        raise ValueError("Raid schema_version=1 and bosses list required")
    if data["bosses"] and len(data["bosses"]) != 4:
        raise ValueError("Provide all four real raid bosses together")
    codes = set()
    for boss in data["bosses"]:
        allowed = {
            "code",
            "name",
            "element",
            "image",
            "max_hp",
            "counter_elements",
            "features",
            "regeneration_percent",
            "shadow_extractable",
            "shadow_hero_code",
            "counter_policy",
        }
        if not isinstance(boss, dict) or set(boss) - allowed:
            raise ValueError("Unknown raid boss fields")
        for field in ("code", "name", "element", "image"):
            if not isinstance(boss.get(field), str) or not boss[field].strip():
                raise ValueError("Raid " + field + " required")
        if boss["code"] in codes:
            raise ValueError("Duplicate raid code")
        codes.add(boss["code"])
        if boss["element"] not in ELEMENTS:
            raise ValueError("Unknown raid element")
        if (
            type(boss.get("max_hp")) is not int
            or not 100000 <= boss["max_hp"] <= 2**63 - 1
        ):
            raise ValueError("Raid HP must be >=100000")
        counters = boss.get("counter_elements")
        if (
            not isinstance(counters, list)
            or not counters
            or any(not isinstance(e, str) or e not in ELEMENTS for e in counters)
            or len(set(counters)) != len(counters)
        ):
            raise ValueError("Explicit counter_elements required")
        if Path(boss["image"]).name != boss["image"] or not boss["image"].endswith(
            ".png"
        ):
            raise ValueError("Raid image must be a PNG basename")
        if require_images:
            from app.mini.mythic.content import png

            png(images / boss["image"])
        regen = boss.get("regeneration_percent", 0)
        if type(regen) is not int or not 0 <= regen <= 100:
            raise ValueError("Invalid regeneration")
        if "shadow_hero_code" in boss and (
            not isinstance(boss["shadow_hero_code"], str)
            or not boss["shadow_hero_code"].strip()
        ):
            raise ValueError("Invalid raid Shadow code")
        if boss.get("shadow_extractable"):
            from app.mini.catalog import load_hero_catalog

            shadows = {
                h["code"]
                for h in load_hero_catalog()["heroes"]
                if h["rarity"] == "shadow"
            }
            if boss.get("shadow_hero_code") not in shadows:
                raise ValueError("Unknown raid Shadow target")
        if boss.get("shadow_extractable", False) and not boss.get("shadow_hero_code"):
            raise ValueError("Shadow code required")
        if not isinstance(boss.get("features", []), list) or any(
            v not in ("flying", "armored") for v in boss.get("features", [])
        ):
            raise ValueError("Unsupported raid defense feature")
        if type(boss.get("shadow_extractable", False)) is not bool:
            raise ValueError("shadow_extractable must be boolean")
        if boss.get("counter_policy", COUNTER_POLICY) != COUNTER_POLICY:
            raise ValueError("Unapproved counter activation policy")
    return data["bosses"]


def load(path=None, *, require_images=True):
    path = Path(path) if path is not None else CATALOG
    if not path.exists():
        return []
    return validate(
        json.loads(path.read_text(encoding="utf-8")), require_images=require_images
    )
