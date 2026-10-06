from app.mini.ui.context import personal_callback as _personal_callback
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def back_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=_personal_callback(
                        "home", world_id, user_id
                    ),
                )
            ]
        ]
    )


def clip(value: str, limit: int) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"

