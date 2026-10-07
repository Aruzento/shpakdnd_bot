"""Compact shared favorite/all/filter views. State lives in bounded callbacks."""
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.catalog import load_hero_catalog
from app.mini.favorites import get_favorites
from app.mini.heroes import get_player_heroes
from app.mini.hero_upgrades import hero_upgrade_state
from app.mini.presentation import (rarity_emoji, FACTION_LABELS, CLASS_LABELS,
    DAMAGE_LABELS, RANGE_LABELS, TRAIT_LABELS, tag_label)
from app.mini.ui.context import load_extended_context, personal_callback
from app.mini.ui.transport import send_private_text_from_callback

router=Router(name='mini_hero_selector')
PAGE_SIZE=6
FIELDS=('rarity','faction','class_tag','damage_type','attack_range','special_trait')
NAMES=('Редкость','Фракция','Класс','Тип урона','Дальность','Особое свойство')
LABELS=({'common':'Обычный','uncommon':'Необычный','rare':'Редкий','legendary':'Легендарный'},
        FACTION_LABELS,CLASS_LABELS,DAMAGE_LABELS,RANGE_LABELS,TRAIT_LABELS)
DIGITS='0123456789abcdefghijklmnopqrstuvwxyz'
EMPTY='0000000'


def options():
    return [sorted({h.get(field,'none') for h in load_hero_catalog()['heroes']}) for field in FIELDS]


def decode(token, mode):
    if len(token)!=7 or any(c not in DIGITS for c in token): raise ValueError('Фильтр устарел.')
    opts=options();result={}
    for n,field in enumerate(FIELDS):
        value=DIGITS.index(token[n])
        if value>len(opts[n]): raise ValueError('Фильтр устарел.')
        if value: result[field]=opts[n][value-1]
    if token[6] not in ('0','1') or (mode!='c' and token[6]!='0'): raise ValueError('Некорректный фильтр.')
    return result,token[6]=='1'


def filtered(heroes, token=EMPTY, mode='c'):
    fields,upgrade=decode(token,mode)
    return [h for h in heroes if h.get('active',1) and
            all(h.get(k)==v for k,v in fields.items()) and
            (not upgrade or hero_upgrade_state(h)['can_upgrade'])]


def hero_label(hero, *, active_marker=False):
    marker=" ✅" if active_marker and int(hero.get("is_active",0)) else ""
    return f"{rarity_emoji(hero.get('rarity'))} {hero['name'][:27]} ★{int(hero.get('stars',0))}{marker}"


def choice(mode,world,user,context,hero_id):
    if mode=='c': return f'mini:hero:{world}:{user}:{hero_id}'
    if mode=='b': return f'miniboss:hero:{world}:{user}:{context}:{hero_id}'
    return f'mini:tower:{world}:{user}:pick.{context}.{hero_id}'


