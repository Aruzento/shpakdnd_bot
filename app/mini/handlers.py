from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.editorial.registry import glossary
from app.mini.editorial.renderer import Text
from app.mini.ui.home import player_menu as _player_menu, home_content as _home_content
from app.mini.ui.navigation import back_menu
from app.mini.ui.hero_cards import hero_caption, send_hero_card
from app.mini.ui.context import (
    username_from_user as _username_from_user,
    personal_callback as _personal_callback,
    parse_personal_callback as _parse_personal_callback,
    load_personal_context as _load_personal_context,
    callback_location_allowed,
)
from app.mini.ui.transport import (
    send_private_text_from_callback as _send_private_text_from_callback,
    send_private_from_launcher as _send_private_from_launcher,
    edit_private as _edit_private,
)
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, EphemeralMessageParameters, FSInputFile, InlineKeyboardMarkup, Message, ReplyParameters
from app.context import check_topic_admin_permission, get_thread_id
from app.mini.presentation import hero_heading, hero_trait_lines
from app.mini.onboarding import starter_message
from app.mini.notifications import publish_pending_notifications
from app.mini.heroes import get_active_hero, get_hero_image
from app.mini.players import create_mini_player, get_mini_player, touch_mini_player
from app.mini.worlds import (
    ensure_configured_mini_world,
    get_launcher_message_id,
    get_mini_world_by_id,
    set_launcher_message_id,
)


router = Router(name="mini")


@editorial_screen
def _launcher_text(world: dict) -> str:
    return (
        editorial_text('handlers.launcher_text.line_01', name=f"{world['name']}")
    )


@editorial_screen
def _launcher_menu(world_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('handlers.launcher_menu.line_01'),
                    callback_data=f"mini:launch:{world_id}",
                )
            ]
        ]
    )




SUBMENUS={'adventures': ['boss', 'tower', 'daily', 'duels'], 'heroarea': ['character', 'inventory', 'equipment', 'collection'], 'fair': ['shop', 'events', 'village'], 'more': ['rules', 'home', 'mythic']}


@editorial_screen
def submenu(action,world,user):
    entries=SUBMENUS[action]
    title=glossary("section."+action, decorated=True)
    rows=[[InlineKeyboardButton(text=Text(glossary("section."+target, decorated=True), icon_key="icon.section."+target),callback_data=_personal_callback(target,world,user))] for target in entries]
    if action in ('adventures','heroarea'):
        rows=[rows[0]+rows[1],rows[2]+rows[3]]
    rows.append([InlineKeyboardButton(text=editorial_text('navigation.back'),callback_data=_personal_callback('home',world,user))])
    return title,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.regexp(r'^mini:(adventures|heroarea|fair|more):'))
@editorial_screen
async def submenu_callback(callback):
    context=await _load_personal_context(callback)
    if context is None: return
    world,player=context
    text,markup=submenu(callback.data.split(':')[1],world['id'],callback.from_user.id)
    await callback.answer()
    await _send_private_text_from_callback(callback,world,text,markup)


@editorial_screen
def _create_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('handlers.create_menu.line_01'),
                    style="success",
                    callback_data=_personal_callback(
                        "create_help", world_id, user_id
                    ),
                )
            ]
        ]
    )




@editorial_screen
def _format_home(world: dict, player: dict) -> str:
    return _home_content(player, get_active_hero(player["id"]))


@editorial_screen
async def _send_home_from_callback(callback: CallbackQuery, world: dict, player: dict):
    active = get_active_hero(player["id"])
    text = _home_content(player, active)
    markup = _player_menu(world["id"], callback.from_user.id)
    if active is not None:
        return await send_hero_card(callback, world, active, text, markup)
    return await _send_private_text_from_callback(callback, world, text, markup)


@editorial_screen
def _parse_character_name(message: Message) -> str | None:
    if not message.text:
        return None

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        return None

    name = parts[1].strip()
    if (
        len(name) >= 2
        and name[0] == name[-1]
        and name[0] in {'"', "'"}
    ):
        name = name[1:-1].strip()

    name = " ".join(name.split())
    return name or None


