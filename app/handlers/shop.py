"""Telegram screens for ordinary D&D only."""
import asyncio
import logging
import sqlite3
from fractions import Fraction

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.context import get_thread_id, check_topic_admin_permission
from app.dnd_shop.catalog import CatalogError
from app.dnd_shop.money import MoneyError, exact_text
from app.dnd_shop.service import PriceChanged, Shop, ShopError

router = Router(name='dnd_shop')
logger = logging.getLogger(__name__)


def callback(session, action, argument='0', page=0):
    data = f'ds:{session}:{action}:{argument}:{page}'
    if len(data.encode('utf-8')) > 64:
        raise ValueError('Shop callback exceeds Telegram limit')
    return data


def compact(text, units=900):
    # Telegram counts UTF-16 code units; source and receipt remain unabridged.
    encoded = str(text).encode('utf-16-le')
    if len(encoded) <= units * 2:
        return str(text)
    return encoded[:(units - 1) * 2].decode('utf-16-le', errors='ignore') + '…'


def render(session, view, page=0):
    def button(text, action, argument='0', back_page=page):
        return InlineKeyboardButton(text=text, callback_data=callback(session, action, argument, back_page))

    balance = Fraction(view.get('credit_balance', 0))
    credit_text = f'\nТорговый остаток: {exact_text(balance)}.' if balance else ''
    screen = view['screen']
    if screen == 'exit':
        return 'Магазин закрыт.', None
    if screen == 'home':
        return 'Торговец: «Чего желаешь, путник?»' + credit_text, InlineKeyboardMarkup(inline_keyboard=[
            [button('🛒 Купить', 'b')], [button('💰 Продать', 's')], [button('🚪 Выйти', 'e')]])
    if screen in ('buy', 'sell'):
        page = view['page']
        buttons = [button(compact(name, 64), 'v' if screen == 'buy' else 'c', argument, page) for name, argument in view['buttons']]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        action = 'b' if screen == 'buy' else 's'
        rows.append([button('◀️', action, str(max(0, page - 1)), page),
                     button('▶️', action, str(min(view['pages'] - 1, page + 1)), page)])
        if balance:
            rows.append([button('💱 Обменять остаток', 'q')])
        rows.append([button('Назад', 'h')])
        title = '🛒 Товары' if screen == 'buy' else '💰 Ваши предметы'
        empty = '\nЗдесь пока пусто.' if not buttons else ''
        return f"{title}\nСтраница {page + 1}/{view['pages']}{empty}{credit_text}", InlineKeyboardMarkup(inline_keyboard=rows)
    if screen == 'credit':
        return (f'Обменять весь торговый остаток: {exact_text(balance)}?\n'
                'Обмен выполняется только при точном размене; иначе остаток сохраняется.',
                InlineKeyboardMarkup(inline_keyboard=[
                    [button('✅ Обменять на монеты', 'r', view['token'])],
                    [button('◀️ Назад', 's', '0')]]))
    product = view['product']
    price = exact_text(product.price)
    base_name = compact(view.get('base_name', 'базовой валюты'), 80)
    if screen == 'buy_card':
        text = (f'{compact(product.name, 256)}\n\nОписание: {compact(product.description)}\n\n'
                f"Стоимость: {price} {base_name}.\n\nВ наличии: {view['available']} шт.")
        if product.master_sha256:
            text += (f'\nКатегория: {compact(product.category, 100)}; редкость: {compact(product.rarity, 100)}'
                     f'\nЭффект: {compact(product.effect)}')
        rows = [[button('🛒 Купить', 'x', view['token'])], [button('◀️ Назад', 'b', str(page))]]
    else:
        text = (f"Торговец: «Могу предложить вот столько».\n\n{compact(view['name'], 256)}\n\n"
                f"В инвентаре: {view['quantity']} шт.\n\nЦена в магазине: {price} {base_name}.\n\n"
                f"Предложение торговца: {exact_text(view['payout'])} {base_name} за 1 шт.")
        rows = [[button('✅ Подтвердить продажу', 'x', view['token'])], [button('◀️ Назад', 's', str(page))]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def result_text(result):
    def coins(rows):
        return ', '.join(f'{compact(name, 48)}: {quantity}' for name, quantity in rows) or 'нет'
    balance = Fraction(result.get('credit_balance', 0))
    suffix = f'\nТорговый остаток: {exact_text(balance)}.'
    if Fraction(result.get('credit_spent', 0)):
        suffix += f"\nСписано с остатка: {exact_text(result['credit_spent'])}."
    if Fraction(result.get('credit_added', 0)):
        suffix += f"\nВся выплата {exact_text(result['credit_added'])} зачислена в торговый остаток."
    if result['kind'] == 'redeem':
        return f"Получены монеты: {coins(result['credit'])}" + suffix
    if result['kind'] == 'buy':
        return (f"Торговец: «Хороший выбор!»\n\n{compact(result['name'], 256)} × 1\n"
                f"Списано: {coins(result['debit'])}\nСдача: {coins(result['credit'])}\n"
                f"Итоговая стоимость: {exact_text(Fraction(result['value']))} {compact(result.get('base_name', 'базовой валюты'), 80)}.\n"
                f"Операция №{result['trade_id']}") + suffix
    return (f"Торговец: «По рукам!»\n\n{compact(result['name'], 256)} × 1\n"
            f"Получено: {coins(result['credit'])}\nОперация №{result['trade_id']}") + suffix


def actor(user, message):
    return user.id, user.username, message.chat.id, get_thread_id(message)


async def edit(message, text, keyboard):
    try:
        await message.edit_text(compact(text, 4000), reply_markup=keyboard, parse_mode=None)
    except TelegramBadRequest as error:
        if 'message is not modified' not in str(error).lower():
            raise


@router.message(Command('shop'))
async def shop_handler(message: Message):
    if not message.from_user:
        return
    try:
        shop = Shop()
        session = await asyncio.to_thread(shop.open, *actor(message.from_user, message))
        view = await asyncio.to_thread(shop.view, session, actor(message.from_user, message))
        text, keyboard = render(session, view)
        await message.answer(text, reply_markup=keyboard, parse_mode=None)
    except (ShopError, CatalogError, MoneyError) as error:
        await message.answer(str(error), parse_mode=None)
    except sqlite3.Error:
        logger.exception('Ordinary D&D shop database failure')
        await message.answer('Ошибка базы магазина. Сделка не выполнена; повторите позже.')


@router.callback_query(F.data.startswith('ds:'))
async def shop_callback(query: CallbackQuery):
    if not isinstance(query.message, Message):
        await query.answer('Сообщение магазина недоступно.', show_alert=True)
        return
    try:
        shop = Shop()
        parts = query.data.split(':')
        if len(parts) != 5:
            raise ShopError('Некорректная кнопка магазина.')
        _, session, action, argument, page_text = parts
        page = int(page_text)
        if not 0 <= page <= 2**63 - 1:
            raise ShopError('Некорректная страница.')
        who = actor(query.from_user, query.message)
        actions = {'h': 'home', 'b': 'buy', 's': 'sell', 'e': 'exit', 'v': 'buy_card', 'c': 'sell_card', 'q': 'credit'}
        if action in ('x', 'r'):
            try:
                result = await asyncio.to_thread(shop.execute if action == 'x' else shop.redeem, session, argument, who)
            except PriceChanged as error:
                view = await asyncio.to_thread(shop.view, session, who, 'refresh', argument)
                text, keyboard = render(session, view, page)
                text = str(error) + '\n\n' + text
            else:
                back = 'b' if result['kind'] == 'buy' else 's'
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text='◀️ Назад', callback_data=callback(session, back, str(page), page))]])
                if Fraction(result.get('credit_balance', 0)):
                    keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
                        text='💱 Обменять остаток', callback_data=callback(session, 'q'))])
                text = result_text(result)
        else:
            if action not in actions:
                raise ShopError('Неизвестная кнопка.')
            view = await asyncio.to_thread(shop.view, session, who, actions[action], argument)
            text, keyboard = render(session, view, page)
        await edit(query.message, text, keyboard)
        await query.answer()
    except (ShopError, CatalogError, MoneyError, ValueError) as error:
        # Alert preserves the previous screen and its Back button.
        await query.answer(str(error)[:200], show_alert=True)
    except sqlite3.Error:
        logger.exception('Ordinary D&D shop database failure')
        await query.answer('Ошибка базы. Повторное нажатие безопасно.', show_alert=True)


