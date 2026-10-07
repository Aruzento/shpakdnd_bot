import unittest
from app.mini import handlers as mini_ui

class LauncherTests(unittest.TestCase):
    def test_summon_is_separate_green_row_between_new_submenus(self):
        menu = mini_ui._player_menu(1, 101)
        self.assertEqual([b.text for b in menu.inline_keyboard[0]], ["⚔️ Приключения", "🧙 Герой"])
        self.assertEqual(len(menu.inline_keyboard[1]), 1)
        self.assertEqual(menu.inline_keyboard[1][0].text, "✨ Призвать героя")
        self.assertEqual(menu.inline_keyboard[1][0].style, "success")
        self.assertTrue(all(b.style is None for i, row in enumerate(menu.inline_keyboard)
                            if i != 1 for b in row))
