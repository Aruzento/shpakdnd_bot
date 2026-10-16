from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.ui.hero_village import with_village
import secrets
from app.mini.presentation import format_player_mention
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
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
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
from app.mini.presentation import rarity_emoji
from app.mini.ui.hero_cards import (
    hero_caption,
    hero_card_menu,
    send_hero_card,
    send_public_hero_share,
    show_gacha_pull_result,
)

from app.mini.favorites import get_favorites,add_favorite,remove_favorite,replace_favorite,is_favorite
from app.mini.ui.hero_selector import render_selector

from app.mini.ui.state import split_context, restore, validate_navigation, legacy_operation, claim_publication

router = Router(name="mini_heroes")

COLLECTION_PAGE_SIZE = 4
@editorial_screen
def _collection_menu(
    world_id: int,
    user_id: int,
    summary: dict,
    page: int = 0,
    *, view: str = 'collection',
) -> InlineKeyboardMarkup:
    return render_selector(summary['heroes'],summary.get('favorites',[]),'c',world_id,user_id,view=view,page=page)[1]


@editorial_screen
def _format_collection(world: dict, player: dict, summary: dict) -> str:
    active = summary.get("active_hero")
    active_text = active["name"] if active else editorial_text('heroes.format_collection.line_01')
    return (
        editorial_text('heroes.format_collection.line_02', owned=f"{summary['owned']}", total_active=f"{summary['total_active']}", active_text=f'{active_text}', coins=f"{player['coins']}", shards=f"{player['shards']}")
        + editorial_text('heroes.format_collection.line_03')
    )



@editorial_screen
def _gacha_menu(
    world_id: int,
    user_id: int,
    state: dict,
) -> InlineKeyboardMarkup:
    token=secrets.token_hex(4)
    def b(label,action,tail=None):
        data=_personal_callback(action,world_id,user_id)+(':'+tail if tail is not None else '')
        return InlineKeyboardButton(text=label,callback_data=data)
    rows=[[b(editorial_text('heroes.gacha_menu.line_01', pull_price=f"{state['pull_price']}"),'gachapull','coins.'+token),
           b(editorial_text('heroes.gacha_menu.line_02'),'gachabatch',token)]]
    second=[]
    if int(state.get('tickets',0))>0:second.append(b(editorial_text('heroes.gacha_menu.line_03', tickets=f"{state['tickets']}"),'gachapull','ticket.'+token))
    if state.get('coupons'):second.append(b(editorial_text('heroes.gacha_menu.line_04'),'gachacoupon'))
    if second:rows.append(second)
    rows.append([b(editorial_text('navigation.home'),'home'),b(editorial_text('navigation.collection'),'collection'),b(editorial_text('navigation.back'),'home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)



from app.mini.pity import format_progress



@editorial_screen
def _format_gacha(world: dict, state: dict) -> str:
    chances = state["rarity_chances"]
    luck_line = (
        editorial_text('heroes.format_gacha.line_01')
        if state.get("luck_active")
        else ""
    )
    return (
        editorial_text('heroes.format_gacha.line_02', pull_price=f"{state['pull_price']}", tickets=f"{state['tickets']}", coins=f"{state['coins']}", shards=f"{state['shards']}", luck_line=f'{luck_line}', value_6=f"{chances.get('common', 0):g}", value_7=f"{chances.get('uncommon', 0):g}", value_8=f"{chances.get('rare', 0):g}", value_9=f"{chances.get('legendary', 0):g}", owned=f"{state['owned']}", total=f"{state['total']}")
        + format_progress(state.get("pity", {}))
    )


@editorial_screen
def _shard_sell_menu(
    world_id: int,
    user_id: int,
    hero: dict,
    *, nav_token: str = '',
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=editorial_text("navigation.back"),
        callback_data=f"mini:hero:{world_id}:{user_id}:{hero['id']}")]])


@editorial_screen
def _format_shard_sell(hero: dict, balance: int) -> str:
    return editorial_text("hero.shards.closed")



@router.callback_query(F.data.startswith("mini:collection:"))
@editorial_screen
async def collection_callback(callback: CallbackQuery):
    context = await _load_personal_context(callback)
    if context is None:
        return

    world, player = context
    summary = get_collection_summary(player["id"])
    summary["favorites"] = get_favorites(player["id"])

    await callback.answer()
    await _edit_private(
        callback,
        _format_collection(world, player, summary),
        _collection_menu(
            world["id"], callback.from_user.id, summary, page=0
        ),
    )


