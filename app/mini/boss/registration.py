from app.mini.ui.context import load_world as _load_world, load_player as _load_player
from app.mini.ui.transport import send_private_text_from_callback as _send_private
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from app.mini.boss.catalog import get_boss_template, list_boss_templates
from app.mini.boss.public import format_participants, format_public_boss
import json
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
    unregister_player,
)
from app.mini.boss.presentation import template_menu, preview_menu
from app.mini.boss.ui import (
    is_admin,
    show_boss_home,
    refresh_from_callback,
    publish_boss,
    retire_old_public_boss,
    admin_action_context,
)

router = Router(name="mini_boss_registration")

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
    await show_boss_home(callback, world, player)


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
    if world is None or not is_admin(callback, world):
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
    await _send_private(callback, world, text, template_menu(world_id, owner_id))


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
    if world is None or not is_admin(callback, world):
        await callback.answer("Нет прав администратора.", show_alert=True)
        return
    if template is None or not template.get("active", True):
        await callback.answer("Босс больше не доступен.", show_alert=True)
        return

    preview = dict(template, status="announced", current_hp=template["max_hp"],
                   features_json=json.dumps(template["features"]),
                   reward_items_json=json.dumps(template.get("reward_items", [])), trait_rules_version=1)
    text = format_public_boss(preview, [])
    await callback.answer()
    await _send_private(
        callback,
        world,
        text,
        preview_menu(world_id, owner_id, code),
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
    if world is None or player is None or not is_admin(callback, world):
        await callback.answer("Нет прав администратора.", show_alert=True)
        return

    try:
        boss = create_boss_event(world_id, code, callback.from_user.id)
        await publish_boss(callback, world, boss)
        boss = get_boss(boss["id"])
    except (BossError, TelegramAPIError) as error:
        await callback.answer(f"Не удалось объявить босса: {error}", show_alert=True)
        return

    await callback.answer("Регистрация открыта")
    if boss is not None:
        await show_boss_home(callback, world, player)


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
        await refresh_from_callback(callback, world, boss)

    if callback.message is not None and callback.message.ephemeral_message_id is not None:
        await callback.answer(
            "Ты уже записан." if not result["applied"] else "Ты записан на босса!"
        )
        await show_boss_home(callback, world, player)
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
        await refresh_from_callback(callback, world, boss)

    message = "Ты вышел из регистрации." if removed else "Ты и так не был записан."
    if callback.message is not None and callback.message.ephemeral_message_id is not None:
        await callback.answer(message)
        await show_boss_home(callback, world, player)
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
        f"{format_participants(participants)}\n\n"
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


@router.callback_query(F.data.startswith("miniboss:close:"))
async def boss_close_callback(callback: CallbackQuery):
    context = await admin_action_context(callback, "close")
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

    await refresh_from_callback(callback, world, boss)
    player = _load_player(world["id"], callback)
    await callback.answer("Регистрация закрыта")
    if player is not None:
        await show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:reopen:"))
async def boss_reopen_callback(callback: CallbackQuery):
    context = await admin_action_context(callback, "reopen")
    if context is None:
        return
    world, boss, _ = context
    try:
        boss = reopen_registration(boss["id"])
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await refresh_from_callback(callback, world, boss)
    player = _load_player(world["id"], callback)
    await callback.answer("Регистрация снова открыта")
    if player is not None:
        await show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:republish:"))
async def boss_republish_callback(callback: CallbackQuery):
    context = await admin_action_context(callback, "republish")
    if context is None:
        return
    world, boss, _ = context

    if boss["status"] not in {"announced", "ready"}:
        await callback.answer(
            "Повторный анонс доступен только до начала боя.",
            show_alert=True,
        )
        return

    old_message_id = boss.get("signup_message_id")
    try:
        await publish_boss(
            callback,
            world,
            boss,
            replace_existing=True,
            expected_message_id=old_message_id,
        )
    except TelegramAPIError as error:
        await callback.answer(
            f"Не удалось повторить анонс: {error}",
            show_alert=True,
        )
        return

    refreshed = get_boss(boss["id"])
    new_message_id = (refreshed or {}).get("signup_message_id")
    if old_message_id and int(old_message_id) != int(new_message_id or 0):
        await retire_old_public_boss(callback, world, int(old_message_id))

    player = _load_player(world["id"], callback)
    await callback.answer("Анонс опубликован повторно")
    if player is not None:
        await show_boss_home(callback, world, player)


@router.callback_query(F.data.startswith("miniboss:cancel:"))
async def boss_cancel_callback(callback: CallbackQuery):
    context = await admin_action_context(callback, "cancel")
    if context is None:
        return
    world, boss, _ = context
    try:
        boss = cancel_boss(boss["id"])
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await refresh_from_callback(callback, world, boss)
    player = _load_player(world["id"], callback)
    await callback.answer("Босс отменён")
    if player is not None:
        await show_boss_home(callback, world, player)

