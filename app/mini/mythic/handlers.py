from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.ui.context import load_personal_context,load_extended_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.mythic import service

router=Router(name='mini_mythic')


def screen(player_id,world_id,user_id,page=0):
    heroes=service.list_heroes(player_id);size=6;pages=max(1,(len(heroes)+size-1)//size)
    page=max(0,min(page,pages-1));lines=['✨ Мифические герои'];rows=[]
    for hero in heroes[page*size:(page+1)*size]:
        lines.append(f"\n{hero['name']}\nФрагменты {hero['fragments']}/{hero['fragment_cost']}"+(' • Получен' if hero['owned'] else ''))
        if hero.get('id') and not hero['owned'] and hero['fragments']>=hero['fragment_cost']:
            rows.append([Button(text='Создать: '+hero['name'],callback_data=f'mini:myth:{world_id}:{user_id}:craft.{hero["id"]}')])
    if not heroes:lines.append('\nМифические герои пока не добавлены.')
    nav=[]
    if page:nav.append(Button(text='←',callback_data=f'mini:myth:{world_id}:{user_id}:page.{page-1}'))
    if page+1<pages:nav.append(Button(text='→',callback_data=f'mini:myth:{world_id}:{user_id}:page.{page+1}'))
    if nav:rows.append(nav)
    rows.append([Button(text='Назад',callback_data=personal_callback('heroarea',world_id,user_id))])
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:mythic:'))
async def home(callback):
    context=await load_personal_context(callback)
    if not context:return
    world,player=context;text,markup=screen(player['id'],world['id'],callback.from_user.id)
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:myth:'))
async def action(callback):
    context=await load_extended_context(callback,'myth')
    if not context:return
    world,player,payload=context
    try:
        action,value=payload.split('.',1);page=0
        if action=='craft':service.craft_by_id(player['id'],int(value))
        elif action=='page':page=int(value)
        else:raise ValueError('Неизвестное действие.')
        text,markup=screen(player['id'],world['id'],callback.from_user.id,page)
    except ValueError as error:await callback.answer(str(error),show_alert=True);return
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)
