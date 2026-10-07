from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.context import get_thread_id,get_topic_characters,get_character_name,normalize_username
from app.db.inventory import get_inventory,get_all_inventory
from app.services.inventory import format_inventory_item,format_inventory
from app.topic_guard import allow_dnd

router=Router(name="inventory")


@router.message(Command("inv"))
async def inventory_handler(message: Message):
    if not await allow_dnd(message):
        return
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

    await message.answer(format_inventory(character_name,items))
