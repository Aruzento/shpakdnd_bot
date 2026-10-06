from app.mini.ui.context import load_world as _load_world
from app.mini.ui.transport import send_private_text_from_callback as _send_private
import asyncio
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile
from app.context import get_topic_admin
from app.mini.boss.catalog import boss_image_path
from app.mini.boss.combat import BossCombatError, advance_expired_turns
from app.mini.boss.notices import timeout_event_lines
from app.mini.boss.public import (
    ensure_public_turn,
    format_public_boss,
    public_boss_menu,
    refresh_public_boss,
    replace_public_turn,
)
from app.mini.boss.service import (
    BossError,
    get_active_boss,
    get_boss,
    list_participants,
    set_signup_message,
)
from app.mini.ui.context import username_from_user
from app.mini.boss.presentation import format_private_boss, boss_private_menu, no_boss_menu

_BOSS_PUBLISH_LOCKS: dict[int, asyncio.Lock] = {}

def boss_publish_lock(boss_id: int) -> asyncio.Lock:
    boss_id = int(boss_id)
    lock = _BOSS_PUBLISH_LOCKS.get(boss_id)
    if lock is None:
        lock = asyncio.Lock()
        _BOSS_PUBLISH_LOCKS[boss_id] = lock
    return lock


def is_admin(callback: CallbackQuery, world: dict) -> bool:
    required = get_topic_admin(int(world["chat_id"]), int(world["thread_id"]))
    current = username_from_user(callback.from_user)
    return bool(required and current and current == required)


def is_joined(player_id: int, participants: list[dict]) -> bool:
    return any(int(row["player_id"]) == int(player_id) for row in participants)


async def show_boss_home(
    callback: CallbackQuery,
    world: dict,
    player: dict,
):
    boss = get_active_boss(world["id"])
    admin = is_admin(callback, world)

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
            no_boss_menu(
                world["id"], callback.from_user.id, is_admin=admin
            ),
        )
        return

    participants = list_participants(boss["id"])
    joined = is_joined(player["id"], participants)
    await _send_private(
        callback,
        world,
        format_private_boss(boss, participants, joined=joined),
        boss_private_menu(
            world["id"],
            callback.from_user.id,
            boss,
            joined=joined,
            is_admin=admin,
        ),
    )


async def refresh_from_callback(callback: CallbackQuery, world: dict, boss: dict):
    await refresh_public_boss(callback.bot, world, boss)


async def publish_boss(
    callback: CallbackQuery,
    world: dict,
    boss: dict,
    *,
    replace_existing: bool = False,
    expected_message_id: int | None = None,
):
    """Публикует одну карточку босса и защищается от двойных callback'ов."""
    boss_id = int(boss["id"])

    async with boss_publish_lock(boss_id):
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
        text = format_public_boss(fresh, participants)
        markup = public_boss_menu(world["id"], fresh)
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


async def retire_old_public_boss(
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


async def admin_action_context(
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
        or not is_admin(callback, world)
    ):
        await callback.answer("Нет прав администратора.", show_alert=True)
        return None
    return world, boss, owner_id

