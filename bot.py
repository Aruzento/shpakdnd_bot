import asyncio

from aiogram import Bot, Dispatcher

from app.config import DB_PATH, TIMEZONE_NAME, TOKEN
from app.db.schema import init_db
from app.handlers import ROUTERS
from app.services.timers import restore_timers


async def main():
    init_db()

    print(f"База данных: {DB_PATH}")
    print(f"Часовой пояс: {TIMEZONE_NAME}")

    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    for router in ROUTERS:
        dp.include_router(router)

    await restore_timers(bot)

    print("Бот запущен.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
