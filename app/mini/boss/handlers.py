from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    EphemeralMessageParameters,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.context import get_topic_admin, normalize_username
from app.mini.boss.catalog import (
    boss_image_path,
    get_boss_template,
    list_boss_templates,
)
from app.mini.boss.service import (
    BossError,
    BossNotEnoughPlayers,
    cancel_boss,
    close_registration,
    create_boss_event,
    get_active_boss,
    get_boss,
    list_participants,
    register_player,
    reopen_registration,
    set_signup_message,
    unregister_player,
)
from app.mini.players import get_mini_player, touch_mini_player
from app.mini.worlds import get_mini_world_by_id


router = Router(name="mini_boss")


STATUS_TEXT = {
    "announced": "🟢 Регистрация открыта",
    "ready": "🟡 Регистрация закрыта",
    "fighting": "🔴 Бой идёт",
    "cancelled": "⚫ Босс отменён",
    "defeated": "🏆 Босс побеждён",
}


def _username_from_user(user) -> str:
    if user is None or not user.username:
        return ""
    return normalize_username(user.username)


def _is_admin(callback: CallbackQuery, world: dict) -> bool:
    required = get_topic_admin(int(world["chat_id"]), int(world["thread_id"]))
    current = _username_from_user(callback.from_user)
    return bool(required and current and current == required)


def _is_joined(player_id: int, participants: list[dict]) -> bool:
    return any(int(row["player_id"]) == int(player_id) for row in participants)


def _participant_label(row: dict) -> str:
    username = str(row.get("username") or "").strip()
    character = str(row.get("character_name") or "Игрок").strip()
    hero = str(row.get("hero_name") or "без героя").strip()
    who = username or character
    return f"{row['queue_position']}. {who} — {hero}"


def _format_participants(participants: list[dict]) -> str:
    if not participants:
        return "Пока никто не записался."
    return "\n".join(_participant_label(row) for row in participants)


def _format_public_boss(boss: dict, participants: list[dict]) -> str:
    status = STATUS_TEXT.get(str(boss["status"]), str(boss["status"]))
    minimum = int(boss["min_players"])
    count = len(participants)
    reward = int(boss.get("reward_coins", 0))

    lines = [
        f"👹 {boss['name']}",
        "",
        str(boss.get("description") or "Без описания."),
        "",
        f"❤️ HP: {boss['max_hp']}",
        f"👥 Участники: {count} • минимум {minimum}",
        f"🎁 Награда: {reward} 🪙",
        f"{status}",
    ]

    if boss["status"] == "announced":
        lines.extend(["", "Запишись на бой кнопкой ниже."])
    elif boss["status"] == "ready":
        lines.extend(["", "Состав зафиксирован. Следующий этап — запуск боя."])
    elif boss["status"] == "cancelled":
        lines.extend(["", "Регистрация отменена."])

    return "\n".join(lines)[:1024]


def _format_private_boss(
    boss: dict,
    participants: list[dict],
    *,
    joined: bool,
) -> str:
    text = _format_public_boss(boss, participants)
    if boss["status"] == "announced":
        text += (
            "\n\n✅ Ты записан на этого босса."
            if joined
            else "\n\nТы пока не записан."
        )
    return text


