"""Telegram screens for ordinary D&D only."""
import asyncio
import logging
import sqlite3
from fractions import Fraction

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.context import get_thread_id
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


def render(session, view, page=0):
    def button(text, action, argument='0', back_page=page):
        return InlineKeyboardButton(text=text, callback_data=callback(session, action, argument, back_page))

    screen = view['screen']
    if screen == 'exit':
        return 'Магазин закрыт.', None
    if screen == 'home':
        return 'Торговец: «Чего желаешь, путник?»', InlineKeyboardMarkup(inline_keyboard=[
            [button('🛒 Купить', 'b')], [button('💰 Продать', 's')], [button('🚪 Выйти', 'e')]])
    if screen in ('buy', 'sell'):
        page = view['page']
        buttons = [button(name, 'v' if screen == 'buy' else 'c', argument, page) for name, argument in view['buttons']]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        action = 'b' if screen == 'buy' else 's'
        rows.append([button('◀️', action, str(max(0, page - 1)), page),
                     button('▶️', action, str(min(view['pages'] - 1, page + 1)), page)])
        rows.append([button('Назад', 'h')])
        title = '🛒 Товары' if screen == 'buy' else '💰 Ваши предметы'
        empty = '\nЗдесь пока пусто.' if not buttons else ''
        return f"{title}\nСтраница {page + 1}/{view['pages']}{empty}", InlineKeyboardMarkup(inline_keyboard=rows)
    product = view['product']
    price = exact_text(product.price)
    base_name = view.get('base_name', 'базовой валюты')
    if screen == 'buy_card':
        text = (f'{product.name}\n\nОписание: {product.description}\n\n'
                f"Стоимость: {price} {base_name}.\n\nВ наличии: {view['available']} шт.")
        rows = [[button('🛒 Купить', 'x', view['token'])], [button('◀️ Назад', 'b', str(page))]]
    else:
        text = (f"Торговец: «Могу предложить вот столько».\n\n{view['name']}\n\n"
                f"В инвентаре: {view['quantity']} шт.\n\nЦена в магазине: {price} {base_name}.\n\n"
                f"Предложение торговца: {exact_text(view['payout'])} {base_name} за 1 шт.")
        rows = [[button('✅ Подтвердить продажу', 'x', view['token'])], [button('◀️ Назад', 's', str(page))]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def result_text(result):
    def coins(rows):
        return ', '.join(f'{name}: {quantity}' for name, quantity in rows) or 'нет'
    if result['kind'] == 'buy':
        return (f"Торговец: «Хороший выбор!»\n\n{result['name']} × 1\n"
                f"Списано: {coins(result['debit'])}\nСдача: {coins(result['credit'])}\n"
                f"Итоговая стоимость: {exact_text(Fraction(result['value']))} {result.get('base_name', 'базовой валюты')}.\n"
                f"Операция №{result['trade_id']}")
    return (f"Торговец: «По рукам!»\n\n{result['name']} × 1\n"
            f"Получено: {coins(result['credit'])}\nОперация №{result['trade_id']}")


def actor(user, message):
    return user.id, user.username, message.chat.id, get_thread_id(message)


async def edit(message, text, keyboard):
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode=None)
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
        text, keyboard = render(session, {'screen': 'home'})
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
        actions = {'h': 'home', 'b': 'buy', 's': 'sell', 'e': 'exit', 'v': 'buy_card', 'c': 'sell_card'}
        if action == 'x':
            try:
                result = await asyncio.to_thread(shop.execute, session, argument, who)
            except PriceChanged as error:
                view = await asyncio.to_thread(shop.view, session, who, 'refresh', argument)
                text, keyboard = render(session, view, page)
                text = str(error) + '\n\n' + text
            else:
                back = 'b' if result['kind'] == 'buy' else 's'
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text='◀️ Назад', callback_data=callback(session, back, str(page), page))]])
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
