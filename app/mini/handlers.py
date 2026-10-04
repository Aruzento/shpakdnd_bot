from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    EphemeralMessageParameters,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)

from app.context import (
    check_topic_admin_permission,
    get_thread_id,
)
from app.mini.players import (
    create_mini_player,
    get_mini_player,
    touch_mini_player,
)
from app.mini.worlds import (
    get_launcher_message_id,
    get_mini_world,
    set_launcher_message_id,
)


router = Router(name="mini")


def _launcher_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎮 Открыть D&D Mini",
                    callback_data="mini:launch",
                ),
            ],
        ]
    )


def _launcher_text(world: dict) -> str:
    return (
        f"🎲 {world['name']}\n"
        "━━━━━━━━━━━━━━\n\n"
        "Ежедневные приключения, боссы,\n"
        "герои, награды и магазин.\n\n"
        "Нажми кнопку — личное меню увидишь только ты."
    )


def _username_from_user(user) -> str:
    if user is None or not user.username:
        return ""

    return "@" + user.username.strip().lower()


def _player_menu(
    telegram_user_id: int,
) -> InlineKeyboardMarkup:
    suffix = str(telegram_user_id)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Дейлик",
                    callback_data=f"mini:daily:{suffix}",
                ),
                InlineKeyboardButton(
                    text="👹 Босс",
                    callback_data=f"mini:boss:{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛒 Магазин",
                    callback_data=f"mini:shop:{suffix}",
                ),
                InlineKeyboardButton(
                    text="🎴 Коллекция",
                    callback_data=f"mini:collection:{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Персонаж",
                    callback_data=f"mini:character:{suffix}",
                ),
                InlineKeyboardButton(
                    text="🏆 Рейтинг",
                    callback_data=f"mini:rating:{suffix}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=f"mini:home:{suffix}",
                ),
            ],
        ]
    )


def _create_menu(
    telegram_user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Создать персонажа",
                    callback_data=(
                        f"mini:create_help:{telegram_user_id}"
                    ),
                ),
            ],
        ]
    )


def _back_menu(
    telegram_user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"mini:home:{telegram_user_id}",
                ),
            ],
        ]
    )


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


def _get_scope(message: Message) -> tuple[int, int]:
    return message.chat.id, get_thread_id(message)


def _format_home(
    world: dict,
    player: dict,
) -> str:
    active_hero = (
        "пока не выбран"
        if player["active_hero_id"] is None
        else f"#{player['active_hero_id']}"
    )

    return (
        f"🎲 {world['name']}\n\n"
        f"👤 Персонаж: {player['character_name']}\n"
        f"🎴 Активный герой: {active_hero}\n"
        f"🪙 {world['currency_name']}: {player['coins']}\n\n"
        "Выбери раздел:"
    )