@editorial_screen
async def _send_private(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    hero: dict | None = None,
):
    """Отправляет ephemeral-сообщение только автору команды."""
    if message.from_user is None:
        return None

    kwargs = {
        "chat_id": message.chat.id,
        "text": text,
        "reply_markup": reply_markup,
        "ephemeral_message_parameters": EphemeralMessageParameters(
            receiver_user_id=message.from_user.id,
        ),
    }

    thread_id = get_thread_id(message)
    if thread_id:
        kwargs["message_thread_id"] = thread_id

    if message.ephemeral_message_id is not None:
        kwargs["reply_parameters"] = ReplyParameters(
            ephemeral_message_id=message.ephemeral_message_id,
        )

    image = get_hero_image(hero) if hero else None
    if image is not None:
        photo_kwargs = dict(kwargs)
        photo_kwargs.pop("text")
        try:
            return await message.bot.send_photo(
                **photo_kwargs, photo=FSInputFile(image), caption=text,
            )
        except TelegramBadRequest:
            pass
    try:
        return await message.bot.send_message(**kwargs)
    except TelegramBadRequest:
        return None


@router.message(Command("minipanel"))
@editorial_screen
async def minipanel_handler(message: Message):
    """Создаёт/обновляет публичный launcher D&D Mini в текущей теме."""
    if not await check_topic_admin_permission(message):
        return

    thread_id = get_thread_id(message)
    world = ensure_configured_mini_world(
        message.chat.id,
        thread_id,
    )

    if not world or not world["enabled"]:
        await _send_private(
            message,
            editorial_text('handlers.minipanel_handler.line_01'),
        )
        return

    old_message_id = get_launcher_message_id(world["id"])
    launcher_message_id = old_message_id

    if old_message_id is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=old_message_id,
                text=_launcher_text(world),
                reply_markup=_launcher_menu(world["id"]),
            )
        except TelegramBadRequest:
            launcher_message_id = None

    if launcher_message_id is None:
        sent = await message.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=thread_id or None,
            text=_launcher_text(world),
            reply_markup=_launcher_menu(world["id"]),
        )
        launcher_message_id = sent.message_id
        set_launcher_message_id(world["id"], launcher_message_id)

    pinned = True
    try:
        await message.bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=launcher_message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pinned = False

    await _send_private(
        message,
        (
            editorial_text('handlers.minipanel_handler.line_02')
            if pinned
            else (
                editorial_text('handlers.minipanel_handler.line_03')
            )
        ),
    )

    try:
        await message.delete()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("mini:launch:"))
@editorial_screen
async def launch_callback(callback: CallbackQuery):
    try:
        world_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        await callback.answer(editorial_text('handlers.launch_callback.line_05'), show_alert=True)
        return

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(
            editorial_text('handlers.launch_callback.line_03'),
            show_alert=True,
        )
        return

    if not callback_location_allowed(callback,world,callback.from_user.id):
        await callback.answer(
            editorial_text('handlers.launch_callback.line_04'),
            show_alert=True,
        )
        return

    player = get_mini_player(world_id, callback.from_user.id)

    if player is None:
        text = (
            editorial_text('handlers.launch_callback.line_01', name=f"{world['name']}")
        )
        markup = _create_menu(world_id, callback.from_user.id)
    else:
        touch_mini_player(
            world_id,
            callback.from_user.id,
            _username_from_user(callback.from_user),
        )
        player = get_mini_player(world_id, callback.from_user.id)
        text = _format_home(world, player)
        markup = _player_menu(world_id, callback.from_user.id)

    try:
        if player is not None:
            await _send_home_from_callback(callback, world, player)
        else:
            await _send_private_from_launcher(callback, world, text, markup)
    except TelegramAPIError as error:
        print(
            "Ошибка открытия D&D Mini: "
            f"user={callback.from_user.id} "
            f"world={world_id} "
            f"{type(error).__name__}: {error}"
        )
        await callback.answer(
            editorial_text('handlers.launch_callback.line_06'),
            show_alert=True,
        )
        return

    await callback.answer(editorial_text('handlers.launch_callback.line_02'))


@router.message(Command("mini"))
@editorial_screen
async def mini_handler(message: Message):
    """Скрытый резервный вход; основной вход — кнопка в закрепе."""
    if message.from_user is None:
        return

    world = ensure_configured_mini_world(
        message.chat.id,
        get_thread_id(message),
    )
    if not world or not world["enabled"]:
        return

    player = get_mini_player(world["id"], message.from_user.id)
    if player is None:
        await _send_private(
            message,
            editorial_text('handlers.mini_handler.line_01', name=f"{world['name']}"),
            _create_menu(world["id"], message.from_user.id),
        )
        return

    touch_mini_player(
        world["id"],
        message.from_user.id,
        _username_from_user(message.from_user),
    )
    player = get_mini_player(world["id"], message.from_user.id)

    active = get_active_hero(player["id"])
    await _send_private(
        message, _home_content(player, active),
        _player_menu(world["id"], message.from_user.id), hero=active,
    )


