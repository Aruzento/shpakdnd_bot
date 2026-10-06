
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.mini.boss.catalog import load_boss_catalog
from app.mini.catalog import load_hero_catalog
from app.mini.combat.tags import CREATURE_TRAITS, LEGACY_HERO_TRAITS
from app.mini.presentation import TRAIT_LABELS, FEATURE_DESCRIPTIONS, TRAIT_DESCRIPTIONS
from app.mini.content_safety import validate_combat_content

class CreatureContentTests(unittest.TestCase):
    def load_bosses(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"bosses.json"
            path.write_text(json.dumps(data),encoding="utf8")
            with patch("app.mini.boss.catalog.BOSSES_JSON",path):
                return load_boss_catalog()

    def test_old_hero_json_missing_combat_fields_uses_safe_defaults(self):
        data=copy.deepcopy(load_hero_catalog())
        for hero in data["heroes"]:
            for field in LEGACY_HERO_TRAITS:hero.pop(field)
            hero.pop("passive_key")
        with patch("app.mini.catalog._read_json",return_value=data):
            result=load_hero_catalog()
        for hero in result["heroes"]:
            for field,value in LEGACY_HERO_TRAITS.items():self.assertEqual(hero[field],value)
            self.assertEqual(hero["passive_key"],"none")

    def test_old_boss_json_missing_fields_has_no_features_or_ability(self):
        data=copy.deepcopy(load_boss_catalog())
        for boss in data["bosses"]:
            for key in ("faction","ability_key","ability_text","features","ability_config"):
                boss.pop(key,None)
        for boss in self.load_bosses(data)["bosses"]:
            self.assertEqual(boss["faction"],"commoners")
            self.assertEqual(boss["ability_key"],"none")
            self.assertEqual(boss["features"],[])

    def test_verified_legacy_mechanism_template_is_explicitly_migrated(self):
        data=copy.deepcopy(load_boss_catalog())
        iron=next(b for b in data["bosses"] if b["code"]=="iron_juggernaut")
        iron.update(ability_key="mechanism",ability_text="Механизм: −20%",ability_config={"damage_percent":80})
        migrated=next(b for b in self.load_bosses(data)["bosses"] if b["code"]=="iron_juggernaut")
        self.assertEqual(migrated["ability_key"],"none")
        self.assertEqual(migrated["ability_config"],{})
        self.assertEqual(migrated["features"],iron["features"])
        for field in ("faction","max_hp","reward_coins","reward_shields","reward_items"):
            self.assertEqual(migrated[field],iron[field])

    def test_unknown_duplicate_mechanism_assignment_requires_explicit_migration(self):
        data=copy.deepcopy(load_boss_catalog())
        data["bosses"][0].update(ability_key="mechanism",features=["construct"])
        with self.assertRaisesRegex(ValueError,"mechanism.*construct.*explicit content migration"):
            self.load_bosses(data)

    def test_registered_creature_tags_have_russian_names_and_descriptions(self):
        for tag in CREATURE_TRAITS-{"none"}:
            self.assertIn(tag,FEATURE_DESCRIPTIONS);self.assertIn(tag,TRAIT_DESCRIPTIONS)
            self.assertRegex(TRAIT_LABELS[tag],r"[А-Яа-яЁё]")
        self.assertIn("demon",TRAIT_DESCRIPTIONS)

    def test_demonic_hero_and_feature_have_valid_release_labels(self):
        heroes=copy.deepcopy(load_hero_catalog()["heroes"]);bosses=copy.deepcopy(load_boss_catalog()["bosses"])
        heroes[0]["special_trait"]="demonic";bosses[0]["features"].append("demonic")
        validate_combat_content(heroes,bosses)

    def test_existing_invalid_present_hero_values_do_not_fall_through_to_defaults(self):
        for field in LEGACY_HERO_TRAITS:
            data=copy.deepcopy(load_hero_catalog());data["heroes"][0][field]=None
            with patch("app.mini.catalog._read_json",return_value=data),self.assertRaises(ValueError):
                load_hero_catalog()


    def test_hero_selector_shows_traits_and_stays_within_telegram_text_limit(self):
        from app.mini.boss.presentation import selection_details
        heroes=[dict(id=i,name="Герой",stars=2,faction="dark",damage_type="magic",class_tag="mage",
                     attack_range="ranged",special_trait="holy",attack=123,passive_text="X"*1000) for i in range(8)]
        text=selection_details(heroes,0)
        self.assertIn("Тьма",text); self.assertIn("Магический | Маг",text)
        self.assertIn("Дальний бой | Святой",text)
        self.assertIn("Урон: 123",text)
        self.assertLessEqual(len(text)+1024+40,4096)

    def test_hero_card_shows_traits_before_description_and_effect(self):
        from app.mini.ui.hero_cards import hero_caption
        hero=dict(name="Герой",stars=1,faction="commoners",damage_type="magic",class_tag="mage",
                  attack_range="ranged",special_trait="holy",attack=123,
                  description="Описание.",passive_text="Пассивка.")
        text=hero_caption(hero)
        self.assertIn("Дальний бой | Святой",text)
        self.assertLess(text.index("Урон: 123"),text.index("Описание."))
        self.assertLess(text.index("Описание."),text.index("Особый эффект:"))
        self.assertIn("25% промаха",text)
