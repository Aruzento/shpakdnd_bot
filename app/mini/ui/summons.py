"""Batch/coupon presentation; economic writes only happen in existing services."""
import secrets
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.ui.context import load_extended_context,load_personal_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.gacha import perform_gacha_batch,get_gacha_batch,get_gacha_state
from app.mini.ui.hero_cards import show_gacha_pull_result
from app.mini.presentation import rarity_emoji
from app.mini.pity import format_progress

router=Router(name='mini_summons')


def batch_screen(batch,world,user,page=0):
    results=batch['results'];page=max(0,min(int(page),len(results)-1));bid=batch['batch_id']
    def b(text,action,tail=''):
        return Button(text=text,callback_data=personal_callback(action,world,user)+(':'+tail if tail else ''))
    lines=['✨ Призыв ×10','']
    for n,r in enumerate(results,1):
        outcome=f"дубликат +{r['shards_awarded']} 🧩" if r['is_duplicate'] else 'новый'
        lines.append(f"{n}. {rarity_emoji(r['rarity'])} {r['name']} — {outcome}")
    lines += ['',f"🪙 Потрачено: {batch['cost_coins']} · Баланс: {batch['balance']}",
              f"🧩 Получено осколков: {batch['shards_awarded']}",format_progress(batch['pity'])]
    r=results[page]
    rows=[[b(f"🎴 {rarity_emoji(r['rarity'])} {r['name']}",'batchhero',f'{bid}.{page}')]]
    nav=[]
    if page:nav.append(b('◀️','batchpage',f'{bid}.{page-1}'))
    nav.append(b(f'{page+1}/{len(results)}','batchpage',f'{bid}.{page}'))
    if page+1<len(results):nav.append(b('▶️','batchpage',f'{bid}.{page+1}'))
    rows += [nav,[b('✨ Ещё 10','gachabatch',secrets.token_hex(4))],
             [b('🏠 Главная','home'),b('📚 Коллекция','collection'),b('⬅️ Назад','gacha')]]
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:gachabatch:'))
async def batch_callback(callback):
    context=await load_extended_context(callback,'gachabatch')
    if context is None:return
    world,player,token=context
    try:
        if len(token)!=8 or any(c not in '0123456789abcdef' for c in token):raise ValueError('Некорректная кнопка.')
        batch=perform_gacha_batch(player['id'],operation_key='batch:'+token)
        text,markup=batch_screen(batch,world['id'],callback.from_user.id)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    try:
        await send_private_text_from_callback(callback,world,text,markup)
    except TelegramAPIError:
        await callback.answer('Результат сохранён. Повтори эту кнопку или открой меню ещё раз.',show_alert=True)


@router.callback_query(F.data.regexp(r'^mini:(batchpage|batchhero):'))
async def batch_result_callback(callback):
    prefix=callback.data.split(':')[1]
    context=await load_extended_context(callback,prefix)
    if context is None:return
    world,player,payload=context
    try:
        bid,page=map(int,payload.split('.'));batch=get_gacha_batch(player['id'],bid)
        if not 0<=page<10:raise ValueError('Некорректная страница.')
        if prefix=='batchhero':
            await show_gacha_pull_result(callback,world,player,batch['results'][page],
                return_callback=f"mini:batchpage:{world['id']}:{callback.from_user.id}:{bid}.{page}");return
        text,markup=batch_screen(batch,world['id'],callback.from_user.id,page)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    try:
        await send_private_text_from_callback(callback,world,text,markup)
    except TelegramAPIError:
        await callback.answer('Результат сохранён. Повтори эту кнопку или открой меню ещё раз.',show_alert=True)


def coupon_screen(state,world,user,chosen=None):
    coupons=state.get('coupons',[]);rows=[]
    def b(text,action,tail=None):
        return Button(text=text,callback_data=personal_callback(action,world,user)+(':'+tail if tail is not None else ''))
    for coupon in coupons:
        rows.append([b(f"{coupon['name']} ×{coupon['quantity']}",'couponpick',str(coupon['item_id']))])
    selected=next((c for c in coupons if c['item_id']==chosen),None)
    if selected:
        # The same inventory confirm handler as useitems; no second coupon economy.
        rows.append([b('✅ Применить купон','useconfirm',f"item,{chosen},{secrets.token_hex(4)}")])
    rows.append([b('🏠 Главная','home'),b('⬅️ Назад','gacha')])
    text='🎟 Купоны призыва\n\nВыбери купон, затем подтверди применение.' if coupons else '🎟 Купонов пока нет.'
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:gachacoupon:'))
@router.callback_query(F.data.startswith('mini:couponpick:'))
async def coupon_callback(callback):
    picked=callback.data.startswith('mini:couponpick:')
    context=await load_extended_context(callback,'couponpick') if picked else await load_personal_context(callback)
    if context is None:return
    world,player=context[:2]
    try:
        chosen=int(context[2]) if picked else None
        text,markup=coupon_screen(get_gacha_state(player['id']),world['id'],callback.from_user.id,chosen)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    try:
        await send_private_text_from_callback(callback,world,text,markup)
    except TelegramAPIError:
        await callback.answer('Не удалось показать купоны. Открой «Призвать героя» ещё раз.',show_alert=True)