async def _send_private(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """
    Отправляет меню только пользователю, который вызвал команду.

    Если команда уже пришла как ephemeral, отвечаем на неё.
    Если команда была обычной, пробуем отправить ephemeral напрямую.
    Это сработает без callback, если бот является администратором чата.
    """
    if message.from_user is None:
        return None

    thread_id = get_thread_id(message)

    kwargs = {
        "chat_id": message.chat.id,
        "text": text,
        "reply_markup": reply_markup,
        "ephemeral_message_parameters": EphemeralMessageParameters(
            receiver_user_id=message.from_user.id,
        ),
    }

    if thread_id:
        kwargs["message_thread_id"] = thread_id

    if message.ephemeral_message_id is not None:
        kwargs["reply_parameters"] = ReplyParameters(
            ephemeral_message_id=message.ephemeral_message_id,
        )

    try:
        return await message.bot.send_message(**kwargs)
    except TelegramBadRequest:
        # Не показываем само меню публично.
        # Оставляем только короткую инструкцию на случай старого клиента
        # или если команда ещё не зарегистрировалась как ephemeral.
        await message.answer(
            "⚠️ Приватное Mini-меню не открылось.\n"
            "Обнови Telegram и выбери /mini "
            "из меню команд бота."
        )
        return None


async def _send_private_from_callback(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """
    Открывает персональное ephemeral-меню вместо публичного launcher
    только для пользователя, который нажал кнопку.
    """
    if callback.message is None:
        return None

    thread_id = get_thread_id(callback.message)

    kwargs = {
        "chat_id": callback.message.chat.id,
        "text": text,
        "reply_markup": reply_markup,
        "ephemeral_message_parameters": EphemeralMessageParameters(
            receiver_user_id=callback.from_user.id,
            callback_query_id=callback.id,
            replace_callback_query_message=True,
        ),
    }

    if thread_id:
        kwargs["message_thread_id"] = thread_id

    return await callback.bot.send_message(**kwargs)


async def _edit_private(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    if callback.message is None:
        return

    if callback.message.ephemeral_message_id is not None:
        await callback.message.edit_ephemeral_text(
            text=text,
            reply_markup=reply_markup,
        )
        return

    # Резерв для старого публичного сообщения от предыдущей версии.
    await callback.message.edit_text(
        text=text,
        reply_markup=reply_markup,
    )


async def _get_world_for_message(
    message: Message,
) -> dict | None:
    chat_id, thread_id = _get_scope(message)

    world = get_mini_world(
        chat_id,
        thread_id,
    )

    if not world or not world["enabled"]:
        await _send_private(
            message,
            "❌ В этой теме D&D Mini не настроен.",
        )
        return None

    return world


def _callback_owner_id(
    callback: CallbackQuery,
) -> int | None:
    if not callback.data:
        return None

    try:
        return int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return None


async def _check_callback_owner(
    callback: CallbackQuery,
) -> bool:
    owner_id = _callback_owner_id(callback)

    if (
        owner_id is None
        or callback.from_user.id != owner_id
    ):
        await callback.answer(
            "Это меню другого игрока. Напиши /mini.",
            show_alert=True,
        )
        return False

    return True


async def _load_callback_context(
    callback: CallbackQuery,
) -> tuple[dict, dict] | None:
    if not await _check_callback_owner(callback):
        return None

    if callback.message is None:
        await callback.answer(
            "Не удалось открыть Mini.",
            show_alert=True,
        )
        return None

    chat_id = callback.message.chat.id
    thread_id = get_thread_id(callback.message)

    world = get_mini_world(
        chat_id,
        thread_id,
    )

    if not world or not world["enabled"]:
        await callback.answer(
            "D&D Mini в этой теме отключён.",
            show_alert=True,
        )
        return None

    player = get_mini_player(
        world["id"],
        callback.from_user.id,
    )

    if player is None:
        await callback.answer(
            "Сначала создай Mini-персонажа.",
            show_alert=True,
        )
        return None

    touch_mini_player(
        world["id"],
        callback.from_user.id,
        _username_from_user(callback.from_user),
    )

    return world, player


@router.message(Command("minipanel"))
async def minipanel_handler(message: Message):
    """
    Один раз создаёт публичный launcher D&D Mini и пытается
    закрепить его в текущей теме.
    """
    if not await check_topic_admin_permission(message):
        return

    world = await _get_world_for_message(message)

    if world is None:
        return

    old_message_id = get_launcher_message_id(
        world["id"],
    )

    launcher_message = None

    if old_message_id is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=old_message_id,
                text=_launcher_text(world),
                reply_markup=_launcher_menu(),
            )

            launcher_message_id = old_message_id
        except TelegramBadRequest:
            old_message_id = None

    if old_message_id is None:
        launcher_message = await message.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=(
                get_thread_id(message) or None
            ),
            text=_launcher_text(world),
            reply_markup=_launcher_menu(),
        )

        launcher_message_id = launcher_message.message_id

        set_launcher_message_id(
            world["id"],
            launcher_message_id,
        )

    pinned = True

    try:
        await message.bot.pin_chat_message(
            chat_id=message.chat.id,
            message_id=launcher_message_id,
            disable_notification=True,
        )
    except TelegramBadRequest:
        pinned = False

    # Стараемся убрать одноразовую админ-команду из истории.
    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    status = (
        "✅ Панель D&D Mini создана и закреплена."
        if pinned
        else (
            "✅ Панель D&D Mini создана.\\n"
            "⚠️ Бот не смог закрепить её автоматически — "
            "закрепи сообщение вручную."
        )
    )

    try:
        await message.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=(
                get_thread_id(message) or None
            ),
            text=status,
            ephemeral_message_parameters=EphemeralMessageParameters(
                receiver_user_id=message.from_user.id,
            ),
        )
    except TelegramBadRequest:
        # Ничего публичного не отправляем: launcher уже создан.
        pass


