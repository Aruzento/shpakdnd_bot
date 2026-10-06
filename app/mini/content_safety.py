"""Offline release checks; combat accepts open tags without UI dependencies."""
import re

from app.mini.boss.catalog import load_boss_catalog
from app.mini.boss.matchups import faction_multiplier_percent
from app.mini.catalog import load_hero_catalog
from app.mini.presentation import (
    CLASS_LABELS, DAMAGE_LABELS, FACTION_LABELS, RANGE_LABELS, TRAIT_LABELS,
)


def _require_label(kind: str, code: str, field: str, tag: str, labels: dict) -> None:
    label = labels.get(tag)
    if (
        not isinstance(label, str)
        or not re.search(r"[А-Яа-яЁё]", label)
        or re.search(r"[A-Za-z]", label)
    ):
        raise ValueError(
            f"Combat content: {kind} {code}: {field}={tag!r} has no readable Russian label."
        )


def validate_combat_content(
    heroes: list[dict] | None = None,
    bosses: list[dict] | None = None,
) -> dict:
    """Validate loaded catalogs, returning nonblocking product warnings.

    Inactive owned heroes still need labels. Only active heroes provide release
    counter coverage. Neutral bosses have no faction advantage by definition.
    Open class/special/feature tags remain valid in the catalog/engine; production
    publication additionally requires their display labels here.
    """
    if heroes is None:
        heroes = load_hero_catalog()["heroes"]
    if bosses is None:
        bosses = load_boss_catalog()["bosses"]

    for hero in heroes:
        for field, labels in (
            ("faction", FACTION_LABELS), ("damage_type", DAMAGE_LABELS),
            ("attack_range", RANGE_LABELS), ("class_tag", CLASS_LABELS),
            ("special_trait", TRAIT_LABELS),
        ):
            _require_label("hero", hero["code"], field, hero[field], labels)
    for boss in bosses:
        _require_label("boss", boss["code"], "faction", boss["faction"], FACTION_LABELS)
        for tag in boss["features"]:
            _require_label("boss", boss["code"], "features", tag, TRAIT_LABELS)

    active_heroes = [hero for hero in heroes if bool(hero.get("active", True))]
    active_bosses = [boss for boss in bosses if bool(boss.get("active", True))]
    warnings = []
    for boss in active_bosses:
        code = boss["code"]
        faction = boss["faction"]
        if faction != "neutral" and not any(
            faction_multiplier_percent(hero["faction"], faction) == 200
            for hero in active_heroes
        ):
            raise ValueError(
                f"Combat content: boss {code}: faction={faction!r} has no ACTIVE hero with faction advantage."
            )
        if boss["ability_key"] == "magic_shield" and not any(
            hero["class_tag"] in ("mage", "magical") for hero in active_heroes
        ):
            raise ValueError(
                f"Combat content: boss {code}: magic_shield has no ACTIVE mage/magical counter."
            )
        if boss["ability_key"] == "mechanism" and not any(
            hero["class_tag"] == "technical" for hero in active_heroes
        ):
            raise ValueError(
                f"Combat content: boss {code}: mechanism has no ACTIVE technical counter."
            )
        if boss["ability_key"] == "rapier" and int(boss.get("reward_shields", 3)) == 0:
            warnings.append(
                f"boss {code}: rapier + reward_shields=0 — обход щитов не создаёт дополнительного эффекта; требуется продуктовое решение."
            )
    return {
        "active_heroes": len(active_heroes), "active_bosses": len(active_bosses),
        "warnings": warnings,
    }
