import json
from aiogram import F,Router
from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from app.topic_guard import is_mini_topic
from app.mini.ui.context import shop_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.tower.service import get_state,list_heroes,start_selected_attempt,select_hero,attack
from app.mini.combat.matchups import faction_multiplier_percent
from app.mini.presentation import faction_name,tag_label,hero_trait_lines,TRAIT_LABELS
from app.mini.favorites import get_favorites
from app.mini.ui.hero_selector import render_selector,hero_label
from app.mini.tower.catalog import load_catalog
TOTAL_FLOORS=len(load_catalog()["floors"])

router=Router(name="mini_tower")


def button(text,world,user,tail=''):
    data=personal_callback('tower',world,user)
    return InlineKeyboardButton(text=text,callback_data=data+(':'+tail if tail else ''))


def enemy_lines(enemy, *, selection=False):
    heading=f"{enemy['name']} {'·' if selection else '●'} {faction_name(enemy['faction'])}"
    lines=[heading,'']
    if not selection:
        lines += [f"HP: {enemy['max_hp']}",'']
    lines += hero_trait_lines(enemy)
    labels=[tag_label(trait,TRAIT_LABELS) for trait in enemy['features']]
    lines += ['', 'Особенности: '+(', '.join(labels) or 'нет')]
    return lines


def hero_button_text(hero, bonus):
    extra=f' +{bonus}' if bonus else ''
    return f"{hero['name']} · {faction_name(hero['faction'])} · ATK {hero['attack']}{extra}"


def render_hero_selection(state,heroes,world,user,page=0,favorites=None):
    text,markup=render_selector(heroes,favorites or [],'t',world,user,state['floor']['floor'])
    return '\n'.join(['🎴 Выбор героя','']+enemy_lines(state['floor'],selection=True))+'\n\n'+text,markup


def render_state(state,world,user,reward=None):
    rows=[];attempt=state['attempt']
    enemy=json.loads(attempt['enemy_json']) if attempt else state['floor']
    floor=enemy['floor'] if enemy else TOTAL_FLOORS
    lines=[f'🏰 Испытания: {floor}/{TOTAL_FLOORS}','']
    if enemy:
        lines+=enemy_lines(enemy)
    if attempt:
        hero=json.loads(attempt['hero_json'])
        lines += ['',f"HP сейчас: {attempt['current_hp']}/{enemy['max_hp']}",
                  f"Герой: {hero_button_text(hero,attempt['equipment_bonus'])}",
                  f"Урон по фракции: ×{faction_multiplier_percent(hero['faction'],enemy['faction'])/100:g}",
                  f"🛡 Щиты: {attempt['shields']}/3"]
        lines += [e.get('message','') for e in json.loads(attempt['events_json'])]
        if attempt['status']=='active':
            rows.append([button('⚔️ Атаковать',world,user,f"hit.{attempt['id']}.{attempt['turn']}")])
        elif attempt['status']=='lost':
            lines.append('💀 Щиты закончились. Можно повторить бой или сменить героя.')
            rows.append([button('🔄 Повторить бой',world,user)])
            rows.append([button('🔄 Сменить героя',world,user,f"heroes.{attempt['floor']}.0")])
        else:
            lines.append('🏆 Этаж пройден!')
            if state['completed']: lines.append('🏁 Все 200 этажей пройдены!')
            else: rows.append([button('➡️ Следующий этаж',world,user)])
    elif state['completed']:
        lines.append('🏁 Все 200 этажей пройдены!')
    else:
        lines += ['',f"Бонус экипировки: +{state['equipment_bonus']} к атаке",'🛡 Новая попытка: 3/3',
                  'Выбери героя под фракцию и особенности противника.']
        if enemy.get('response')=='prepare':
            lines.append('Противник сначала готовит атаку, затем бьёт по щиту.')
        selected=state.get('selected_hero')
        if selected:
            lines += ['', 'Текущий герой:', hero_label(selected)]
            rows.append([button('⚔️ Начать бой',world,user,f"start.{enemy['floor']}.{selected['id']}")])
            rows.append([button('🔄 Сменить героя',world,user,f"heroes.{enemy['floor']}.0")])
        else:
            rows.append([button('🎴 Выбрать героя',world,user,f"heroes.{enemy['floor']}.0")])
    if reward:
        lines.append(f"💎 Награда: {reward['shards']} осколков")
        if reward['equipment']:
            e=reward['equipment'];lines.append(f"🎁 Сундук: {e['name']} +{e['attack_bonus']}")
    rows.append([InlineKeyboardButton(text='🛡 Экипировка',callback_data=personal_callback('equipment',world,user))])
    rows.append([InlineKeyboardButton(text='Назад',callback_data=personal_callback('adventures',world,user))])
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:tower:'))
async def tower_callback(callback):
    context=await shop_context(callback,'tower')
    if context is None: return
    world,player,tail=context
    if not is_mini_topic(world['chat_id'],world['thread_id']):
        await callback.answer('Эта тема больше не работает в режиме Mini.',show_alert=True);return
    w,u=world['id'],callback.from_user.id
    try:
        state=get_state(player['id']);reward=None
        parts=tail.split('.')
        if parts[0] in {'heroes','pick','start'}:
            floor=int(parts[1])
            if state['completed'] or floor!=state['floor']['floor'] or state['attempt']:
                raise ValueError('Кнопка выбора героя устарела. Открой текущий бой.')
        if parts[0]=='heroes' and len(parts)==3:
            text,markup=render_hero_selection(state,list_heroes(player['id']),w,u,int(parts[2]),get_favorites(player["id"]))
        elif parts[0]=='pick' and len(parts)==3:
            select_hero(player['id'],int(parts[2]),expected_floor=floor)
            state=get_state(player['id'])
            text,markup=render_state(state,w,u)
        else:
            if parts[0]=='start' and len(parts)==3:
                state['attempt']=start_selected_attempt(player['id'],floor,int(parts[2]))
            elif parts[0]=='hit' and len(parts)==3:
                result=attack(player['id'],int(parts[1]),int(parts[2]))
                state=get_state(player['id']);state['attempt']=result['attempt'];reward=result['reward']
            elif tail:
                raise ValueError('Некорректная кнопка.')
            if not state.get('selected_hero') and not state['attempt'] and not state['completed']:
                text,markup=render_hero_selection(state,list_heroes(player['id']),w,u,favorites=get_favorites(player['id']))
            else:
                text,markup=render_state(state,w,u,reward)
    except (ValueError,IndexError) as error:
        await callback.answer(str(error) or 'Некорректная кнопка.',show_alert=True);return
    await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
