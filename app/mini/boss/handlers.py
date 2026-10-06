from app.mini.ui.context import load_world as _load_world, load_player as _load_player
from app.mini.ui.transport import delete_current_ephemeral as _delete_current_ephemeral, send_private_text_from_callback as _send_private
import asyncio

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.context import get_topic_admin, normalize_username
from app.mini.boss.catalog import (
    boss_image_path,
    get_boss_template,
    list_boss_reward_items,
    list_boss_templates,
)
from app.mini.boss.combat import (
    BossCombatError,
    BossNotParticipant,
    BossNotYourTurn,
    advance_expired_turns,
    force_finish_battle,
    hit_boss,
    start_battle,
)
from app.mini.boss.notices import combat_event_lines, timeout_event_lines, boss_event_line
from app.mini.boss.public import (
    boss_attack_passive_lines,
    ensure_public_turn,
    format_participants,
    format_public_boss,
    public_boss_menu,
    publish_admin_victory,
    refresh_public_boss,
    replace_public_turn,
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
    select_battle_hero,
)
from app.mini.heroes import get_player_heroes


router = Router(name="mini_boss")


_BOSS_PUBLISH_LOCKS: dict[int, asyncio.Lock] = {}


def _boss_publish_lock(boss_id: int) -> asyncio.Lock:
    boss_id = int(boss_id)
    lock = _BOSS_PUBLISH_LOCKS.get(boss_id)
    if lock is None:
        lock = asyncio.Lock()
        _BOSS_PUBLISH_LOCKS[boss_id] = lock
    return lock


