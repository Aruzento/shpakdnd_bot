"""Personal history and confirmation of one exact administrative turn skip."""

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.ui.context import load_world, load_player, username_from_user
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.boss import operations
from app.mini.boss.ui import is_admin
from app.mini.boss.service import get_boss
from app.mini.boss.public import refresh_public_boss, replace_public_turn

router = Router(name="mini_boss_v15")


@router.callback_query(F.data.startswith("miniboss:history:"))
async def history(callback):
    try:
        _, _, wid, bid, page = callback.data.split(":")
        wid, bid, page = map(int, (wid, bid, page))
        world = load_world(wid, callback)
        if not world or not load_player(wid, callback):
            raise ValueError("Сначала зарегистрируйся в этом Mini-мире.")
        result = operations.history(bid, wid, page)
        rows = []
        nav = []
        if result["page"]:
            nav.append(
                Button(
                    text="⬅️", callback_data=f"miniboss:history:{wid}:{bid}:{page-1}"
                )
            )
        if result["page"] + 1 < result["pages"]:
            nav.append(
                Button(
                    text="➡️", callback_data=f"miniboss:history:{wid}:{bid}:{page+1}"
                )
            )
        if nav:
            rows.append(nav)
        rows.append(
            [
                Button(
                    text="Закрыть",
                    callback_data=f"mini:close:{wid}:{callback.from_user.id}",
                )
            ]
        )
        await callback.answer()
        await send_private_text_from_callback(
            callback, world, result["text"], InlineKeyboardMarkup(inline_keyboard=rows)
        )
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(
    F.data.startswith("miniboss:skip:") | F.data.startswith("miniboss:skipyes:")
)
async def skip(callback):
    try:
        _, action, wid, bid, token = callback.data.split(":")
        wid, bid = int(wid), int(bid)
        world = load_world(wid, callback)
        boss = get_boss(bid)
        if (
            not world
            or not boss
            or boss["world_id"] != wid
            or not is_admin(callback, world)
        ):
            raise ValueError("Нет прав администратора этой темы.")
        if action == "skip":
            if boss["status"] != "fighting" or operations.turn_token(boss) != token:
                raise ValueError("Подтверждение устарело.")
            from app.mini.boss.service import list_participants

            current = next(
                (
                    p
                    for p in list_participants(bid)
                    if p["queue_position"] == boss["current_turn_position"]
                ),
                {},
            )
            text = f"Пропустить текущий ход: {current.get('character_name','игрок')}, {current.get('hero_name','герой')}? Раунд {boss['current_round']}."
            rows = [
                [
                    Button(
                        text="Подтвердить",
                        callback_data=f"miniboss:skipyes:{wid}:{bid}:{token}",
                    )
                ],
                [
                    Button(
                        text="Отмена",
                        callback_data=f"mini:boss:{wid}:{callback.from_user.id}",
                    )
                ],
            ]
            await callback.answer()
            await send_private_text_from_callback(
                callback, world, text, InlineKeyboardMarkup(inline_keyboard=rows)
            )
        else:
            operations.admin_skip(
                bid,
                wid,
                callback.from_user.id,
                username_from_user(callback.from_user),
                token,
                "admin-skip:" + callback.id,
            )
            boss = get_boss(bid)
            await callback.answer("Ход пропущен администратором.")
            await refresh_public_boss(callback.bot, world, boss)
            await replace_public_turn(
                callback.bot,
                world,
                boss,
                notice="⏭ Ход пропущен администратором. Право на награду сохранено.",
            )
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
