import secrets
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import SUPPORTED_DICE
from app.context import (
    get_inventory_admin,
    get_thread_id,
)


router = Router(name="common")


@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "🎲 Шпаковский ДНДнарь\n\n"
        "⏱ Таймер:\n"
        "/timer 5m Перерыв\n"
        "/timer until 01.10.2026 18:30 Игра\n"
        "/stop\n\n"
        "🎲 Кубики:\n"
        "/roll d4\n"
        "/roll d6\n"
        "/roll d8\n"
        "/roll d10\n"
        "/roll d12\n"
        "/roll d20\n"
        "/roll d100\n\n"
        "🧙 Обычный D&D:\n"
        '/create @username "Имя персонажа"\n'
        "/char @username\n"
        "/charset @username 5 | Монах | Человек\n"
        "/lvlup\n\n"
        "🎒 Инвентарь текущей темы:\n"
        "/inv @username\n"
        "/inv all\n\n"
        "Управление обычным D&D:\n"
        "/admadd CHAT:THEME @user предмет\n"
        "/admdel CHAT:THEME @user предмет или ALL\n"
        "/adminv CHAT:THEME @user\n"
        "/admcharset CHAT:THEME @user 5 | Монах | Человек\n\n"
        "D&D Mini: личное меню в закрепе, 🏰 Испытания и 🛡 Экипировка.\n"
        "Владелец Mini: /superlook /superadd /superdel /superchars /superluck\n\n"
        "ℹ️ Служебное:\n"
        "/chatid"
    )


@router.message(Command("chatid"))
async def chatid_handler(message: Message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)

    admin = get_inventory_admin(chat_id, thread_id)
    admin_text = admin if admin is not None else "не назначен"

    await message.answer(
        f"ID чата:\n{chat_id}\n\n"
        f"ID темы:\n{thread_id}\n\n"
        f"Администратор этой темы:\n{admin_text}"
    )


@router.message(Command("roll"))
async def roll_handler(message: Message):
    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "🎲 Доступно:\n"
            "d4, d6, d8, d10, d12, d20, d100"
        )
        return

    match = re.fullmatch(r"d(\d+)", parts[1].lower())

    if not match:
        await message.answer("🎲 Не понял кубик.")
        return

    sides = int(match.group(1))

    if sides not in SUPPORTED_DICE:
        await message.answer(
            "🎲 Доступно:\n"
            "d4, d6, d8, d10, d12, d20, d100"
        )
        return

    result = secrets.randbelow(sides) + 1

    if sides == 20 and result == 1:
        text = (
            "🎲 d20: 1\n\n"
            "💀 ЕДИНИЦА, КРИТИЧЕСКАЯ НЕУДАЧА!"
        )
    elif sides == 20 and result == 20:
        text = (
            "🎲 d20: 20\n\n"
            "🔥 О БОЖЕ МОЙ, КРИТИЧЕСКАЯ УДАЧА!"
        )
    else:
        text = f"🎲 d{sides}: {result}"

    await message.answer(text)
