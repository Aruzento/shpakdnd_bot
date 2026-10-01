import asyncio
import re
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from app.config import TIMEZONE
from app.db.timers import (
    delete_timer,
    get_all_timers,
)


# Как и в исходной версии: один активный таймер на весь chat_id.
active_timers: dict[int, asyncio.Task] = {}


def parse_time(value: str) -> int | None:
    match = re.fullmatch(
        r"(\d+)(s|m|h)",
        value.lower(),
    )

    if not match:
        return None

    number = int(match.group(1))
    unit = match.group(2)

    if unit == "s":
        return number
    if unit == "m":
        return number * 60
    if unit == "h":
        return number * 3600

    return None


def parse_until(
    date_value: str,
    time_value: str,
) -> datetime | None:
    try:
        target = datetime.strptime(
            f"{date_value} {time_value}",
            "%d.%m.%Y %H:%M",
        )
        return target.replace(tzinfo=TIMEZONE)
    except ValueError:
        return None


def format_time(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return (
            f"{days} д. "
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    if hours > 0:
        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return f"{minutes:02d}:{seconds:02d}"


def timer_text(
    title: str,
    remaining: int,
    target_time: datetime,
) -> str:
    return (
        f"⏳ {title}\n\n"
        f"Осталось: {format_time(remaining)}\n\n"
        f"🎯 До: {target_time.strftime('%d.%m.%Y %H:%M')}"
    )


async def run_countdown(
    bot: Bot,
    chat_id: int,
    message_id: int,
    end_time: datetime,
    title: str,
):
    try:
        while True:
            now = datetime.now(TIMEZONE)
            remaining = int((end_time - now).total_seconds())

            if remaining <= 0:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"✅ {title}\n\nВремя вышло!",
                    )
                except TelegramRetryAfter as error:
                    await asyncio.sleep(error.retry_after + 1)
                    continue
                except TelegramNetworkError:
                    await asyncio.sleep(5)
                    continue
                except TelegramBadRequest as error:
                    print("Ошибка завершения таймера:", error)

                delete_timer(chat_id)
                break

            await asyncio.sleep(min(10, remaining))

            now = datetime.now(TIMEZONE)
            remaining = max(
                0,
                int((end_time - now).total_seconds()),
            )

            if remaining <= 0:
                continue

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=timer_text(
                        title,
                        remaining,
                        end_time,
                    ),
                )
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after + 1)
            except TelegramNetworkError:
                await asyncio.sleep(5)
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    continue

                print("Ошибка таймера:", error)
                delete_timer(chat_id)
                break

    except asyncio.CancelledError:
        # При рестарте запись в SQLite остаётся и будет восстановлена.
        raise

    finally:
        current_task = asyncio.current_task()

        if active_timers.get(chat_id) is current_task:
            active_timers.pop(chat_id, None)


async def restore_timers(bot: Bot):
    timers = get_all_timers()

    if not timers:
        print("Сохранённых таймеров нет.")
        return

    print("Сохранённых таймеров:", len(timers))

    for chat_id, message_id, title, end_time_string in timers:
        try:
            end_time = datetime.fromisoformat(end_time_string)
        except ValueError:
            delete_timer(chat_id)
            continue

        task = asyncio.create_task(
            run_countdown(
                bot=bot,
                chat_id=chat_id,
                message_id=message_id,
                end_time=end_time,
                title=title,
            )
        )
        active_timers[chat_id] = task
