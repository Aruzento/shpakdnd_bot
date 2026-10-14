from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.ui.state import legacy_operation
from app.mini.ui.context import (
    personal_callback as _personal_callback,
    load_personal_context as _load_personal_context,
    shop_context as _shop_context,
)
from app.mini.ui.transport import edit_private as _edit_private
import secrets
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from app.mini.shop import (
    ShopError,
    ShopInsufficientFunds,
    ShopLimitReached,
    ShopOutOfStock,
    get_offer,
    get_offer_purchase_count,
    get_offers_by_category,
    get_shop_categories,
    purchase_offer,
)



router = Router(name="mini_shop")

@editorial_screen
def _shop_main_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for category in get_shop_categories(world_id):
        count = int(category.get("product_count", 0))
        suffix = f" · {count}" if count else editorial_text('shop.shop_main_menu.line_01')
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
            text=editorial_text('navigation.inventory'),
            callback_data=f"mini:goods:{world_id}:{user_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text=editorial_text('navigation.back'),
            callback_data=_personal_callback("fair", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
def _shop_category_menu(
    world_id: int,
    user_id: int,
    category: str,
) -> InlineKeyboardMarkup:
    rows = []
    for offer in get_offers_by_category(world_id, category):
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('shop.shop_category_menu.line_02', title=f"{offer['title']}", price=f"{offer['price']}"),
                callback_data=(
                    f"mini:shopitem:{world_id}:{user_id}:{offer['id']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=editorial_text('shop.shop_category_menu.line_01'),
            callback_data=_personal_callback("shop", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
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
                    text=editorial_text('shop.shop_item_menu.line_01', price=f'{price}'),
                    callback_data=(
                        f"mini:shopbuy:{world_id}:{user_id}:{offer_id}.{secrets.token_hex(4)}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=editorial_text('shop.shop_item_menu.line_02'),
                    callback_data=_personal_callback("shop", world_id, user_id),
                )
            ],
        ]
    )


@editorial_screen
def _shop_after_purchase_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('navigation.inventory'),
                    callback_data=f"mini:goods:{world_id}:{user_id}",
                ),
                InlineKeyboardButton(
                    text=editorial_text('navigation.shop'),
                    callback_data=_personal_callback("shop", world_id, user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=editorial_text('shop.shop_after_purchase_menu.line_03'),
                    callback_data=_personal_callback("home", world_id, user_id),
                )
            ],
        ]
    )


@router.callback_query(F.data.startswith("mini:shop:"))
@editorial_screen
async def shop_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    categories = get_shop_categories(world["id"])

    await callback.answer()
    await _edit_private(
        callback,
        editorial_text('shop.shop_callback.line_01', coins=f"{player['coins']}", currency_name=f"{world['currency_name']}"),
        _shop_main_menu(world["id"], callback.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:shopcat:"))
@editorial_screen
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
        await callback.answer(editorial_text('shop.shop_category_callback.line_03'), show_alert=True)
        return

    offers = get_offers_by_category(world["id"], category_code)
    await callback.answer()

    if not offers:
        text = (
            editorial_text('shop.shop_category_callback.line_01', title=f"{category['title']}", value_2=f"{category.get('description', '')}")
        )
    else:
        text = (
            editorial_text('shop.shop_category_callback.line_02', title=f"{category['title']}", value_2=f"{category.get('description', '')}", coins=f"{player['coins']}")
        )

    await _edit_private(
        callback,
        text,
        _shop_category_menu(
            world["id"], callback.from_user.id, category_code
        ),
    )


@router.callback_query(F.data.startswith("mini:shopitem:"))
@editorial_screen
async def shop_item_callback(callback: CallbackQuery):
    context = await _shop_context(callback, "shopitem")
    if context is None:
        return

    world, player, offer_id_text = context
    try:
        offer_id = int(offer_id_text)
    except ValueError:
        await callback.answer(editorial_text('shop.shop_item_callback.line_05'), show_alert=True)
        return

    offer = get_offer(world["id"], offer_id)
    if offer is None or not offer["active"]:
        await callback.answer(editorial_text('shop.shop_item_callback.line_03'), show_alert=True)
        return

    bought = get_offer_purchase_count(player["id"], offer_id)
    limits = []
    if offer["max_per_player"] is not None:
        limits.append(editorial_text('shop.shop_item_callback.line_01', bought=f'{bought}', max_per_player=f"{offer['max_per_player']}"))
    if offer["stock"] is not None:
        limits.append(editorial_text('shop.shop_item_callback.line_02', stock=f"{offer['stock']}"))

    await callback.answer()
    await _edit_private(
        callback,
        editorial_text('shop.shop_item_callback.line_04', title=f"{offer['title']}", description=f"{offer['description']}", price=f"{offer['price']}", coins=f"{player['coins']}")
        + (("\n" + "\n".join(limits)) if limits else ""),
        _shop_item_menu(
            world["id"],
            callback.from_user.id,
            offer_id,
            int(offer["price"]),
        ),
    )


@router.callback_query(F.data.startswith("mini:shopbuy:"))
@editorial_screen
async def shop_buy_callback(callback: CallbackQuery):
    context = await _shop_context(callback, "shopbuy")
    if context is None:
        return

    world, player, offer_id_text = context
    try:
        offer_id = int(offer_id_text.split(".")[0])
    except ValueError:
        await callback.answer(editorial_text('shop.shop_buy_callback.line_05'), show_alert=True)
        return

    try:
        result = purchase_offer(
            player["id"],
            world["id"],
            offer_id,
            operation_key="shop:"+offer_id_text if "." in offer_id_text else legacy_operation(callback,"legacy-shop:"+offer_id_text),
        )
    except ShopInsufficientFunds as error:
        await callback.answer(str(error), show_alert=True)
        return
    except (ShopLimitReached, ShopOutOfStock, ShopError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    delivery_text = (
        editorial_text('shop.shop_buy_callback.line_01')
        if result["delivery"] == "inventory"
        else editorial_text('shop.shop_buy_callback.line_02')
    )

    await callback.answer(editorial_text('shop.shop_buy_callback.line_03'))
    await _edit_private(
        callback,
        editorial_text('shop.shop_buy_callback.line_04', title=f"{result['title']}", price=f"{result['price']}", balance=f"{result['balance']}", delivery_text=f'{delivery_text}'),
        _shop_after_purchase_menu(world["id"], callback.from_user.id),
    )
