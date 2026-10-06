from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, EphemeralMessageParameters, InlineKeyboardMarkup


def replacement_ephemeral_kwargs(callback: CallbackQuery) -> dict:
    """Параметры нового ephemeral-сообщения по текущему callback."""
    return {
        "ephemeral_message_parameters": EphemeralMessageParameters(
            receiver_user_id=callback.from_user.id,
            callback_query_id=callback.id,
            replace_callback_query_message=False,
        ),
    }



async def send_private_text_from_callback(
    callback: CallbackQuery,
    world: dict,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    sent = await callback.bot.send_message(
        chat_id=world["chat_id"],
        message_thread_id=world["thread_id"] or None,
        text=text,
        reply_markup=reply_markup,
        **replacement_ephemeral_kwargs(callback),
    )
    await delete_current_ephemeral(callback)
    return sent



async def delete_current_ephemeral(callback: CallbackQuery) -> bool:
    message = callback.message
    if message is None or message.ephemeral_message_id is None:
        return False

    try:
        await message.delete_ephemeral()
    except TelegramAPIError as error:
        print(
            "Не удалось удалить предыдущее ephemeral-сообщение: "
            f"error={type(error).__name__}: {error}"
        )
        return False

    return True



async def send_private_from_launcher(
    callback: CallbackQuery,
    world: dict,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    """
    Отправляет отдельное ephemeral-меню конкретному игроку.

    Не используем replace_callback_query_message=True:
    публичный закреп остаётся на месте, а личное меню приходит
    отдельным приватным сообщением. Это надёжнее в forum topics
    и на клиентах Telegram, где overlay ещё работает нестабильно.
    """
    return await callback.bot.send_message(
        chat_id=world["chat_id"],
        message_thread_id=world["thread_id"] or None,
        text=text,
        reply_markup=reply_markup,
        ephemeral_message_parameters=EphemeralMessageParameters(
            receiver_user_id=callback.from_user.id,
            callback_query_id=callback.id,
        ),
    )



async def edit_private(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    if callback.message is None:
        return

    if callback.message.ephemeral_message_id is not None:
        sent = await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            message_thread_id=callback.message.message_thread_id or None,
            text=text,
            reply_markup=reply_markup,
            **replacement_ephemeral_kwargs(callback),
        )
        await delete_current_ephemeral(callback)
        return sent

    # Резерв для старого публичного меню, если оно осталось после обновления.
    # Публичный launcher здесь не удаляем.
    return await callback.message.edit_text(
        text=text,
        reply_markup=reply_markup,
    )

