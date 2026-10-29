"""Strict raid content. Empty catalog is supported; partial entries are not."""

import json
import hashlib
from copy import deepcopy
from functools import lru_cache
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
            "mechanic",
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
        if (
            "/" in boss["image"]
            or "\\" in boss["image"]
            or Path(boss["image"]).name != boss["image"]
            or not boss["image"].endswith(".png")
        ):
            raise ValueError("Raid image must be a PNG basename")
        if require_images:
            from app.mini.mythic.content import png

            png(images / boss["image"], background=0)
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
        if len(boss.get("features", [])) != len(set(boss.get("features", []))):
            raise ValueError("Duplicate raid defense feature")
        if "mechanic" in boss:
            from app.mini.mythic.raid.mechanics import validate_profile

            validate_profile(boss)
        if boss.get("counter_policy", COUNTER_POLICY) != COUNTER_POLICY:
            raise ValueError("Unapproved counter activation policy")
    dynamic = [b for b in data["bosses"] if "mechanic" in b]
    if dynamic:
        from app.mini.mythic.raid.mechanics import SPECS

        if len(dynamic) != 4 or {b["mechanic"]["type"] for b in dynamic} != set(SPECS):
            raise ValueError(
                "Supply all four distinct approved raid mechanics together"
            )
        if len({b["image"] for b in dynamic}) != 4:
            raise ValueError("Each of the four raid bosses needs its own image")
    return data["bosses"]


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate raid JSON field: " + key)
        result[key] = value
    return result


@lru_cache(maxsize=8)
def _validated(text, image_root, require_images, fingerprints):
    return validate(json.loads(text, object_pairs_hook=_unique_fields),
                    images=Path(image_root), require_images=require_images)


def _load(path=None, *, require_images=True, images=None):
    path = Path(path) if path is not None else CATALOG
    if not path.exists():
        return []
    image_root = Path(IMAGES if images is None else images)
    text = path.read_text(encoding="utf-8")
    # Parse/validate JSON even for cache hits; image file replacement invalidates
    # the expensive decoder by content, not timestamps. Return no shared mutable data.
    data = json.loads(text, object_pairs_hook=_unique_fields)
    validate(data, images=image_root, require_images=False)
    fingerprints = tuple((b["image"], hashlib.sha256((image_root/b["image"]).read_bytes()).digest())
                         for b in data["bosses"]) if require_images else ()
    return deepcopy(_validated(text, str(image_root.resolve()), require_images, fingerprints))


def load(path=None, *, require_images=True, images=None):
    from app.mini.ui.timing import span
    with span("catalog"):
        return _load(path, require_images=require_images, images=images)
