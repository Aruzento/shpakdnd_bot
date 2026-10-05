import asyncio

from aiogram import Bot, Dispatcher

from app.config import DB_PATH, TIMEZONE_NAME, TOKEN
from app.db.schema import init_db
from app.handlers import ROUTERS
from app.mini.boss.schema import init_boss_db
from app.mini.boss.watcher import boss_watch_loop
from app.mini.commands import configure_mini_commands
from app.mini.heroes import sync_hero_catalog
from app.mini.shop import sync_shop_catalog
from app.mini.schema import init_mini_db
from app.mini.worlds import sync_configured_mini_worlds
from app.services.timers import restore_timers


async def main():
    init_db()
    init_mini_db()
    init_boss_db()
    mini_worlds = sync_configured_mini_worlds()
    hero_count = sync_hero_catalog()

    for world in mini_worlds:
        sync_shop_catalog(world["id"])

    print(f"База данных: {DB_PATH}")
    print(f"Часовой пояс: {TIMEZONE_NAME}")
    print(f"D&D Mini: героев в каталоге: {hero_count}")

    for world in mini_worlds:
        print(
            "D&D Mini: "
            f"chat={world['chat_id']} "
            f"topic={world['thread_id']} "
            f"name={world['name']}"
        )

    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    try:
        await configure_mini_commands(bot)
    except Exception as error:
        # Ошибка настройки меню команд не должна останавливать самого бота.
        print(f"Предупреждение: не удалось обновить Mini-команды: {error}")

    for router in ROUTERS:
        dp.include_router(router)

    await restore_timers(bot)

    boss_watch_task = asyncio.create_task(boss_watch_loop(bot))
    print("Бот запущен.")
    try:
        await dp.start_polling(bot)
    finally:
        boss_watch_task.cancel()
        try:
            await boss_watch_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
