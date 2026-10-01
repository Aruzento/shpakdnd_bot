import unittest

from app.services.inventory import (
    format_inventory_item,
    parse_inventory_item,
)


class InventoryParserTests(unittest.TestCase):
    def test_plain_item(self):
        self.assertEqual(
            parse_inventory_item("Меч"),
            ("Меч", 1, ""),
        )

    def test_quantity(self):
        self.assertEqual(
            parse_inventory_item("Зелье x3"),
            ("Зелье", 3, ""),
        )

    def test_description(self):
        self.assertEqual(
            parse_inventory_item("Меч x2 :: Стальной клинок"),
            ("Меч", 2, "Стальной клинок"),
        )

    def test_format(self):
        self.assertEqual(
            format_inventory_item("Зелье", 3, "Лечит"),
            "• Зелье ×3\n  ↳ Лечит",
        )


if __name__ == "__main__":
    unittest.main()
