"""Shared Russian labels for Combat v2 cards; balance stays in catalogs."""

FACTION_LABELS = {
    "commoners": "🧑 Простолюдины", "beasts": "🐾 Звери",
    "monsters": "👾 Монстры", "warriors": "⚔️ Воины",
    "dark": "🌑 Тьма", "neutral": "⚖️ Нейтральная",
}
DAMAGE_LABELS = {
    "slashing": "Рубящий", "piercing": "Колющий",
    "bludgeoning": "Дробящий", "magic": "Магический",
}
CLASS_LABELS = {
    "none": "Без класса", "magical": "Магический класс",
    "technical": "Технический класс", "martial": "Боевой класс",
    "warrior": "Воин", "guardian": "Страж", "mage": "Маг",
    "sneaky": "Плут", "healer": "Целитель", "beast": "Зверь",
}
RANGE_LABELS = {"melee": "Ближний бой", "ranged": "Дальний бой"}
TRAIT_LABELS = {
    "none": "Нет особого свойства", "undead": "Нежить", "construct": "Конструкт",
    "flying": "Летающий", "demon": "Демон", "armored": "Бронированный",
    "holy": "Святой",
}


def tag_label(value: str, labels: dict) -> str:
    return labels.get(value, str(value or "Нет").replace("_", " "))


def faction_label(value: str) -> str:
    return FACTION_LABELS.get(value, "❓ Неизвестная фракция")


def hero_heading(hero: dict) -> str:
    return f"{hero['name']} • {faction_label(hero.get('faction', 'commoners'))} • ⭐ {int(hero.get('stars', 0))}"


def hero_trait_lines(hero: dict) -> list[str]:
    return [
        f"{tag_label(hero.get('damage_type', 'slashing'), DAMAGE_LABELS)} | "
        f"{tag_label(hero.get('class_tag', 'none'), CLASS_LABELS)}",
        f"{tag_label(hero.get('attack_range', 'melee'), RANGE_LABELS)} | "
        f"{tag_label(hero.get('special_trait', 'none'), TRAIT_LABELS)}",
    ]
