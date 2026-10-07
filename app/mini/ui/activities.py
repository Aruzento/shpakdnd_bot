from app.mini.ui.context import (
    personal_callback as _personal_callback,
    load_personal_context as _load_personal_context,
)
from app.mini.ui.transport import edit_private as _edit_private
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from app.mini.daily import claim_daily
from app.mini.rules import RULES_TEXT, SECTIONS
from app.mini.ui.context import load_extended_context
from app.mini.ui.navigation import back_menu

router = Router(name="mini_activities")

def _daily_result_menu(
    world_id: int,
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎒 Инвентарь",
                    callback_data=_personal_callback(
                        "inventory", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=_personal_callback(
                        "adventures", world_id, user_id
                    ),
                ),
            ]
        ]
    )


@router.callback_query(F.data.startswith("mini:daily:"))
async def daily_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context

    try:
        result = claim_daily(
            player["id"],
            player["character_name"],
        )
    except ValueError as error:
        await callback.answer(
            f"Не удалось выполнить дейлик: {error}",
            show_alert=True,
        )
        return

    if result["claimed"]:
        await callback.answer(
            f"+{result['coins_earned']} "
            f"{world['currency_name']}"
        )

        prefix = "⚔️ Ежедневное приключение завершено!"
    else:
        await callback.answer(
            "Сегодняшняя награда уже получена."
        )

        prefix = (
            "⚔️ Сегодня ты уже ходил в приключение.\n"
            "Вот чем оно закончилось:"
        )

    rare_line = ""
    if result["rarity"] == "rare":
        rare_line = "\n\n✨ Редкое событие!"
    elif result["rarity"] == "legendary":
        rare_line = "\n\n🌟 Очень редкое событие!"

    reward_line = (
        f"\n\n🪙 +{result['coins_earned']} "
        f"{world['currency_name']}"
    )

    if not result["claimed"]:
        reward_line = (
            f"\n\n🪙 Награда уже получена: "
            f"+{result['coins_earned']}"
        )

    streak=result.get('streak',1)
    days=('день' if streak%10==1 and streak%100!=11 else
          'дня' if streak%10 in (2,3,4) and streak%100 not in (12,13,14) else 'дней')
    streak_line = (f"🔥 Серия: {streak} {days}\n"
        f"📅 День цикла: {result.get('cycle_day',1)}/7\n"
        f"🪙 Бонус: +{(result.get('cycle_day',1)-1)*10}%\n")
    if result.get('chest_code'): streak_line += "📦 Недельный сундук получен: проверь инвентарь.\n"
    text = (
        f"{prefix}\n\n{streak_line}\n"
        f"{result['story_text']}"
        f"{rare_line}"
        f"{reward_line}\n"
        f"💰 Баланс: {result['balance']}\n\n"
        "Следующий дейлик будет доступен завтра."
    )

    await _edit_private(
        callback,
        text,
        _daily_result_menu(
            world["id"],
            callback.from_user.id,
        ),
    )


@router.callback_query(F.data.startswith("mini:rules:"))
@router.callback_query(F.data.startswith("mini:rating:"))
async def rules_callback(callback: CallbackQuery):
    # Старые уже открытые кнопки «Рейтинг» тоже ведут в правила.
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, _ = context
    await callback.answer()
    await _edit_private(
        callback,
        RULES_TEXT,
        rules_menu(world["id"], callback.from_user.id),
    )



def rules_menu(world_id, user_id, *, section=False):
    rows=[]
    if not section:
        rows=[[InlineKeyboardButton(text=label,callback_data=f'mini:rulespage:{world_id}:{user_id}:{key}')]
              for key,(label,_) in SECTIONS.items()]
    rows.append([InlineKeyboardButton(text='Назад',callback_data=_personal_callback(
        'rules' if section else 'more',world_id,user_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:rulespage:'))
async def rules_section_callback(callback):
    context=await load_extended_context(callback,'rulespage')
    if context is None:
        return
    world,_,key=context
    if key not in SECTIONS:
        await callback.answer('Этот раздел недоступен.',show_alert=True)
        return
    await callback.answer()
    await _edit_private(callback,SECTIONS[key][1],rules_menu(world['id'],callback.from_user.id,section=True))
