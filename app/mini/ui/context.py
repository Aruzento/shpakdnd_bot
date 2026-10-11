from aiogram.types import CallbackQuery
from app.mini.players import get_mini_player, touch_mini_player
from app.mini.worlds import get_mini_world_by_id


async def valid_world_callback(callback,world):
    from app.topic_guard import is_mini_topic
    if not is_mini_topic(world['chat_id'],world.get('thread_id',0)):
        await callback.answer('Эта тема больше не работает в режиме Mini.',show_alert=True);return False
    message=getattr(callback,'message',None)
    if message is not None:
        chat=getattr(message,'chat',None)
        chat_id=getattr(chat,'id',None)
        if isinstance(chat_id,int) and chat_id not in (world['chat_id'],callback.from_user.id):
            await callback.answer('Кнопка из другого мира.',show_alert=True);return False
        thread=getattr(message,'message_thread_id',None)
        if chat_id==world['chat_id'] and isinstance(thread,(int,type(None))) and (thread or 0)!=(world.get('thread_id') or 0):
            await callback.answer('Кнопка из другой темы.',show_alert=True);return False
    return True


def username_from_user(user) -> str:
    if user is None or not user.username:
        return ""
    return "@" + user.username.strip().lower()


def personal_callback(action: str, world_id: int, user_id: int) -> str:
    return f"mini:{action}:{world_id}:{user_id}"


def parse_extended_callback(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[int, int, str] | None:
    if not callback.data:
        return None
    parts = callback.data.split(":", maxsplit=4)
    if len(parts) != 5 or parts[0] != "mini" or parts[1] != prefix:
        return None
    try:
        return int(parts[2]), int(parts[3]), parts[4]
    except ValueError:
        return None


async def load_extended_context(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[dict, dict, str] | None:
    parsed = parse_extended_callback(callback, prefix)
    if parsed is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None

    world_id, owner_id, extra = parsed
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return None

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer("D&D Mini сейчас недоступен.", show_alert=True)
        return None

    if not await valid_world_callback(callback,world):return None
    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer("Сначала создай Mini-персонажа.", show_alert=True)
        return None

    touch_mini_player(world_id, owner_id, username_from_user(callback.from_user))
    player = get_mini_player(world_id, owner_id)
    return world, player, extra


def parse_personal_callback(
    callback: CallbackQuery,
) -> tuple[str, int, int] | None:
    if not callback.data:
        return None

    parts = callback.data.split(":")
    if len(parts) != 4 or parts[0] != "mini":
        return None

    try:
        return parts[1], int(parts[2]), int(parts[3])
    except ValueError:
        return None


async def load_personal_context(
    callback: CallbackQuery,
) -> tuple[dict, dict] | None:
    parsed = parse_personal_callback(callback)
    if parsed is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None

    _, world_id, owner_id = parsed

    if callback.from_user.id != owner_id:
        await callback.answer(
            "Это меню другого игрока.",
            show_alert=True,
        )
        return None

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(
            "D&D Mini сейчас недоступен.",
            show_alert=True,
        )
        return None

    if not await valid_world_callback(callback,world):return None
    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer(
            "Сначала создай Mini-персонажа.",
            show_alert=True,
        )
        return None

    touch_mini_player(
        world_id,
        owner_id,
        username_from_user(callback.from_user),
    )

    player = get_mini_player(world_id, owner_id)
    return world, player


def parse_shop_callback(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[int, int, str] | None:
    if not callback.data:
        return None
    parts = callback.data.split(":", 4)
    if len(parts) not in {4, 5} or parts[0] != "mini" or parts[1] != prefix:
        return None
    try:
        world_id = int(parts[2])
        owner_id = int(parts[3])
    except ValueError:
        return None
    tail = parts[4] if len(parts) == 5 else ""
    return world_id, owner_id, tail


async def shop_context(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[dict, dict, str] | None:
    parsed = parse_shop_callback(callback, prefix)
    if parsed is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return None
    world_id, owner_id, tail = parsed
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return None
    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer("D&D Mini сейчас недоступен.", show_alert=True)
        return None
    if not await valid_world_callback(callback,world):return None
    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer("Сначала создай Mini-персонажа.", show_alert=True)
        return None
    return world, player, tail


def load_world(world_id: int) -> dict | None:
    world = get_mini_world_by_id(world_id)
    return world if world and world["enabled"] else None


def load_player(world_id: int, callback: CallbackQuery) -> dict | None:
    player = get_mini_player(world_id, callback.from_user.id)
    if player is not None:
        touch_mini_player(world_id, callback.from_user.id, username_from_user(callback.from_user))
        player = get_mini_player(world_id, callback.from_user.id)
    return player
