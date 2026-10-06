from app.mini.ui.context import personal_callback as _personal_callback, load_personal_context as _load_personal_context, shop_context as _shop_context

from app.mini.ui.transport import edit_private as _edit_private

from aiogram import F, Router



from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup










from app.mini.shop import ShopError, ShopInsufficientFunds, ShopLimitReached, ShopOutOfStock, get_offer, get_offer_purchase_count, get_offers_by_category, get_shop_categories, purchase_offer





router = Router(name="mini_shop")

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
            text="🎒 Инвентарь",
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
                    text="🎒 Инвентарь",
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
        "Предмет добавлен в инвентарь."
        if result["delivery"] == "inventory"
        else "Сертификат сохранён в инвентаре."
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

