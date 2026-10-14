"""Tower-only rarity → list → card selector. Boss retains its shared selector."""
from aiogram.types import InlineKeyboardButton as Button,InlineKeyboardMarkup
from app.mini.presentation import rarity_emoji,faction_name,hero_trait_lines,tag_label,TRAIT_LABELS
from app.mini.combat.matchups import faction_multiplier_percent
from app.mini.ui.hero_selector import hero_label,LABELS
from app.mini.ui.hero_cards import hero_caption

RARITIES=('common','uncommon','rare','legendary','shadow','mythic')


def render_selection(state,heroes,world,user,*,stage='rarity',rarity=0,page=0,hero_id=None):
    if state['completed'] or state['attempt']:raise ValueError('Нельзя выбирать героя во время боя.')
    floor=state['floor']['floor'];enemy=state['floor']
    def b(text,tail):
        data=f'mini:tower:{world}:{user}'+(':'+tail if tail else '')
        if len(data.encode())>64:raise ValueError('Слишком длинная кнопка.')
        return Button(text=text,callback_data=data)
    home=Button(text='🏠 Главная',callback_data=f'mini:home:{world}:{user}')
    title=f"🎴 Выбор героя\n\n{enemy['name']} · {faction_name(enemy['faction'])}\nHP: {enemy['max_hp']}"
    rows=[];hero=None
    if stage=='rarity':
        counts={key:sum(h['rarity']==key for h in heroes) for key in RARITIES}
        buttons=[b(f"{rarity_emoji(key)} {LABELS[0][key]} · {counts[key]}",f'rarity.{floor}.{n}.0')
                 for n,key in enumerate(RARITIES) if counts[key]]
        rows=[buttons[n:n+2] for n in range(0,len(buttons),2)]
        text=title+'\n\nВыбери редкость.' if buttons else title+'\n\nНет доступных героев.'
        rows.append([home,b('⬅️ Назад','')])
    else:
        if not 0<=rarity<len(RARITIES):raise ValueError('Некорректная редкость.')
        pool=[h for h in heroes if h['rarity']==RARITIES[rarity]]
        if stage=='list':
            pages=max(1,(len(pool)+5)//6);page=max(0,min(page,pages-1))
            buttons=[b(hero_label(h),f"card.{floor}.{rarity}.{h['id']}.{page}") for h in pool[page*6:(page+1)*6]]
            rows=[buttons[n:n+2] for n in range(0,len(buttons),2)]
            nav=[]
            if page:nav.append(b('◀️',f'rarity.{floor}.{rarity}.{page-1}'))
            nav.append(b(f'{page+1}/{pages}',f'rarity.{floor}.{rarity}.{page}'))
            if page+1<pages:nav.append(b('▶️',f'rarity.{floor}.{rarity}.{page+1}'))
            rows += [nav,[home,b('⬅️ Назад',f'heroes.{floor}.0')]]
            text=title+'\n\n'+LABELS[0][RARITIES[rarity]]+('\nНет доступных героев.' if not pool else '')
        elif stage=='card':
            index=next((i for i,h in enumerate(pool) if h['id']==hero_id),None)
            if index is None:raise ValueError('Этот герой больше недоступен.')
            hero=pool[index];page=index//6
            selected=state.get('selected_hero') or {}
            chosen=selected.get('id')==hero_id
            rows=[[b('✅ Герой выбран' if chosen else '✅ Выбрать этого героя',
                     f"{'card' if chosen else 'choose'}.{floor}.{rarity}.{hero_id}.{page}")]]
            arrows=[]
            if index:arrows.append(b('◀️ Предыдущий',f"card.{floor}.{rarity}.{pool[index-1]['id']}.{(index-1)//6}"))
            if index+1<len(pool):arrows.append(b('Следующий ▶️',f"card.{floor}.{rarity}.{pool[index+1]['id']}.{(index+1)//6}"))
            if arrows:rows.append(arrows)
            rows += [[b('🏰 К испытанию',''),b('⬅️ К героям',f'rarity.{floor}.{rarity}.{page}')],[home]]
            matchup=faction_multiplier_percent(hero['faction'],enemy['faction'])
            hint=(f"Против {enemy['name']}: фракция ×{matchup/100:g}.\nОсобенности врага: "+
                  (', '.join(tag_label(key,TRAIT_LABELS) for key in enemy.get('features',[])) or 'нет'))
            # Reserve room for the counterpick hint even with a long hero description.
            text=hero_caption(hero)[:max(0,1018-len(hint))]+'\n\n'+hint
        else:raise ValueError('Некорректный этап выбора.')
    return text,InlineKeyboardMarkup(inline_keyboard=rows),hero
