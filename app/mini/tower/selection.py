"""Three-step Tower selector. Boss and legacy selectors stay separate."""
from aiogram.types import InlineKeyboardButton as Button,InlineKeyboardMarkup
from app.mini.ui.context import personal_callback
from app.mini.ui.hero_selector import LABELS,hero_label
from app.mini.ui.hero_cards import hero_caption
from app.mini.presentation import rarity_emoji
from app.mini.combat.matchups import faction_multiplier_percent

RARITIES=('common','uncommon','rare','legendary','mythic','shadow')


def render(state,heroes,world,user,*,rarity=None,page=0,hero_id=None):
    floor=state['floor']['floor']
    def b(text,tail):return Button(text=text,callback_data=f'mini:tower:{world}:{user}'+(':'+tail if tail else ''))
    def main():return Button(text='🏠 Главная',callback_data=personal_callback('home',world,user))
    if rarity is None:
        text='🎴 Выбери редкость героя\nПротивник: '+state['floor']['name']
        buttons=[]
        for i,r in enumerate(RARITIES):
            count=sum(h['rarity']==r for h in heroes)
            if count:buttons.append(b(f"{rarity_emoji(r)} {LABELS[0][r]} · {count}",f'rarity.{floor}.{i}.0'))
        rows=[buttons[n:n+2] for n in range(0,len(buttons),2)]
        if not buttons:text+='\nНет доступных героев. Поселённого героя сначала высели из Деревни.'
        rows.append([main(),b('⬅️ Назад','')])
        return text,InlineKeyboardMarkup(inline_keyboard=rows),None
    rarity=int(rarity)
    if not 0<=rarity<len(RARITIES):raise ValueError('Редкость устарела.')
    heroes=[h for h in heroes if h['rarity']==RARITIES[rarity]]
    if hero_id is not None:
        index=next((i for i,h in enumerate(heroes) if h['id']==hero_id),None)
        if index is None:raise ValueError('Этот герой больше недоступен.')
        hero=heroes[index];selected=state.get('selected_hero')
        chosen=selected and selected['id']==hero['id']
        rows=[[b('✅ Герой выбран' if chosen else '✅ Выбрать этого героя',f"card.{floor}.{rarity}.{hero_id}" if chosen else f"choose.{floor}.{rarity}.{hero_id}").model_copy(update={'style':'success'})]]
        nav=[]
        if index:nav.append(b('◀️ Предыдущий',f"card.{floor}.{rarity}.{heroes[index-1]['id']}"))
        if index+1<len(heroes):nav.append(b('Следующий ▶️',f"card.{floor}.{rarity}.{heroes[index+1]['id']}"))
        if nav:rows.append(nav)
        trial=[b('🏰 К испытанию','ready')] if selected else []
        rows.append(trial+[b('⬅️ К героям',f'rarity.{floor}.{rarity}.{index//6}')])
        rows.append([main()])
        text=hero_caption(hero)+f"\n\nКонтрпик: ×{faction_multiplier_percent(hero['faction'],state['floor']['faction'])/100:g} против {state['floor']['name']}"
        return text[:1020],InlineKeyboardMarkup(inline_keyboard=rows),hero
    pages=max(1,(len(heroes)+5)//6);page=max(0,min(int(page),pages-1))
    buttons=[b(hero_label(h),f"card.{floor}.{rarity}.{h['id']}") for h in heroes[page*6:(page+1)*6]]
    rows=[buttons[n:n+2] for n in range(0,len(buttons),2)]
    rows.append([b('◀️',f'rarity.{floor}.{rarity}.{max(0,page-1)}'),b(f'{page+1}/{pages}',f'rarity.{floor}.{rarity}.{page}'),b('▶️',f'rarity.{floor}.{rarity}.{min(pages-1,page+1)}')])
    rows.append([main(),b('⬅️ Назад',f'heroes.{floor}.0')])
    return f"🎴 {LABELS[0][RARITIES[rarity]]}\n"+('Выбери героя.' if heroes else 'Нет доступных героев.'),InlineKeyboardMarkup(inline_keyboard=rows),None
