from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.ui.context import (
    personal_callback as _personal_callback,
    load_personal_context as _load_personal_context,
)
from app.mini.ui.transport import edit_private as _edit_private
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from app.mini.daily import claim_daily
from app.mini.rules import rules_text, SECTIONS
from app.mini.ui.context import load_extended_context
from app.mini.ui.navigation import back_menu


router = Router(name="mini_activities")

@editorial_screen
def _daily_result_menu(
    world_id: int,
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('navigation.inventory'),
                    callback_data=_personal_callback(
                        "inventory", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text=editorial_text('navigation.back'),
                    callback_data=_personal_callback(
                        "adventures", world_id, user_id
                    ),
                ),
            ]
        ]
    )


@router.callback_query(F.data.startswith("mini:dailyclaim:"))
@editorial_screen
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
            editorial_text('activities.daily_callback.line_14', error=f'{error}'),
            show_alert=True,
        )
        return

    if result["claimed"]:
        await callback.answer(
            f"+{result['coins_earned']} "
            f"{world['currency_name']}"
        )

        prefix = editorial_text('activities.daily_callback.line_04')
    else:
        await callback.answer(
            editorial_text('activities.daily_callback.line_13')
        )

        prefix = (
            editorial_text('activities.daily_callback.line_05')
        )

    rare_line = ""
    if result["rarity"] == "rare":
        rare_line = editorial_text('activities.daily_callback.line_06')
    elif result["rarity"] == "legendary":
        rare_line = editorial_text('activities.daily_callback.line_10')

    reward_line = (
        editorial_text('activities.daily_callback.line_01', coins_earned=f"{result['coins_earned']}", currency_name=f"{world['currency_name']}")
    )

    if not result["claimed"]:
        reward_line = (
            editorial_text('activities.daily_callback.line_07', coins_earned=f"{result['coins_earned']}")
        )

    streak=result.get('streak',1)
    days=(editorial_text('activities.daily_callback.line_08') if streak%10==1 and streak%100!=11 else
          editorial_text('activities.daily_callback.line_11') if streak%10 in (2,3,4) and streak%100 not in (12,13,14) else editorial_text('activities.daily_callback.line_12'))
    streak_line = (editorial_text('activities.daily_callback.line_02', streak=f'{streak}', days=f'{days}', value_3=f"{result.get('cycle_day', 1)}", value_4=f"{(result.get('cycle_day', 1) - 1) * 10}"))
    if result.get('chest_code'): streak_line += editorial_text('activities.daily_callback.line_09')
    text = (
        editorial_text('activities.daily_callback.line_03', prefix=f'{prefix}', streak_line=f'{streak_line}', story_text=f"{result['story_text']}", rare_line=f'{rare_line}', reward_line=f'{reward_line}', balance=f"{result['balance']}")
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
@editorial_screen
async def rules_callback(callback: CallbackQuery):
    # Старые уже открытые кнопки «Рейтинг» тоже ведут в правила.
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, _ = context
    await callback.answer()
    await _edit_private(
        callback,
        rules_text(),
        rules_menu(world["id"], callback.from_user.id),
    )



@editorial_screen
def rules_menu(world_id, user_id, *, section=False):
    rows=[]
    if not section:
        rows=[[InlineKeyboardButton(text=label,callback_data=f'mini:rulespage:{world_id}:{user_id}:{key}')]
              for key,(label,_) in SECTIONS.items()]
    if not section:
        rows.append([InlineKeyboardButton(text=editorial_text("guide.title"),callback_data=f"mini:guide:{world_id}:{user_id}:0")])
    rows.append([InlineKeyboardButton(text=editorial_text('general.back'),callback_data=_personal_callback(
        'rules' if section else 'more',world_id,user_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:rulespage:'))
@editorial_screen
async def rules_section_callback(callback):
    context=await load_extended_context(callback,'rulespage')
    if context is None:
        return
    world,_,key=context
    if key not in SECTIONS:
        await callback.answer(editorial_text('activities.rules_section_callback.line_01'),show_alert=True)
        return
    await callback.answer()
    await _edit_private(callback,SECTIONS[key][1],rules_menu(world['id'],callback.from_user.id,section=True))


@router.callback_query(F.data.startswith("mini:guide:"))
@editorial_screen
async def combat_guide_callback(callback):
    from app.mini.ui.combat_guide import guide_screen
    context=await load_extended_context(callback,"guide")
    if context is None:return
    world,_,page=context
    try:body,markup=guide_screen(world["id"],callback.from_user.id,int(page))
    except ValueError:
        await callback.answer(editorial_text("activities.rules_section_callback.line_01"),show_alert=True)
        return
    await callback.answer()
    await _edit_private(callback,body,markup)


def daily_overview(player_id, *, day=None, db_path=None):
    from datetime import date, timedelta
    from app.config import DB_PATH
    from app.mini.daily import _normalize_claim_date, get_daily_claim
    from app.mini.db import connect_mini_db
    day = _normalize_claim_date(day)
    path = DB_PATH if db_path is None else db_path
    claim = get_daily_claim(player_id, day, path)
    with connect_mini_db(path) as conn:
        row = conn.execute("SELECT last_day,streak FROM mini_daily_streak WHERE player_id=?", (player_id,)).fetchone()
        previous = (date.fromisoformat(day)-timedelta(days=1)).isoformat()
        streak = (row[1] if row and row[0]==day else row[1]+1 if row and row[0]==previous else 1)
        if not row:
            prior = previous
            while conn.execute("SELECT 1 FROM mini_daily_claims WHERE player_id=? AND claim_date=?", (player_id,prior)).fetchone():
                streak += 1
                prior = (date.fromisoformat(prior)-timedelta(days=1)).isoformat()
    cycle = (streak-1)%7+1
    return {"claimed": bool(claim), "streak": streak, "cycle": cycle,
            "text": f"Ежедневные активности\nСегодня: {'награда получена' if claim else 'награда доступна'}.\nСерия: {streak} дн. · день цикла {cycle}/7.\nСундук — за седьмой день подряд. До сундука: {7-cycle} дн.\nПропуск дня начинает серию и недельный цикл заново."}

@router.callback_query(F.data.startswith("mini:daily:"))
async def daily_overview_callback(callback):
    from app.mini.ui.timing import run_sync
    context = await _load_personal_context(callback)
    if context is None:
        return
    world, player = context
    await callback.answer()
    state = await run_sync("daily_overview", daily_overview, player["id"])
    markup = _daily_result_menu(world["id"],callback.from_user.id)
    if not state["claimed"]:
        markup.inline_keyboard.insert(0,[InlineKeyboardButton(text="Получить ежедневную награду",
            callback_data=_personal_callback("dailyclaim",world["id"],callback.from_user.id))])
    await _edit_private(callback,state["text"],markup)
