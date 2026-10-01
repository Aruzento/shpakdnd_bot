from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.context import (
    check_topic_admin_permission,
    get_character_name,
    get_thread_id,
    normalize_username,
)
from app.db.characters import (
    get_character_profile,
    save_character_profile,
)


router = Router(name="characters")


@router.message(Command("char"))
async def char_handler(message: Message):
    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/char @username"
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

    profile = get_character_profile(
        chat_id,
        thread_id,
        username,
    )

    if profile is None:
        level_text = "не указан"
        class_text = "не указан"
        race_text = "не указана"
    else:
        level, class_name, race = profile
        level_text = str(level)
        class_text = class_name
        race_text = race

    await message.answer(
        f"{character_name}:\n"
        f"уровень: {level_text}\n"
        f"Класс: {class_text}\n"
        f"Раса: {race_text}"
    )


@router.message(Command("charset"))
async def charset_handler(message: Message):
    """
    Пример:
    /charset @daniil_savenko 5 | Монах | Человек
    """

    if not await check_topic_admin_permission(message):
        return

    parts = message.text.split(
        maxsplit=2
    )

    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "/charset @username "
            "5 | Монах | Человек"
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

    profile_parts = [
        value.strip()
        for value in parts[2].split("|")
    ]

    if len(profile_parts) != 3:
        await message.answer(
            "Использование:\n"
            "/charset @username "
            "5 | Монах | Человек"
        )
        return

    level_text, class_name, race = profile_parts

    try:
        level = int(level_text)
    except ValueError:
        await message.answer(
            "❌ Уровень должен быть целым числом."
        )
        return

    if level < 1:
        await message.answer(
            "❌ Уровень должен быть больше 0."
        )
        return

    if not class_name:
        await message.answer(
            "❌ Не указан класс."
        )
        return

    if not race:
        await message.answer(
            "❌ Не указана раса."
        )
        return

    save_character_profile(
        chat_id,
        thread_id,
        username,
        level,
        class_name,
        race,
    )

    await message.answer(
        f"✅ Данные {character_name} сохранены:\n\n"
        f"уровень: {level}\n"
        f"Класс: {class_name}\n"
        f"Раса: {race}"
    )
