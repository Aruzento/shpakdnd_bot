import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
)
from aiogram.filters import Command
from aiogram.types import Message

from app.config import TIMEZONE
from app.db.timers import (
    delete_timer,
    get_timer,
    save_timer,
)
from app.services.timers import (
    active_timers,
    parse_time,
    parse_until,
    run_countdown,
    timer_text,
)


router = Router(name="timers")


@router.message(Command("timer"))
async def timer_handler(
    message: Message,
    bot: Bot,
):
    chat_id = message.chat.id

    existing_task = active_timers.get(chat_id)

    if existing_task and not existing_task.done():
        await message.answer(
            "⚠️ В этом чате уже работает таймер.\n"
            "Используй /stop."
        )
        return

    saved_timer = get_timer(chat_id)

    if saved_timer:
        try:
            saved_end_time = datetime.fromisoformat(
                saved_timer[3]
            )

            if saved_end_time > datetime.now(TIMEZONE):
                await message.answer(
                    "⚠️ В этом чате уже работает таймер.\n"
                    "Используй /stop."
                )
                return
        except ValueError:
            pass

        delete_timer(chat_id)

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "Например:\n"
            "/timer 5m Перерыв\n\n"
            "/timer until 01.10.2026 18:30 Игра"
        )
        return

    args = parts[1].strip()

    if args.lower().startswith("until "):
        until_parts = args.split(maxsplit=3)

        if len(until_parts) < 3:
            await message.answer(
                "Формат:\n"
                "/timer until 01.10.2026 18:30 Игра"
            )
            return

        target_time = parse_until(
            until_parts[1],
            until_parts[2],
        )

        if target_time is None:
            await message.answer(
                "Неверная дата.\n"
                "Формат: ДД.ММ.ГГГГ ЧЧ:ММ"
            )
            return

        if target_time <= datetime.now(TIMEZONE):
            await message.answer("Эта дата уже наступила.")
            return

        title = (
            until_parts[3].strip() or "Таймер"
            if len(until_parts) == 4
            else "Таймер"
        )
        end_time = target_time

    else:
        timer_parts = args.split(maxsplit=1)
        duration = parse_time(timer_parts[0])

        if duration is None:
            await message.answer(
                "Пример:\n"
                "/timer 5m Перерыв"
            )
            return

        if duration < 10:
            await message.answer(
                "Минимальный таймер — 10 секунд."
            )
            return

        title = (
            timer_parts[1].strip() or "Таймер"
            if len(timer_parts) == 2
            else "Таймер"
        )

        end_time = (
            datetime.now(TIMEZONE)
            + timedelta(seconds=duration)
        )

    remaining = max(
        0,
        int(
            (
                end_time
                - datetime.now(TIMEZONE)
            ).total_seconds()
        ),
    )

    timer_message = await message.answer(
        timer_text(
            title,
            remaining,
            end_time,
        )
    )

    save_timer(
        chat_id,
        timer_message.message_id,
        title,
        end_time,
    )

    task = asyncio.create_task(
        run_countdown(
            bot=bot,
            chat_id=chat_id,
            message_id=timer_message.message_id,
            end_time=end_time,
            title=title,
        )
    )
    active_timers[chat_id] = task


@router.message(Command("stop"))
async def stop_handler(
    message: Message,
    bot: Bot,
):
    chat_id = message.chat.id
    saved_timer = get_timer(chat_id)
    task = active_timers.get(chat_id)

    if (
        saved_timer is None
        and (task is None or task.done())
    ):
        await message.answer(
            "Сейчас активного таймера нет."
        )
        return

    if task and not task.done():
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

    if saved_timer:
        _, message_id, title, _ = saved_timer

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"❌ {title}\n\n"
                    "Таймер остановлен."
                ),
            )
        except (
            TelegramBadRequest,
            TelegramNetworkError,
        ):
            pass

    delete_timer(chat_id)
