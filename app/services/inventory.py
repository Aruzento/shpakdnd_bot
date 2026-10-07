import re


MAX_ITEM_QUANTITY = 999999


def parse_inventory_item(
    raw_value: str,
) -> tuple[str, int, str] | None:
    """
    Поддерживаем:

    Меч
    Меч x3
    Меч ×3
    Меч х3
    Меч x2 :: Стальной клинок
    """
    raw_value = raw_value.strip()

    if not raw_value:
        return None

    if "::" in raw_value:
        item_part, description = raw_value.split(
            "::",
            maxsplit=1,
        )
        description = description.strip()
    else:
        item_part = raw_value
        description = ""

    item_part = item_part.strip()

    match = re.fullmatch(
        r"(.+?)(?:\s+[xх×](\d+))?",
        item_part,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    name = match.group(1).strip()
    if not name:
        return None

    quantity_text = match.group(2)
    quantity = int(quantity_text) if quantity_text else 1

    if quantity <= 0 or quantity > MAX_ITEM_QUANTITY:
        return None

    return name, quantity, description


def format_inventory_item(
    name: str,
    quantity: int,
    description: str,
) -> str:
    quantity_text = f" ×{quantity}" if quantity > 1 else ""
    line = f"• {name}{quantity_text}"

    if description:
        line += f"\n  ↳ {description}"

    return line


def format_inventory(character_name: str, items: list) -> str:
    body="\n".join(format_inventory_item(name,quantity,description) for name,quantity,description in items)
    return f"🎒 {character_name}:\n\n"+(body or "Инвентарь пуст.")
