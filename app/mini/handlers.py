from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    EphemeralMessageParameters,
    FSInputFile,
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
from app.mini.gacha import (
    GachaError,
    GachaInsufficientFunds,
    GachaNoHeroes,
    GachaNoTicket,
    get_gacha_state,
    perform_gacha_pull,
)
from app.mini.heroes import (
    get_active_hero,
    get_collection_summary,
    get_hero_image,
    get_player_hero,
    set_active_hero,
)
from app.mini.hero_upgrades import (
    HeroShardSellError,
    HeroUpgradeError,
    HeroUpgradeInsufficientShards,
    HeroUpgradeMaxStars,
    hero_upgrade_state,
    sell_hero_shards,
    upgrade_hero,
)
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



RARITY_EMOJI = {
    "common": "⚪",
    "uncommon": "🟢",
    "rare": "🟣",
    "legendary": "🟡",
}

RARITY_TITLE = {
    "common": "Обычный",
    "uncommon": "Необычный",
    "rare": "Редкий",
    "legendary": "Легендарный",
}

COLLECTION_PAGE_SIZE = 6


def _clip(value: str, limit: int) -> str:
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _collection_menu(
    world_id: int,
    user_id: int,
    summary: dict,
    page: int = 0,
) -> InlineKeyboardMarkup:
    heroes = summary["heroes"]
    max_page = max(0, (len(heroes) - 1) // COLLECTION_PAGE_SIZE)
    page = max(0, min(int(page), max_page))
    start = page * COLLECTION_PAGE_SIZE
    visible = heroes[start : start + COLLECTION_PAGE_SIZE]

    rows = [[
        InlineKeyboardButton(
            text="✨ Призвать героя",
            callback_data=_personal_callback("gacha", world_id, user_id),
        )
    ]]

    for hero in visible:
        rarity = RARITY_EMOJI.get(hero.get("rarity"), "⚪")
        active = " ✅" if int(hero.get("is_active", 0)) else ""
        copies = int(hero.get("copies", 1))
        copies_text = f" ×{copies}" if copies > 1 else ""
        stars = int(hero.get("stars", 0))
        stars_text = f" ⭐{stars}" if stars > 0 else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{rarity} {_clip(hero['name'], 29)}{stars_text}{copies_text}{active}",
                callback_data=(
                    f"mini:hero:{world_id}:{user_id}:{hero['id']}"
                ),
            )
        ])

    if max_page > 0:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="◀️",
                callback_data=f"mini:collectionpage:{world_id}:{user_id}:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{max_page + 1}",
            callback_data=f"mini:collectionpage:{world_id}:{user_id}:{page}",
        ))
        if page < max_page:
            nav.append(InlineKeyboardButton(
                text="▶️",
                callback_data=f"mini:collectionpage:{world_id}:{user_id}:{page + 1}",
            ))
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=_personal_callback("home", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_collection(world: dict, player: dict, summary: dict) -> str:
    active = summary.get("active_hero")
    active_text = active["name"] if active else "пока не выбран"
    return (
        "🎴 Коллекция героев\n\n"
        f"Героев в коллекции: {summary['owned']}\n"
        f"Доступно в текущей гаче: {summary['total_active']}\n"
        f"⭐ Активный герой: {active_text}\n"
        f"🪙 Баланс: {player['coins']} {world['currency_name']}\n\n"
        + (
            "Выбери героя, чтобы открыть его карточку."
            if summary["heroes"]
            else "У тебя пока нет героев. Сделай первый призыв."
        )
    )


def _gacha_menu(
    world_id: int,
    user_id: int,
    state: dict,
) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(
            text=f"🪙 Призвать за {state['pull_price']}",
            callback_data=f"mini:gachapull:{world_id}:{user_id}:coins",
        )
    ]]

    if int(state["tickets"]) > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"🎟 Использовать билет · {state['tickets']}",
                callback_data=f"mini:gachapull:{world_id}:{user_id}:ticket",
            )
        ])

    rows.extend([
        [InlineKeyboardButton(
            text="🎴 Коллекция",
            callback_data=_personal_callback("collection", world_id, user_id),
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=_personal_callback("home", world_id, user_id),
        )],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_gacha(world: dict, state: dict) -> str:
    chances = state["rarity_chances"]
    return (
        "✨ Призыв героев\n\n"
        f"🪙 Цена: {state['pull_price']}\n"
        f"🎟 Билеты: {state['tickets']}\n"
        f"💰 Баланс: {state['coins']} {world['currency_name']}\n\n"
        "Текущие шансы редкостей:\n"
        f"⚪ Обычный — {chances.get('common', 0):g}%\n"
        f"🟢 Необычный — {chances.get('uncommon', 0):g}%\n"
        f"🟣 Редкий — {chances.get('rare', 0):g}%\n"
        f"🟡 Легендарный — {chances.get('legendary', 0):g}%\n\n"
        f"Героев в коллекции: {state['owned']}\n"
        f"Доступно сейчас: {state['total']}"
    )


def _hero_caption(hero: dict, *, pull_result: dict | None = None) -> str:
    rarity = RARITY_EMOJI.get(hero.get("rarity"), "⚪")
    rarity_title = RARITY_TITLE.get(hero.get("rarity"), hero.get("rarity", ""))
    description = _clip(hero.get("description", ""), 250)
    passive = _clip(hero.get("passive_text", ""), 250)
    upgrade = hero.get("upgrade") or hero_upgrade_state(hero)

    maximum = upgrade["max_stars"]
    max_text = "∞" if maximum is None else str(maximum)
    current_attack = int(upgrade["current_attack"])
    base_attack = int(upgrade["base_attack"])

    lines = [
        f"{rarity} {hero['name']}",
        f"{rarity_title} • {hero.get('race', '')} • {hero.get('class_name', '')}",
        f"⭐ Звёзды: {upgrade['stars']}/{max_text}",
        (
            f"⚔️ Атака: {current_attack} (база {base_attack})"
            if int(upgrade["stars"]) > 0
            else f"⚔️ Атака: {current_attack}"
        ),
        f"🧩 Осколки: {upgrade['shards']}",
        "",
        description or "Без описания.",
    ]

    if passive:
        lines.extend(["", "Пассивка:", passive])

    lines.append("")
    if upgrade["at_max"]:
        lines.append("🏆 Максимум звёзд достигнут.")
    else:
        lines.append(
            f"⬆️ Следующая: ⭐{upgrade['next_star']} • "
            f"атака {current_attack} → {upgrade['next_attack']} • "
            f"{upgrade['upgrade_cost']} 🧩"
        )

    if pull_result is not None:
        lines.append("")
        if pull_result["is_duplicate"]:
            lines.append(
                f"♻️ Дубликат: +{pull_result['shards_awarded']} осколков"
            )
            lines.append(
                f"Копий: {pull_result['copies']} • Осколки: {pull_result['shards']}"
            )
        else:
            lines.append("🎉 Новый герой добавлен в коллекцию!")

        if pull_result["used_ticket"]:
            lines.append("🎟 Потрачено: 1 билет призыва")
        else:
            lines.append(f"🪙 Потрачено: {pull_result['cost_coins']}")

        lines.append(f"💰 Баланс: {pull_result['balance']}")
        if pull_result["auto_activated"]:
            lines.append("⭐ Первый герой автоматически выбран активным.")

    return "\n".join(lines)[:1020]


def _hero_share_caption(hero: dict) -> str:
    description = _clip(hero.get("description", ""), 800)
    return (
        f"🎉 Смотри, что мне выпало: {hero['name']}!\n\n"
        f"{description or 'Без описания.'}"
    )[:1020]


def _hero_card_menu(
    world_id: int,
    user_id: int,
    hero: dict,
    state: dict,
    *,
    allow_share: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    upgrade = hero.get("upgrade") or hero_upgrade_state(hero)

    if not upgrade["at_max"]:
        rows.append([
            InlineKeyboardButton(
                text=(
                    f"⬆️ До ⭐{upgrade['next_star']} · "
                    f"{upgrade['upgrade_cost']} 🧩"
                ),
                callback_data=(
                    f"mini:heroupgrade:{world_id}:{user_id}:{hero['id']}"
                ),
            )
        ])

    if int(upgrade["shards"]) > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"💱 Продать осколки · {upgrade['shards']}",
                callback_data=(
                    f"mini:heroshards:{world_id}:{user_id}:{hero['id']}"
                ),
            )
        ])

    if not int(hero.get("is_active", 0)):
        rows.append([
            InlineKeyboardButton(
                text="✅ Сделать активным",
                callback_data=f"mini:heroactive:{world_id}:{user_id}:{hero['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=f"🪙 Ещё призыв · {state['pull_price']}",
            callback_data=f"mini:gacharepeat:{world_id}:{user_id}:coins",
        )
    ])
    if int(state.get("tickets", 0)) > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"🎟 Билет · {state['tickets']}",
                callback_data=f"mini:gacharepeat:{world_id}:{user_id}:ticket",
            )
        ])

    if allow_share:
        rows.append([
            InlineKeyboardButton(
                text="📣 Похвастаться",
                callback_data=(
                    f"mini:heroshare:{world_id}:{user_id}:{hero['id']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="🎴 Открыть коллекцию",
            callback_data=f"mini:collectionopen:{world_id}:{user_id}:0",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shard_sell_menu(
    world_id: int,
    user_id: int,
    hero: dict,
) -> InlineKeyboardMarkup:
    shards = int(hero.get("shards", 0))
    rows = []

    if shards >= 1:
        rows.append([
            InlineKeyboardButton(
                text="Продать 1 → +1 🪙",
                callback_data=f"mini:shardsell:{world_id}:{user_id}:{hero['id']},1",
            )
        ])
    if shards >= 10:
        rows.append([
            InlineKeyboardButton(
                text="Продать 10 → +10 🪙",
                callback_data=f"mini:shardsell:{world_id}:{user_id}:{hero['id']},10",
            )
        ])
    if shards > 0:
        rows.append([
            InlineKeyboardButton(
                text=f"Продать все {shards} → +{shards} 🪙",
                callback_data=f"mini:shardsell:{world_id}:{user_id}:{hero['id']},all",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ К герою",
            callback_data=f"mini:hero:{world_id}:{user_id}:{hero['id']}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_shard_sell(hero: dict, balance: int) -> str:
    shards = int(hero.get("shards", 0))
    return (
        "💱 Продажа осколков\n\n"
        f"{hero['name']}\n"
        f"🧩 Осколки: {shards}\n"
        f"💰 Баланс: {balance}\n\n"
        "Курс: 1 осколок = 1 монета.\n"
        "Проданные осколки исчезают навсегда."
    )

def _parse_extended_callback(
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


async def _load_extended_context(
    callback: CallbackQuery,
    prefix: str,
) -> tuple[dict, dict, str] | None:
    parsed = _parse_extended_callback(callback, prefix)
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

    player = get_mini_player(world_id, owner_id)
    if player is None:
        await callback.answer("Сначала создай Mini-персонажа.", show_alert=True)
        return None

    touch_mini_player(world_id, owner_id, _username_from_user(callback.from_user))
    player = get_mini_player(world_id, owner_id)
    return world, player, extra


async def _send_private_text_from_callback(
    callback: CallbackQuery,
    world: dict,
    text: str,
    reply_markup: InlineKeyboardMarkup,
):
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


async def _send_hero_card(
    callback: CallbackQuery,
    world: dict,
    hero: dict,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
):
    image = get_hero_image(hero)
    if image is not None:
        try:
            return await callback.bot.send_photo(
                chat_id=world["chat_id"],
                message_thread_id=world["thread_id"] or None,
                photo=FSInputFile(image),
                caption=caption,
                reply_markup=reply_markup,
                ephemeral_message_parameters=EphemeralMessageParameters(
                    receiver_user_id=callback.from_user.id,
                    callback_query_id=callback.id,
                ),
            )
        except TelegramAPIError as error:
            print(
                "Не удалось отправить картинку героя: "
                f"hero={hero.get('code')} error={type(error).__name__}: {error}"
            )

    return await _send_private_text_from_callback(
        callback,
        world,
        caption,
        reply_markup,
    )


async def _send_public_hero_share(
    callback: CallbackQuery,
    world: dict,
    hero: dict,
):
    caption = _hero_share_caption(hero)
    image = get_hero_image(hero)

    if image is not None:
        try:
            return await callback.bot.send_photo(
                chat_id=world["chat_id"],
                message_thread_id=world["thread_id"] or None,
                photo=FSInputFile(image),
                caption=caption,
            )
        except TelegramAPIError as error:
            print(
                "Не удалось публично отправить картинку героя: "
                f"hero={hero.get('code')} "
                f"error={type(error).__name__}: {error}"
            )

    return await callback.bot.send_message(
        chat_id=world["chat_id"],
        message_thread_id=world["thread_id"] or None,
        text=caption,
    )


async def _delete_current_ephemeral(callback: CallbackQuery) -> bool:
    message = callback.message
    if message is None or message.ephemeral_message_id is None:
        return False

    try:
        await message.delete_ephemeral()
    except TelegramAPIError as error:
        print(
            "Не удалось удалить прошлую карточку гачи: "
            f"error={type(error).__name__}: {error}"
        )
        return False

    return True


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
    active = get_active_hero(player["id"])
    if active is None:
        active_text = "пока не выбран"
    else:
        active_text = (
            f"{active['name']} — "
            f"{active.get('race', '')} / {active.get('class_name', '')} • "
            f"⭐{active.get('stars', 0)} • ⚔️{active.get('attack', 1)}"
        )

    return (
        f"🎲 {world['name']}\n\n"
        f"👤 Персонаж: {player['character_name']}\n"
        f"🎴 Активный герой: {active_text}\n"
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
    active = get_active_hero(player["id"])

    if active is None:
        hero_text = (
            "🎴 Активный герой: пока не выбран\n"
            "Раса: —\n"
            "Класс: —\n"
            "⚔️ Атака: —"
        )
    else:
        hero_text = (
            f"🎴 Активный герой: {active['name']}\n"
            f"Раса: {active.get('race', '')}\n"
            f"Класс: {active.get('class_name', '')}\n"
            f"⚔️ Атака: {active.get('attack', 1)}"
        )
        if active.get("passive_text"):
            hero_text += f"\nПассивка: {active['passive_text']}"

    await callback.answer()
    await _edit_private(
        callback,
        "👤 Mini-персонаж\n\n"
        f"Имя: {player['character_name']}\n"
        f"Игрок: {username}\n"
        f"{hero_text}\n"
        f"🪙 {world['currency_name']}: {player['coins']}\n\n"
        "Уровень в D&D Mini фиксированный. "
        "Раса и класс определяются активным героем.",
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
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    summary = get_collection_summary(player["id"])

    await callback.answer()
    await _edit_private(
        callback,
        _format_collection(world, player, summary),
        _collection_menu(
            world["id"], callback.from_user.id, summary, page=0
        ),
    )


@router.callback_query(F.data.startswith("mini:collectionpage:"))
async def collection_page_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "collectionpage")
    if context is None:
        return

    world, player, page_text = context
    try:
        page = int(page_text)
    except ValueError:
        page = 0

    summary = get_collection_summary(player["id"])
    await callback.answer()
    await _edit_private(
        callback,
        _format_collection(world, player, summary),
        _collection_menu(
            world["id"], callback.from_user.id, summary, page=page
        ),
    )


@router.callback_query(F.data.startswith("mini:collectionopen:"))
async def collection_open_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "collectionopen")
    if context is None:
        return

    world, player, page_text = context
    try:
        page = int(page_text)
    except ValueError:
        page = 0

    summary = get_collection_summary(player["id"])
    try:
        await _send_private_text_from_callback(
            callback,
            world,
            _format_collection(world, player, summary),
            _collection_menu(
                world["id"], callback.from_user.id, summary, page=page
            ),
        )
    except TelegramAPIError as error:
        print(f"Ошибка открытия коллекции: {type(error).__name__}: {error}")
        await callback.answer("Не удалось открыть коллекцию.", show_alert=True)
        return
    await callback.answer("Коллекция открыта")


@router.callback_query(F.data.startswith("mini:gacha:"))
async def gacha_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    try:
        state = get_gacha_state(player["id"])
    except GachaError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await callback.answer()
    await _edit_private(
        callback,
        _format_gacha(world, state),
        _gacha_menu(world["id"], callback.from_user.id, state),
    )


async def _handle_gacha_pull(
    callback: CallbackQuery,
    *,
    prefix: str,
    delete_source_after_success: bool,
):
    context = await _load_extended_context(callback, prefix)
    if context is None:
        return

    world, player, payment = context
    if payment not in {"coins", "ticket"}:
        await callback.answer("Неизвестный способ призыва.", show_alert=True)
        return

    try:
        result = perform_gacha_pull(player["id"], payment=payment)
    except (GachaInsufficientFunds, GachaNoTicket, GachaNoHeroes, GachaError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    state = get_gacha_state(player["id"])
    hero = get_player_hero(player["id"], result["id"])
    if hero is None:
        hero = result
        hero["is_active"] = 1 if result["auto_activated"] else 0

    markup = _hero_card_menu(
        world["id"],
        callback.from_user.id,
        hero,
        state,
        allow_share=True,
    )

    try:
        await _send_hero_card(
            callback,
            world,
            hero,
            _hero_caption(hero, pull_result=result),
            markup,
        )
    except TelegramAPIError as error:
        print(f"Ошибка показа результата гачи: {type(error).__name__}: {error}")
        await callback.answer(
            "Герой получен, но карточку показать не удалось. Открой коллекцию.",
            show_alert=True,
        )
        return

    if delete_source_after_success:
        await _delete_current_ephemeral(callback)

    toast = (
        f"Новый герой: {hero['name']}"
        if not result["is_duplicate"]
        else f"Дубликат: +{result['shards_awarded']} осколков"
    )
    await callback.answer(toast)


@router.callback_query(F.data.startswith("mini:gachapull:"))
async def gacha_pull_callback(callback: CallbackQuery):
    await _handle_gacha_pull(
        callback,
        prefix="gachapull",
        delete_source_after_success=False,
    )


@router.callback_query(F.data.startswith("mini:gacharepeat:"))
async def gacha_repeat_callback(callback: CallbackQuery):
    await _handle_gacha_pull(
        callback,
        prefix="gacharepeat",
        delete_source_after_success=True,
    )


@router.callback_query(F.data.startswith("mini:heroshare:"))
async def hero_share_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroshare")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text)
    except ValueError:
        await callback.answer("Некорректный герой.", show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    if hero is None:
        await callback.answer("Этого героя нет в коллекции.", show_alert=True)
        return

    try:
        await _send_public_hero_share(callback, world, hero)
    except TelegramAPIError as error:
        print(
            "Ошибка публичной публикации героя: "
            f"{type(error).__name__}: {error}"
        )
        await callback.answer(
            "Не удалось похвастаться героем.",
            show_alert=True,
        )
        return

    await callback.answer("Показал всем 🎉")


@router.callback_query(F.data.startswith("mini:hero:"))
async def hero_card_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "hero")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text)
    except ValueError:
        await callback.answer("Некорректный герой.", show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    if hero is None:
        await callback.answer("Этого героя нет в коллекции.", show_alert=True)
        return

    state = get_gacha_state(player["id"])
    try:
        await _send_hero_card(
            callback,
            world,
            hero,
            _hero_caption(hero),
            _hero_card_menu(
                world["id"], callback.from_user.id, hero, state
            ),
        )
    except TelegramAPIError as error:
        print(f"Ошибка карточки героя: {type(error).__name__}: {error}")
        await callback.answer("Не удалось открыть карточку героя.", show_alert=True)
        return

    await callback.answer(hero["name"])


@router.callback_query(F.data.startswith("mini:heroupgrade:"))
async def hero_upgrade_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroupgrade")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text)
    except ValueError:
        await callback.answer("Некорректный герой.", show_alert=True)
        return

    try:
        result = upgrade_hero(player["id"], hero_id)
    except (HeroUpgradeInsufficientShards, HeroUpgradeMaxStars, HeroUpgradeError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    state = get_gacha_state(player["id"])

    if hero is not None:
        try:
            await _send_hero_card(
                callback,
                world,
                hero,
                _hero_caption(hero),
                _hero_card_menu(
                    world["id"], callback.from_user.id, hero, state
                ),
            )
        except TelegramAPIError as error:
            print(f"Ошибка карточки после улучшения: {type(error).__name__}: {error}")
            await callback.answer(
                f"⭐ {result['stars']}: атака {result['old_attack']} → {result['attack']}",
                show_alert=True,
            )
            return

    await callback.answer(
        f"⭐ {result['stars']}: атака {result['old_attack']} → {result['attack']}",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("mini:heroshards:"))
async def hero_shards_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroshards")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text)
    except ValueError:
        await callback.answer("Некорректный герой.", show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    if hero is None:
        await callback.answer("Этого героя нет в коллекции.", show_alert=True)
        return

    await _send_private_text_from_callback(
        callback,
        world,
        _format_shard_sell(hero, int(player["coins"])),
        _shard_sell_menu(world["id"], callback.from_user.id, hero),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mini:shardsell:"))
async def shard_sell_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "shardsell")
    if context is None:
        return

    world, player, payload = context
    try:
        hero_id_text, quantity_text = payload.split(",", 1)
        hero_id = int(hero_id_text)
    except (ValueError, IndexError):
        await callback.answer("Некорректная продажа.", show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    if hero is None:
        await callback.answer("Этого героя нет в коллекции.", show_alert=True)
        return

    if quantity_text == "all":
        quantity = int(hero.get("shards", 0))
    else:
        try:
            quantity = int(quantity_text)
        except ValueError:
            await callback.answer("Некорректное количество.", show_alert=True)
            return

    try:
        result = sell_hero_shards(
            player["id"],
            hero_id,
            quantity,
            operation_key=callback.id,
        )
    except HeroShardSellError as error:
        await callback.answer(str(error), show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    player = get_mini_player(world["id"], callback.from_user.id)

    await callback.answer(
        f"+{result['coins_earned']} монет",
        show_alert=True,
    )

    if hero is not None and player is not None:
        await _edit_private(
            callback,
            _format_shard_sell(hero, int(player["coins"])),
            _shard_sell_menu(world["id"], callback.from_user.id, hero),
        )


@router.callback_query(F.data.startswith("mini:heroactive:"))
async def hero_active_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroactive")
    if context is None:
        return

    _, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text)
    except ValueError:
        await callback.answer("Некорректный герой.", show_alert=True)
        return

    try:
        hero = set_active_hero(player["id"], hero_id)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await callback.answer(
        f"⭐ Активный герой: {hero['name']}",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("mini:rating:"))
async def rating_callback(callback: CallbackQuery):
    await _not_ready(callback, "🏆 Рейтинг будет добавлен позже.")