@router.callback_query(F.data == "mini:launch")
async def launch_callback(
    callback: CallbackQuery,
):
    if callback.message is None:
        await callback.answer(
            "Не удалось открыть D&D Mini.",
            show_alert=True,
        )
        return

    chat_id = callback.message.chat.id
    thread_id = get_thread_id(callback.message)

    world = get_mini_world(
        chat_id,
        thread_id,
    )

    if not world or not world["enabled"]:
        await callback.answer(
            "D&D Mini в этой теме не настроен.",
            show_alert=True,
        )
        return

    player = get_mini_player(
        world["id"],
        callback.from_user.id,
    )

    if player is None:
        text = (
            f"🎲 {world['name']}\\n\\n"
            "У тебя пока нет Mini-персонажа.\\n\\n"
            "Создание займёт несколько секунд."
        )
        markup = _create_menu(
            callback.from_user.id,
        )
    else:
        touch_mini_player(
            world["id"],
            callback.from_user.id,
            _username_from_user(callback.from_user),
        )

        player = get_mini_player(
            world["id"],
            callback.from_user.id,
        )

        text = _format_home(
            world,
            player,
        )
        markup = _player_menu(
            callback.from_user.id,
        )

    try:
        await _send_private_from_callback(
            callback,
            text,
            reply_markup=markup,
        )
    except TelegramBadRequest:
        await callback.answer(
            "Не удалось открыть личное меню. "
            "Обнови Telegram и попробуй ещё раз.",
            show_alert=True,
        )
        return

    await callback.answer()


@router.message(Command("mini"))
async def mini_handler(message: Message):
    world = await _get_world_for_message(message)

    if world is None or message.from_user is None:
        return

    player = get_mini_player(
        world["id"],
        message.from_user.id,
    )

    if player is None:
        await _send_private(
            message,
            f"🎲 {world['name']}\n\n"
            "У тебя пока нет Mini-персонажа.\n\n"
            "Создание займёт несколько секунд.",
            reply_markup=_create_menu(
                message.from_user.id,
            ),
        )
        return

    touch_mini_player(
        world["id"],
        message.from_user.id,
        _username_from_user(message.from_user),
    )

    player = get_mini_player(
        world["id"],
        message.from_user.id,
    )

    await _send_private(
        message,
        _format_home(
            world,
            player,
        ),
        reply_markup=_player_menu(
            message.from_user.id,
        ),
    )


