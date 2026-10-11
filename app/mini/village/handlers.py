"""Personal ephemeral Village screens; mutation callbacks carry a state revision."""
import secrets
from aiogram import F, Router
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.mini.ui.context import load_personal_context, load_extended_context, personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.ui.callbacks import number, integer
from app.mini.heroes import get_player_heroes
from app.mini.village import service

router=Router(name='mini_village')
PAGE_SIZE=4
ACTIONS={'buy':'b','settle':'s','evict':'e','assign':'a','collect':'c'}
JOBS={'-':'-','mine':'m','market':'r','hunt':'h'}
LABELS={None:'Без работы','mine':'⛏ Шахта','market':'🏪 Рынок','hunt':'🏹 Охота'}
VIEWS={'home':'o','residents':'r','add':'s','hero':'c',
       'mine':'m','market':'k','hunt':'h',
       'add_mine':'am','add_market':'ak','add_hunt':'ah',
       'workers_mine':'wm','workers_market':'wk','workers_hunt':'wh'}


def screen(state,world_id,user_id,view='home',page=0,hero_id=None):
    def b(text,payload): return Button(text=text,callback_data=f'mini:vlg:{world_id}:{user_id}:{payload}')
    rev=state['revision'];token=secrets.token_hex(4)
    def mutation(text,action,hid=0,building='-',target=None):
        target=target or view
        return b(text,f'x.{number(rev)}.{ACTIONS[action]}.{number(hid)}.{JOBS[building]}.{state["houses"]}.{token}.{VIEWS[target]}{page}')
    rows=[]
    if view=='home':
        h=state['hourly'];remaining=state['seconds_left']
        stopped=' • остановлена: не хватает еды' if not state['fed'] else ''
        text=(f"🏘 Моя деревня\n\n🏠 Дома {state['houses']}/5\n👥 Жители {len(state['residents'])}/{state['capacity']}\n\n"
            f"🍖 Еда +{h['hunt']}/ч\n🍽 Расход {h['consumption']}/ч\n\n"
            f"⛏ Шахта +{h['mine']} shards/ч{stopped}\n🏪 Рынок +{h['market']} coins/ч{stopped}\n🏹 Охота +{h['hunt']} food/ч\n\n"
            f"Накоплено: {state['coins']:.3f} монет • {state['shards']:.3f} осколков\n"
            f"До остановки: {remaining//3600} ч {(remaining%3600)//60} мин" if remaining else
            f"🏘 Моя деревня\n\n🏠 Дома {state['houses']}/5\n👥 Жители {len(state['residents'])}/{state['capacity']}\n\n"
            f"🍖 Еда +{h['hunt']}/ч\n🍽 Расход {h['consumption']}/ч\n"
            f"⛏ Шахта +{h['mine']} shards/ч{stopped}\n🏪 Рынок +{h['market']} coins/ч{stopped}\n🏹 Охота +{h['hunt']} food/ч\n\n"
            f"Накоплено: {state['coins']:.3f} монет • {state['shards']:.3f} осколков\n⏸ Достигнут предел 8 часов. Собери ресурсы.")
        for resource,end in state.get('boosts',{}).items():
            label='Рынок' if resource=='coins' else 'Шахта'
            text+=f'\n✨ {label}: +25% • осталось {(end-service.timestamp())//3600} ч'
        rows.append([mutation(f"🏠 Купить дом — {state['next_price']} монет",'buy')] if state['next_price'] else [b('🏠 Все дома куплены','home')])
        rows.extend([[b('👥 Жители','residents.0')],[b('⛏ Шахта','mine.0'),b('🏪 Рынок','market.0')],
            [b('🏹 Охота','hunt.0')],[mutation('📦 Собрать','collect')]])
    elif view=='hero':
        hero=next((h for h in state['residents'] if h['hero_id']==hero_id),None)
        if not hero: raise ValueError('Житель уже выселен.')
        text=f"👥 {hero['name']}\nМесто работы: {LABELS[hero['building']]}\n\nЖитель недоступен для Boss, Tower и дуэлей."
        rows=[[mutation('Снять с производства','assign',hero_id)], [mutation('Выселить','evict',hero_id,target='residents')]]
    elif view in service.BUILDINGS:
        text=LABELS[view]+'\n\nУправление работниками.'
        rows=[[b('Добавить работника',f'add_{view}.0')], [b('Кто работает',f'workers_{view}.0')]]
    else:
        if view=='add':
            owned=get_player_heroes(state['player_id'],sync_catalog=False)
            resident_ids={h['hero_id'] for h in state['residents']}
            heroes=[h for h in owned if h['id'] not in resident_ids]
            text='👥 Поселить героя\n\nВыбери героя из коллекции.'
        elif view=='residents':
            heroes=state['residents']
            text='🏘 Жильцы\n\nВыбери жителя.'
        else:
            mode,building=view.split('_',1)
            if building not in service.BUILDINGS or mode not in ('add','workers'):
                raise ValueError('Неизвестный экран.')
            heroes=[h for h in state['residents'] if (h['building']!=building if mode=='add' else h['building']==building)]
            text=LABELS[building]+('\n\nВыбери работника для назначения / перевода.' if mode=='add' else '\n\nВыбери работника, чтобы снять с производства.')
        pages=max(1,(len(heroes)+PAGE_SIZE-1)//PAGE_SIZE);page=max(0,min(page,pages-1))
        buttons=[]
        for hero in heroes[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
            hid=hero.get('hero_id',hero.get('id'))
            if view=='add': button=mutation(hero['name'],'settle',hid)
            elif view=='residents': button=b(hero['name'],f'hero.{hid}.{page}.{number(rev)}')
            else: button=mutation(hero['name']+' • '+LABELS[hero['building']],'assign',hid,building if mode=='add' else '-')
            buttons.append(button)
        rows.extend([buttons[i:i+2] for i in range(0,len(buttons),2)])
        if not heroes: text+='\n\nНет доступных кандидатов.' if view.startswith('add_') else '\n\nПока пусто.'
        if view=='residents': rows.append([b('➕ Поселить','add.0')])
        nav=[]
        if page: nav.append(b('←',f'{view}.{page-1}'))
        if pages>1: nav.append(b(f'{page+1} / {pages}',f'{view}.{page}'))
        if page+1<pages: nav.append(b('→',f'{view}.{page+1}'))
        if nav: rows.append(nav)
        text+=f'\nСтраница {page+1}/{pages}'
    back=f'residents.{page}' if view=='hero' else (view.split('_',1)[1]+'.0' if '_' in view else 'home')
    rows.append([Button(text='Назад',callback_data=personal_callback('home',world_id,user_id))] if view=='home' else [b('Назад',back)])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:village:'))
async def home(callback):
    context=await load_personal_context(callback)
    if not context:return
    world,player=context
    text,markup=screen(service.get_state(player['id']),world['id'],callback.from_user.id)
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:vlg:'))
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
            result=service.mutate(player['id'],act,hero_id=integer(hid) or None,building=None if building=='-' else building,
                expected_revision=integer(rev),expected_houses=int(houses),operation_key=payload)
            if len(parts)>7:
                import re
                match=re.fullmatch(r'([a-z]+)(\d+)',parts[7])
                if not match: raise ValueError('Некорректный экран возврата.')
                view={v:k for k,v in VIEWS.items()}[match[1]];page=int(match[2]);hid=integer(hid) or None
            else:view='home'
            notice=f"Собрано: {result['coins']} монет, {result['shards']} осколков" if act=='collect' else result.get('status','Готово')
            await callback.answer(notice)
        else:
            if view=='hero':
                hid=int(parts[1]);page=int(parts[2]) if len(parts)>2 else 0
                if len(parts)>3 and integer(parts[3])!=service.get_state(player['id'])['revision']:
                    raise ValueError('Кнопка деревни устарела. Открой список заново.')
            elif view!='home':page=int(parts[1])
            if view not in VIEWS:raise ValueError('Неизвестный экран.')
            await callback.answer()
        text,markup=screen(service.get_state(player['id']),world['id'],callback.from_user.id,view,page,hid)
    except (ValueError,IndexError,KeyError) as error:
        await callback.answer(str(error)[:180],show_alert=True);return
    await send_private_text_from_callback(callback,world,text,markup)