STATUS_TEXT = {
    "announced": "🟢 Регистрация открыта",
    "ready": "🟡 Регистрация закрыта",
    "fighting": "🔴 Бой идёт",
    "cancelled": "⚫ Босс отменён",
    "defeated": "🏆 Босс побеждён",
    "failed": "💀 Бой проигран",
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
    extra = ""
    if int(row.get("battle_attack") or 0) > 0:
        extra = f" • ⚔️ {int(row['battle_attack'])}"
    return f"{row['queue_position']}. {who} — {hero}{extra}"

def _format_participants(participants: list[dict]) -> str:
    return format_participants(participants)

def _format_public_boss(boss: dict, participants: list[dict]) -> str:
    return format_public_boss(boss, participants)

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
    elif boss["status"] == "fighting" and joined:
        text += "\n\n⚔️ Ты участвуешь в этом бою."
    return text


def _public_boss_menu(world_id: int, boss: dict) -> InlineKeyboardMarkup:
    return public_boss_menu(world_id, boss)

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

    if joined and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text="🎴 Выбрать героя",
                callback_data=f"miniboss:heroes:{world_id}:{user_id}:{boss['id']}:0",
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
                text="▶️ Начать бой",
                callback_data=(
                    f"miniboss:start:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="🔓 Открыть регистрацию снова",
                callback_data=(
                    f"miniboss:reopen:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] == "fighting":
        rows.append([
            InlineKeyboardButton(
                text="⚡ Завершить бой",
                callback_data=(
                    f"miniboss:forcefinish:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])

    if is_admin and boss["status"] in {"announced", "ready"}:
        rows.append([
            InlineKeyboardButton(
                text="📣 Повторить анонс",
                callback_data=(
                    f"miniboss:republish:{world_id}:{user_id}:{boss['id']}"
                ),
            )
        ])
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


async def _show_boss_home(
    callback: CallbackQuery,
    world: dict,
    player: dict,
):
    boss = get_active_boss(world["id"])
    admin = _is_admin(callback, world)

    if boss is not None and boss["status"] == "fighting":
        try:
            timeout_result = advance_expired_turns(boss["id"])
            boss = timeout_result["state"]["boss"]
            if timeout_result["changed"]:
                await refresh_public_boss(callback.bot, world, boss)
                notice = "\n".join(timeout_event_lines(timeout_result))
                await replace_public_turn(
                    callback.bot, world, boss, notice=notice
                )
            else:
                await ensure_public_turn(callback.bot, world, boss)
        except BossCombatError as error:
            print(f"Boss: ошибка проверки таймера хода: {error}")

    if boss is None or boss["status"] not in {"announced", "ready", "fighting"}:
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
    await refresh_public_boss(callback.bot, world, boss)


async def _publish_boss(
    callback: CallbackQuery,
    world: dict,
    boss: dict,
    *,
    replace_existing: bool = False,
    expected_message_id: int | None = None,
):
    """Публикует одну карточку босса и защищается от двойных callback'ов."""
    boss_id = int(boss["id"])

    async with _boss_publish_lock(boss_id):
        fresh = get_boss(boss_id)
        if fresh is None:
            raise BossError("Босс больше не существует.")

        current_message_id = fresh.get("signup_message_id")
        if not replace_existing and current_message_id:
            # Повторная доставка одного callback не должна создавать второй анонс.
            return

        if replace_existing:
            expected = (
                int(expected_message_id)
                if expected_message_id is not None
                else None
            )
            current = (
                int(current_message_id)
                if current_message_id is not None
                else None
            )
            if current != expected:
                # Другой callback уже успел перепубликовать карточку.
                return

        participants = list_participants(boss_id)
        text = _format_public_boss(fresh, participants)
        markup = _public_boss_menu(world["id"], fresh)
        image = boss_image_path(fresh.get("image_path", ""))

        if image is not None:
            try:
                sent = await callback.bot.send_photo(
                    chat_id=world["chat_id"],
                    message_thread_id=world["thread_id"] or None,
                    photo=FSInputFile(image),
                    caption=text,
                    reply_markup=markup,
                )
                set_signup_message(boss_id, sent.message_id, "photo")
                return
            except TelegramBadRequest as error:
                # Только явный BadRequest гарантирует, что фото не было принято.
                # При сетевом/серверном TelegramAPIError нельзя посылать fallback:
                # Telegram мог уже принять фото, что и давало редкие дубли.
                print(
                    "Boss: фото отклонено Telegram, использую текст: "
                    f"{type(error).__name__}: {error}"
                )

        sent = await callback.bot.send_message(
            chat_id=world["chat_id"],
            message_thread_id=world["thread_id"] or None,
            text=text,
            reply_markup=markup,
        )
        set_signup_message(boss_id, sent.message_id, "text")


async def _retire_old_public_boss(
    callback: CallbackQuery,
    world: dict,
    old_message_id: int | None,
) -> None:
    """Убирает кнопки со старого анонса после успешной повторной публикации."""
    if not old_message_id:
        return
    try:
        await callback.bot.edit_message_reply_markup(
            chat_id=world["chat_id"],
            message_id=int(old_message_id),
            reply_markup=None,
        )
    except TelegramAPIError as error:
        text_error = str(error).lower()
        if "message is not modified" not in text_error:
            print(
                "Boss: не удалось убрать кнопки со старого анонса: "
                f"{type(error).__name__}: {error}"
            )


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

    item_names = {
        item["code"]: item["name"]
        for item in list_boss_reward_items(active_only=False)
    }
    reward_item_text = ", ".join(
        (
            f"{item_names.get(item['code'], item['code'])}"
            + (f" ×{item.get('quantity', 1)}" if int(item.get('quantity', 1)) > 1 else "")
        )
        for item in template.get("reward_items", [])
    )
    text = (
        f"👹 {template['name']}\n\n"
        f"{template.get('description', '')}\n\n"
        f"❤️ HP: {template['max_hp']}\n"
        f"👥 Минимум игроков: {template['min_players']}\n"
        f"🎁 Награда: {template.get('reward_coins', 0)} 🪙\n"
        + (f"📦 Предметы: {reward_item_text}\n" if reward_item_text else "")
        + f"🛡 Щиты награды: {template.get('reward_shields', 3)}\n"
        + f"💥 После щитов: −{template.get('reward_decay_percent', 10)}% за раунд\n\n"
        + "После объявления в теме появится публичная карточка регистрации."
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


@router.callback_query(F.data.startswith("miniboss:start:"))
async def boss_start_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "start")
    if context is None:
        return
    world, boss, _ = context
    try:
        state = start_battle(boss["id"])
    except BossCombatError as error:
        await callback.answer(str(error), show_alert=True)
        return

    boss = state["boss"]
    await refresh_public_boss(callback.bot, world, boss)
    await replace_public_turn(
        callback.bot,
        world,
        boss,
        notice="\n".join([
            "⚔️ Бой начался!",
            *(boss_event_line(event, state["participants"]) for event in state.get("boss_events", [])),
        ]),
    )
    current = state.get("current")
    who = (
        (current or {}).get("username")
        or (current or {}).get("character_name")
        or "первого игрока"
    )
    await callback.answer(f"Бой начался. Ход {who}")
    await _delete_current_ephemeral(callback)


@router.callback_query(F.data.startswith("miniboss:hit:"))
async def boss_hit_callback(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) not in {4, 6}:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    try:
        world_id, boss_id = int(parts[2]), int(parts[3])
        expected_round = int(parts[4]) if len(parts) == 6 else None
        expected_position = int(parts[5]) if len(parts) == 6 else None
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

    # Старые карточки (до turn-token) и дубли предыдущего хода не должны
    # оставаться рабочими после обновления. Актуальную карточку восстановит
    # ensure_public_turn. Это также обезвреживает уже висящие старые дубли.
    if expected_round is None or expected_position is None:
        await ensure_public_turn(callback.bot, world, boss)
        await callback.answer(
            "Эта кнопка хода устарела. Используй актуальное сообщение ниже.",
            show_alert=True,
        )
        return

    if (
        boss.get("status") != "fighting"
        or int(boss.get("current_round") or 0) != expected_round
        or int(boss.get("current_turn_position") or 0) != expected_position
    ):
        await ensure_public_turn(callback.bot, world, boss)
        await callback.answer(
            "Этот ход уже завершён. Используй актуальное сообщение ниже.",
            show_alert=True,
        )
        return

    try:
        result = hit_boss(
            boss_id, player["id"],
            expected_round=expected_round, expected_position=expected_position,
        )
    except (BossNotYourTurn, BossNotParticipant, BossCombatError) as error:
        refreshed = get_boss(boss_id)
        if refreshed is not None:
            await refresh_public_boss(callback.bot, world, refreshed)
            await ensure_public_turn(callback.bot, world, refreshed)
        await callback.answer(str(error), show_alert=True)
        return

    boss = result["state"]["boss"]

    passive_messages = [
        str(event.get("message", "")).strip()
        for event in result.get("passive_events", [])
        if str(event.get("message", "")).strip()
    ]
    passive_text = " ".join(passive_messages)

    attacker = (
        _username_from_user(callback.from_user)
        or str(player.get("username") or "").strip()
        or str(player.get("character_name") or "Игрок").strip()
    )
    public_parts = [f"💥 {attacker} наносит {result.get('damage', 0)} урона."]
    if passive_text:
        public_parts.append(passive_text)

    reward_event = result.get("reward_event")
    public_parts.extend(combat_event_lines(result))
    public_notice = "\n".join(public_parts)

    if result.get("battle_ended"):
        if boss["status"] == "defeated":
            rewards = result.get("rewards") or {}
            message = (
                f"🏆 Победа! Урон: {result.get('damage', 0)}. "
                f"Награда за участие: {rewards.get('coins_each', 0)} монет."
            )
            if passive_text:
                message += f" {passive_text}"
            await callback.answer(message[:200], show_alert=True)
        else:
            rewards = result.get("rewards") or {}
            await callback.answer(
                (
                    f"💀 Бой проигран. Награда за участие: "
                    f"{rewards.get('shards_each', int(boss.get('reward_coins', 0)) // 10)} осколков."
                )[:200],
                show_alert=True,
            )
    else:
        suffix = ""
        if passive_text:
            suffix += f" {passive_text}."
        if reward_event:
            if reward_event.get("type") == "shield":
                suffix += f" Босс разбил щит: осталось {reward_event['shields']}."
            elif reward_event.get("type") == "reward_damage":
                suffix += f" Награда: {reward_event['reward_percent']}%."
            elif reward_event.get("type") == "boss_skip":
                suffix += " 🎵 Босс пропускает атаку по награде."
            passive_lines = boss_attack_passive_lines(reward_event)
            if passive_lines:
                suffix += " " + " ".join(passive_lines)
        await callback.answer(f"⚔️ Урон: {result['damage']}.{suffix}"[:200])

    # Если удар был сделан из старого личного ephemeral-меню, оно больше
    # не должно висеть после хода. Публичная карточка следующего хода
    # появится для всей темы ниже.
    await _delete_current_ephemeral(callback)
    await refresh_public_boss(callback.bot, world, boss)
    await replace_public_turn(
        callback.bot,
        world,
        boss,
        notice=public_notice,
    )


@router.callback_query(F.data.startswith("miniboss:forcefinish:"))
async def boss_force_finish_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "forcefinish")
    if context is None:
        return
    world, boss, _ = context

    if boss["status"] != "fighting":
        await callback.answer(
            "Завершить можно только идущий бой.",
            show_alert=True,
        )
        return

    try:
        result = force_finish_battle(boss["id"])
    except BossCombatError as error:
        await callback.answer(str(error), show_alert=True)
        return

    finished = result["state"]["boss"]
    await refresh_public_boss(callback.bot, world, finished)
    await publish_admin_victory(
        callback.bot,
        world,
        finished,
        old_turn_message_id=result.get("old_turn_message_id"),
    )
    await callback.answer("Бой завершён победой")
    await _delete_current_ephemeral(callback)


@router.callback_query(F.data.startswith("miniboss:republish:"))
async def boss_republish_callback(callback: CallbackQuery):
    context = await _admin_action_context(callback, "republish")
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
        await _publish_boss(
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
        await _retire_old_public_boss(callback, world, int(old_message_id))

    player = _load_player(world["id"], callback)
    await callback.answer("Анонс опубликован повторно")
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


HERO_PAGE_SIZE = 8


def _battle_hero_menu(world_id: int, user_id: int, boss_id: int, heroes: list[dict], page: int):
    pages = max(1, (len(heroes) + HERO_PAGE_SIZE - 1) // HERO_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = [[InlineKeyboardButton(
        text=f"{hero['name']} • {hero['rarity']}",
        callback_data=f"miniboss:hero:{world_id}:{user_id}:{boss_id}:{hero['id']}",
    )] for hero in heroes[page * HERO_PAGE_SIZE:(page + 1) * HERO_PAGE_SIZE]]
    navigation = []
    for target, label in ((page - 1, "⬅️"), (page + 1, "➡️")):
        if 0 <= target < pages:
            navigation.append(InlineKeyboardButton(
                text=label,
                callback_data=f"miniboss:heroes:{world_id}:{user_id}:{boss_id}:{target}",
            ))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(
        text="⬅️ К боссу", callback_data=f"mini:boss:{world_id}:{user_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _hero_selection_context(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    try:
        if len(parts) != 6:
            raise ValueError
        world_id, owner_id, boss_id, value = map(int, parts[2:])
    except ValueError:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return None
    world = _load_world(world_id)
    player = _load_player(world_id, callback)
    boss = get_boss(boss_id)
    if world is None or player is None or boss is None or int(boss["world_id"]) != world_id:
        await callback.answer("Босс больше не актуален.", show_alert=True)
        return None
    if boss["status"] not in {"announced", "ready"}:
        await callback.answer("Героя можно выбрать только до начала боя.", show_alert=True)
        return None
    if not _is_joined(player["id"], list_participants(boss_id)):
        await callback.answer("Ты не зарегистрирован на этого босса.", show_alert=True)
        return None
    return world, player, boss, value


@router.callback_query(F.data.startswith("miniboss:heroes:"))
async def boss_heroes_callback(callback: CallbackQuery):
    context = await _hero_selection_context(callback)
    if context is None:
        return
    world, player, boss, page = context
    heroes = get_player_heroes(player["id"])
    await callback.answer()
    await _send_private(
        callback, world, "🎴 Выбери героя на этот бой:",
        _battle_hero_menu(world["id"], callback.from_user.id, boss["id"], heroes, page),
    )


@router.callback_query(F.data.startswith("miniboss:hero:"))
async def boss_select_hero_callback(callback: CallbackQuery):
    context = await _hero_selection_context(callback)
    if context is None:
        return
    world, player, boss, hero_id = context
    try:
        hero = select_battle_hero(boss["id"], player["id"], hero_id)
    except BossError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer(f"✅ На этот бой выбран: {hero['name']}"[:200])
    await _show_boss_home(callback, world, player)
