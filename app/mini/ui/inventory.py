from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.ui.state import legacy_operation
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
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
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

@editorial_screen
def _format_inventory(world: dict, player: dict, goods: dict) -> str:
    lines = [
        editorial_text('navigation.inventory'),
        "",
        editorial_text('inventory.format_inventory.line_02'),
        editorial_text('inventory.format_inventory.line_03', coins=f"{player['coins']}"),
        editorial_text('inventory.format_inventory.line_04', shards=f"{player['shards']}"),
        "",
    ]

    inventory = goods["inventory"]
    certificates = goods["certificates"]
    effects = goods.get("effects", [])

    if inventory:
        lines.append(editorial_text('inventory.format_inventory.line_05'))
        for item in inventory:
            suffix = f" ×{item['quantity']}" if int(item["quantity"]) > 1 else ""
            lines.append(f"• {item['name']}{suffix}")
        lines.append("")

    if certificates:
        lines.append(editorial_text('inventory.format_inventory.line_06'))
        for certificate in certificates:
            lines.append(
                f"• #{certificate['purchase_id']} {certificate['title']}"
            )
        lines.append("")

    if effects:
        lines.append(editorial_text('inventory.format_inventory.line_07'))
        for effect in effects:
            charges = int(effect.get("charges", 0))
            suffix = f" ×{charges}" if charges > 1 else ""
            lines.append(
                f"• {active_effect_title(effect.get('effect_key', ''))}{suffix}"
            )
        lines.append("")

    if not inventory and not certificates:
        lines.append(editorial_text('inventory.format_inventory.line_08'))

    return "\n".join(lines).rstrip()


@editorial_screen
def _inventory_menu(
    world_id: int,
    user_id: int,
    goods: dict,
) -> InlineKeyboardMarkup:
    def b(label,action,tail=''):
        return InlineKeyboardButton(text=label,callback_data=_personal_callback(action,world_id,user_id)+(':'+tail if tail else ''))
    first=[]
    if goods.get('inventory') or goods.get('certificates'):first.append(b(editorial_text('inventory.inventory_menu.line_02'),'useitems'))
    first.append(b(editorial_text('inventory.inventory_menu.line_01'),'equipment','inventory.0'))
    return InlineKeyboardMarkup(inline_keyboard=[first,[b(editorial_text('navigation.shop'),'shop'),b(editorial_text('navigation.home'),'home'),b(editorial_text('navigation.back'),'heroarea')]])



@editorial_screen
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
                text=editorial_text('inventory.use_items_menu.line_02', value_1=f"{clip(item['name'], 28)}", suffix=f'{suffix}'),
                callback_data=(
                    f"mini:usepick:{world_id}:{user_id}:item,{item['item_id']}"
                ),
            )
        ])

    for certificate in goods.get("certificates", []):
        rows.append([
            InlineKeyboardButton(
                text=editorial_text('inventory.use_items_menu.line_03', value_1=f"{clip(certificate['title'], 28)}"),
                callback_data=(
                    f"mini:usepick:{world_id}:{user_id}:cert,{certificate['purchase_id']}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=editorial_text('inventory.use_items_menu.line_01'),
            callback_data=_personal_callback("inventory", world_id, user_id),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
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
                    text=editorial_text('inventory.use_item_confirm_menu.line_01'),
                    callback_data=(
                        f"mini:useconfirm:{world_id}:{user_id}:{kind},{value},{__import__('secrets').token_hex(4)}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=editorial_text('inventory.use_item_confirm_menu.line_02'),
                    callback_data=_personal_callback("useitems", world_id, user_id),
                )
            ],
        ]
    )


@editorial_screen
def _item_use_result_menu(world_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=editorial_text('inventory.item_use_result_menu.line_01'),
                    callback_data=_personal_callback("inventory", world_id, user_id),
                )
            ],
            [
                InlineKeyboardButton(
                    text=editorial_text('inventory.item_use_result_menu.line_02'),
                    callback_data=_personal_callback("home", world_id, user_id),
                )
            ],
        ]
    )


