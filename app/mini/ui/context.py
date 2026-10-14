from app.mini.editorial import text as editorial_text, screen as editorial_screen
from aiogram.types import CallbackQuery
from app.mini.players import get_mini_player, touch_mini_player
from app.mini.worlds import get_mini_world_by_id
from app.topic_guard import is_mini_topic



@editorial_screen
def username_from_user(user) -> str:
    if user is None or not user.username:
        return ""
    return "@" + user.username.strip().lower()


@editorial_screen
def personal_callback(action: str, world_id: int, user_id: int) -> str:
    data=f"mini:{action}:{world_id}:{user_id}"
    if len(data.encode())>64:raise ValueError(editorial_text('errors.ui.context.personal_callback.01'))
    return data


@editorial_screen
def callback_location_allowed(callback,world,owner_id):
    message=getattr(callback,'message',None)
    chat=getattr(message,'chat',None)
    private=chat is not None and getattr(chat,'type',None)=='private' and chat.id==owner_id
    topic=chat is not None and chat.id==world['chat_id'] and (getattr(message,'message_thread_id',None) or 0)==world['thread_id']
    return is_mini_topic(world['chat_id'],world['thread_id']) and (private or topic)


@editorial_screen
async def validate_callback_location(callback,world,owner_id):
    if callback_location_allowed(callback,world,owner_id):return True
    error=(editorial_text('context.validate_callback_location.line_01') if is_mini_topic(world['chat_id'],world['thread_id'])
           else editorial_text('context.validate_callback_location.line_02'))
    await callback.answer(error,show_alert=True)
    return False


@editorial_screen
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


@editorial_screen
async def load_extended_context(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[dict, dict, str] | None:
    parsed = parse_extended_callback(callback, prefix)
    if parsed is None:
        await callback.answer(editorial_text('context.load_extended_context.line_01'), show_alert=True)
        return None

    world_id, owner_id, extra = parsed
    if callback.from_user.id != owner_id:
        await callback.answer(editorial_text('context.load_extended_context.line_02'), show_alert=True)
        return None

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(editorial_text('context.load_extended_context.line_03'), show_alert=True)
        return None

    if not await validate_callback_location(callback,world,owner_id):
        return None

    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer(editorial_text('context.load_extended_context.line_04'), show_alert=True)
        return None

    touch_mini_player(world_id, owner_id, username_from_user(callback.from_user))
    player = get_mini_player(world_id, owner_id)
    return world, player, extra


@editorial_screen
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


@editorial_screen
async def load_personal_context(
    callback: CallbackQuery,
) -> tuple[dict, dict] | None:
    parsed = parse_personal_callback(callback)
    if parsed is None:
        await callback.answer(editorial_text('context.load_personal_context.line_01'), show_alert=True)
        return None

    _, world_id, owner_id = parsed

    if callback.from_user.id != owner_id:
        await callback.answer(
            editorial_text('context.load_personal_context.line_02'),
            show_alert=True,
        )
        return None

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(
            editorial_text('context.load_personal_context.line_03'),
            show_alert=True,
        )
        return None

    if not await validate_callback_location(callback,world,owner_id):
        return None

    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer(
            editorial_text('context.load_personal_context.line_04'),
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


@editorial_screen
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


@editorial_screen
async def shop_context(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[dict, dict, str] | None:
    parsed = parse_shop_callback(callback, prefix)
    if parsed is None:
        await callback.answer(editorial_text('context.shop_context.line_01'), show_alert=True)
        return None
    world_id, owner_id, tail = parsed
    if callback.from_user.id != owner_id:
        await callback.answer(editorial_text('context.shop_context.line_02'), show_alert=True)
        return None
    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(editorial_text('context.shop_context.line_03'), show_alert=True)
        return None
    if not await validate_callback_location(callback,world,owner_id):
        return None

    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer(editorial_text('context.shop_context.line_04'), show_alert=True)
        return None
    return world, player, tail


@editorial_screen
def load_world(world_id: int, callback=None) -> dict | None:
    world = get_mini_world_by_id(world_id)
    if not world or not world['enabled']:return None
    if callback is not None and not callback_location_allowed(callback,world,callback.from_user.id):return None
    return world


@editorial_screen
def load_player(world_id: int, callback: CallbackQuery) -> dict | None:
    player = get_mini_player(world_id, callback.from_user.id)
    if player is not None:
        touch_mini_player(world_id, callback.from_user.id, username_from_user(callback.from_user))
        player = get_mini_player(world_id, callback.from_user.id)
    return player


@editorial_screen
async def valid_world_callback(callback,world):
    """Earlier draft entry point; use the canonical owner/location validation."""
    return await validate_callback_location(callback,world,callback.from_user.id)
