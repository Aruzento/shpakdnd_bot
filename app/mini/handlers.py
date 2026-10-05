from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
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
from app.mini.daily import claim_daily
from app.mini.players import (
    create_mini_player,
    get_mini_player,
    touch_mini_player,
)
from app.mini.shop import (
    ShopError,
    ShopInsufficientFunds,
    ShopLimitReached,
    ShopOutOfStock,
    get_offer,
    get_offer_purchase_count,
    get_offers_by_category,
    get_player_goods,
    get_shop_categories,
    purchase_offer,
)
from app.mini.wallet import get_wallet_history
from app.mini.worlds import (
    ensure_configured_mini_world,
    get_launcher_message_id,
    get_mini_world_by_id,
    set_launcher_message_id,
)


router = Router(name="mini")


def _username_from_user(user) -> str:
    if user is None or not user.username:
        return ""
    return "@" + user.username.strip().lower()


def _launcher_text(world: dict) -> str:
    return (
        f"🎲 {world['name']}\n"
        "━━━━━━━━━━━━━━\n\n"
        "Ежедневные приключения, боссы,\n"
        "герои, награды и магазин.\n\n"
        "Нажми кнопку — личное меню увидишь только ты."
    )


def _launcher_menu(world_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎮 Открыть D&D Mini",
                    callback_data=f"mini:launch:{world_id}",
                )
            ]
        ]
    )


def _personal_callback(action: str, world_id: int, user_id: int) -> str:
    return f"mini:{action}:{world_id}:{user_id}"


def _player_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Дейлик",
                    callback_data=_personal_callback(
                        "daily", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text="👹 Босс",
                    callback_data=_personal_callback(
                        "boss", world_id, user_id
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛒 Магазин",
                    callback_data=_personal_callback(
                        "shop", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text="🎴 Коллекция",
                    callback_data=_personal_callback(
                        "collection", world_id, user_id
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Персонаж",
                    callback_data=_personal_callback(
                        "character", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text="💰 Кошелёк",
                    callback_data=_personal_callback(
                        "wallet", world_id, user_id
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Рейтинг",
                    callback_data=_personal_callback(
                        "rating", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=_personal_callback(
                        "home", world_id, user_id
                    ),
                ),
            ],
        ]
    )


def _create_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Создать персонажа",
                    callback_data=_personal_callback(
                        "create_help", world_id, user_id
                    ),
                )
            ]
        ]
    )


def _back_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=_personal_callback(
                        "home", world_id, user_id
                    ),
                )
            ]
        ]
    )


def _daily_result_menu(
    world_id: int,
    user_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Кошелёк",
                    callback_data=_personal_callback(
                        "wallet", world_id, user_id
                    ),
                ),
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=_personal_callback(
                        "home", world_id, user_id
                    ),
                ),
            ]
        ]
    )



