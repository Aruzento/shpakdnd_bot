from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
from app.mini.editorial.registry import LiveMapping, text as editorial_text
from aiogram import F,Router
from aiogram.types import InlineKeyboardMarkup
from app.topic_guard import is_mini_topic
from app.mini.ui.context import shop_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.equipment.service import get_equipment,equip,unequip
from app.mini.equipment.catalog import SLOTS


router=Router(name='mini_equipment')
LABELS=LiveMapping({key: "slot."+key for key in SLOTS}, terms=True)

def equipment_label(item, slot=None):
    return editorial_text("equipment.item", bonus=f"{int(item['attack_bonus']):+d}",
                name=item['name'], slot=LABELS[item.get('slot', slot)])


@editorial_screen
def render_equipment(state,world,user,page=0,mode='home',parent='heroarea'):
    base=personal_callback('equipment',world,user)
    def btn(text,tail): return InlineKeyboardButton(text=text,callback_data=base+(':'+tail if tail else ':home.0')+('~t' if parent=='tower' else ''))
    rows=[]
    if mode=='home':
        lines=[editorial_text('navigation.equipment'),'',editorial_text('equipment.render_equipment.line_02'),'']
        for slot in SLOTS:
            item=state['equipped'].get(slot)
            lines.append(equipment_label(item, slot) if item else editorial_text('equipment.render_equipment.line_06', value_1=f'{LABELS[slot]}'))
        lines += ['',editorial_text('equipment.render_equipment.line_03')]
        rows=[[btn(editorial_text('equipment.equip'),'choose_equip.0')],[btn(editorial_text('equipment.unequip'),'choose_remove.0')],
              [InlineKeyboardButton(text=editorial_text('general.back'),callback_data=personal_callback(parent,world,user))]]
    elif mode=='choose_remove':
        lines=[editorial_text('equipment.render_equipment.line_04'),'',editorial_text('equipment.render_equipment.line_05')]
        for slot in SLOTS:
            item=state['equipped'].get(slot)
            if item: rows.append([btn(equipment_label(item, slot),f'unequip.{slot}')])
        if not rows: lines.append(editorial_text('equipment.render_equipment.line_09'))
        rows.append([btn(editorial_text('general.back'),'')])
    else:
        owned=state['owned']
        if mode=='choose_equip':
            owned=[e for e in owned if state['equipped'].get(e['slot'],{}).get('code')!=e['code']]
        page=min(max(0,page),max(0,(len(owned)-1)//8))
        lines=[editorial_text('equipment.render_equipment.line_10') if mode=='choose_equip' else editorial_text('equipment.render_equipment.line_11'),'']
        for e in owned[page*8:(page+1)*8]:
            label=equipment_label(e)
            if mode=='choose_equip': rows.append([btn(label,f"equip.{e['code']}")])
            else:
                selected=state['equipped'].get(e['slot'],{}).get('code')==e['code']
                lines.append(f"{'✅' if selected else '•'} {label} · ×{e['quantity']}")
        if not owned: lines.append(editorial_text('equipment.render_equipment.line_12'))
        nav=[]
        if page: nav.append(btn(editorial_text('equipment.render_equipment.line_15'),f'{mode}.{page-1}'))
        if (page+1)*8<len(owned): nav.append(btn(editorial_text('equipment.render_equipment.line_16'),f'{mode}.{page+1}'))
        if nav: rows.append(nav)
        if mode=='inventory':
            rows.append([btn(editorial_text('equipment.equip'),'choose_equip.0')])
            rows.append([InlineKeyboardButton(text=editorial_text('general.back'),callback_data=personal_callback('inventory',world,user))])
        else: rows.append([btn(editorial_text('general.back'),'')])
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:equipment:'))
@editorial_screen
async def equipment_callback(callback):
    context=await shop_context(callback,'equipment')
    if context is None: return
    world,player,tail=context
    if not is_mini_topic(world['chat_id'],world['thread_id']):
        await callback.answer(editorial_text('equipment.equipment_callback.line_01'),show_alert=True);return
    page=0;mode='home';parent='heroarea'
    try:
        if tail=='tower':tail='';parent='tower'
        if '~' in tail:
            tail,origin=tail.split('~',1)
            if origin!='t':raise ValueError(editorial_text('errors.equipment.handlers.equipment_callback.01'))
            parent='tower'
        if tail:
            action,value=tail.split('.',1)
            if action=='equip': equip(player['id'],value)
            elif action=='unequip': unequip(player['id'],value)
            elif action in {'home','page','choose_equip','choose_remove','inventory'}:
                page=max(0,int(value));mode='choose_equip' if action=='page' else action
            else: raise ValueError(editorial_text('errors.equipment.handlers.equipment_callback.02'))
        text,markup=render_equipment(get_equipment(player['id']),world['id'],callback.from_user.id,page,mode,parent)
    except ValueError as error:
        await callback.answer(str(error),show_alert=True);return
    await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
