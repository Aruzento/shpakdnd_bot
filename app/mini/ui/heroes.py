from app.mini.ui.context import (
    username_from_user as _username_from_user,
    personal_callback as _personal_callback,
    load_extended_context as _load_extended_context,
    load_personal_context as _load_personal_context,
)
from app.mini.ui.transport import (
    send_private_text_from_callback as _send_private_text_from_callback,
    edit_private as _edit_private,
)
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from app.mini.gacha import (
    GachaError,
    GachaInsufficientFunds,
    GachaNoHeroes,
    GachaNoTicket,
    get_gacha_state,
    perform_gacha_pull,
)
from app.mini.heroes import get_collection_summary, get_player_hero, set_active_hero
from app.mini.hero_upgrades import (
    HeroShardSellError,
    HeroUpgradeError,
    HeroUpgradeInsufficientShards,
    HeroUpgradeMaxStars,
    sell_hero_shards,
    upgrade_hero,
)
from app.mini.players import get_mini_player
from app.mini.ui.navigation import clip
from app.mini.ui.hero_cards import (
    hero_caption,
    hero_card_menu,
    send_hero_card,
    send_public_hero_share,
    show_gacha_pull_result,
)

router = Router(name="mini_heroes")

RARITY_EMOJI = {"common":"⚪","uncommon":"🟢","rare":"🟣","legendary":"🟡"}
COLLECTION_PAGE_SIZE = 6
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
                text=f"{rarity} {clip(hero['name'], 29)}{stars_text}{copies_text}{active}",
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
        f"🪙 Монеты: {player['coins']}\n"
        f"🧩 Осколки: {player['shards']}\n\n"
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
    luck_line = (
        "🍀 Зелье удачи активно: эта крутка будет усилена.\n\n"
        if state.get("luck_active")
        else ""
    )
    return (
        "✨ Призыв героев\n\n"
        f"🪙 Цена: {state['pull_price']}\n"
        f"🎟 Билеты: {state['tickets']}\n"
        f"🪙 Монеты: {state['coins']}\n"
        f"🧩 Осколки: {state['shards']}\n\n"
        f"{luck_line}"
        "Текущие шансы редкостей:\n"
        f"⚪ Обычный — {chances.get('common', 0):g}%\n"
        f"🟢 Необычный — {chances.get('uncommon', 0):g}%\n"
        f"🟣 Редкий — {chances.get('rare', 0):g}%\n"
        f"🟡 Легендарный — {chances.get('legendary', 0):g}%\n\n"
        f"Героев в коллекции: {state['owned']}\n"
        f"Доступно сейчас: {state['total']}"
    )


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

    await show_gacha_pull_result(callback, world, player, result)


@router.callback_query(F.data.startswith("mini:gachapull:"))
async def gacha_pull_callback(callback: CallbackQuery):
    await _handle_gacha_pull(
        callback,
        prefix="gachapull",
    )


@router.callback_query(F.data.startswith("mini:gacharepeat:"))
async def gacha_repeat_callback(callback: CallbackQuery):
    await _handle_gacha_pull(
        callback,
        prefix="gacharepeat",
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

    sharer = (
        _username_from_user(callback.from_user)
        or player.get("username")
        or player.get("character_name")
        or "Игрок"
    )

    try:
        await send_public_hero_share(callback, world, hero, sharer)
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

    # После публичной публикации оставляем игроку одну свежую личную
    # карточку без повторной кнопки «Похвастаться». Старая карточка удалится.
    try:
        state = get_gacha_state(player["id"])
        await send_hero_card(
            callback,
            world,
            hero,
            hero_caption(hero),
            hero_card_menu(
                world["id"], callback.from_user.id, hero, state,
                allow_share=False,
            ),
        )
    except TelegramAPIError as error:
        print(
            "Не удалось обновить личную карточку после публикации: "
            f"{type(error).__name__}: {error}"
        )

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
        await send_hero_card(
            callback,
            world,
            hero,
            hero_caption(hero),
            hero_card_menu(
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
            await send_hero_card(
                callback,
                world,
                hero,
                hero_caption(hero),
                hero_card_menu(
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

    world, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text)
    except ValueError:
        await callback.answer("Некорректный герой.", show_alert=True)
        return


    try:
        set_active_hero(player["id"], hero_id)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return


    hero = get_player_hero(player["id"], hero_id)
    state = get_gacha_state(player["id"])
    if hero is not None:
        try:
            await send_hero_card(
                callback,
                world,
                hero,
                hero_caption(hero),
                hero_card_menu(
                    world["id"], callback.from_user.id, hero, state
                ),
            )
        except TelegramAPIError as error:
            print(
                "Ошибка карточки после выбора активного героя: "
                f"{type(error).__name__}: {error}"
            )
            await callback.answer(
                f"⭐ Активный герой: {hero['name']}",
                show_alert=True,
            )
            return

    await callback.answer(
        f"⭐ Активный герой: {hero['name'] if hero else 'выбран'}"
    )

