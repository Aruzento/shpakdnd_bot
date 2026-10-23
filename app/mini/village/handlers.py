from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as Button
from app.mini.editorial.registry import LiveMapping
"""Personal ephemeral Village screens; mutation callbacks carry a state revision."""
import secrets
from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup
from app.mini.ui.context import load_personal_context, load_extended_context, personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.ui.callbacks import number, integer
from app.mini.heroes import get_player_heroes
from app.mini.village import service


router=Router(name='mini_village')
PAGE_SIZE=4
ACTIONS={'buy':'b','settle':'s','evict':'e','assign':'a','collect':'c'}
JOBS={'-':'-','mine':'m','market':'r','hunt':'h'}
LABELS = LiveMapping({None: 'job.none', 'mine': 'job.mine', 'market': 'job.market', 'hunt': 'job.hunt'}, terms=True, decorated=True)
VIEWS={'home':'o','residents':'r','add':'s','hero':'c',
       'mine':'m','market':'k','hunt':'h',
       'houses':'b','evict':'e','add_mine':'am','add_market':'ak','add_hunt':'ah',
       'workers_mine':'wm','workers_market':'wk','workers_hunt':'wh'}


@editorial_screen
def screen(state,world_id,user_id,view='home',page=0,hero_id=None):
    def b(text,payload): return Button(text=text,callback_data=f'mini:vlg:{world_id}:{user_id}:{payload}')
    rev=state['revision'];token=secrets.token_hex(4)
    def mutation(text,action,hid=0,building='-',target=None):
        target=target or view
        return b(text,f'x.{number(rev)}.{ACTIONS[action]}.{number(hid)}.{JOBS[building]}.{state["houses"]}.{token}.{VIEWS[target]}{page}')
    rows=[]
    if view=='home':
        h=state['hourly'];remaining=state['seconds_left']
        stopped=editorial_text('village.screen.line_01') if not state['fed'] else ''
        text=(editorial_text('village.screen.line_02', houses=f"{state['houses']}", value_2=f"{len(state['residents'])}", capacity=f"{state['capacity']}", hunt=f"{h['hunt']}", consumption=f"{h['consumption']}", mine=f"{h['mine']}", stopped=f'{stopped}', market=f"{h['market']}", stopped_9=f'{stopped}', hunt_10=f"{h['hunt']}", coins=f"{state['coins']:.3f}", shards=f"{state['shards']:.3f}", value_13=f'{remaining // 3600}', value_14=f'{remaining % 3600 // 60}') if remaining else
            editorial_text('village.screen.line_03', houses=f"{state['houses']}", value_2=f"{len(state['residents'])}", capacity=f"{state['capacity']}", hunt=f"{h['hunt']}", consumption=f"{h['consumption']}", mine=f"{h['mine']}", stopped=f'{stopped}', market=f"{h['market']}", stopped_9=f'{stopped}', hunt_10=f"{h['hunt']}", coins=f"{state['coins']:.3f}", shards=f"{state['shards']:.3f}"))
        for resource,end in state.get('boosts',{}).items():
            label=editorial_text('village.screen.line_06') if resource=='coins' else editorial_text('village.screen.line_07')
            text+=editorial_text('village.screen.line_04', label=f'{label}', value_2=f'{(end - service.timestamp()) // 3600}')
        rows=[[b(editorial_text('village.screen.line_09'),'houses.0'),mutation(editorial_text('village.screen.line_10'),'collect')],
              [b(editorial_text('village.screen.line_11'),'mine.0'),b(editorial_text('village.screen.line_12'),'market.0'),b(editorial_text('village.screen.line_13'),'hunt.0')]]
        rows[0][1].style='success'
    elif view=='houses':
        text=editorial_text('village.screen.line_05', houses=f"{state['houses']}", value_2=f"{len(state['residents'])}", capacity=f"{state['capacity']}")
        if state['next_price']:
            rows.append([mutation(editorial_text('village.screen.line_24', next_price=f"{state['next_price']}"),'buy')])
        actions=[]
        if len(state['residents'])<state['capacity'] and state.get('unsettled_count',1)>0:actions.append(b(editorial_text('village.screen.line_17'),'add.0'))
        if state['residents']:actions.append(b(editorial_text('village.screen.line_18'),'evict.0'))
        if actions:rows.append(actions)
        rows.append([b(editorial_text('village.screen.line_19'),'residents.0')])
    elif view=='hero':
        hero=next((h for h in state['residents'] if h['hero_id']==hero_id),None)
        if not hero: raise ValueError(editorial_text('errors.village.handlers.screen.02'))
        text=editorial_text('village.screen.line_08', name=f"{hero['name']}", value_2=f"{LABELS[hero['building']]}")
        rows=[[mutation(editorial_text('village.screen.line_25'),'assign',hero_id)], [mutation(editorial_text('village.screen.line_26'),'evict',hero_id,target='residents')]]
    elif view in service.BUILDINGS:
        from app.mini.editorial import glossary
        workers=[h for h in state['residents'] if h['building']==view]
        start=max(0,int(page))*8
        text=LABELS[view]+'\n\n'+f"Работников: {len(workers)}\n"
        text+='\n'.join(f"{glossary('rarity.'+h['rarity'])+' · ' if h.get('rarity') else ''}{h['name']} — +{h.get('hourly_income',0)}/час" for h in workers[start:start+8])
        text+=f"\n\nИтого: +{state['hourly'][view]}/час"
        if not state['fed'] and view in ('mine','market'):text+='\nПроизводство остановлено: недостаточно еды.'
        if start:rows.append([b('⬅️',f'{view}.{page-1}')])
        if start+8<len(workers):rows.append([b('➡️',f'{view}.{page+1}')])
        rows.append([b(editorial_text('village.screen.line_31'),f'add_{view}.0'),b(editorial_text('village.screen.line_32'),f'workers_{view}.0')])
    else:
        if view=='add':
            owned=get_player_heroes(state['player_id'],sync_catalog=False)
            resident_ids={h['hero_id'] for h in state['residents']}
            heroes=[h for h in owned if h['id'] not in resident_ids]
            text=editorial_text('village.screen.line_21')
        elif view=='evict':
            heroes=state['residents'];text=editorial_text('village.screen.line_27')
        elif view=='residents':
            heroes=state['residents']
            text=editorial_text('village.screen.line_33')
        else:
            mode,building=view.split('_',1)
            if building not in service.BUILDINGS or mode not in ('add','workers'):
                raise ValueError(editorial_text('errors.village.handlers.screen.03'))
            heroes=[h for h in state['residents'] if (h['building'] is None if mode=='add' else h['building']==building)]
            text=LABELS[building]+(editorial_text('village.screen.line_36') if mode=='add' else editorial_text('village.screen.line_37'))
        if view=='add' and len(state['residents'])>=state['capacity']:heroes=[]
        size=PAGE_SIZE if view=='residents' else 8
        pages=max(1,(len(heroes)+size-1)//size);page=max(0,min(page,pages-1))
        buttons=[]
        for hero in heroes[page*size:(page+1)*size]:
            hero=dict(hero)
            from app.mini.editorial import glossary
            hero['name']=(glossary('rarity.'+hero['rarity'])+' · ' if hero.get('rarity') else '')+hero['name']
            hid=hero.get('hero_id',hero.get('id'))
            if view=='add': button=mutation(hero['name'],'settle',hid)
            elif view=='evict':button=mutation(editorial_text('village.screen.line_38')+hero['name'],'evict',hid)
            elif view=='residents': button=b(hero['name']+' • '+LABELS[hero['building']],f'hero.{hid}.{page}.{number(rev)}')
            else: button=mutation(hero['name']+' • '+LABELS[hero['building']],'assign',hid,building if mode=='add' else '-')
            buttons.append(button)
        columns=2 if view=='residents' else 1
        rows.extend([buttons[i:i+columns] for i in range(0,len(buttons),columns)])
        if not heroes: text+=editorial_text('village.screen.line_28') if view.startswith('add_') else editorial_text('village.screen.line_29')

        nav=[]
        if page: nav.append(b(editorial_text('village.screen.line_34'),f'{view}.{page-1}'))
        nav.append(b(f'{page+1} / {pages}',f'{view}.{page}'))
        if page+1<pages: nav.append(b(editorial_text('village.screen.line_35'),f'{view}.{page+1}'))
        if nav: rows.append(nav)
        text+=editorial_text('village.screen.line_14', value_1=f'{page + 1}', pages=f'{pages}')
    if view=='home':rows.append([Button(text='✨ Мифический зверь',callback_data=personal_callback('mythic',world_id,user_id))])
    if view=='hero':rows.append([b(editorial_text('general.back'),f'residents.{page}')])
    elif view in service.BUILDINGS or '_' in view:
        back=view.split('_',1)[1]+'.0' if '_' in view else 'home'
        rows.append([b(editorial_text('navigation.back'),back)])
    else:
        parent='houses.0' if view in ('add','evict','residents') else 'home'
        back=Button(text=editorial_text('navigation.back'),callback_data=personal_callback('fair',world_id,user_id)) if view=='home' else b(editorial_text('navigation.back'),parent)
        rows.append([Button(text=editorial_text('navigation.home'),callback_data=personal_callback('home',world_id,user_id)),back])
    if any(len(button.callback_data.encode())>64 for row in rows for button in row):raise ValueError(editorial_text('errors.village.handlers.screen.01'))
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:village:'))
@editorial_screen
async def home(callback):
    context=await load_personal_context(callback)
    if not context:return
    world,player=context
    text,markup=screen(service.get_state(player['id']),world['id'],callback.from_user.id)
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:vlg:'))
@editorial_screen
async def action(callback):
    context=await load_extended_context(callback,'vlg')
    if not context:return
    world,player,payload=context
    try:
        parts=payload.split('.');view=parts[0];page=0;hid=None
        if view=='x':
            _,rev,act,hid,building,houses,token=parts[:7]
            act={v:k for k,v in ACTIONS.items()}[act]
            building={v:k for k,v in JOBS.items()}[building]
            if len(parts)>7:
                import re
                match=re.fullmatch(r'([a-z]+)(\d+)',parts[7])
                if not match:raise ValueError(editorial_text('errors.village.handlers.action.03'))
                view={v:k for k,v in VIEWS.items()}[match[1]];page=int(match[2]);hid=integer(hid) or None
            else:
                view='home'
                if act=='assign' and building!='-':raise ValueError(editorial_text('errors.village.handlers.action.04'))
            if act=='assign' and building!='-' and view!='add_'+building:raise ValueError(editorial_text('errors.village.handlers.action.01'))
            result=service.mutate(player['id'],act,hero_id=integer(parts[3]) or None,building=None if building=='-' else building,
                expected_revision=integer(rev),expected_houses=int(houses),operation_key=payload)
            notice=editorial_text('village.action.line_01', coins=f"{result['coins']}", shards=f"{result['shards']}") if act=='collect' else result.get('status',editorial_text('village.action.line_02'))
            await callback.answer(notice)
        else:
            if view=='hero':
                hid=int(parts[1]);page=int(parts[2]) if len(parts)>2 else 0
                if len(parts)>3 and integer(parts[3])!=service.get_state(player['id'])['revision']:
                    raise ValueError(editorial_text('errors.village.handlers.action.05'))
            elif view!='home':page=int(parts[1])
            if view not in VIEWS:raise ValueError(editorial_text('errors.village.handlers.action.02'))
            await callback.answer()
        text,markup=screen(service.get_state(player['id']),world['id'],callback.from_user.id,view,page,hid)
    except (ValueError,IndexError,KeyError) as error:
        await callback.answer(str(error)[:180],show_alert=True);return
    await send_private_text_from_callback(callback,world,text,markup)
