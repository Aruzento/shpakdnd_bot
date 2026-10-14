import secrets

from app.mini.ui.transport import (
    replacement_ephemeral_kwargs as _replacement_ephemeral_kwargs,
    callback_delivery,
    send_private_text_from_callback as _send_private_text_from_callback,
    delete_current_ephemeral as _delete_current_ephemeral,
)
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from app.mini.presentation import hero_heading, hero_trait_lines, rarity_emoji, TRAIT_DESCRIPTIONS
from app.mini.gacha import get_gacha_state
from app.mini.heroes import get_hero_image, get_player_hero
from app.mini.hero_upgrades import hero_upgrade_state
from app.mini.ui.navigation import clip

def hero_caption(hero: dict, *, pull_result: dict | None = None, full: bool = False) -> str:
    description = clip(hero.get("description", ""), 220 if full else 300)
    passive = clip(hero.get("passive_text", ""), 200 if full else 300)
    lines = [
        f"{rarity_emoji(hero.get('rarity'))} {hero_heading(hero)}",
        *hero_trait_lines(hero),
        "",
        f"⚔️ Урон: {int(hero.get('attack', 1))}",
        "",
        description or "Без описания.",
        "",
        f"💫 Особый эффект: {passive or 'Нет особых способностей.'}",
        *([f"✨ Свойство: {TRAIT_DESCRIPTIONS[hero['special_trait']]}"]
          if hero.get("special_trait") in TRAIT_DESCRIPTIONS else []),
    ]

    if full:
        rarity={'common':'Обычный','uncommon':'Необычный','rare':'Редкий','legendary':'Легендарный','mythic':'Мифический','shadow':'Теневой'}.get(hero.get('rarity'),'Неизвестная редкость')
        upgrade=hero.get('upgrade') or hero_upgrade_state(hero)
        lines += ['', f"Редкость: {rarity}", f"Копий: {hero.get('copies',1)} · Осколки: {hero.get('shards',0)}",
                  '⭐ Максимум звёзд' if upgrade['at_max'] else f"Улучшение до ★{upgrade['next_star']}: {upgrade['upgrade_cost']} осколков"]
    if pull_result is not None:
        lines.append("")
        if pull_result["is_duplicate"]:
            lines.append(
                f"♻️ Дубликат: +{pull_result['shards_awarded']} осколков"
            )
            lines.append(
                f"Копий: {pull_result['copies']} • Общие осколки: {pull_result['shards']}"
            )
        else:
            lines.append("🎉 Новый герой добавлен в коллекцию!")

        if pull_result.get('payment') == 'coupon':
            lines.append('🎟 Потрачено: 1 купон целевой редкости')
        elif pull_result["used_ticket"]:
            lines.append("🎟 Потрачено: 1 билет призыва")
        else:
            lines.append(f"🪙 Потрачено: {pull_result['cost_coins']}")

        if pull_result.get("luck_used"):
            lines.append("🍀 Зелье удачи сработало на эту крутку.")

        lines.append(f"💰 Баланс: {pull_result['balance']}")
        if pull_result["auto_activated"]:
            lines.append("⭐ Первый герой автоматически выбран активным.")

    caption = "\n".join(lines)
    if pull_result is not None and pull_result.get('pity') is not None:
        from app.mini.pity import format_progress
        progress = format_progress(pull_result['pity'])
        return caption[:1018-len(progress)] + '\n\n' + progress
    return caption[:1020]


def hero_share_caption(hero: dict, sharer: str) -> str:
    description = clip(hero.get("description", ""), 760)
    sharer = (sharer or "Игрок").strip()
    return (
        f"{sharer}: «Смотри, что мне выпало: {hero['name']}!»\n\n"
        f"{description or 'Без описания.'}"
    )[:1020]


def profile_menu(world,user,hero):
    rows=[]
    if hero:
        upgrade=hero.get('upgrade') or hero_upgrade_state(hero)
        if not upgrade['at_max']:
            rows.append([InlineKeyboardButton(text=f"⬆️ Улучшить · {upgrade['upgrade_cost']} 🧩",
                callback_data=f"mini:heroupgrade:{world}:{user}:{hero['id']}.{int(hero.get('stars',0))}.p")])
    rows.append([InlineKeyboardButton(text='🏠 Главная',callback_data=f'mini:home:{world}:{user}'),
                 InlineKeyboardButton(text='⬅️ Назад',callback_data=f'mini:heroarea:{world}:{user}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hero_card_menu(world_id,user_id,hero,state,*,allow_share=False,collection=False,favorite=False,
                   nav_token='',return_callback=None,profile=False):
    if profile:return profile_menu(world_id,user_id,hero)
    token=secrets.token_hex(4)
    def button(text,action,tail=None):
        data=f'mini:{action}:{world_id}:{user_id}'+(':'+tail if tail is not None else '')
        if len(data.encode())>64:raise ValueError('Слишком длинная кнопка.')
        return InlineKeyboardButton(text=text,callback_data=data)
    suffix=('~'+nav_token) if nav_token else ''
    rows=[];upgrade=hero.get('upgrade') or hero_upgrade_state(hero)
    active=not int(hero.get('is_active',0))
    if collection:
        if not upgrade['at_max']:
            rows.append([button(f"⬆️ Улучшить · ⭐{upgrade['next_star']} · {upgrade['upgrade_cost']} 🧩",
                'heroupgrade',f"{hero['id']}.{int(hero.get('stars',0))}.c{suffix}")])
        if active:rows.append([button('✅ Сделать активным','heroactive',f"{hero['id']}.c{suffix}")])
        rows.append([button('☆ Убрать из избранного' if favorite else '⭐ Сделать избранным','favorite',
                            f"{'remove' if favorite else 'add'}.{hero['id']}{suffix}")])
        if int(hero.get('shards',0))>0:rows.append([button('💱 Продать осколки','heroshards',str(hero['id'])+suffix)])
        rows.append([button('📚 Коллекция','collectionopen','0')])
        if nav_token:rows.append([button('⬅️ К списку','collectionback',nav_token)])
    else:
        first=[]
        if active:first.append(button('✅ Сделать активным','heroactive',str(hero['id'])))
        if allow_share:first.append(button('📣 Похвастаться','heroshare',f"{hero['id']}.{token}"))
        if first:rows.append(first)
        rows.append([button('🪙 Ещё призыв','gacharepeat','coins.'+token),button('✨ Ещё 10','gachabatch',token)])
        rows.append([button('🏠 Главная','home'),button('📚 Коллекция','collection'),
                     InlineKeyboardButton(text='⬅️ Назад',callback_data=return_callback or f'mini:gacha:{world_id}:{user_id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_hero_card(
    callback: CallbackQuery,
    world: dict,
    hero: dict,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
):
    image = get_hero_image(hero)
    if image is not None:
        try:
            sent = await callback.bot.send_photo(
                **callback_delivery(callback,world),
                photo=FSInputFile(image),
                caption=caption,
                reply_markup=reply_markup,
                **_replacement_ephemeral_kwargs(callback),
            )
            await _delete_current_ephemeral(callback)
            return sent
        except TelegramBadRequest as error:
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
            "Герой получен, но карточку показать не удалось. Открой коллекцию.",
            show_alert=True,
        )
        return

    toast = (
        f"Новый герой: {hero['name']}"
        if not result["is_duplicate"]
        else f"Дубликат: +{result['shards_awarded']} осколков"
    )
    await callback.answer(toast)

