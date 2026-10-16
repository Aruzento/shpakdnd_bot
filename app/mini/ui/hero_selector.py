from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as Button
from app.mini.editorial.registry import LiveMapping, LiveSequence
"""Compact shared favorite/all/filter views. State lives in bounded callbacks."""
from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup
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
NAMES=LiveSequence(['heroes.filter.rarity', 'heroes.filter.faction', 'heroes.filter.class', 'heroes.filter.damage', 'heroes.filter.range', 'heroes.filter.trait'])
LABELS=(LiveMapping({key:"rarity."+key for key in ('common', 'uncommon', 'rare', 'legendary', 'mythic', 'shadow')}, terms=True),
        FACTION_LABELS,CLASS_LABELS,DAMAGE_LABELS,RANGE_LABELS,TRAIT_LABELS)
DIGITS='0123456789abcdefghijklmnopqrstuvwxyz'
EMPTY='0000000'


@editorial_screen
def options():
    return [sorted({h.get(field,'none') for h in load_hero_catalog()['heroes']}) for field in FIELDS]


@editorial_screen
def decode(token, mode):
    if len(token)!=7 or any(c not in DIGITS for c in token): raise ValueError(editorial_text('errors.ui.hero_selector.decode.01'))
    opts=options();result={}
    for n,field in enumerate(FIELDS):
        value=DIGITS.index(token[n])
        if value>len(opts[n]): raise ValueError(editorial_text('errors.ui.hero_selector.decode.03'))
        if value: result[field]=opts[n][value-1]
    if token[6] not in ('0','1') or (mode!='c' and token[6]!='0'): raise ValueError(editorial_text('errors.ui.hero_selector.decode.02'))
    return result,token[6]=='1'


@editorial_screen
def filtered(heroes, token=EMPTY, mode='c'):
    fields,upgrade=decode(token,mode)
    return [h for h in heroes if
            all(h.get(k)==v for k,v in fields.items()) and
            (not upgrade or hero_upgrade_state(h)['can_upgrade'])]


@editorial_screen
def hero_label(hero, *, active_marker=False):
    marker=editorial_text('hero_selector.hero_label.line_01') if active_marker and int(hero.get("is_active",0)) else ""
    return f"{rarity_emoji(hero.get('rarity'))} {hero['name'][:27]} ★{int(hero.get('stars',0))}{marker}"


@editorial_screen
def choice(mode,world,user,context,hero_id):
    if mode=='c': return f'mini:hero:{world}:{user}:{hero_id}'
    if mode=='b': return f'miniboss:hero:{world}:{user}:{context}:{hero_id}'
    return f'mini:tower:{world}:{user}:pick.{context}.{hero_id}'


