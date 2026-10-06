import unittest

from app.mini.presentation import hero_trait_lines
from app.mini.rules import RULES_TEXT


class MiniReleaseRulesTests(unittest.TestCase):
    def test_battle_hero_is_selected_separately_and_locked_at_start(self):
        self.assertNotIn("Перед боссом выбери героя активным", RULES_TEXT)
        self.assertNotIn("именно он будет драться", RULES_TEXT)
        self.assertIn("После регистрации", RULES_TEXT)
        self.assertIn("🎴 Выбрать героя", RULES_TEXT)
        self.assertIn("именно на этот бой", RULES_TEXT)
        self.assertIn("После начала боя сменить героя нельзя", RULES_TEXT)
        self.assertIn("Смена активного героя в коллекции не меняет текущий бой", RULES_TEXT)

    def test_rules_explain_faction_cycle_and_multipliers(self):
        self.assertIn("Простолюдины → Звери → Монстры → Воины → Тьма → Простолюдины", RULES_TEXT)
        for multiplier in ("×2", "×0.25", "×1"):
            self.assertIn(multiplier, RULES_TEXT)
        self.assertIn("нейтральная с любой стороны", RULES_TEXT)

    def test_rules_describe_class_exceptions_and_information_only_tags(self):
        for text in ("mage", "magical", "0 урона HP", "Технический класс", "20%",
                     "после пассивки и фракции, до бонуса зелья", "Тип урона, дальность",
                     "не дают универсальных модификаторов урона"):
            self.assertIn(text, RULES_TEXT)

    def test_rules_keep_timer_shields_rewards_and_consolation(self):
        for text in ("4 часа", "сначала ломает щиты", "10 процентных пунктов",
                     "хотя бы раз ударил", "фантомного участия", "утешительные осколки",
                     "÷ 10", "округлением вниз"):
            self.assertIn(text, RULES_TEXT)
        self.assertLessEqual(len(RULES_TEXT), 4096)

    def test_all_damage_labels_render_with_agreed_terminology(self):
        for code, expected in (("slashing", "Режущий"), ("piercing", "Колющий"),
                               ("bludgeoning", "Дробящий"), ("magic", "Магический")):
            with self.subTest(code=code):
                line = hero_trait_lines({"damage_type": code})[0]
                self.assertTrue(line.startswith(expected + " | "))
                self.assertNotIn("Рубящий", line)
