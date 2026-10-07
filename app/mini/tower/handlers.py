import json
from aiogram import F,Router
from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
from app.topic_guard import is_mini_topic
from app.mini.ui.context import shop_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.tower.service import get_state,list_heroes,start_attempt,attack
from app.mini.combat.matchups import faction_multiplier_percent
from app.mini.presentation import faction_label,tag_label,hero_trait_lines,TRAIT_LABELS,FEATURE_DESCRIPTIONS

router=Router(name="mini_tower")


def button(text,world,user,tail=''):
    data=personal_callback('tower',world,user)
    return InlineKeyboardButton(text=text,callback_data=data+(':'+tail if tail else ''))


def enemy_lines(enemy):
    lines = [f"Этаж {enemy['floor']}/200 · {enemy['name']}", f"HP: {enemy['max_hp']}",
             f"Фракция: {faction_label(enemy['faction'])}"]
    lines += hero_trait_lines(enemy)
    lines.append('Ответный ход: сначала подготовка, затем удар по щиту.'
                 if enemy.get('response') == 'prepare' else 'Ответный ход: удар по щиту.')
    labels = [tag_label(trait,TRAIT_LABELS) for trait in enemy['features']]
    lines.append('Особенности: '+(', '.join(labels) or 'нет'))
    for trait in enemy['features']:
        if trait == 'demonic':
            lines.append('Святой герой может ослепить демонического противника.')
        elif trait in FEATURE_DESCRIPTIONS:
            lines.append(FEATURE_DESCRIPTIONS[trait])
    return lines


def render_state(state,world,user,reward=None):
    rows=[];lines=['🏰 Испытания',f"Пройдено: {state['highest_cleared']}/200"]
    attempt=state['attempt']
    if attempt:
        hero=json.loads(attempt['hero_json']);enemy=json.loads(attempt['enemy_json'])
        lines+=enemy_lines(enemy)
        lines += [f"HP сейчас: {attempt['current_hp']}/{enemy['max_hp']}",
                  f"Герой: {hero['name']} ★{hero['stars']} · ATK {hero['attack']}",
                  f"Equipment: +{attempt['equipment_bonus']} ATK · до matchup: {hero['attack']+attempt['equipment_bonus']}",
                  f"Фракция героя: {hero['faction']} · matchup ×{faction_multiplier_percent(hero['faction'],enemy['faction'])/100:g}",
                  f"🛡 Щиты: {attempt['shields']}/3"]
        lines += [e.get('message','') for e in json.loads(attempt['events_json'])]
        if attempt['status']=='active':
            rows.append([button('⚔️ Атаковать',world,user,f"hit.{attempt['id']}.{attempt['turn']}")])
        elif attempt['status']=='lost':
            lines.append('💀 Щиты закончились. Выбери героя для новой попытки.')
            rows.append([button('🔄 Повторить другим героем',world,user,f"heroes.{attempt['floor']}.0")])
        else:
            lines.append('🏆 Этаж пройден!')
            if state['completed']: lines.append('🏁 Все 200 этажей пройдены!')
            else: rows.append([button('➡️ Следующий этаж',world,user)])
    elif state['completed']:
        lines.append('🏁 Все 200 этажей пройдены!')
    else:
        lines+=enemy_lines(state['floor'])
        lines += [f"Equipment: +{state['equipment_bonus']} ATK",'🛡 Новая попытка: 3/3',
                  'Выбери героя под фракцию и особенности противника.']
        rows.append([button('🎴 Выбрать героя',world,user,f"heroes.{state['floor']['floor']}.0")])
    if reward:
        lines.append(f"💎 Награда: {reward['shards']} осколков")
        if reward['equipment']:
            e=reward['equipment'];lines.append(f"🎁 Сундук: {e['name']} (+{e['attack_bonus']} ATK)")
    rows.append([InlineKeyboardButton(text='🛡 Экипировка',callback_data=personal_callback('equipment',world,user))])
    rows.append([InlineKeyboardButton(text='⬅️ Mini',callback_data=personal_callback('home',world,user))])
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
            page=max(0,int(parts[2]));heroes=list_heroes(player['id']);page=min(page,max(0,(len(heroes)-1)//8))
            rows=[];lines=['🎴 Выбор героя']+enemy_lines(state['floor'])+[f"Equipment +{state['equipment_bonus']} ATK · 🛡 3/3"]
            for h in heroes[page*8:(page+1)*8]:
                m=faction_multiplier_percent(h['faction'],state['floor']['faction'])/100
                lines.append(f"{h['name']} · {faction_label(h['faction'])} · ATK {h['attack']} +{state['equipment_bonus']} · ×{m:g}")
                lines += hero_trait_lines(h)
                rows.append([button(h['name'],w,u,f"pick.{floor}.{h['id']}")])
            nav=[]
            if page: nav.append(button('⬅️',w,u,f'heroes.{floor}.{page-1}'))
            if (page+1)*8<len(heroes): nav.append(button('➡️',w,u,f'heroes.{floor}.{page+1}'))
            if nav: rows.append(nav)
            if not heroes: lines.append('Героев пока нет. Открой гачу в коллекции.')
            rows.append([button('⬅️ Испытания',w,u)])
            text='\n'.join(lines);markup=InlineKeyboardMarkup(inline_keyboard=rows)
        elif parts[0]=='pick' and len(parts)==3:
            hero=next((h for h in list_heroes(player['id']) if h['id']==int(parts[2])),None)
            if not hero: raise ValueError('Этого героя нет в коллекции.')
            text='\n'.join(enemy_lines(state['floor'])+[f"Герой: {hero['name']} ★{hero['stars']}",
                f"ATK: {hero['attack']} + Equipment {state['equipment_bonus']}",hero.get('passive_text',''),'🛡 Начало: 3/3'])
            markup=InlineKeyboardMarkup(inline_keyboard=[
                [button('▶️ Начать бой',w,u,f"start.{floor}.{hero['id']}")],
                [button('🎴 Другой герой',w,u,f'heroes.{floor}.0')]])
        else:
            if parts[0]=='start' and len(parts)==3:
                state['attempt']=start_attempt(player['id'],int(parts[2]),floor)
            elif parts[0]=='hit' and len(parts)==3:
                result=attack(player['id'],int(parts[1]),int(parts[2]))
                state=get_state(player['id']);state['attempt']=result['attempt'];reward=result['reward']
            elif tail:
                raise ValueError('Некорректная кнопка.')
            text,markup=render_state(state,w,u,reward)
    except (ValueError,IndexError) as error:
        await callback.answer(str(error) or 'Некорректная кнопка.',show_alert=True);return
    await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
