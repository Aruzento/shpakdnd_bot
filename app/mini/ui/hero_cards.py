from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.editorial.registry import LiveMapping
from app.mini.editorial import glossary
from app.mini.ui.callbacks import number
import secrets

from app.mini.ui.transport import (
    replacement_ephemeral_kwargs as _replacement_ephemeral_kwargs,
    callback_delivery,
    send_private_text_from_callback as _send_private_text_from_callback,
    delete_current_ephemeral as _delete_current_ephemeral,
)
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup
from app.mini.presentation import hero_heading, hero_trait_lines, rarity_emoji, TRAIT_DESCRIPTIONS
from app.mini.gacha import get_gacha_state
from app.mini.heroes import get_hero_image, get_player_hero
from app.mini.hero_upgrades import hero_upgrade_state
from app.mini.ui.navigation import clip


@editorial_screen
def hero_caption(hero: dict, *, pull_result: dict | None = None, full: bool = False) -> str:
    description = clip(hero.get("description", ""), 220 if full else 300)
    passive = clip(hero.get("passive_text", ""), 200 if full else 300)
    lines = [
        f"{rarity_emoji(hero.get('rarity'))} {hero_heading(hero)}",
        *hero_trait_lines(hero),
        "",
        editorial_text('hero_cards.hero_caption.line_01', value_1=f"{int(hero.get('attack', 1))}"),
        "",
        description or editorial_text('hero_cards.hero_caption.line_03'),
        "",
        editorial_text('hero_cards.hero_caption.line_02', value_1=f"{passive or 'Нет особых способностей.'}"),
        *([editorial_text('hero_cards.hero_caption.line_16', value_1=f"{TRAIT_DESCRIPTIONS[hero['special_trait']]}")]
          if hero.get("special_trait") in TRAIT_DESCRIPTIONS else []),
    ]

    if full:
        rarity=LiveMapping({key:"rarity."+key for key in ('common', 'uncommon', 'rare', 'legendary', 'mythic', 'shadow')}, terms=True).get(hero.get('rarity'),editorial_text('hero_cards.hero_caption.line_04'))
        upgrade=hero.get('upgrade') or hero_upgrade_state(hero)
        lines += ['', editorial_text('hero_cards.hero_caption.line_05', rarity=f'{rarity}'), editorial_text('hero_cards.hero_caption.line_06', value_1=f"{hero.get('copies', 1)}", value_2=f"{hero.get('shards', 0)}"),
                  editorial_text('hero_cards.hero_caption.line_08') if upgrade['at_max'] else editorial_text('hero_cards.hero_caption.line_09', next_star=f"{upgrade['next_star']}", upgrade_cost=f"{upgrade['upgrade_cost']}")]
    if hero.get('rarity')=='mythic':
        lines+=['',f"Мифические осколки: {hero.get('mythic_shards',0)}",'Звёзды усиливают способность; базовая атака не растёт. Получение — только выращиванием.']
    if pull_result is not None:
        lines.append("")
        if pull_result["is_duplicate"]:
            lines.append(
                editorial_text('hero_cards.hero_caption.line_10', shards_awarded=f"{pull_result['shards_awarded']}")
            )
            lines.append(
                editorial_text('hero_cards.hero_caption.line_11', copies=f"{pull_result['copies']}", shards=f"{pull_result['shards']}")
            )
        else:
            lines.append(editorial_text('hero_cards.hero_caption.line_12'))

        if pull_result.get('payment') == 'coupon':
            lines.append(editorial_text('hero_cards.hero_caption.line_13'))
        elif pull_result["used_ticket"]:
            lines.append(editorial_text('hero_cards.hero_caption.line_17'))
        else:
            lines.append(editorial_text('hero_cards.hero_caption.line_18', cost_coins=f"{pull_result['cost_coins']}"))

        if pull_result.get("luck_used"):
            lines.append(editorial_text('hero_cards.hero_caption.line_14'))

        lines.append(editorial_text('hero_cards.hero_caption.line_07', balance=f"{pull_result['balance']}"))
        if pull_result["auto_activated"]:
            lines.append(editorial_text('hero_cards.hero_caption.line_15'))

    resident=hero.get("village_resident") if pull_result is None else None
    if resident:
        lines.append(editorial_text("hero.village.status",job=glossary("job."+(resident["building"] or "none"))))
    caption = "\n".join(lines)
    if pull_result is not None and pull_result.get('pity') is not None:
        from app.mini.pity import format_progress
        progress = format_progress(pull_result['pity'])
        return caption[:1018-len(progress)] + '\n\n' + progress
    return caption if resident else caption[:1020]


