from aiogram import F,Router
from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from app.topic_guard import is_mini_topic
from app.mini.ui.context import shop_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.equipment.service import get_equipment,equip,unequip
from app.mini.equipment.catalog import SLOTS

router=Router(name='mini_equipment')
LABELS={'helmet':'Шлем','ring':'Кольцо','cloak':'Плащ'}


def render_equipment(state,world,user,page=0,mode='home'):
    base=personal_callback('equipment',world,user)
    def btn(text,tail): return InlineKeyboardButton(text=text,callback_data=base+(':'+tail if tail else ''))
    rows=[]
    if mode=='home':
        lines=['🛡 Экипировка','','Надетая экипировка даёт бонусы в «Испытаниях».','']
        for slot in SLOTS:
            item=state['equipped'].get(slot)
            lines.append(f"{LABELS[slot]}: {item['name']} +{item['attack_bonus']}" if item else f"{LABELS[slot]}: пусто")
        lines += ['','Ненадетые предметы можно посмотреть в «Инвентаре».']
        rows=[[btn('Надеть...','choose_equip.0')],[btn('Снять...','choose_remove.0')],
              [InlineKeyboardButton(text='Назад',callback_data=personal_callback('home',world,user))]]
    elif mode=='choose_remove':
        lines=['🛡 Снять экипировку','','Выбери слот. Снятый предмет останется в инвентаре.']
        for slot in SLOTS:
            item=state['equipped'].get(slot)
            if item: rows.append([btn(f"{LABELS[slot]}: {item['name']} +{item['attack_bonus']}",f'unequip.{slot}')])
        if not rows: lines.append('Все слоты пусты.')
        rows.append([btn('Назад','')])
    else:
        owned=state['owned']
        if mode=='choose_equip':
            owned=[e for e in owned if state['equipped'].get(e['slot'],{}).get('code')!=e['code']]
        page=min(max(0,page),max(0,(len(owned)-1)//8))
        lines=['🛡 Надеть экипировку' if mode=='choose_equip' else '🎒 Экипировка в инвентаре','']
        for e in owned[page*8:(page+1)*8]:
            label=f"{e['name']} · {LABELS[e['slot']]} · +{e['attack_bonus']}"
            if mode=='choose_equip': rows.append([btn(label,f"equip.{e['code']}")])
            else:
                selected=state['equipped'].get(e['slot'],{}).get('code')==e['code']
                lines.append(f"{'✅' if selected else '•'} {label} · ×{e['quantity']}")
        if not owned: lines.append('Предметов пока нет. Сундук с экипировкой ждёт на каждом пятом этаже Испытаний.')
        nav=[]
        if page: nav.append(btn('⬅️',f'{mode}.{page-1}'))
        if (page+1)*8<len(owned): nav.append(btn('➡️',f'{mode}.{page+1}'))
        if nav: rows.append(nav)
        if mode=='inventory':
            rows.append([btn('Надеть...','choose_equip.0')])
            rows.append([InlineKeyboardButton(text='Назад',callback_data=personal_callback('inventory',world,user))])
        else: rows.append([btn('Назад','')])
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:equipment:'))
async def equipment_callback(callback):
    context=await shop_context(callback,'equipment')
    if context is None: return
    world,player,tail=context
    if not is_mini_topic(world['chat_id'],world['thread_id']):
        await callback.answer('Эта тема больше не работает в режиме Mini.',show_alert=True);return
    page=0;mode='home'
    try:
        if tail:
            action,value=tail.split('.',1)
            if action=='equip': equip(player['id'],value)
            elif action=='unequip': unequip(player['id'],value)
            elif action in {'page','choose_equip','choose_remove','inventory'}:
                page=max(0,int(value));mode='choose_equip' if action=='page' else action
            else: raise ValueError('Некорректная кнопка.')
        text,markup=render_equipment(get_equipment(player['id']),world['id'],callback.from_user.id,page,mode)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
