from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.ui.context import personal_callback as _personal_callback
from aiogram.types import InlineKeyboardMarkup



@editorial_screen
def back_menu(world_id: int, user_id: int, action: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('navigation.back'),
                    callback_data=_personal_callback(
                        action, world_id, user_id
                    ),
                )
            ]
        ]
    )


@editorial_screen
def clip(value: str, limit: int) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"
