import copy
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.boss.catalog import load_boss_catalog
from app.mini.boss.matchups import faction_multiplier_percent
from app.mini.boss.public import format_public_boss
from app.mini.catalog import load_hero_catalog
from app.mini.content_safety import validate_combat_content
from app.mini.presentation import (
    CLASS_LABELS, DAMAGE_LABELS, FACTION_LABELS, RANGE_LABELS, TRAIT_LABELS,
    faction_label, hero_trait_lines,
)


class CombatContentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.heroes = load_hero_catalog()["heroes"]
        self.bosses = load_boss_catalog()["bosses"]

    def test_current_catalog_has_active_faction_and_class_counters(self):
        result = validate_combat_content(self.heroes, self.bosses)
        self.assertEqual(result["active_heroes"], sum(h["active"] for h in self.heroes))
        self.assertEqual(result["active_bosses"], sum(b["active"] for b in self.bosses))
        for boss in self.bosses:
            if not boss["active"]:
                continue
            with self.subTest(boss=boss["code"]):
                self.assertTrue(any(h["active"] and faction_multiplier_percent(h["faction"], boss["faction"]) == 200
                                    for h in self.heroes))
                if boss["ability_key"] == "magic_shield":
                    self.assertTrue(any(h["active"] and h["class_tag"] in ("mage", "magical") for h in self.heroes))
                if boss["ability_key"] == "mechanism":
                    self.assertTrue(any(h["active"] and h["class_tag"] == "technical" for h in self.heroes))

    def test_each_current_boss_rejects_missing_active_faction_counter(self):
        for boss in self.bosses:
            with self.subTest(boss=boss["code"]):
                heroes = copy.deepcopy(self.heroes)
                for hero in heroes:
                    if faction_multiplier_percent(hero["faction"], boss["faction"]) == 200:
                        hero["active"] = False
                with self.assertRaisesRegex(ValueError, boss["code"] + ".*no ACTIVE hero with faction advantage"):
                    validate_combat_content(heroes, [boss])

    def test_magic_shield_requires_active_mage_counter(self):
        boss = next(b for b in self.bosses if b["ability_key"] == "magic_shield")
        heroes = copy.deepcopy(self.heroes)
        for hero in heroes:
            if hero["class_tag"] in ("mage", "magical"):
                hero["active"] = False
        with self.assertRaisesRegex(ValueError, "no ACTIVE mage/magical counter"):
            validate_combat_content(heroes, [boss])

    def test_legacy_magical_class_is_a_valid_magic_shield_counter(self):
        boss = next(b for b in self.bosses if b["ability_key"] == "magic_shield")
        heroes = copy.deepcopy(self.heroes)
        for hero in heroes:
            if hero["class_tag"] in ("mage", "magical"):
                hero["class_tag"] = "none"
        next(h for h in heroes if h["active"])["class_tag"] = "magical"
        validate_combat_content(heroes, [boss])

    def test_mechanism_requires_active_technical_counter(self):
        boss = next(b for b in self.bosses if b["ability_key"] == "mechanism")
        heroes = copy.deepcopy(self.heroes)
        for hero in heroes:
            if hero["class_tag"] == "technical":
                hero["active"] = False
        with self.assertRaisesRegex(ValueError, "no ACTIVE technical counter"):
            validate_combat_content(heroes, [boss])
        # The inactive compensation hero alone cannot satisfy release coverage.
        self.assertTrue(any(h["class_tag"] == "technical" for h in heroes))

    def test_inactive_boss_does_not_require_active_counters(self):
        boss = copy.deepcopy(self.bosses[0])
        boss["active"] = False
        result = validate_combat_content([], [boss])
        self.assertEqual(result["active_bosses"], 0)

    def test_neutral_boss_has_no_faction_advantage_requirement(self):
        boss = copy.deepcopy(self.bosses[0])
        boss.update(faction="neutral", ability_key="none")
        validate_combat_content([], [boss])
        boss["ability_key"] = "magic_shield"
        with self.assertRaisesRegex(ValueError, "mage/magical"):
            validate_combat_content([], [boss])

    def test_all_current_hero_tags_render_as_russian_labels(self):
        for hero in self.heroes:
            with self.subTest(hero=hero["code"]):
                for field, labels in (("faction", FACTION_LABELS), ("damage_type", DAMAGE_LABELS),
                                      ("attack_range", RANGE_LABELS), ("class_tag", CLASS_LABELS),
                                      ("special_trait", TRAIT_LABELS)):
                    self.assertIn(hero[field], labels)
                    self.assertRegex(labels[hero[field]], "[А-Яа-яЁё]")
                    self.assertNotRegex(labels[hero[field]], "[A-Za-z]")
                self.assertNotRegex(" ".join(hero_trait_lines(hero)) + faction_label(hero["faction"]), "[A-Za-z]")

    def test_all_current_boss_features_have_russian_card_labels(self):
        required = {"construct", "armored", "undead", "poisonous", "holy"}
        self.assertTrue(required <= {tag for b in self.bosses for tag in b["features"]})
        for boss in self.bosses:
            with self.subTest(boss=boss["code"]):
                card_boss = dict(boss, current_hp=boss["max_hp"], status="announced",
                                 features_json=json.dumps(boss["features"]), reward_items_json="[]")
                card = format_public_boss(card_boss, [])
                line = next(line for line in card.splitlines() if line.startswith("‼️ Особенности:"))
                self.assertNotRegex(line, "[A-Za-z]")
                for tag in boss["features"]:
                    self.assertIn(TRAIT_LABELS[tag], line)

    def test_each_hero_tag_requires_a_known_production_label(self):
        for field in ("faction", "damage_type", "attack_range", "class_tag", "special_trait"):
            with self.subTest(field=field):
                hero = copy.deepcopy(self.heroes[0])
                hero.update(active=False)
                hero[field] = "future_tag"
                with self.assertRaisesRegex(ValueError, hero["code"] + ".*" + field + ".*future_tag"):
                    validate_combat_content([hero], [])

    def test_open_class_and_special_tags_remain_structurally_valid(self):
        catalog = copy.deepcopy(load_hero_catalog())
        catalog["heroes"][0].update(class_tag="future_class", special_trait="future_trait")
        with patch("app.mini.catalog._read_json", return_value=catalog):
            loaded = load_hero_catalog()
        self.assertEqual(loaded["heroes"][0]["class_tag"], "future_class")
        self.assertEqual(loaded["heroes"][0]["special_trait"], "future_trait")
        with self.assertRaisesRegex(ValueError, "no readable Russian label"):
            validate_combat_content(loaded["heroes"], self.bosses)

    def test_unknown_boss_feature_reports_boss_and_tag(self):
        boss = copy.deepcopy(self.bosses[0])
        boss["features"].append("future_feature")
        with self.assertRaisesRegex(ValueError, boss["code"] + ".*features.*future_feature"):
            validate_combat_content(self.heroes, [boss])

    def test_raw_english_label_is_rejected(self):
        for label in ("Construct", "Конструкт construct"):
            with self.subTest(label=label), patch.dict(TRAIT_LABELS, {"construct": label}):
                with self.assertRaisesRegex(ValueError, "construct.*no readable Russian label"):
                    validate_combat_content(self.heroes, self.bosses)

    def test_rapier_without_shields_is_warning_and_does_not_change_catalog(self):
        before = copy.deepcopy(self.bosses)
        result = validate_combat_content(self.heroes, self.bosses)
        self.assertEqual(self.bosses, before)
        self.assertTrue(any("fallen_sun_champion" in warning and "rapier + reward_shields=0" in warning
                            for warning in result["warnings"]))
        boss = copy.deepcopy(next(b for b in self.bosses if b["code"] == "fallen_sun_champion"))
        boss["reward_shields"] = 1
        self.assertEqual(validate_combat_content(self.heroes, [boss])["warnings"], [])