@editorial_screen
def _format_wallet_history(history: list[dict]) -> str:
    if not history:
        return (
            editorial_text('inventory.format_wallet_history.line_01')
        )

    lines = []
    for transaction in history:
        amount = int(transaction["amount"])
        sign = "+" if amount > 0 else ""
        lines.append(
            editorial_text('inventory.format_wallet_history.line_02', sign=f'{sign}', amount=f'{amount}', reason=f"{transaction['reason']}", balance_after=f"{transaction['balance_after']}")
        )
    return "\n".join(lines)


@editorial_screen
def _format_wallet(world: dict, player: dict, history: list[dict]) -> str:
    return (
        editorial_text('inventory.format_wallet.line_01', currency_name=f"{world['currency_name']}", coins=f"{player['coins']}", value_3=f'{_format_wallet_history(history)}')
    )


@editorial_screen
async def _open_inventory(callback: CallbackQuery, world: dict, player: dict):
    goods = get_player_goods(player["id"])
    await _edit_private(
        callback,
        _format_inventory(world, player, goods),
        _inventory_menu(world["id"], callback.from_user.id, goods),
    )


@router.callback_query(F.data.startswith("mini:inventory:"))
@editorial_screen
async def inventory_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    await callback.answer()
    await _open_inventory(callback, world, player)


@router.callback_query(F.data.startswith("mini:wallet:"))
@editorial_screen
async def wallet_callback(callback: CallbackQuery):
    """Совместимость со старыми кнопками «Кошелёк»."""
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    await callback.answer()
    await _open_inventory(callback, world, player)


@router.callback_query(F.data.startswith("mini:useitems:"))
@editorial_screen
async def use_items_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    goods = get_player_goods(player["id"])
    has_usable = bool(goods.get("inventory") or goods.get("certificates"))
    text = (
        editorial_text('inventory.use_items_callback.line_01')
        if has_usable
        else editorial_text('inventory.use_items_callback.line_02')
    )
    await callback.answer()
    await _edit_private(
        callback,
        text,
        _use_items_menu(world["id"], callback.from_user.id, goods),
    )