@router.message(Command('shopcreate', 'stopshop'))
async def shop_admin_handler(message: Message):
    if not message.from_user:
        return
    try:
        shop = Shop()
        who = actor(message.from_user, message)
        shop.scope(*who[2:])
        if not await check_topic_admin_permission(message):
            return
        parts = (message.text or '').split()
        command = parts[0].split('@')[0] if parts else ''
        if command == '/shopcreate':
            if len(parts) != 2 or parts[1] not in ('-b', '-m'):
                await message.answer('Использование: /shopcreate -b (универсальные цены) или /shopcreate -m (цены мастера). Ничего не изменено.')
                return
            result = await asyncio.to_thread(shop.create, who, parts[1][1:])
            mode = 'базовом режиме' if result['pricing_mode'] == 'b' else 'режиме мастера'
            prefix = 'Магазин уже существует; каталоги проверены.' if result['already'] else f'Магазин создан в {mode}.'
            await message.answer(
                f"{prefix}\nРежим: {mode}.\nБазовая валюта: {result['base_name']}.\n"
                f"Зарегистрировано предметов: {result['registered']}; доступно к покупке: {result['buying']}.\n"
                'Состояние: включён.', parse_mode=None)
        else:
            if len(parts) != 1:
                await message.answer('Использование: /stopshop в текущей обычной теме.')
                return
            result = await asyncio.to_thread(shop.stop, who)
            await message.answer('Магазин уже остановлен.' if result['already'] else 'Магазин остановлен.')
    except (ShopError, CatalogError, MoneyError, OSError) as error:
        await message.answer(str(error), parse_mode=None)
    except sqlite3.Error:
        logger.exception('Ordinary D&D shop lifecycle database failure')
        await message.answer('Ошибка базы магазина. Создание не завершено; торговля не включена.')
