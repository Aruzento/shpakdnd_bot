"""Batch/coupon UI delegates all economic operations to existing services."""
import secrets
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.gacha import perform_gacha_batch,get_gacha_batch,GachaError
from app.mini.shop import get_player_goods
from app.mini.items import get_inventory_item
from app.mini.ui.context import personal_callback,shop_context
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.ui.hero_cards import show_gacha_pull_result
from app.mini.pity import format_progress

router=Router(name='mini_gacha_extra')
COUPONS=('rarity_coupon_uncommon','rarity_coupon_rare','rarity_coupon_legendary')


def batch_menu(result,world,user,page=0):
    page=max(0,min(9,int(page)));bid=result['batch_id']
    def b(text,tail):return Button(text=text,callback_data=f'mini:batchview:{world}:{user}:{tail}')
    hero=result['results'][page]
    nav=([b('◀️',f'page.{bid}.{page-1}')] if page else [])+[b(f'{page+1}/10',f'page.{bid}.{page}')]+([b('▶️',f'page.{bid}.{page+1}')] if page<9 else [])
    rows=[[b('🎴 '+hero['name'],f'card.{bid}.{page}')],nav,
          [Button(text='✨ Ещё 10',style='success',callback_data=f'mini:gachabatch:{world}:{user}:{secrets.token_hex(6)}')],
          [Button(text='🏠 Главная',callback_data=personal_callback('home',world,user)),Button(text='📚 Коллекция',callback_data=personal_callback('collection',world,user)),Button(text='⬅️ Назад',callback_data=personal_callback('gacha',world,user))]]
    lines=['✨ Десять призывов',f"🪙 Потрачено: {result['cost_coins']} • Баланс: {result['balance']}",f"🧩 Получено осколков: {result['shards_awarded']}",'']
    lines += [f"{i+1}. {h['name']} — {h['rarity']}"+(' • дубликат' if h['is_duplicate'] else ' • новый') for i,h in enumerate(result['results'])]
    lines += ['',format_progress(result['pity'])]
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


def coupon_menu(goods,world,user):
    items=sorted((i for i in goods.get('inventory',[]) if i['code'] in COUPONS and i['quantity']>0),key=lambda i:COUPONS.index(i['code']))
    rows=[[Button(text=f"{i['name']} ×{i['quantity']}",callback_data=f"mini:couponpick:{world}:{user}:{i['item_id']}")] for i in items]
    rows.append([Button(text='🏠 Главная',callback_data=personal_callback('home',world,user)),Button(text='⬅️ Назад',callback_data=personal_callback('gacha',world,user))])
    return '🎟 Выбери купон целевой редкости.' if items else '🎟 Купонов пока нет.',InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:gachabatch:'))
async def batch_callback(callback):
    context=await shop_context(callback,'gachabatch')
    if not context:return
    world,player,token=context
    try:
        if len(token)!=12 or any(c not in '0123456789abcdef' for c in token):raise GachaError('Кнопка призыва устарела.')
        result=perform_gacha_batch(player['id'],operation_key='batch:'+token)
        text,markup=batch_menu(result,world['id'],callback.from_user.id)
    except GachaError as error:await callback.answer(str(error),show_alert=True);return
    await callback.answer('Десять призывов сохранены')
    await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:batchview:'))
async def batch_view(callback):
    context=await shop_context(callback,'batchview')
    if not context:return
    world,player,tail=context
    try:
        action,bid,page=tail.split('.');page=int(page)
        if not 0<=page<10 or action not in ('page','card'):raise ValueError('Некорректная страница.')
        result=get_gacha_batch(player['id'],int(bid))
        if action=='card':
            pull=dict(result['results'][page],back_callback=f"mini:batchview:{world['id']}:{callback.from_user.id}:page.{bid}.{page}")
            await show_gacha_pull_result(callback,world,player,pull);return
        text,markup=batch_menu(result,world['id'],callback.from_user.id,page)
    except (ValueError,IndexError) as error:await callback.answer(str(error),show_alert=True);return
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:gachacoupon:'))
async def coupon_callback(callback):
    context=await shop_context(callback,'gachacoupon')
    if not context:return
    world,player,_=context
    text,markup=coupon_menu(get_player_goods(player['id']),world['id'],callback.from_user.id)
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:couponpick:'))
async def coupon_pick(callback):
    context=await shop_context(callback,'couponpick')
    if not context:return
    world,player,tail=context
    try:
        item=get_inventory_item(player['id'],int(tail))
        if not item or item['code'] not in COUPONS:raise ValueError('Купона больше нет.')
    except ValueError as error:await callback.answer(str(error),show_alert=True);return
    token=secrets.token_hex(4);w,u=world['id'],callback.from_user.id
    markup=InlineKeyboardMarkup(inline_keyboard=[[Button(text='✅ Применить купон',style='success',callback_data=f"mini:useconfirm:{w}:{u}:item,{item['item_id']},{token}")],
        [Button(text='🏠 Главная',callback_data=personal_callback('home',w,u)),Button(text='⬅️ Назад',callback_data=personal_callback('gachacoupon',w,u))]])
    await callback.answer();await send_private_text_from_callback(callback,world,item['name']+'\n\n'+item.get('description',''),markup)
