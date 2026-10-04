from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
)


# /mini больше не показываем в меню команд:
# основная точка входа — закреплённая кнопка.
# /minipanel также не регистрируем, это одноразовая админ-команда.
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
    """
    Убирает старый /mini из меню команд.
    Оставляет только ephemeral /minicreate для приватного ввода имени.
    """
    scope = BotCommandScopeAllGroupChats()

    existing = await bot.get_my_commands(
        scope=scope,
    )

    result = [
        command
        for command in existing
        if command.command not in REMOVED_MINI_COMMANDS
    ]

    result.extend(MINI_COMMANDS.values())

    await bot.set_my_commands(
        result,
        scope=scope,
    )
