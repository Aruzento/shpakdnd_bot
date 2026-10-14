"""Batch/coupon presentation; economic writes only happen in existing services."""
from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as Button
import secrets
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from app.mini.ui.context import load_extended_context,load_personal_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.gacha import perform_gacha_batch,get_gacha_batch,get_gacha_state
from app.mini.ui.hero_cards import show_gacha_pull_result
from app.mini.presentation import rarity_emoji
from app.mini.pity import format_progress


router=Router(name='mini_summons')


@editorial_screen
def batch_screen(batch,world,user,page=0):
    results=batch['results'];page=max(0,min(int(page),len(results)-1));bid=batch['batch_id']
    def b(text,action,tail=''):
        return Button(text=text,callback_data=personal_callback(action,world,user)+(':'+tail if tail else ''))
    lines=[editorial_text('summons.batch_screen.line_01'),'']
    for n,r in enumerate(results,1):
        outcome=editorial_text('summons.batch_screen.line_04', shards_awarded=f"{r['shards_awarded']}") if r['is_duplicate'] else editorial_text('summons.batch_screen.line_05')
        lines.append(f"{n}. {rarity_emoji(r['rarity'])} {r['name']} — {outcome}")
    lines += ['',editorial_text('summons.batch_screen.line_02', cost_coins=f"{batch['cost_coins']}", balance=f"{batch['balance']}"),
              editorial_text('summons.batch_screen.line_03', shards_awarded=f"{batch['shards_awarded']}"),format_progress(batch['pity'])]
    r=results[page]
    rows=[[b(editorial_text('summons.batch_screen.line_06', value_1=f"{rarity_emoji(r['rarity'])}", name=f"{r['name']}"),'batchhero',f'{bid}.{page}')]]
    nav=[]
    if page:nav.append(b('◀️','batchpage',f'{bid}.{page-1}'))
    nav.append(b(f'{page+1}/{len(results)}','batchpage',f'{bid}.{page}'))
    if page+1<len(results):nav.append(b('▶️','batchpage',f'{bid}.{page+1}'))
    rows += [nav,[b(editorial_text('summons.batch_screen.line_07'),'gachabatch',secrets.token_hex(4))],
             [b(editorial_text('navigation.home'),'home'),b(editorial_text('navigation.collection'),'collection'),b(editorial_text('navigation.back'),'gacha')]]
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:gachabatch:'))
@editorial_screen
async def batch_callback(callback):
    context=await load_extended_context(callback,'gachabatch')
    if context is None:return
    world,player,token=context
    try:
        if len(token) not in (8,12) or any(c not in '0123456789abcdef' for c in token):raise ValueError(editorial_text('errors.ui.summons.batch_callback.01'))
        batch=perform_gacha_batch(player['id'],operation_key='batch:'+token)
        text,markup=batch_screen(batch,world['id'],callback.from_user.id)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    try:
        await send_private_text_from_callback(callback,world,text,markup)
    except TelegramAPIError:
        await callback.answer(editorial_text('summons.batch_callback.line_01'),show_alert=True)


@router.callback_query(F.data.regexp(r'^mini:(batchpage|batchhero|batchview):'))
@editorial_screen
async def batch_result_callback(callback):
    prefix=callback.data.split(':')[1]
    context=await load_extended_context(callback,prefix)
    if context is None:return
    world,player,payload=context
    try:
        if prefix=='batchview':
            action,payload=payload.split('.',1)
            if action not in ('page','card'):raise ValueError(editorial_text('errors.ui.summons.batch_result_callback.02'))
            prefix='batchhero' if action=='card' else 'batchpage'
        bid,page=map(int,payload.split('.'));batch=get_gacha_batch(player['id'],bid)
        if not 0<=page<10:raise ValueError(editorial_text('errors.ui.summons.batch_result_callback.01'))
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
        await callback.answer(editorial_text('summons.batch_result_callback.line_01'),show_alert=True)


@editorial_screen
def coupon_screen(state,world,user,chosen=None):
    coupons=state.get('coupons',[]);rows=[]
    def b(text,action,tail=None):
        return Button(text=text,callback_data=personal_callback(action,world,user)+(':'+tail if tail is not None else ''))
    for coupon in coupons:
        rows.append([b(f"{coupon['name']} ×{coupon['quantity']}",'couponpick',str(coupon['item_id']))])
    selected=next((c for c in coupons if c['item_id']==chosen),None)
    if selected:
        # The same inventory confirm handler as useitems; no second coupon economy.
        rows.append([b(editorial_text('summons.coupon_screen.line_05'),'useconfirm',f"item,{chosen},{secrets.token_hex(4)}")])
    rows.append([b(editorial_text('navigation.home'),'home'),b(editorial_text('navigation.back'),'gacha')])
    text=editorial_text('summons.coupon_screen.line_01') if coupons else editorial_text('summons.coupon_screen.line_02')
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:gachacoupon:'))
@router.callback_query(F.data.startswith('mini:couponpick:'))
@editorial_screen
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
        await callback.answer(editorial_text('summons.coupon_callback.line_01'),show_alert=True)
