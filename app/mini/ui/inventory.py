from app.mini.presentation import format_player_mention
from app.mini.ui.context import (
    username_from_user as _username_from_user,
    personal_callback as _personal_callback,
    load_extended_context as _load_extended_context,
    load_personal_context as _load_personal_context,
    shop_context as _shop_context,
)
from app.mini.ui.transport import edit_private as _edit_private
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from app.context import get_topic_admin
from app.mini.gacha import GachaError, GachaNoHeroes, GachaNoTicket, perform_gacha_pull
from app.mini.items import (
    EFFECT_GACHA_TICKET,
    ItemUseError,
    active_effect_title,
    effect_description,
    get_certificate_for_use,
    get_inventory_item,
    mark_certificate_requested,
    use_inventory_item,
)
from app.mini.shop import get_player_goods
from app.mini.ui.navigation import clip
from app.mini.ui.hero_cards import show_gacha_pull_result

router = Router(name="mini_inventory")

def _format_inventory(world: dict, player: dict, goods: dict) -> str:
    lines = [
        "🎒 Инвентарь",
        "",
        "Валюты:",
        f"🪙 Монеты: {player['coins']}",
        f"🧩 Осколки: {player['shards']}",
        "",
    ]

    inventory = goods["inventory"]
    certificates = goods["certificates"]
    effects = goods.get("effects", [])

    if inventory:
        lines.append("Предметы Mini:")
        for item in inventory:
            suffix = f" ×{item['quantity']}" if int(item["quantity"]) > 1 else ""
            lines.append(f"• {item['name']}{suffix}")
        lines.append("")

    if certificates:
        lines.append("Сертификаты:")
        for certificate in certificates:
            lines.append(
                f"• #{certificate['purchase_id']} {certificate['title']}"
            )
        lines.append("")

    if effects:
        lines.append("Активные эффекты:")
        for effect in effects:
            charges = int(effect.get("charges", 0))
            suffix = f" ×{charges}" if charges > 1 else ""
            lines.append(
                f"• {active_effect_title(effect.get('effect_key', ''))}{suffix}"
            )
        lines.append("")

    if not inventory and not certificates:
        lines.append("Предметов пока нет.")

    return "\n".join(lines).rstrip()


