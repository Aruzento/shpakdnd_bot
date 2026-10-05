from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
)


# Основной вход в Mini — кнопка в закрепе.
# В меню команд оставляем только приватный ввод имени.
MINI_COMMANDS = {
    "minicreate": BotCommand(
        command="minicreate",
        description="Создать Mini-персонажа",
        is_ephemeral=True,
    ),
}

REMOVED_MINI_COMMANDS = {
    "mini",
    "minipanel",
    "minicreate",
}


async def configure_mini_commands(bot: Bot):
    scope = BotCommandScopeAllGroupChats()

    existing = await bot.get_my_commands(scope=scope)
    result = [
        command
        for command in existing
        if command.command not in REMOVED_MINI_COMMANDS
    ]
    result.extend(MINI_COMMANDS.values())

    await bot.set_my_commands(result, scope=scope)