@router.message(Command("minicreate"))
async def minicreate_handler(message: Message):
    world = await _get_world_for_message(message)

    if world is None or message.from_user is None:
        return

    existing = get_mini_player(
        world["id"],
        message.from_user.id,
    )

    if existing is not None:
        await _send_private(
            message,
            "❌ У тебя уже есть Mini-персонаж:\n"
            f"{existing['character_name']}\n\n"
            "Открой меню командой /mini.",
        )
        return

    character_name = _parse_character_name(
        message,
    )

    if character_name is None:
        await _send_private(
            message,
            "Использование:\n"
            '/minicreate "Имя персонажа"\n\n'
            "Кавычки необязательны, но удобны "
            "для имени из нескольких слов.",
        )
        return

    if len(character_name) > 64:
        await _send_private(
            message,
            "❌ Имя слишком длинное. Максимум — 64 символа.",
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
        await _send_private(
            message,
            f"❌ {error}",
        )
        return

    await _send_private(
        message,
        "✅ Mini-персонаж создан!\n\n"
        + _format_home(
            world,
            player,
        ),
        reply_markup=_player_menu(
            message.from_user.id,
        ),
    )


@router.callback_query(
    F.data.startswith("mini:create_help:")
)
async def create_help_callback(
    callback: CallbackQuery,
):
    if not await _check_callback_owner(callback):
        return

    await callback.answer()

    await _edit_private(
        callback,
        "👤 Создание Mini-персонажа\n\n"
        "Напиши:\n"
        '/minicreate "Дед Максим"\n\n'
        "Эту команду видишь только ты и бот.\n\n"
        "Можно создать кого угодно. "
        "Класс и раса появятся позже через героев гачи.",
        reply_markup=_back_menu(
            callback.from_user.id,
        ),
    )


@router.callback_query(
    F.data.startswith("mini:home:")
)
async def home_callback(
    callback: CallbackQuery,
):
    context = await _load_callback_context(
        callback,
    )

    if context is None:
        return

    world, _ = context

    player = get_mini_player(
        world["id"],
        callback.from_user.id,
    )

    await callback.answer()

    await _edit_private(
        callback,
        _format_home(
            world,
            player,
        ),
        reply_markup=_player_menu(
            callback.from_user.id,
        ),
    )


@router.callback_query(
    F.data.startswith("mini:character:")
)
async def character_callback(
    callback: CallbackQuery,
):
    context = await _load_callback_context(
        callback,
    )

    if context is None:
        return

    world, player = context

    username = (
        player["username"]
        if player["username"]
        else "без @username"
    )

    await callback.answer()

    await _edit_private(
        callback,
        "👤 Mini-персонаж\n\n"
        f"Имя: {player['character_name']}\n"
        f"Игрок: {username}\n"
        "🎴 Активный герой: пока не выбран\n"
        f"🪙 {world['currency_name']}: "
        f"{player['coins']}\n\n"
        "Уровень в D&D Mini фиксированный.\n"
        "Раса и класс будут определяться "
        "активным героем из коллекции.",
        reply_markup=_back_menu(
            callback.from_user.id,
        ),
    )


async def _not_ready_callback(
    callback: CallbackQuery,
    text: str,
):
    if not await _check_callback_owner(callback):
        return

    await callback.answer(
        text,
        show_alert=True,
    )


@router.callback_query(
    F.data.startswith("mini:daily:")
)
async def daily_callback(callback: CallbackQuery):
    await _not_ready_callback(
        callback,
        "⚔️ Дейлики добавим на шаге 4.",
    )


@router.callback_query(
    F.data.startswith("mini:boss:")
)
async def boss_callback(callback: CallbackQuery):
    await _not_ready_callback(
        callback,
        "👹 Боссы появятся после базовых Mini-механик.",
    )


@router.callback_query(
    F.data.startswith("mini:shop:")
)
async def shop_callback(callback: CallbackQuery):
    await _not_ready_callback(
        callback,
        "🛒 Магазин добавим на следующем этапе экономики.",
    )


@router.callback_query(
    F.data.startswith("mini:collection:")
)
async def collection_callback(
    callback: CallbackQuery,
):
    await _not_ready_callback(
        callback,
        "🎴 Коллекция появится вместе с гачей.",
    )


@router.callback_query(
    F.data.startswith("mini:rating:")
)
async def rating_callback(callback: CallbackQuery):
    await _not_ready_callback(
        callback,
        "🏆 Рейтинг добавим позже.",
    )