def _shop_main_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for category in get_shop_categories(world_id):
        count = int(category.get("product_count", 0))
        suffix = f" · {count}" if count else " · скоро"
        rows.append([
            InlineKeyboardButton(
                text=f"{category['title']}{suffix}",
                callback_data=(
                    f"mini:shopcat:{world_id}:{user_id}:{category['code']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="🎒 Мои товары",
            callback_data=f"mini:goods:{world_id}:{user_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=_personal_callback("home", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shop_category_menu(
    world_id: int,
    user_id: int,
    category: str,
) -> InlineKeyboardMarkup:
    rows = []
    for offer in get_offers_by_category(world_id, category):
        rows.append([
            InlineKeyboardButton(
                text=f"{offer['title']} — {offer['price']} 🪙",
                callback_data=(
                    f"mini:shopitem:{world_id}:{user_id}:{offer['id']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К категориям",
            callback_data=_personal_callback("shop", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shop_item_menu(
    world_id: int,
    user_id: int,
    offer_id: int,
    price: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Купить за {price} 🪙",
                    callback_data=(
                        f"mini:shopbuy:{world_id}:{user_id}:{offer_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В магазин",
                    callback_data=_personal_callback("shop", world_id, user_id),
                )
            ],
        ]
    )


def _shop_after_purchase_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎒 Мои товары",
                    callback_data=f"mini:goods:{world_id}:{user_id}",
                ),
                InlineKeyboardButton(
                    text="🛒 Магазин",
                    callback_data=_personal_callback("shop", world_id, user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ На главную",
                    callback_data=_personal_callback("home", world_id, user_id),
                )
            ],
        ]
    )


def _parse_shop_callback(
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


async def _shop_context(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[dict, dict, str] | None:
    parsed = _parse_shop_callback(callback, prefix)
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
    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer("Сначала создай Mini-персонажа.", show_alert=True)
        return None
    return world, player, tail


def _format_goods(goods: dict) -> str:
    lines = ["🎒 Мои товары", ""]

    inventory = goods["inventory"]
    certificates = goods["certificates"]

    if inventory:
        lines.append("Предметы:")
        for item in inventory:
            suffix = f" ×{item['quantity']}" if int(item['quantity']) > 1 else ""
            lines.append(f"• {item['name']}{suffix}")
        lines.append("")

    if certificates:
        lines.append("Сертификаты:")
        for certificate in certificates:
            lines.append(
                f"• #{certificate['purchase_id']} {certificate['title']}"
            )
        lines.append("")

    if not inventory and not certificates:
        lines.append("Пока ничего нет.")

    lines.append("Сертификаты позже можно будет погашать отдельной кнопкой мастера.")
    return "\n".join(lines)

def _format_home(world: dict, player: dict) -> str:
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


def _format_wallet_history(history: list[dict]) -> str:
    if not history:
        return (
            "История пока пустая.\n"
            "Первые монеты можно будет получить в дейликах и за боссов."
        )

    lines = []
    for transaction in history:
        amount = int(transaction["amount"])
        sign = "+" if amount > 0 else ""
        lines.append(
            f"{sign}{amount} — {transaction['reason']} "
            f"→ {transaction['balance_after']}"
        )
    return "\n".join(lines)


def _format_wallet(world: dict, player: dict, history: list[dict]) -> str:
    return (
        "💰 Кошелёк\n\n"
        f"🪙 {world['currency_name']}: {player['coins']}\n\n"
        "Последние операции:\n"
        f"{_format_wallet_history(history)}"
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


def _parse_personal_callback(
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


async def _send_private(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
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

    try:
        return await message.bot.send_message(**kwargs)
    except TelegramBadRequest:
        return None


async def _send_private_from_launcher(
    callback: CallbackQuery,
    world: dict,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    """
    Отправляет отдельное ephemeral-меню конкретному игроку.

    Не используем replace_callback_query_message=True:
    публичный закреп остаётся на месте, а личное меню приходит
    отдельным приватным сообщением. Это надёжнее в forum topics
    и на клиентах Telegram, где overlay ещё работает нестабильно.
    """
    return await callback.bot.send_message(
        chat_id=world["chat_id"],
        message_thread_id=world["thread_id"] or None,
        text=text,
        reply_markup=reply_markup,
        ephemeral_message_parameters=EphemeralMessageParameters(
            receiver_user_id=callback.from_user.id,
            callback_query_id=callback.id,
        ),
    )


async def _edit_private(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
    if callback.message is None:
        return

    if callback.message.ephemeral_message_id is not None:
        await callback.message.edit_ephemeral_text(
            text=text,
            reply_markup=reply_markup,
        )
        return

    # Резерв для старого публичного меню, если оно осталось после обновления.
    await callback.message.edit_text(
        text=text,
        reply_markup=reply_markup,
    )


async def _load_personal_context(
    callback: CallbackQuery,
) -> tuple[dict, dict] | None:
    parsed = _parse_personal_callback(callback)
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
        _username_from_user(callback.from_user),
    )

    player = get_mini_player(world_id, owner_id)
    return world, player


@router.message(Command("minipanel"))
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
            "❌ Эта тема не помечена как D&D Mini в app/topics.py.",
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
            "✅ Панель D&D Mini готова и закреплена."
            if pinned
            else (
                "✅ Панель D&D Mini готова.\n"
                "⚠️ Закрепить автоматически не получилось — "
                "закрепи сообщение вручную."
            )
        ),
    )

    try:
        await message.delete()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("mini:launch:"))
async def launch_callback(callback: CallbackQuery):
    try:
        world_id = int(callback.data.rsplit(":", 1)[1])
    except (AttributeError, ValueError):
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer(
            "D&D Mini сейчас недоступен.",
            show_alert=True,
        )
        return

    if callback.message is None or callback.message.chat.id != world["chat_id"]:
        await callback.answer(
            "Эта панель больше не актуальна.",
            show_alert=True,
        )
        return

    player = get_mini_player(world_id, callback.from_user.id)

    if player is None:
        text = (
            f"🎲 {world['name']}\n\n"
            "У тебя пока нет Mini-персонажа.\n"
            "Создание займёт несколько секунд."
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
        await _send_private_from_launcher(
            callback,
            world,
            text,
            markup,
        )
    except TelegramAPIError as error:
        print(
            "Ошибка открытия D&D Mini: "
            f"user={callback.from_user.id} "
            f"world={world_id} "
            f"{type(error).__name__}: {error}"
        )
        await callback.answer(
            "Не удалось открыть личное меню. "
            "Ошибка записана в лог бота.",
            show_alert=True,
        )
        return

    await callback.answer("D&D Mini открыто")


@router.message(Command("mini"))
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
            f"🎲 {world['name']}\n\nУ тебя пока нет Mini-персонажа.",
            _create_menu(world["id"], message.from_user.id),
        )
        return

    touch_mini_player(
        world["id"],
        message.from_user.id,
        _username_from_user(message.from_user),
    )
    player = get_mini_player(world["id"], message.from_user.id)

    await _send_private(
        message,
        _format_home(world, player),
        _player_menu(world["id"], message.from_user.id),
    )


@router.message(Command("minicreate"))
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
            "❌ У тебя уже есть Mini-персонаж:\n"
            f"{existing['character_name']}",
            _player_menu(world["id"], message.from_user.id),
        )
        return

    character_name = _parse_character_name(message)
    if character_name is None:
        await _send_private(
            message,
            "Использование:\n"
            '/minicreate "Имя персонажа"\n\n'
            "Команда зарегистрирована как личная: другие участники её не видят.",
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
        await _send_private(message, f"❌ {error}")
        return

    await _send_private(
        message,
        "✅ Mini-персонаж создан!\n\n" + _format_home(world, player),
        _player_menu(world["id"], message.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:create_help:"))
async def create_help_callback(callback: CallbackQuery):
    parsed = _parse_personal_callback(callback)
    if parsed is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    _, world_id, owner_id = parsed
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return

    world = get_mini_world_by_id(world_id)
    if not world or not world["enabled"]:
        await callback.answer("D&D Mini недоступен.", show_alert=True)
        return

    await callback.answer()
    await _edit_private(
        callback,
        "👤 Создание Mini-персонажа\n\n"
        "Напиши личную команду:\n"
        '/minicreate "Дед Максим"\n\n'
        "Класс и раса позже будут определяться активным героем из коллекции.",
        _back_menu(world_id, owner_id),
    )


@router.callback_query(F.data.startswith("mini:home:"))
async def home_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    await callback.answer()
    await _edit_private(
        callback,
        _format_home(world, player),
        _player_menu(world["id"], callback.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:wallet:"))
async def wallet_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    history = get_wallet_history(player["id"], limit=10)

    await callback.answer()
    await _edit_private(
        callback,
        _format_wallet(world, player, history),
        _back_menu(world["id"], callback.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:character:"))
async def character_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    username = player["username"] or "без @username"

    await callback.answer()
    await _edit_private(
        callback,
        "👤 Mini-персонаж\n\n"
        f"Имя: {player['character_name']}\n"
        f"Игрок: {username}\n"
        "🎴 Активный герой: пока не выбран\n"
        f"🪙 {world['currency_name']}: {player['coins']}\n\n"
        "Уровень в D&D Mini фиксированный.\n"
        "Раса и класс будут определяться активным героем из коллекции.",
        _back_menu(world["id"], callback.from_user.id),
    )


async def _not_ready(callback: CallbackQuery, text: str):
    parsed = _parse_personal_callback(callback)
    if parsed is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    _, _, owner_id = parsed
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню другого игрока.", show_alert=True)
        return

    await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("mini:daily:"))
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
            f"Не удалось выполнить дейлик: {error}",
            show_alert=True,
        )
        return

    if result["claimed"]:
        await callback.answer(
            f"+{result['coins_earned']} "
            f"{world['currency_name']}"
        )

        prefix = "⚔️ Ежедневное приключение завершено!"
    else:
        await callback.answer(
            "Сегодняшняя награда уже получена."
        )

        prefix = (
            "⚔️ Сегодня ты уже ходил в приключение.\n"
            "Вот чем оно закончилось:"
        )

    rare_line = ""
    if result["rarity"] == "rare":
        rare_line = "\n\n✨ Редкое событие!"
    elif result["rarity"] == "legendary":
        rare_line = "\n\n🌟 Очень редкое событие!"

    reward_line = (
        f"\n\n🪙 +{result['coins_earned']} "
        f"{world['currency_name']}"
    )

    if not result["claimed"]:
        reward_line = (
            f"\n\n🪙 Награда уже получена: "
            f"+{result['coins_earned']}"
        )

    text = (
        f"{prefix}\n\n"
        f"{result['story_text']}"
        f"{rare_line}"
        f"{reward_line}\n"
        f"💰 Баланс: {result['balance']}\n\n"
        "Следующий дейлик будет доступен завтра."
    )

    await _edit_private(
        callback,
        text,
        _daily_result_menu(
            world["id"],
            callback.from_user.id,
        ),
    )


@router.callback_query(F.data.startswith("mini:boss:"))
async def boss_callback(callback: CallbackQuery):
    await _not_ready(callback, "👹 Боссы будут добавлены позже.")


@router.callback_query(F.data.startswith("mini:shop:"))
async def shop_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    categories = get_shop_categories(world["id"])

    await callback.answer()
    await _edit_private(
        callback,
        "🛒 Магазин\n\n"
        f"🪙 Баланс: {player['coins']} {world['currency_name']}\n\n"
        "Выбери раздел. Каталог лежит в app/mini/content/shop.json.",
        _shop_main_menu(world["id"], callback.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:shopcat:"))
async def shop_category_callback(callback: CallbackQuery):
    context = await _shop_context(callback, "shopcat")
    if context is None:
        return

    world, player, category_code = context
    categories = {
        category["code"]: category
        for category in get_shop_categories(world["id"])
    }
    category = categories.get(category_code)
    if category is None:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    offers = get_offers_by_category(world["id"], category_code)
    await callback.answer()

    if not offers:
        text = (
            f"{category['title']}\n\n"
            f"{category.get('description', '')}\n\n"
            "Здесь пока нет активных товаров."
        )
    else:
        text = (
            f"{category['title']}\n\n"
            f"{category.get('description', '')}\n\n"
            f"🪙 Баланс: {player['coins']}\n"
            "Выбери товар:"
        )

    await _edit_private(
        callback,
        text,
        _shop_category_menu(
            world["id"], callback.from_user.id, category_code
        ),
    )


@router.callback_query(F.data.startswith("mini:shopitem:"))
async def shop_item_callback(callback: CallbackQuery):
    context = await _shop_context(callback, "shopitem")
    if context is None:
        return

    world, player, offer_id_text = context
    try:
        offer_id = int(offer_id_text)
    except ValueError:
        await callback.answer("Некорректный товар.", show_alert=True)
        return

    offer = get_offer(world["id"], offer_id)
    if offer is None or not offer["active"]:
        await callback.answer("Товар больше не доступен.", show_alert=True)
        return

    bought = get_offer_purchase_count(player["id"], offer_id)
    limits = []
    if offer["max_per_player"] is not None:
        limits.append(f"Лимит на игрока: {bought}/{offer['max_per_player']}")
    if offer["stock"] is not None:
        limits.append(f"Общий запас: {offer['stock']}")

    await callback.answer()
    await _edit_private(
        callback,
        f"{offer['title']}\n\n"
        f"{offer['description']}\n\n"
        f"Цена: {offer['price']} 🪙\n"
        f"Твой баланс: {player['coins']} 🪙"
        + (("\n" + "\n".join(limits)) if limits else ""),
        _shop_item_menu(
            world["id"],
            callback.from_user.id,
            offer_id,
            int(offer["price"]),
        ),
    )


@router.callback_query(F.data.startswith("mini:shopbuy:"))
async def shop_buy_callback(callback: CallbackQuery):
    context = await _shop_context(callback, "shopbuy")
    if context is None:
        return

    world, player, offer_id_text = context
    try:
        offer_id = int(offer_id_text)
    except ValueError:
        await callback.answer("Некорректный товар.", show_alert=True)
        return

    try:
        result = purchase_offer(
            player["id"],
            world["id"],
            offer_id,
        )
    except ShopInsufficientFunds as error:
        await callback.answer(str(error), show_alert=True)
        return
    except (ShopLimitReached, ShopOutOfStock, ShopError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    delivery_text = (
        "Предмет добавлен в твои товары."
        if result["delivery"] == "inventory"
        else "Сертификат сохранён в разделе «Мои товары»."
    )

    await callback.answer("Покупка успешна")
    await _edit_private(
        callback,
        "✅ Покупка успешна!\n\n"
        f"{result['title']}\n"
        f"Списано: {result['price']} 🪙\n"
        f"Осталось: {result['balance']} 🪙\n\n"
        f"{delivery_text}",
        _shop_after_purchase_menu(world["id"], callback.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:goods:"))
async def goods_callback(callback: CallbackQuery):
    context = await _shop_context(callback, "goods")
    if context is None:
        return

    world, player, _ = context
    goods = get_player_goods(player["id"])

    await callback.answer()
    await _edit_private(
        callback,
        _format_goods(goods),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛒 Магазин",
                        callback_data=_personal_callback(
                            "shop", world["id"], callback.from_user.id
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=_personal_callback(
                            "home", world["id"], callback.from_user.id
                        ),
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("mini:collection:"))
async def collection_callback(callback: CallbackQuery):
    await _not_ready(callback, "🎴 Коллекция появится вместе с гачей.")


@router.callback_query(F.data.startswith("mini:rating:"))
async def rating_callback(callback: CallbackQuery):
    await _not_ready(callback, "🏆 Рейтинг будет добавлен позже.")