def _public_boss_menu(world_id: int, boss: dict) -> InlineKeyboardMarkup:
    rows = []
    if boss["status"] == "announced":
        rows.append([
            InlineKeyboardButton(
                text="⚔️ Записаться",
                callback_data=f"miniboss:join:{world_id}:{boss['id']}",
            ),
            InlineKeyboardButton(
                text="🚪 Выйти",
                callback_data=f"miniboss:leave:{world_id}:{boss['id']}",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="👥 Участники",
            callback_data=f"miniboss:list:{world_id}:{boss['id']}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _boss_private_menu(
    world_id: int,
    user_id: int,
    boss: dict,
    *,
    joined: bool,
    is_admin: bool,
) -> InlineKeyboardMarkup:
    rows = []

    if boss["status"] == "announced":
        if joined:
            rows.append([
                InlineKeyboardButton(
                    text="🚪 Выйти из регистрации",
                    callback_data=f"miniboss:leave:{world_id}:{boss['id']}",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text="⚔️ Записаться на босса",
                    callback_data=f"miniboss:join:{world_id}:{boss['id']}",
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="👥 Участники",
            callback_data=f"miniboss:list:{world_id}:{boss['id']}",
        )
    ])

    if is_admin and boss["status"] == "announced":
        rows.append([
            InlineKeyboardButton(
                text="🔒 Закрыть регистрацию",
                callback_data=(
                    f"miniboss:close:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] == "ready":
        rows.append([
            InlineKeyboardButton(
                text="🔓 Открыть регистрацию снова",
                callback_data=(
                    f"miniboss:reopen:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text="❌ Отменить босса",
                callback_data=(
                    f"miniboss:cancel:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ На главную",
            callback_data=f"mini:home:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _no_boss_menu(
    world_id: int,
    user_id: int,
    *,
    is_admin: bool,
) -> InlineKeyboardMarkup:
    rows = []
    if is_admin:
        rows.append([
            InlineKeyboardButton(
                text="➕ Объявить босса",
                callback_data=f"miniboss:create:{world_id}:{user_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ На главную",
            callback_data=f"mini:home:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _template_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for boss in list_boss_templates():
        rows.append([
            InlineKeyboardButton(
                text=f"👹 {boss['name']} • {boss['max_hp']} HP",
                callback_data=(
                    f"miniboss:preview:{world_id}:{user_id}:{boss['code']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"mini:boss:{world_id}:{user_id}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _preview_menu(
    world_id: int,
    user_id: int,
    code: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📣 Объявить и открыть регистрацию",
                    callback_data=(
                        f"miniboss:announce:{world_id}:{user_id}:{code}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку боссов",
                    callback_data=f"miniboss:create:{world_id}:{user_id}",
                )
            ],
        ]
    )


def _replacement_ephemeral_kwargs(callback: CallbackQuery) -> dict:
    return {
        "ephemeral_message_parameters": EphemeralMessageParameters(
            receiver_user_id=callback.from_user.id,
            callback_query_id=callback.id,
            replace_callback_query_message=False,
        )
    }


async def _delete_current_ephemeral(callback: CallbackQuery) -> None:
    if callback.message is None or callback.message.ephemeral_message_id is None:
        return
    try:
        await callback.message.delete_ephemeral()
    except TelegramAPIError as error:
        print(
            "Boss: не удалось удалить старое ephemeral-сообщение: "
            f"{type(error).__name__}: {error}"
        )


async def _send_private(
    callback: CallbackQuery,
    world: dict,
    text: str,
    markup: InlineKeyboardMarkup,
):
    sent = await callback.bot.send_message(
        chat_id=world["chat_id"],
        message_thread_id=world["thread_id"] or None,
        text=text,
        reply_markup=markup,
        **_replacement_ephemeral_kwargs(callback),
    )
    await _delete_current_ephemeral(callback)
    return sent


def _load_world(world_id: int) -> dict | None:
    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        return None
    return world


def _load_player(world_id: int, callback: CallbackQuery) -> dict | None:
    player = get_mini_player(world_id, callback.from_user.id)
    if player is not None:
        touch_mini_player(
            world_id,
            callback.from_user.id,
            _username_from_user(callback.from_user),
        )
        player = get_mini_player(world_id, callback.from_user.id)
    return player


async def _show_boss_home(
    callback: CallbackQuery,
    world: dict,
    player: dict,
):
    boss = get_active_boss(world["id"])
    admin = _is_admin(callback, world)

    if boss is None:
        await _send_private(
            callback,
            world,
            (
                "👹 Боссы\n\nСейчас активного босса нет."
                + (
                    "\n\nТы администратор темы — можешь объявить нового босса."
                    if admin
                    else ""
                )
            ),
            _no_boss_menu(
                world["id"], callback.from_user.id, is_admin=admin
            ),
        )
        return

    participants = list_participants(boss["id"])
    joined = _is_joined(player["id"], participants)
    await _send_private(
        callback,
        world,
        _format_private_boss(boss, participants, joined=joined),
        _boss_private_menu(
            world["id"],
            callback.from_user.id,
            boss,
            joined=joined,
            is_admin=admin,
        ),
    )


async def _refresh_public_boss(callback: CallbackQuery, world: dict, boss: dict):
    message_id = boss.get("signup_message_id")
    if not message_id:
        return

    participants = list_participants(boss["id"])
    text = _format_public_boss(boss, participants)
    markup = _public_boss_menu(world["id"], boss)

    try:
        if boss.get("signup_message_kind") == "photo":
            await callback.bot.edit_message_caption(
                chat_id=world["chat_id"],
                message_id=int(message_id),
                caption=text,
                reply_markup=markup,
            )
        else:
            await callback.bot.edit_message_text(
                chat_id=world["chat_id"],
                message_id=int(message_id),
                text=text,
                reply_markup=markup,
            )
    except TelegramBadRequest as error:
        print(
            "Boss: не удалось обновить публичную карточку: "
            f"{type(error).__name__}: {error}"
        )


async def _publish_boss(callback: CallbackQuery, world: dict, boss: dict):
    participants = list_participants(boss["id"])
    text = _format_public_boss(boss, participants)
    markup = _public_boss_menu(world["id"], boss)
    image = boss_image_path(boss.get("image_path", ""))

    if image is not None:
        try:
            sent = await callback.bot.send_photo(
                chat_id=world["chat_id"],
                message_thread_id=world["thread_id"] or None,
                photo=FSInputFile(image),
                caption=text,
                reply_markup=markup,
            )
            set_signup_message(boss["id"], sent.message_id, "photo")
            return
        except TelegramAPIError as error:
            print(
                "Boss: картинка не отправлена, использую текст: "
                f"{type(error).__name__}: {error}"
            )

    sent = await callback.bot.send_message(
        chat_id=world["chat_id"],
        message_thread_id=world["thread_id"] or None,
        text=text,
        reply_markup=markup,
    )
    set_signup_message(boss["id"], sent.message_id, "text")


@router.callback_query(F.data.startswith("mini:boss:"))
async def boss_home_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id = int(parts[2])
        owner_id = int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return

    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    if world is None or player is None:
        await callback.answer("D&D Mini сейчас недоступен.", show_alert=True)
        return

    await callback.answer()
    await _show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:create:"))
async def boss_create_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, owner_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return

    world = _load_world(world_id)
    if world is None or not _is_admin(callback, world):
        await callback.answer("Только администратор темы может создать босса.", show_alert=True)
        return
    if get_active_boss(world_id) is not None:
        await callback.answer("Сначала заверши текущего босса.", show_alert=True)
        return

    templates = list_boss_templates()
    text = "👹 Выбор босса\n\nВыбери босса из boss/content/bosses.json."
    if not templates:
        text += "\n\nАктивных боссов в каталоге пока нет."

    await callback.answer()
    await _send_private(callback, world, text, _template_menu(world_id, owner_id))


@router.callback_query(F.data.startswith("miniboss:preview:"))
async def boss_preview_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":", 4)
    if len(parts) != 5:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, owner_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    code = parts[4]
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return

    world = _load_world(world_id)
    template = get_boss_template(code)
    if world is None or not _is_admin(callback, world):
        await callback.answer("Нет прав администратора.", show_alert=True)
        return
    if template is None or not template.get("active", True):
        await callback.answer("Босс больше не доступен.", show_alert=True)
        return

    text = (
        f"👹 {template['name']}\n\n"
        f"{template.get('description', '')}\n\n"
        f"❤️ HP: {template['max_hp']}\n"
        f"👥 Минимум игроков: {template['min_players']}\n"
        f"🎁 Награда: {template.get('reward_coins', 0)} 🪙\n\n"
        "После объявления в теме появится публичная карточка регистрации."
    )
    await callback.answer()
    await _send_private(
        callback,
        world,
        text,
        _preview_menu(world_id, owner_id, code),
    )


@router.callback_query(F.data.startswith("miniboss:announce:"))
async def boss_announce_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":", 4)
    if len(parts) != 5:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, owner_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    code = parts[4]
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return

    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    if world is None or player is None or not _is_admin(callback, world):
        await callback.answer("Нет прав администратора.", show_alert=True)
        return

    try:
        boss = create_boss_event(world_id, code, callback.from_user.id)
        await _publish_boss(callback, world, boss)
        boss = get_boss(boss["id"])
    except (BossError, TelegramAPIError) as error:
        await callback.answer(f"Не удалось объявить босса: {error}", show_alert=True)
        return

    await callback.answer("Регистрация открыта")
    if boss is not None:
        await _show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:join:"))
async def boss_join_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, boss_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    boss = get_boss(boss_id)
    if world is None or boss is None or int(boss["world_id"]) != world_id:
        await callback.answer("Босс больше не актуален.", show_alert=True)
        return
    if player is None:
        await callback.answer("Сначала создай Mini-персонажа.", show_alert=True)
        return

    try:
        result = register_player(boss_id, player["id"])
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    boss = get_boss(boss_id)
    if boss is not None:
        await _refresh_public_boss(callback, world, boss)

    if callback.message is not None and callback.message.ephemeral_message_id is not None:
        await callback.answer(
            "Ты уже записан." if not result["applied"] else "Ты записан на босса!"
        )
        await _show_boss_home(callback, world, player)
    else:
        await callback.answer(
            "Ты уже записан." if not result["applied"] else "Ты записан на босса!",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("miniboss:leave:"))
async def boss_leave_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, boss_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    boss = get_boss(boss_id)
    if world is None or boss is None or int(boss["world_id"]) != world_id:
        await callback.answer("Босс больше не актуален.", show_alert=True)
        return
    if player is None:
        await callback.answer("Ты не зарегистрирован в D&D Mini.", show_alert=True)
        return

    try:
        removed = unregister_player(boss_id, player["id"])
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    boss = get_boss(boss_id)
    if boss is not None:
        await _refresh_public_boss(callback, world, boss)

    message = "Ты вышел из регистрации." if removed else "Ты и так не был записан."
    if callback.message is not None and callback.message.ephemeral_message_id is not None:
        await callback.answer(message)
        await _show_boss_home(callback, world, player)
    else:
        await callback.answer(message, show_alert=True)


@router.callback_query(F.data.startswith("miniboss:list:"))
async def boss_list_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, boss_id = int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    world = _load_world(world_id)
    boss = get_boss(boss_id)
    if world is None or boss is None or int(boss["world_id"]) != world_id:
        await callback.answer("Босс больше не актуален.", show_alert=True)
        return

    participants = list_participants(boss_id)
    text = (
        f"👥 Участники: {boss['name']}\n\n"
        f"{_format_participants(participants)}\n\n"
        f"Всего: {len(participants)} • минимум: {boss['min_players']}"
    )

    player = _load_player(world_id, callback)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="⬅️ К боссу",
                callback_data=(
                    f"mini:boss:{world_id}:{callback.from_user.id}"
                    if player is not None
                    else f"miniboss:list:{world_id}:{boss_id}"
                ),
            )
        ]]
    )

    await callback.answer()
    await _send_private(callback, world, text, markup)


async def _admin_action_context(
    callback: CallbackQuery,
    action: str,
) -> tuple[dict, dict, int] | None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or parts[1] != action:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None
    try:
        world_id, owner_id, boss_id = int(parts[2]), int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return None
    world = _load_world(world_id)
    boss = get_boss(boss_id)
    if (
        world is None
        or boss is None
        or int(boss["world_id"]) != world_id
        or not _is_admin(callback, world)
    ):
        await callback.answer("Нет прав администратора.", show_alert=True)
        return None
    return world, boss, owner_id


@router.callback_query(F.data.startswith("miniboss:close:"))
async def boss_close_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "close")
    if context is None:
        return
    world, boss, _ = context
    try:
        boss = close_registration(boss["id"])
    except BossNotEnoughPlayers as error:
        await callback.answer(str(error), show_alert=True)
        return
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await _refresh_public_boss(callback, world, boss)
    player = _load_player(world["id"], callback)
    await callback.answer("Регистрация закрыта")
    if player is not None:
        await _show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:reopen:"))
async def boss_reopen_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "reopen")
    if context is None:
        return
    world, boss, _ = context
    try:
        boss = reopen_registration(boss["id"])
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await _refresh_public_boss(callback, world, boss)
    player = _load_player(world["id"], callback)
    await callback.answer("Регистрация снова открыта")
    if player is not None:
        await _show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:cancel:"))
async def boss_cancel_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "cancel")
    if context is None:
        return
    world, boss, _ = context
    try:
        boss = cancel_boss(boss["id"])
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await _refresh_public_boss(callback, world, boss)
    player = _load_player(world["id"], callback)
    await callback.answer("Босс отменён")
    if player is not None:
        await _show_boss_home(callback, world, player)
