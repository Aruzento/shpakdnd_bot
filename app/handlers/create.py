import shlex
import sqlite3

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.context import (
    check_topic_admin_permission,
    get_character_name,
    get_thread_id,
    normalize_username,
)
from app.db.character_registry import create_character


router = Router(name="create_character")


@router.message(Command("create"))
async def create_handler(message: Message):
    """
    Формат:
    /create @username "Имя персонажа"
    """

    if not await check_topic_admin_permission(message):
        return

    try:
        parts = shlex.split(message.text)
    except ValueError:
        await message.answer(
            "❌ Не удалось разобрать команду.\n\n"
            "Использование:\n"
            '/create @username "Имя персонажа"'
        )
        return

    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            '/create @username "Имя персонажа"\n\n'
            "Если в имени несколько слов — используй кавычки."
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(message)

    username = normalize_username(parts[1])
    character_name = parts[2].strip()

    if not character_name:
        await message.answer(
            "❌ Имя персонажа не может быть пустым."
        )
        return

    if len(character_name) > 100:
        await message.answer(
            "❌ Имя персонажа слишком длинное. "
            "Максимум — 100 символов."
        )
        return

    existing_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if existing_name is not None:
        await message.answer(
            f"❌ У {username} уже есть персонаж "
            f"в этой теме: {existing_name}"
        )
        return

    try:
        create_character(
            chat_id,
            thread_id,
            username,
            character_name,
        )
    except sqlite3.IntegrityError:
        await message.answer(
            f"❌ У {username} уже есть персонаж "
            "в этой теме."
        )
        return

    await message.answer(
        "✅ Персонаж создан!\n\n"
        f"{character_name}\n"
        f"Игрок: {username}"
    )