def _inventory_menu(
    world_id: int,
    user_id: int,
    goods: dict,
) -> InlineKeyboardMarkup:
    rows = []
    if goods.get("inventory") or goods.get("certificates"):
        rows.append([
            InlineKeyboardButton(
                text="✨ Использовать предмет",
                callback_data=_personal_callback("useitems", world_id, user_id),
            )
        ])
    rows.append([InlineKeyboardButton(text='🛡 Экипировка в инвентаре',
        callback_data=_personal_callback('equipment',world_id,user_id)+':inventory.0')])
    rows.append([
        InlineKeyboardButton(
            text="🛒 Магазин",
            callback_data=_personal_callback("shop", world_id, user_id),
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=_personal_callback("home", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _use_items_menu(
    world_id: int,
    user_id: int,
    goods: dict,
) -> InlineKeyboardMarkup:
    rows = []
    for item in goods.get("inventory", []):
        quantity = int(item.get("quantity", 0))
        if quantity <= 0:
            continue
        suffix = f" ×{quantity}" if quantity > 1 else ""
        rows.append([
            InlineKeyboardButton(
                text=f"🎒 {clip(item['name'], 28)}{suffix}",
                callback_data=(
                    f"mini:usepick:{world_id}:{user_id}:item,{item['item_id']}"
                ),
            )
        ])

    for certificate in goods.get("certificates", []):
        rows.append([
            InlineKeyboardButton(
                text=f"🎫 {clip(certificate['title'], 28)}",
                callback_data=(
                    f"mini:usepick:{world_id}:{user_id}:cert,{certificate['purchase_id']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ В инвентарь",
            callback_data=_personal_callback("inventory", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _use_item_confirm_menu(
    world_id: int,
    user_id: int,
    kind: str,
    value: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Использовать",
                    callback_data=(
                        f"mini:useconfirm:{world_id}:{user_id}:{kind},{value}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К выбору",
                    callback_data=_personal_callback("useitems", world_id, user_id),
                )
            ],
        ]
    )


def _item_use_result_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎒 В инвентарь",
                    callback_data=_personal_callback("inventory", world_id, user_id),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ На главную",
                    callback_data=_personal_callback("home", world_id, user_id),
                )
            ],
        ]
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


async def _open_inventory(callback: CallbackQuery, world: dict, player: dict):
    goods = get_player_goods(player["id"])
    await _edit_private(
        callback,
        _format_inventory(world, player, goods),
        _inventory_menu(world["id"], callback.from_user.id, goods),
    )


@router.callback_query(F.data.startswith("mini:inventory:"))
async def inventory_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    await callback.answer()
    await _open_inventory(callback, world, player)


@router.callback_query(F.data.startswith("mini:wallet:"))
async def wallet_callback(callback: CallbackQuery):
    """Совместимость со старыми кнопками «Кошелёк»."""
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    await callback.answer()
    await _open_inventory(callback, world, player)


@router.callback_query(F.data.startswith("mini:useitems:"))
async def use_items_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    goods = get_player_goods(player["id"])
    has_usable = bool(goods.get("inventory") or goods.get("certificates"))
    text = (
        "✨ Использовать предмет\n\nВыбери предмет или сертификат."
        if has_usable
        else "✨ Использовать предмет\n\nИспользуемых предметов сейчас нет."
    )
    await callback.answer()
    await _edit_private(
        callback,
        text,
        _use_items_menu(world["id"], callback.from_user.id, goods),
    )


@router.callback_query(F.data.startswith("mini:usepick:"))
async def use_item_pick_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "usepick")
    if context is None:
        return

    world, player, payload = context
    try:
        kind, value_text = payload.split(",", 1)
        value = int(value_text)
    except (ValueError, IndexError):
        await callback.answer("Некорректный предмет.", show_alert=True)
        return

    if kind == "item":
        item = get_inventory_item(player["id"], value)
        if item is None:
            await callback.answer("Предмета больше нет в инвентаре.", show_alert=True)
            return
        text = (
            f"🎒 {item['name']}\n\n"
            f"{item.get('description') or 'Без описания.'}\n\n"
            f"Эффект: {effect_description(item.get('effect_key', ''))}\n"
            f"Количество: {item['quantity']}"
        )
    elif kind == "cert":
        certificate = get_certificate_for_use(player["id"], value)
        if certificate is None:
            await callback.answer("Сертификат уже использован или недоступен.", show_alert=True)
            return
        admin = get_topic_admin(int(world["chat_id"]), int(world["thread_id"])) or "@arukozento"
        text = (
            f"🎫 {certificate['title']}\n\n"
            f"{certificate.get('description') or 'Сертификат Mini.'}\n\n"
            f"После использования бот позовёт {admin} и сообщит, "
            "что награду нужно применить вручную."
        )
    else:
        await callback.answer("Неизвестный тип предмета.", show_alert=True)
        return

    await callback.answer()
    await _edit_private(
        callback,
        text,
        _use_item_confirm_menu(
            world["id"], callback.from_user.id, kind, value
        ),
    )


@router.callback_query(F.data.startswith("mini:useconfirm:"))
async def use_item_confirm_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "useconfirm")
    if context is None:
        return

    world, player, payload = context
    try:
        kind, value_text = payload.split(",", 1)
        value = int(value_text)
    except (ValueError, IndexError):
        await callback.answer("Некорректный предмет.", show_alert=True)
        return

    if kind == "cert":
        certificate = get_certificate_for_use(player["id"], value)
        if certificate is None:
            await callback.answer(
                "Сертификат уже использован или отправлен мастеру.",
                show_alert=True,
            )
            return

        admin = get_topic_admin(int(world["chat_id"]), int(world["thread_id"])) or "@arukozento"
        who = format_player_mention(dict(player,username=_username_from_user(callback.from_user) or player.get("username")))
        try:
            await callback.bot.send_message(
                chat_id=world["chat_id"],
                message_thread_id=world["thread_id"] or None,
                text=(
                    f"🔔 {format_player_mention(dict(world_id=world['id'],username=admin))}\n\n"
                    f"{who} использовал «{certificate['title']}» "
                    f"(сертификат #{certificate['purchase_id']}).\n"
                    "Нужно применить награду вручную."
                ),
            )
        except TelegramAPIError as error:
            print(
                "Не удалось отправить запрос на сертификат: "
                f"{type(error).__name__}: {error}"
            )
            await callback.answer(
                "Не удалось уведомить администратора. Сертификат не списан.",
                show_alert=True,
            )
            return

        try:
            mark_certificate_requested(player["id"], value)
        except ItemUseError as error:
            await callback.answer(str(error), show_alert=True)
            return

        await callback.answer("Запрос отправлен администратору")
        await _edit_private(
            callback,
            "✅ Сертификат использован.\n\n"
            f"{admin} получил сообщение о том, что для {who} нужно "
            f"применить «{certificate['title']}».",
            _item_use_result_menu(world["id"], callback.from_user.id),
        )
        return

    if kind != "item":
        await callback.answer("Неизвестный тип предмета.", show_alert=True)
        return

    item = get_inventory_item(player["id"], value)
    if item is None:
        await callback.answer("Предмета больше нет в инвентаре.", show_alert=True)
        return

    if item.get("effect_key") == EFFECT_GACHA_TICKET:
        try:
            result = perform_gacha_pull(player["id"], payment="ticket")
        except (GachaNoTicket, GachaNoHeroes, GachaError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await show_gacha_pull_result(callback, world, player, result)
        return

    try:
        result = use_inventory_item(
            player["id"],
            value,
            operation_key=callback.id,
        )
    except ItemUseError as error:
        await callback.answer(str(error), show_alert=True)
        return

    effect_key = result.get("effect_key", "")
    if effect_key == "boss_coin_pouch":
        text = (
            f"🪙 Кошель открыт: +{result['amount']} монет.\n"
            f"Баланс: {result['coins']} 🪙"
        )
    elif effect_key == "boss_shard_casket":
        text = (
            f"🧩 Шкатулка открыта: +{result['amount']} осколков.\n"
            f"Осколки: {result['shards']} 🧩"
        )
    elif effect_key == "gacha_luck":
        text = (
            "🍀 Зелье удачи использовано.\n\n"
            "Следующая крутка получит +10% к весу легендарных "
            "и +30% к весу редких героев."
        )
    elif effect_key == "boss_damage_boost":
        text = (
            "🧪 Зелье урона использовано.\n\n"
            "В следующем бою с боссом твой итоговый урон после "
            "всех способностей героя будет увеличен на 10%."
        )
    elif effect_key == "boss_phantom_participation":
        text = (
            "👻 Зелье фантомного участия использовано.\n\n"
            "В следующем бою ты сохранишь право на награду, даже "
            "если не нанесёшь ни одного удара."
        )
    else:
        text = "✅ Предмет использован."

    await callback.answer("Предмет использован")
    await _edit_private(
        callback,
        text,
        _item_use_result_menu(world["id"], callback.from_user.id),
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
        _format_inventory(world, player, goods),
        _inventory_menu(world["id"], callback.from_user.id, goods),
    )