@editorial_screen
def render_selector(heroes, favorites, mode, world, user, context=0, *, view='home', page=0, token=EMPTY, return_token=''):
    if view=='favorites':view='home'
    if mode not in ('c','b','t'): raise ValueError(editorial_text('errors.ui.hero_selector.render_selector.01'))
    available=filtered(heroes,token,mode)
    def nav(action, target=0, state=token):
        data=f'mini:select:{world}:{user}:{mode}{context}.{action}.{target}.{state}'
        if len(data.encode())>64: raise ValueError(editorial_text('errors.ui.hero_selector.nav.01'))
        return data
    def button(label,action,target=0,state=token): return Button(text=label,callback_data=nav(action,target,state))
    def hero_button(h): return Button(text=hero_label(h,active_marker=mode=="c"),callback_data=choice(mode,world,user,context,h['id'])+('.'+return_token if mode=='c' and return_token else ''))
    rows=[]
    if view=='collection' and mode=='c':
        text=editorial_text('navigation.collection')
        rows=[[button(editorial_text('hero_selector.render_selector.line_04'),'home')],
              [button(editorial_text('hero_selector.render_selector.line_05'),'all',state=EMPTY),button(editorial_text('hero_selector.render_selector.line_06'),'filter')]]
    elif view=='home':
        text=editorial_text('hero_selector.render_selector.line_02')
        by_id={h['id']:h for h in heroes}
        selected=[by_id[h] for h in favorites if h in by_id][:3]
        rows=[[hero_button(h)] for h in selected]
        if not selected: text+=editorial_text('hero_selector.render_selector.line_03')
        if mode=='c':
            rows=[sum(rows[n:n+2],[]) for n in range(0,len(rows),2)]
            rows.append([button(editorial_text('hero_selector.render_selector.line_14'),'all',state=EMPTY),button(editorial_text('hero_selector.render_selector.line_15'),'filter')])
        else:
            rows += [[button(editorial_text('hero_selector.render_selector.line_16'),'all',state=EMPTY)], [button(editorial_text('hero_selector.render_selector.line_17'),'filter')]]
    elif view in ('all','results'):
        size=4 if mode=='c' else PAGE_SIZE
        pages=max(1,(len(available)+size-1)//size);page=min(max(0,int(page)),pages-1)
        text=editorial_text('hero_selector.render_selector.line_07') if view=='all' else editorial_text('hero_selector.render_selector.line_08')
        if not available: text+=editorial_text('hero_selector.render_selector.line_09')
        batch=available[page*size:(page+1)*size]
        rows=[[hero_button(h) for h in batch[n:n+2]] for n in range(0,len(batch),2)]
        pagination=[]
        if page or mode=='c': pagination.append(button('◀️',view,max(0,page-1)))
        pagination.append(button(f'{page+1} / {pages}',view,page))
        if page+1<pages or mode=='c': pagination.append(button('▶️',view,min(pages-1,page+1)))
        rows.append(pagination)
        if mode!='c':rows.append([button(editorial_text('hero_selector.render_selector.line_19'),'filter')])
    elif view=='filter':
        chosen,upgrade=decode(token,mode);text=editorial_text('hero_selector.render_selector.line_10')
        for n,field in enumerate(FIELDS):
            label=tag_label(chosen[field],LABELS[n]) if field in chosen else editorial_text('hero_selector.render_selector.line_20') if n in (0,1,4) else editorial_text('hero_selector.render_selector.line_21')
            rows.append([button(f'{(("Редкость","Фракция","Класс","Урон","Дальность","Свойство") if mode=="c" else NAMES)[n]}: {label}',f'f{n}')])
        if mode=='c':
            rows=[sum(rows[n:n+3],[]) for n in range(0,len(rows),3)]
            rows.append([button(editorial_text('hero_selector.render_selector.line_28') if upgrade else editorial_text('hero_selector.render_selector.line_29'), 'upgrade')])
        rows += [[button(editorial_text('hero_selector.render_selector.line_22'),'results'),button(editorial_text('hero_selector.render_selector.line_23'),'reset')]] if mode=='c' else [[button(editorial_text('hero_selector.render_selector.line_24'),'results')],[button(editorial_text('hero_selector.render_selector.line_25'),'reset')]]
    elif view.startswith('f') and view[1:].isdigit():
        n=int(view[1:]);opts=options()
        if not 0<=n<6: raise ValueError(editorial_text('errors.ui.hero_selector.render_selector.03'))
        text=editorial_text('hero_selector.render_selector.line_12', value_1=f'{NAMES[n]}')
        rows=[[button(editorial_text('hero_selector.render_selector.line_26'),f's{n}0')]]
        rows += [[button(tag_label(value,LABELS[n]),f's{n}{DIGITS[i+1]}')] for i,value in enumerate(opts[n])]
        rows.append([button(editorial_text('hero_selector.render_selector.line_27'),'filter')])
    else: raise ValueError(editorial_text('errors.ui.hero_selector.render_selector.02'))
    if view!='home' and mode!='c' or mode=='c' and view.startswith('f') and view!='filter':rows.append([button(editorial_text('hero_selector.render_selector.line_11'),'home',state=EMPTY)])
    back=(nav('filter' if view=='results' else 'collection') if mode=='c' else
          personal_callback('boss',world,user) if mode=='b' else personal_callback('tower',world,user))
    rows.append(([Button(text=editorial_text('navigation.home'),callback_data=personal_callback('home',world,user))] if mode=='c' else [])+[Button(text=editorial_text('navigation.back'),callback_data=personal_callback('heroarea',world,user) if mode=='c' and view=='collection' else back)])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:select:'))
@editorial_screen
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
                raise ValueError(editorial_text('errors.ui.hero_selector.selector_callback.01'))
        elif mode=='t':
            from app.mini.tower.service import get_state
            state=get_state(player['id'])
            if state['completed'] or state['attempt'] or state['floor']['floor']!=value:
                raise ValueError(editorial_text('errors.ui.hero_selector.selector_callback.04'))
        elif mode!='c' or value!=0: raise ValueError(editorial_text('errors.ui.hero_selector.selector_callback.05'))
        decode(token,mode)
        if view=='reset': token=EMPTY;view='filter'
        if view=='upgrade': token=token[:6]+('0' if token[6]=='1' else '1');view='filter'
        if view.startswith('s'):
            if len(view)!=3:raise ValueError(editorial_text('errors.ui.hero_selector.selector_callback.02'))
            n=int(view[1]);v=view[2]
            if n not in range(6) or v not in DIGITS or DIGITS.index(v)>len(options()[n]): raise ValueError(editorial_text('errors.ui.hero_selector.selector_callback.03'))
            token=token[:n]+v+token[n+1:];view='filter'
        heroes=get_player_heroes(player['id'],sync_catalog=False)
        if mode in ('b','t'):
            from app.mini.availability import filter_heroes
            heroes=filter_heroes(heroes,player['id'])
        return_token=''
        if mode=='c' and view in ('home','all','results','collection'):
            from app.mini.ui.state import remember
            size=4;pages=max(1,(len(filtered(heroes,token,mode))+size-1)//size)
            page=max(0,min(page,pages-1))
            return_token=remember(player['id'],'collection',{'view':view,'page':page,'filter':token})
        text,markup=render_selector(heroes,get_favorites(player['id']),mode,world['id'],callback.from_user.id,value,view=view,page=page,token=token,return_token=return_token)
        if mode=='t':
            from app.mini.tower.handlers import enemy_lines
            text='\n'.join(enemy_lines(state['floor'],selection=True))+'\n\n'+text
        await callback.answer()
        await send_private_text_from_callback(callback,world,text,markup)
    except (ValueError,IndexError) as error:
        await callback.answer(str(error) or editorial_text('hero_selector.selector_callback.line_01'),show_alert=True)
