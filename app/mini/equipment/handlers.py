from aiogram import F,Router
from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from app.topic_guard import is_mini_topic
from app.mini.ui.context import shop_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.equipment.service import get_equipment,equip,unequip
from app.mini.equipment.catalog import SLOTS

router=Router(name='mini_equipment')
LABELS={'helmet':'Шлем','ring':'Кольцо','cloak':'Плащ'}


def render_equipment(state,world,user,page=0):
    base=personal_callback('equipment',world,user)
    def btn(text,tail): return InlineKeyboardButton(text=text,callback_data=base+':'+tail)
    lines=['🛡 Экипировка',f"Суммарный бонус Tower: +{state['attack_bonus']} ATK",'Изменения применятся в новой попытке Tower.']
    rows=[]
    for slot in SLOTS:
        item=state['equipped'].get(slot)
        lines.append(f"{LABELS[slot]}: {item['name']} +{item['attack_bonus']}" if item else f"{LABELS[slot]}: пусто")
        if item: rows.append([btn('Снять: '+LABELS[slot],f'unequip.{slot}')])
    owned=state['owned'];page=min(max(0,page),max(0,(len(owned)-1)//8))
    lines.append('Во владении:')
    for e in owned[page*8:(page+1)*8]:
        selected=state['equipped'].get(e['slot'],{}).get('code')==e['code']
        lines.append(f"{'✅' if selected else '•'} {e['name']} · {LABELS[e['slot']]} · +{e['attack_bonus']} · ×{e['quantity']}")
        if not selected: rows.append([btn('Надеть: '+e['name'],f"equip.{e['code']}")])
    if not owned: lines.append('Сундук с экипировкой: каждый пятый этаж Испытаний.')
    nav=[]
    if page: nav.append(btn('⬅️',f'page.{page-1}'))
    if (page+1)*8<len(owned): nav.append(btn('➡️',f'page.{page+1}'))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton(text='🏰 Испытания',callback_data=personal_callback('tower',world,user))])
    rows.append([InlineKeyboardButton(text='⬅️ Mini',callback_data=personal_callback('home',world,user))])
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:equipment:'))
async def equipment_callback(callback):
    context=await shop_context(callback,'equipment')
    if context is None: return
    world,player,tail=context
    if not is_mini_topic(world['chat_id'],world['thread_id']):
        await callback.answer('Эта тема больше не работает в режиме Mini.',show_alert=True);return
    page=0
    try:
        if tail:
            action,value=tail.split('.',1)
            if action=='equip': equip(player['id'],value)
            elif action=='unequip': unequip(player['id'],value)
            elif action=='page': page=max(0,int(value))
            else: raise ValueError('Некорректная кнопка.')
        text,markup=render_equipment(get_equipment(player['id']),world['id'],callback.from_user.id,page)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