def render_selector(heroes, favorites, mode, world, user, context=0, *, view='home', page=0, token=EMPTY):
    if mode not in ('c','b','t'): raise ValueError('Некорректный выбор героя.')
    available=filtered(heroes,token,mode)
    def nav(action, target=0, state=token):
        data=f'mini:select:{world}:{user}:{mode}{context}.{action}.{target}.{state}'
        if len(data.encode())>64: raise ValueError('Некорректный размер кнопки.')
        return data
    def button(label,action,target=0,state=token): return Button(text=label,callback_data=nav(action,target,state))
    def hero_button(h): return Button(text=hero_label(h,active_marker=mode=="c"),callback_data=choice(mode,world,user,context,h['id']))
    rows=[]
    if view=='home':
        text='⭐ Избранные'
        by_id={h['id']:h for h in heroes if h.get('active',1)}
        selected=[by_id[h] for h in favorites if h in by_id][:3]
        rows=[[hero_button(h)] for h in selected]
        if not selected: text+='\nПока никого нет.'
        rows += [[button('👥 Показать всех','all',state=EMPTY)], [button('🔎 Поиск по фильтру','filter')]]
    elif view in ('all','results'):
        pages=max(1,(len(available)+PAGE_SIZE-1)//PAGE_SIZE);page=min(max(0,int(page)),pages-1)
        text='👥 Все герои' if view=='all' else '🔎 Результаты подбора'
        if not available: text+='\nПодходящих героев нет. Измени или сбрось фильтр.'
        batch=available[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
        rows=[[hero_button(h) for h in batch[n:n+2]] for n in range(0,len(batch),2)]
        pagination=[]
        if page: pagination.append(button('◀️',view,page-1))
        pagination.append(button(f'{page+1} / {pages}',view,page))
        if page+1<pages: pagination.append(button('▶️',view,page+1))
        rows += [pagination,[button('🔎 Изменить фильтр','filter')]]
    elif view=='filter':
        chosen,upgrade=decode(token,mode);text='🔎 Поиск по фильтру\nВыбери свойства подходящего героя.'
        for n,field in enumerate(FIELDS):
            label=tag_label(chosen[field],LABELS[n]) if field in chosen else 'Любая' if n in (0,1,4) else 'Любой'
            rows.append([button(f'{NAMES[n]}: {label}',f'f{n}')])
        if mode=='c': rows.append([button('✅ Можно улучшить' if upgrade else '⬆️ Можно улучшить: нет', 'upgrade')])
        rows += [[button('🔎 Посмотреть результаты','results')],[button('🔄 Сбросить','reset')]]
    elif view.startswith('f') and view[1:].isdigit():
        n=int(view[1:]);opts=options()
        if not 0<=n<6: raise ValueError('Некорректный фильтр.')
        text=f'🔎 {NAMES[n]}'
        rows=[[button('Любое',f's{n}0')]]
        rows += [[button(tag_label(value,LABELS[n]),f's{n}{DIGITS[i+1]}')] for i,value in enumerate(opts[n])]
        rows.append([button('⬅️ К фильтрам','filter')])
    else: raise ValueError('Некорректная кнопка.')
    if view!='home': rows.append([button('⬅️ Избранные','home',state=EMPTY)])
    back=(personal_callback('heroarea',world,user) if mode=='c' else
          personal_callback('boss',world,user) if mode=='b' else personal_callback('tower',world,user))
    rows.append([Button(text='⬅️ Назад',callback_data=back)])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:select:'))
async def selector_callback(callback):
    context=await load_extended_context(callback,'select')
    if context is None: return
    world,player,tail=context
    try:
        route,view,page,token=tail.split('.')
        mode=route[0];value=int(route[1:]);page=int(page)
        if mode=='b':
            from app.mini.boss.service import get_boss,list_participants
            boss=get_boss(value)
            if not boss or boss['world_id']!=world['id'] or boss['status'] not in ('announced','ready') or not any(p['player_id']==player['id'] for p in list_participants(value)):
                raise ValueError('Выбор героя для этого босса устарел.')
        elif mode=='t':
            from app.mini.tower.service import get_state
            state=get_state(player['id'])
            if state['completed'] or state['attempt'] or state['floor']['floor']!=value:
                raise ValueError('Кнопка выбора героя устарела. Открой текущий бой.')
        elif mode!='c' or value!=0: raise ValueError('Некорректный выбор героя.')
        decode(token,mode)
        if view=='reset': token=EMPTY;view='filter'
        if view=='upgrade': token=token[:6]+('0' if token[6]=='1' else '1');view='filter'
        if view.startswith('s'):
            n=int(view[1]);v=view[2]
            if n not in range(6) or v not in DIGITS or DIGITS.index(v)>len(options()[n]): raise ValueError('Фильтр устарел.')
            token=token[:n]+v+token[n+1:];view='filter'
        heroes=get_player_heroes(player['id'],sync_catalog=False)
        text,markup=render_selector(heroes,get_favorites(player['id']),mode,world['id'],callback.from_user.id,value,view=view,page=page,token=token)
        if mode=='t':
            from app.mini.tower.handlers import enemy_lines
            text='\n'.join(enemy_lines(state['floor'],selection=True))+'\n\n'+text
        await callback.answer()
        await send_private_text_from_callback(callback,world,text,markup)
    except (ValueError,IndexError) as error:
        await callback.answer(str(error) or 'Некорректная кнопка.',show_alert=True)