@editorial_screen
def hero_share_caption(hero: dict, sharer: str) -> str:
    description = clip(hero.get("description", ""), 760)
    sharer = (sharer or editorial_text('hero_cards.hero_share_caption.line_02')).strip()
    return (
        editorial_text('hero_cards.hero_share_caption.line_01', sharer=f'{sharer}', name=f"{hero['name']}", value_3=f"{description or 'Без описания.'}")
    )[:1020]


def eviction_button(world,user,hero,origin):
    resident=hero["village_resident"]
    tail=f"{number(hero['id'])}.{number(resident['revision'])}.{origin}"
    return InlineKeyboardButton(text=editorial_text("hero.village.evict"),callback_data=f"mini:hevict:{world}:{user}:{tail}")


@editorial_screen
def profile_menu(world,user,hero):
    rows=[]
    if hero:
        upgrade=hero.get('upgrade') or hero_upgrade_state(hero)
        if not upgrade['at_max']:
            rows.append([InlineKeyboardButton(text=editorial_text('hero_cards.profile_menu.line_03', upgrade_cost=f"{upgrade['upgrade_cost']}"),
                callback_data=f"mini:heroupgrade:{world}:{user}:{hero['id']}.{int(hero.get('stars',0))}.p")])
    if hero and hero.get("village_resident"):
        rows.append([eviction_button(world,user,hero,"p")])
    rows.append([InlineKeyboardButton(text=editorial_text('navigation.home'),callback_data=f'mini:home:{world}:{user}'),
                 InlineKeyboardButton(text=editorial_text('navigation.back'),callback_data=f'mini:heroarea:{world}:{user}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@editorial_screen
def hero_card_menu(world_id,user_id,hero,state,*,allow_share=False,collection=False,favorite=False,
                   nav_token='',return_callback=None,profile=False):
    if profile:return profile_menu(world_id,user_id,hero)
    token=secrets.token_hex(4)
    def button(text,action,tail=None):
        data=f'mini:{action}:{world_id}:{user_id}'+(':'+tail if tail is not None else '')
        if len(data.encode())>64:raise ValueError(editorial_text('errors.ui.hero_cards.button.01'))
        return InlineKeyboardButton(text=text,callback_data=data)
    suffix=('~'+nav_token) if nav_token else ''
    rows=[];upgrade=hero.get('upgrade') or hero_upgrade_state(hero)
    active=not int(hero.get('is_active',0))
    if collection:
        if not upgrade['at_max']:
            rows.append([button(editorial_text('hero_cards.hero_card_menu.line_08', next_star=f"{upgrade['next_star']}", upgrade_cost=f"{upgrade['upgrade_cost']}"),
                'heroupgrade',f"{hero['id']}.{int(hero.get('stars',0))}.c{suffix}")])
        if active:rows.append([button(editorial_text('hero_cards.hero_card_menu.line_09'),'heroactive',f"{hero['id']}.c{suffix}")])
        rows.append([button(editorial_text('hero_cards.hero_card_menu.line_10') if favorite else editorial_text('hero_cards.hero_card_menu.line_11'),'favorite',
                            f"{'remove' if favorite else 'add'}.{hero['id']}{suffix}")])
        if hero.get("village_resident"):
            rows.append([eviction_button(world_id,user_id,hero,"c")])
        rows.append([button(editorial_text('navigation.collection'),'collectionopen','0')])
        if nav_token:rows.append([button(editorial_text('hero_cards.hero_card_menu.line_13'),'collectionback',nav_token)])
    else:
        first=[]
        if active:first.append(button(editorial_text('hero_cards.hero_card_menu.line_02'),'heroactive',str(hero['id'])))
        if allow_share:first.append(button(editorial_text('hero_cards.hero_card_menu.line_03'),'heroshare',f"{hero['id']}.{token}"))
        if first:rows.append(first)
        rows.append([button(editorial_text('hero_cards.hero_card_menu.line_04'),'gacharepeat','coins.'+token),button(editorial_text('hero_cards.hero_card_menu.line_05'),'gachabatch',token)])
        rows.append([button(editorial_text('navigation.home'),'home'),button(editorial_text('navigation.collection'),'collection'),
                     InlineKeyboardButton(text=editorial_text('navigation.back'),callback_data=return_callback or f'mini:gacha:{world_id}:{user_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text_chunks(value,limit=4096):
    chunks=[];current=[];units=0
    for char in value:
        size=len(char.encode("utf-16-le"))//2
        if units+size>limit:
            chunks.append("".join(current));current=[];units=0
        current.append(char);units+=size
    if current:chunks.append("".join(current))
    return chunks or [""]


async def _send_card_text(callback,world,caption,reply_markup):
    chunks=_text_chunks(caption)
    for index,chunk in enumerate(chunks):
        sent=await _send_private_text_from_callback(callback,world,chunk,
            reply_markup if index==len(chunks)-1 else InlineKeyboardMarkup(inline_keyboard=[]))
    return sent


@editorial_screen
async def send_hero_card(
    callback: CallbackQuery,
    world: dict,
    hero: dict,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
):
    image = get_hero_image(hero)
    long_caption=len(caption.encode("utf-16-le"))//2>1024
    if image is not None:
        try:
            sent = await callback.bot.send_photo(
                **callback_delivery(callback,world),
                photo=FSInputFile(image),
                caption=(hero_heading(hero)[:200] if long_caption else caption),
                reply_markup=None if long_caption else reply_markup,
                **_replacement_ephemeral_kwargs(callback),
            )
            if long_caption:
                return await _send_card_text(callback,world,caption,reply_markup)
            await _delete_current_ephemeral(callback)
            return sent
        except TelegramBadRequest as error:
            print(
                "Не удалось отправить картинку героя: "
                f"hero={hero.get('code')} error={type(error).__name__}: {error}"
            )

    return await _send_card_text(
        callback,
        world,
        caption,
        reply_markup,
    )


@editorial_screen
async def send_public_hero_share(
    callback: CallbackQuery,
    world: dict,
    hero: dict,
    sharer: str,
):
    caption = hero_share_caption(hero, sharer)
    image = get_hero_image(hero)

    if image is not None:
        try:
            return await callback.bot.send_photo(
                chat_id=world["chat_id"],
                message_thread_id=world["thread_id"] or None,
                photo=FSInputFile(image),
                caption=caption,
            )
        except TelegramBadRequest as error:
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


@editorial_screen
async def show_gacha_pull_result(
    callback: CallbackQuery,
    world: dict,
    player: dict,
    result: dict,
    *, return_callback: str | None = None,
):
    state = get_gacha_state(player["id"])
    hero = get_player_hero(player["id"], result["id"])
    if hero is None:
        hero = result
        hero["is_active"] = 1 if result["auto_activated"] else 0

    markup = hero_card_menu(
        world["id"],
        callback.from_user.id,
        hero,
        state,
        allow_share=True,
        return_callback=return_callback,
    )

    try:
        await send_hero_card(
            callback,
            world,
            hero,
            hero_caption(result.get("hero_snapshot",hero), pull_result=result),
            markup,
        )
    except TelegramAPIError as error:
        print(f"Ошибка показа результата гачи: {type(error).__name__}: {error}")
        await callback.answer(
            editorial_text('hero_cards.show_gacha_pull_result.line_03'),
            show_alert=True,
        )
        return

    toast = (
        editorial_text('hero_cards.show_gacha_pull_result.line_01', name=f"{hero['name']}")
        if not result["is_duplicate"]
        else editorial_text('hero_cards.show_gacha_pull_result.line_02', shards_awarded=f"{result['shards_awarded']}")
    )
    await callback.answer(toast)