@router.message(Command("minicreate"))
@editorial_screen
async def minicreate_handler(message: Message):
    if message.from_user is None:
        return

    world = ensure_configured_mini_world(
        message.chat.id,
        get_thread_id(message),
    )
    if not world or not world["enabled"]:
        return

    existing = get_mini_player(world["id"], message.from_user.id)
    if existing is not None:
        await _send_private(
            message,
            editorial_text('handlers.minicreate_handler.line_02', character_name=f"{existing['character_name']}"),
            _player_menu(world["id"], message.from_user.id),
        )
        return

    character_name = _parse_character_name(message)
    if character_name is None:
        await _send_private(
            message,
            editorial_text('handlers.minicreate_handler.line_03'),
        )
        return

    if len(character_name) > 64:
        await _send_private(
            message,
            editorial_text('handlers.minicreate_handler.line_04'),
        )
        return

    try:
        player = create_mini_player(
            world["id"],
            message.from_user.id,
            _username_from_user(message.from_user),
            character_name,
        )
    except ValueError as error:
        await _send_private(message, f"❌ {error}")
        return

    await publish_pending_notifications(message.bot, player_id=player['id'])
    gift = starter_message() if player.get('onboarding_granted') else editorial_text('handlers.minicreate_handler.line_01')
    await _send_private(message, editorial_text('handlers.minicreate_handler.line_05') + gift + "\n\n" + _format_home(world,player), _player_menu(world['id'],message.from_user.id))



@router.callback_query(F.data.startswith("mini:create_help:"))
@editorial_screen
async def create_help_callback(callback: CallbackQuery):
    parsed = _parse_personal_callback(callback)
    if parsed is None:
        await callback.answer(editorial_text('handlers.create_help_callback.line_02'), show_alert=True)
        return

    _, world_id, owner_id = parsed
    if callback.from_user.id != owner_id:
        await callback.answer(editorial_text('handlers.create_help_callback.line_03'), show_alert=True)
        return

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(editorial_text('handlers.create_help_callback.line_04'), show_alert=True)
        return

    message=callback.message
    if not callback_location_allowed(callback,world,owner_id):
        await callback.answer(editorial_text('handlers.create_help_callback.line_05'),show_alert=True);return
    existing=get_mini_player(world_id,owner_id)
    if existing:
        await callback.answer();await _send_home_from_callback(callback,world,existing);return
    from app.mini.registration import begin
    token=begin(world_id,owner_id)
    bot_user=await callback.bot.get_me()
    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=editorial_text('handlers.create_help_callback.line_06'),url=f'https://t.me/{bot_user.username}?start=mini_reg_{token}',style='success')]])
    await callback.answer()
    await _edit_private(callback,editorial_text('handlers.create_help_callback.line_01'),markup)



@router.callback_query(F.data.startswith("mini:created:"))
@router.callback_query(F.data.startswith("mini:home:"))
@editorial_screen
async def home_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    await callback.answer()
    await _send_home_from_callback(callback, world, player)


@router.callback_query(F.data.startswith("mini:character:"))
@editorial_screen
async def character_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    active = get_active_hero(player["id"])
    text = hero_caption(active) if active is not None else editorial_text('handlers.character_callback.line_01')
    from app.mini.ui.hero_cards import profile_menu
    markup = profile_menu(world["id"],callback.from_user.id,active)

    await callback.answer()
    if active is not None:
        await send_hero_card(
            callback,
            world,
            active,
            text,
            markup,
        )
    else:
        await _edit_private(
            callback,
            text,
            markup,
        )


from app.mini.ui.hero_cards import hero_caption as _hero_caption

from app.mini.ui import activities, heroes, inventory, shop, hero_selector, registration, summons

for screen in (inventory, shop, heroes, activities, hero_selector, registration, summons):
    router.include_router(screen.router)

from app.mini.tower.handlers import router as tower_router
from app.mini.equipment.handlers import router as equipment_router
router.include_router(tower_router)
router.include_router(equipment_router)

from app.mini.village.handlers import router as village_router
from app.mini.duels.handlers import router as duels_router
from app.mini.mythic.handlers import router as mythic_router

router.include_router(village_router)
router.include_router(duels_router)
router.include_router(mythic_router)
