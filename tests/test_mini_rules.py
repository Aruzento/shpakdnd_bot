import unittest

from app.mini.presentation import hero_trait_lines
from app.mini.rules import RULES_TEXT, SECTIONS


class MiniReleaseRulesTests(unittest.TestCase):
    def test_active_hero_is_shared_and_battle_hero_locked_at_start(self):
        text=SECTIONS['bosses'][1]
        for value in ('в коллекции или меню босса герой становится активным','До начала боя его можно менять','После старта',
                      '🎴 Выбрать героя','Смена активного героя в коллекции не меняет уже начавшийся бой'):
            self.assertIn(value,text)

    def test_rules_explain_faction_cycle_and_multipliers(self):
        text=SECTIONS['factions'][1]
        self.assertIn('Простолюдины → Звери → Монстры → Воины → Тьма → Простолюдины',text)
        for value in ('×2','×0.25','×1','Остальные сочетания','Звери сильны против Монстров'):
            self.assertIn(value,text)

    def test_rules_describe_class_exceptions_and_executable_traits(self):
        text=SECTIONS['factions'][1]
        for value in ('Маг снимает магический щит','сам удар при этом не ранит босса',
                      'Технический герой','летающего врага','Броня','нежить может воскреснуть'):
            self.assertIn(value,text)
        all_text=RULES_TEXT+' '.join(text for _,text in SECTIONS.values())
        for technical in ('mage','magical','Combat v2','effect_key','после пассивки и фракции'):
            self.assertNotIn(technical,all_text)

    def test_rules_keep_timer_shields_rewards_and_consolation(self):
        text=SECTIONS['bosses'][1]
        for value in ('4 часа','ломают защитные щиты','хотя бы одной атакой',
                      'фантомного участия','утешительные осколки','на 10 с округлением вниз'):
            self.assertIn(value,text)
        for _,text in SECTIONS.values(): self.assertLessEqual(len(text),4096)

    def test_all_damage_labels_render_with_agreed_terminology(self):
        for code, expected in (("slashing", "Режущий"), ("piercing", "Колющий"),
                               ("bludgeoning", "Дробящий"), ("magic", "Магический")):
            with self.subTest(code=code):
                line = hero_trait_lines({"damage_type": code})[0]
                self.assertTrue(line.startswith(expected + " | "))
                self.assertNotIn("Рубящий", line)