@router.callback_query(F.data.startswith("mini:usepick:"))
@editorial_screen
async def use_item_pick_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "usepick")
    if context is None:
        return

    world, player, payload = context
    try:
        kind, value_text = payload.split(",", 1)
        value = int(value_text)
    except (ValueError, IndexError):
        await callback.answer(editorial_text('inventory.use_item_pick_callback.line_03'), show_alert=True)
        return

    if kind == "item":
        item = get_inventory_item(player["id"], value)
        if item is None:
            await callback.answer(editorial_text('inventory.use_item_pick_callback.line_04'), show_alert=True)
            return
        text = (
            editorial_text('inventory.use_item_pick_callback.line_01', name=f"{item['name']}", value_2=f"{item.get('description') or 'Без описания.'}", value_3=f"{effect_description(item.get('effect_key', ''))}", quantity=f"{item['quantity']}")
        )
    elif kind == "cert":
        certificate = get_certificate_for_use(player["id"], value)
        if certificate is None:
            await callback.answer(editorial_text('inventory.use_item_pick_callback.line_06'), show_alert=True)
            return
        admin = get_topic_admin(int(world["chat_id"]), int(world["thread_id"])) or "@arukozento"
        text = (
            editorial_text('inventory.use_item_pick_callback.line_02', title=f"{certificate['title']}", value_2=f"{certificate.get('description') or 'Сертификат Mini.'}", admin=f'{admin}')
        )
    else:
        await callback.answer(editorial_text('inventory.use_item_pick_callback.line_05'), show_alert=True)
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
@editorial_screen
async def use_item_confirm_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "useconfirm")
    if context is None:
        return

    world, player, payload = context
    try:
        parts = payload.split(",")
        kind, value_text = parts[:2]
        operation_key = "itemuse:" + payload if len(parts)==3 else legacy_operation(callback,"itemuse:"+payload)
        value = int(value_text)
    except (ValueError, IndexError):
        await callback.answer(editorial_text('inventory.use_item_confirm_callback.line_09'), show_alert=True)
        return

    if kind == "cert":
        certificate = get_certificate_for_use(player["id"], value)
        if certificate is None:
            await callback.answer(
                editorial_text('inventory.use_item_confirm_callback.line_10'),
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
                    editorial_text('inventory.use_item_confirm_callback.line_12', value_1=f"{format_player_mention(dict(world_id=world['id'], username=admin))}", who=f'{who}', title=f"{certificate['title']}", purchase_id=f"{certificate['purchase_id']}")
                ),
            )
        except TelegramAPIError as error:
            print(
                "Не удалось отправить запрос на сертификат: "
                f"{type(error).__name__}: {error}"
            )
            await callback.answer(
                editorial_text('inventory.use_item_confirm_callback.line_13'),
                show_alert=True,
            )
            return

        try:
            mark_certificate_requested(player["id"], value)
        except ItemUseError as error:
            await callback.answer(str(error), show_alert=True)
            return

        await callback.answer(editorial_text('inventory.use_item_confirm_callback.line_04'))
        await _edit_private(
            callback,
            editorial_text('inventory.use_item_confirm_callback.line_05', admin=f'{admin}', who=f'{who}', title=f"{certificate['title']}"),
            _item_use_result_menu(world["id"], callback.from_user.id),
        )
        return

    if kind != "item":
        await callback.answer(editorial_text('inventory.use_item_confirm_callback.line_06'), show_alert=True)
        return

    item = get_inventory_item(player["id"], value)
    if item is None:
        from app.mini.items import get_previous_item_use
        previous=get_previous_item_use(player['id'],operation_key)
        if previous and previous.get('item_id')==value:
            item=previous
        if item is None:
            from app.mini.gacha import get_previous_gacha_pull
            previous_pull = get_previous_gacha_pull(player['id'], operation_key)
            if previous_pull and previous_pull.get('payment') == 'ticket':
                await show_gacha_pull_result(callback, world, player, previous_pull)
                return
    if item is None:
        await callback.answer(editorial_text('inventory.use_item_confirm_callback.line_07'), show_alert=True)
        return

    if item.get("effect_key") == EFFECT_GACHA_TICKET:
        try:
            result = perform_gacha_pull(player["id"], payment="ticket", operation_key=operation_key)
        except (GachaNoTicket, GachaNoHeroes, GachaError) as error:
            await callback.answer(str(error), show_alert=True)
            return
        await show_gacha_pull_result(callback, world, player, result)
        return

    try:
        result = use_inventory_item(
            player["id"],
            value,
            operation_key=operation_key,
        )
    except ItemUseError as error:
        await callback.answer(str(error), show_alert=True)
        return

    if result.get('summon_result'):
        await show_gacha_pull_result(callback, world, player, result['summon_result'])
        return

    effect_key = result.get("effect_key", "")
    if effect_key == "boss_coin_pouch":
        text = (
            editorial_text('inventory.use_item_confirm_callback.line_01', amount=f"{result['amount']}", coins=f"{result['coins']}")
        )
    elif effect_key == "boss_shard_casket":
        text = (
            editorial_text('inventory.use_item_confirm_callback.line_02', amount=f"{result['amount']}", shards=f"{result['shards']}")
        )
    elif effect_key == 'spirit_lantern':
        text = editorial_text('inventory.use_item_confirm_callback.line_08', charges=f"{result['charges']}")
    elif effect_key == "gacha_luck":
        text = (
            editorial_text('inventory.use_item_confirm_callback.line_11')
        )
    elif effect_key == "boss_damage_boost":
        text = (
            editorial_text('inventory.use_item_confirm_callback.line_14')
        )
    elif effect_key == "boss_phantom_participation":
        text = (
            editorial_text('inventory.use_item_confirm_callback.line_15')
        )
    elif effect_key in ('village_gold_boost','village_shards_boost'):
        text = editorial_text('inventory.use_item_confirm_callback.line_16')
    elif effect_key == 'tower_equipment_chest':
        text = editorial_text('inventory.use_item_confirm_callback.line_17')
    else:
        text = editorial_text('inventory.use_item_confirm_callback.line_18')

    await callback.answer(editorial_text('inventory.use_item_confirm_callback.line_03'))
    await _edit_private(
        callback,
        text,
        _item_use_result_menu(world["id"], callback.from_user.id),
    )


@router.callback_query(F.data.startswith("mini:goods:"))
@editorial_screen
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