@router.callback_query(F.data.startswith("mini:collectionpage:"))
@editorial_screen
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
    summary["favorites"] = get_favorites(player["id"])
    await callback.answer()
    await _edit_private(
        callback,
        _format_collection(world, player, summary),
        _collection_menu(
            world["id"], callback.from_user.id, summary, page=page, view="all"
        ),
    )


@router.callback_query(F.data.startswith("mini:collectionopen:"))
@editorial_screen
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
    summary["favorites"] = get_favorites(player["id"])
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
        await callback.answer(editorial_text('heroes.collection_open_callback.line_02'), show_alert=True)
        return
    await callback.answer(editorial_text('heroes.collection_open_callback.line_01'))


@router.callback_query(F.data.startswith("mini:gacha:"))
@editorial_screen
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


@editorial_screen
async def _handle_gacha_pull(
    callback: CallbackQuery,
    *,
    prefix: str,
):
    context = await _load_extended_context(callback, prefix)
    if context is None:
        return

    world, player, payload = context
    payment=payload.split('.',1)[0]
    if payment not in {"coins", "ticket"}:
        await callback.answer(editorial_text('heroes.handle_gacha_pull.line_01'), show_alert=True)
        return

    try:
        result = perform_gacha_pull(player["id"], payment=payment, operation_key="gacha:"+payload if "." in payload else legacy_operation(callback,"legacy-gacha:"+payment))
    except (GachaInsufficientFunds, GachaNoTicket, GachaNoHeroes, GachaError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    await show_gacha_pull_result(callback, world, player, result)


@router.callback_query(F.data.startswith("mini:gachapull:"))
@editorial_screen
async def gacha_pull_callback(callback: CallbackQuery):
    await _handle_gacha_pull(
        callback,
        prefix="gachapull",
    )


@router.callback_query(F.data.startswith("mini:gacharepeat:"))
@editorial_screen
async def gacha_repeat_callback(callback: CallbackQuery):
    await _handle_gacha_pull(
        callback,
        prefix="gacharepeat",
    )


@router.callback_query(F.data.startswith("mini:heroshare:"))
@editorial_screen
async def hero_share_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroshare")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id = int(hero_id_text.split('.',1)[0])
    except ValueError:
        await callback.answer(editorial_text('heroes.hero_share_callback.line_04'), show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    if hero is None:
        await callback.answer(editorial_text('heroes.hero_share_callback.line_02'), show_alert=True)
        return

    key='share:'+hero_id_text if '.' in hero_id_text else legacy_operation(callback,'share:'+str(hero_id))
    if not claim_publication(player['id'],key):
        await callback.answer(editorial_text('heroes.hero_share_callback.line_03'));return

    sharer = format_player_mention(dict(player,username=_username_from_user(callback.from_user) or player.get("username")))

    try:
        await send_public_hero_share(callback, world, hero, sharer)
    except TelegramAPIError as error:
        print(
            "Ошибка публичной публикации героя: "
            f"{type(error).__name__}: {error}"
        )
        await callback.answer(
            editorial_text('heroes.hero_share_callback.line_05'),
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

    await callback.answer(editorial_text('heroes.hero_share_callback.line_01'))


@router.callback_query(F.data.startswith("mini:hero:"))
@editorial_screen
async def hero_card_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "hero")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id_text,legacy_token=split_context(hero_id_text)
        parts=hero_id_text.split('.')
        hero_id=int(parts[0]);nav_token=legacy_token or (parts[1] if len(parts)==2 else '')
        if len(parts)>2:raise ValueError(editorial_text('errors.ui.heroes.hero_card_callback.01'))
        if nav_token:nav_token=validate_navigation(player['id'],nav_token)
    except ValueError as error:
        await callback.answer(str(error) or editorial_text('heroes.hero_card_callback.line_03'),show_alert=True);return

    hero = get_player_hero(player["id"], hero_id)
    if hero is None:
        await callback.answer(editorial_text('heroes.hero_card_callback.line_01'), show_alert=True)
        return

    hero=with_village(hero,player["id"])
    state = get_gacha_state(player["id"])
    try:
        await send_hero_card(
            callback,
            world,
            hero,
            hero_caption(hero,full=True),
            hero_card_menu(
                world["id"], callback.from_user.id, hero, state, collection=True, favorite=is_favorite(player["id"],hero_id), nav_token=nav_token
            ),
        )
    except TelegramAPIError as error:
        print(f"Ошибка карточки героя: {type(error).__name__}: {error}")
        await callback.answer(editorial_text('heroes.hero_card_callback.line_02'), show_alert=True)
        return

    await callback.answer(hero["name"])


@router.callback_query(F.data.startswith("mini:heroupgrade:"))
@editorial_screen
async def hero_upgrade_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroupgrade")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id_text,nav_token=split_context(hero_id_text)
        if nav_token:nav_token=validate_navigation(player["id"],nav_token)
        profile=hero_id_text.endswith(".p")
        hero_id_text=hero_id_text.removesuffix(".p")
        collection=hero_id_text.endswith(".c")
        hero_id,expected_stars = map(int,hero_id_text.removesuffix(".c").split("."))
    except ValueError:
        await callback.answer(editorial_text('heroes.hero_upgrade_callback.line_02'), show_alert=True)
        return

    try:
        result = upgrade_hero(player["id"], hero_id, expected_stars=expected_stars)
    except (HeroUpgradeInsufficientShards, HeroUpgradeMaxStars, HeroUpgradeError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    hero = get_player_hero(player["id"], hero_id)
    if collection or profile:hero=with_village(hero,player["id"])
    state = get_gacha_state(player["id"])

    if hero is not None:
        try:
            await send_hero_card(
                callback,
                world,
                hero,
                hero_caption(hero,full=collection),
                hero_card_menu(
                    world["id"], callback.from_user.id, hero, state, collection=collection, favorite=collection and is_favorite(player["id"],hero_id), nav_token=nav_token, profile=profile
                ),
            )
        except TelegramAPIError as error:
            print(f"Ошибка карточки после улучшения: {type(error).__name__}: {error}")
            await callback.answer(
                editorial_text('heroes.hero_upgrade_callback.line_03', stars=f"{result['stars']}", old_attack=f"{result['old_attack']}", attack=f"{result['attack']}"),
                show_alert=True,
            )
            return

    await callback.answer(
        editorial_text('heroes.hero_upgrade_callback.line_01', stars=f"{result['stars']}", old_attack=f"{result['old_attack']}", attack=f"{result['attack']}"),
        show_alert=True,
    )


@router.callback_query(F.data.startswith("mini:heroshards:"))
@editorial_screen
async def hero_shards_callback(callback: CallbackQuery):
    context=await _load_extended_context(callback,"heroshards")
    if context is None:return
    await callback.answer(editorial_text("hero.shards.closed"),show_alert=True)


@router.callback_query(F.data.startswith("mini:shardsell:"))
@editorial_screen
async def shard_sell_callback(callback: CallbackQuery):
    context=await _load_extended_context(callback,"shardsell")
    if context is None:return
    await callback.answer(editorial_text("hero.shards.closed"),show_alert=True)


@router.callback_query(F.data.startswith("mini:heroactive:"))
@editorial_screen
async def hero_active_callback(callback: CallbackQuery):
    context = await _load_extended_context(callback, "heroactive")
    if context is None:
        return

    world, player, hero_id_text = context
    try:
        hero_id_text,nav_token=split_context(hero_id_text)
        if nav_token:nav_token=validate_navigation(player["id"],nav_token)
        profile=hero_id_text.endswith(".p")
        hero_id_text=hero_id_text.removesuffix(".p")
        collection=hero_id_text.endswith(".c")
        hero_id = int(hero_id_text.removesuffix(".c"))
    except ValueError:
        await callback.answer(editorial_text('heroes.hero_active_callback.line_02'), show_alert=True)
        return


    try:
        set_active_hero(player["id"], hero_id)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return


    hero = get_player_hero(player["id"], hero_id)
    if collection or profile:hero=with_village(hero,player["id"])
    state = get_gacha_state(player["id"])
    if hero is not None:
        try:
            await send_hero_card(
                callback,
                world,
                hero,
                hero_caption(hero,full=collection),
                hero_card_menu(
                    world["id"], callback.from_user.id, hero, state, collection=collection, favorite=collection and is_favorite(player["id"],hero_id), nav_token=nav_token, profile=profile
                ),
            )
        except TelegramAPIError as error:
            print(
                "Ошибка карточки после выбора активного героя: "
                f"{type(error).__name__}: {error}"
            )
            await callback.answer(
                editorial_text('heroes.hero_active_callback.line_03', name=f"{hero['name']}"),
                show_alert=True,
            )
            return

    await callback.answer(
        editorial_text('heroes.hero_active_callback.line_01', value_1=f"{(hero['name'] if hero else 'выбран')}")
    )



@router.callback_query(F.data.startswith('mini:favorite:'))
@editorial_screen
async def favorite_callback(callback):
    context=await _load_extended_context(callback,'favorite')
    if context is None: return
    world,player,tail=context
    try:
        tail,nav_token=split_context(tail)
        if nav_token:nav_token=validate_navigation(player['id'],nav_token)
        action,new_id,*old=tail.split('.')
        new_id=int(new_id)
        hero=get_player_hero(player['id'],new_id)
        if not hero: raise ValueError(editorial_text('errors.ui.heroes.favorite_callback.01'))
        if action=='add':
            ids=get_favorites(player['id'])
            if len(ids)==3 and new_id not in ids:
                rows=[]
                for old_id in ids:
                    old_hero=get_player_hero(player['id'],old_id)
                    if old_hero:
                        data=f"mini:favorite:{world['id']}:{callback.from_user.id}:replace.{new_id}.{old_id}{'~'+nav_token if nav_token else ''}"
                        if len(data.encode())>64:data=data.replace(':replace.',':r.',1)
                        if len(data.encode())>64:raise ValueError(editorial_text('errors.ui.heroes.favorite_callback.03'))
                        rows.append([InlineKeyboardButton(text=old_hero['name'],callback_data=data)])
                rows.append([InlineKeyboardButton(text=editorial_text('heroes.favorite_callback.line_03'),callback_data=f"mini:hero:{world['id']}:{callback.from_user.id}:{new_id}{'.'+nav_token if nav_token else ''}")])
                await callback.answer()
                await _send_private_text_from_callback(callback,world,editorial_text('heroes.favorite_callback.line_02'),InlineKeyboardMarkup(inline_keyboard=rows))
                return
            add_favorite(player['id'],new_id)
        elif action=='remove': remove_favorite(player['id'],new_id)
        elif action in ('replace','r') and len(old)==1: replace_favorite(player['id'],int(old[0]),new_id)
        else: raise ValueError(editorial_text('errors.ui.heroes.favorite_callback.02'))
        hero=with_village(hero,player["id"])
        state=get_gacha_state(player['id'])
        await send_hero_card(callback,world,hero,hero_caption(hero,full=True),hero_card_menu(world['id'],callback.from_user.id,hero,state,collection=True,favorite=is_favorite(player['id'],new_id),nav_token=nav_token))
        await callback.answer(editorial_text('heroes.favorite_callback.line_01'))
    except (ValueError,IndexError) as error:
        await callback.answer(str(error),show_alert=True)


@router.callback_query(F.data.startswith('mini:collectionback:'))
@editorial_screen
async def collection_back_callback(callback):
    context=await _load_extended_context(callback,'collectionback')
    if context is None:return
    world,player,token=context
    try:
        saved=restore(player['id'],token)
        from app.mini.heroes import get_player_heroes
        text,markup=render_selector(get_player_heroes(player['id'],sync_catalog=False),get_favorites(player['id']),
            'c',world['id'],callback.from_user.id,view=saved['view'],page=saved['page'],token=saved['filter'],return_token=token)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer();await _send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith("mini:hevict:"))
@editorial_screen
async def hero_evict_callback(callback):
    from app.mini.ui.callbacks import integer
    from app.mini.village.service import mutate
    context=await _load_extended_context(callback,"hevict")
    if context is None:return
    world,player,payload=context
    try:
        parts=payload.split(".")
        if len(parts) not in (3,4) or parts[2] not in {"c","p"}:raise ValueError
        hid,revision=integer(parts[0]),integer(parts[1])
        profile=parts[2]=="p"
        hero=get_player_hero(player["id"],hid)
        if hero is None:raise ValueError(editorial_text("hero.village.missing"))
        if len(parts)==4:
            if parts[3]!="yes":raise ValueError
            mutate(player["id"],"evict",hero_id=hid,expected_revision=revision,
                   operation_key=f"hero-evict:{hid}:{revision}")
            hero=with_village(hero,player["id"])
            await send_hero_card(callback,world,hero,hero_caption(hero,full=not profile),
                hero_card_menu(world["id"],callback.from_user.id,hero,get_gacha_state(player["id"]),
                    collection=not profile,profile=profile,favorite=is_favorite(player["id"],hid)))
        else:
            hero=with_village(hero,player["id"])
            resident=hero.get("village_resident")
            if not resident or resident["revision"]!=revision:raise ValueError(editorial_text("hero.village.stale"))
            markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=editorial_text("hero.village.confirm"),
                    callback_data=f"mini:hevict:{world['id']}:{callback.from_user.id}:{payload}.yes")],
                [InlineKeyboardButton(text=editorial_text("navigation.back"),
                    callback_data=(f"mini:character:{world['id']}:{callback.from_user.id}" if profile else
                                   f"mini:hero:{world['id']}:{callback.from_user.id}:{hid}"))]])
            await _send_private_text_from_callback(callback,world,
                editorial_text("hero.village.question",name=hero["name"]),markup)
    except (ValueError,IndexError) as error:
        await callback.answer(str(error)[:180] or editorial_text("hero.village.stale"),show_alert=True)
        return
    await callback.answer()
