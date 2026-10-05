from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.context import (
    check_inventory_permission,
    get_character_name,
    get_thread_id,
    get_topic_characters,
    normalize_username,
)
from app.db.inventory import (
    add_inventory_item,
    clean_all_inventory,
    clean_inventory,
    delete_inventory_item,
    get_all_inventory,
    get_inventory,
)
from app.services.inventory import (
    format_inventory_item,
    parse_inventory_item,
)


router = Router(name="inventory")


@router.message(Command("add"))
async def add_handler(message: Message):
    if not await check_inventory_permission(message):
        return

    parts = message.text.split(maxsplit=2)

    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "/add @username Меч\n"
            "/add @username Зелье x3\n"
            "/add @username Меч x2 :: Стальной клинок\n\n"
            "Несколько предметов можно разделять запятыми."
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    username = normalize_username(parts[1])

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    raw_items = [
        item.strip()
        for item in parts[2].split(",")
        if item.strip()
    ]

    if not raw_items:
        await message.answer("Не указаны предметы.")
        return

    parsed_items = []

    for raw_item in raw_items:
        parsed_item = parse_inventory_item(raw_item)

        if parsed_item is None:
            await message.answer(
                "❌ Не понял предмет:\n"
                f"{raw_item}\n\n"
                "Форматы:\n"
                "Меч\n"
                "Зелье x3\n"
                "Меч x2 :: Стальной клинок"
            )
            return

        parsed_items.append(parsed_item)

    added_lines = []

    for name, quantity, description in parsed_items:
        add_inventory_item(
            chat_id,
            thread_id,
            username,
            name,
            quantity,
            description,
        )
        added_lines.append(
            format_inventory_item(
                name,
                quantity,
                description,
            )
        )

    await message.answer(
        f"🎒 {character_name} получил:\n\n"
        + "\n".join(added_lines)
    )


@router.message(Command("del"))
async def del_handler(message: Message):
    if not await check_inventory_permission(message):
        return

    parts = message.text.split(maxsplit=2)

    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "/del @username Меч\n"
            "/del @username Зелье x3"
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    username = normalize_username(parts[1])

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    parsed_item = parse_inventory_item(parts[2])

    if parsed_item is None:
        await message.answer(
            "❌ Не понял предмет.\n\n"
            "Форматы:\n"
            "/del @username Меч\n"
            "/del @username Зелье x3"
        )
        return

    requested_name, requested_quantity, _ = parsed_item

    deleted = delete_inventory_item(
        chat_id,
        thread_id,
        username,
        requested_name,
        requested_quantity,
    )

    if deleted is None:
        await message.answer(
            f"❌ У {character_name} нет предмета:\n"
            f"{requested_name}"
        )
        return

    (
        stored_name,
        removed_quantity,
        remaining_quantity,
        description,
    ) = deleted

    removed_text = format_inventory_item(
        stored_name,
        removed_quantity,
        description,
    )

    if remaining_quantity > 0:
        await message.answer(
            f"🗑 {character_name} потерял:\n\n"
            f"{removed_text}\n\n"
            f"Осталось: {remaining_quantity}"
        )
    else:
        await message.answer(
            f"🗑 {character_name} потерял:\n\n"
            f"{removed_text}\n\n"
            "Предмет полностью удалён из инвентаря."
        )


@router.message(Command("inv"))
async def inventory_handler(message: Message):
    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/inv @username\n"
            "/inv all"
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    target = parts[1].strip()

    if target.lower() == "all":
        characters = get_topic_characters(
            chat_id,
            thread_id,
        )

        if not characters:
            await message.answer(
                "❌ Для этой темы не настроены персонажи.\n\n"
                "Используй /chatid, чтобы узнать ID темы."
            )
            return

        all_inventory = get_all_inventory(
            chat_id,
            thread_id,
        )

        blocks = []

        for username, character_name in characters.items():
            items = all_inventory.get(username, [])

            if not items:
                continue

            item_text = "\n".join(
                format_inventory_item(
                    name,
                    quantity,
                    description,
                )
                for name, quantity, description in items
            )

            blocks.append(
                f"{character_name}:\n{item_text}"
            )

        if not blocks:
            await message.answer(
                "🎒 Ни у одного персонажа "
                "в этой теме нет предметов."
            )
            return

        await message.answer(
            "🎒 Инвентарь\n\n"
            + "\n\n".join(blocks)
        )
        return

    username = normalize_username(target)

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    items = get_inventory(
        chat_id,
        thread_id,
        username,
    )

    if not items:
        await message.answer(
            f"🎒 {character_name}:\n\n"
            "Инвентарь пуст."
        )
        return

    item_text = "\n".join(
        format_inventory_item(
            name,
            quantity,
            description,
        )
        for name, quantity, description in items
    )

    await message.answer(
        f"🎒 {character_name}:\n\n"
        f"{item_text}"
    )


@router.message(Command("clean"))
async def clean_handler(message: Message):
    if not await check_inventory_permission(message):
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/clean @username\n"
            "/clean all"
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    target = parts[1].strip()

    if target.lower() == "all":
        clean_all_inventory(chat_id, thread_id)
        await message.answer(
            "💨 Все персонажи этой темы потеряли вещи."
        )
        return

    username = normalize_username(target)

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    clean_inventory(
        chat_id,
        thread_id,
        username,
    )

    await message.answer(
        f"🧹 Инвентарь {character_name} очищен."
    )
