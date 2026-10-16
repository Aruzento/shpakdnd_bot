from app.mini.editorial import text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as InlineKeyboardButton
import json
from aiogram import F,Router
from aiogram.types import InlineKeyboardMarkup
from app.topic_guard import is_mini_topic
from app.mini.ui.context import shop_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.tower.service import get_state,list_heroes,start_selected_attempt,select_hero,attack,retry_attempt
from app.mini.combat.matchups import faction_multiplier_percent
from app.mini.presentation import faction_name,tag_label,hero_trait_lines,TRAIT_LABELS
from app.mini.favorites import get_favorites
from app.mini.ui.hero_selector import render_selector,hero_label
from app.mini.tower.selection import render_selection
from app.mini.ui.hero_cards import send_hero_card
from app.mini.tower.catalog import load_catalog

TOTAL_FLOORS=len(load_catalog()["floors"])

router=Router(name="mini_tower")


@editorial_screen
def button(text,world,user,tail=''):
    data=personal_callback('tower',world,user)
    return InlineKeyboardButton(text=text,callback_data=data+(':'+tail if tail else ''))


@editorial_screen
def enemy_lines(enemy, *, selection=False):
    heading=f"{enemy['name']} {'·' if selection else '●'} {faction_name(enemy['faction'])}"
    lines=[heading,'']
    if not selection:
        lines += [f"HP: {enemy['max_hp']}",'']
    lines += hero_trait_lines(enemy)
    labels=[tag_label(trait,TRAIT_LABELS) for trait in enemy['features']]
    lines += ['', editorial_text('tower.enemy_lines.line_01')+(', '.join(labels) or editorial_text('tower.enemy_lines.line_02'))]
    return lines


@editorial_screen
def hero_button_text(hero, bonus):
    extra=f' +{bonus}' if bonus else ''
    return f"{hero['name']} · {faction_name(hero['faction'])} · ATK {hero['attack']}{extra}"


@editorial_screen
def render_hero_selection(state,heroes,world,user,page=0,favorites=None):
    # Compatibility formatter for integrations using the old shared selector.
    text,markup=render_selector(heroes,favorites or [],'t',world,user,state['floor']['floor'])
    return '\n'.join([editorial_text('tower.render_hero_selection.line_01'),'']+enemy_lines(state['floor'],selection=True))+'\n\n'+text,markup



@editorial_screen
def render_state(state,world,user,reward=None):
    rows=[];attempt=state['attempt'] or state.get('last_attempt')
    enemy=json.loads(attempt['enemy_json']) if attempt else state['floor']
    floor=enemy['floor'] if enemy else TOTAL_FLOORS
    lines=[editorial_text('tower.render_state.line_01', floor=f'{floor}', TOTAL_FLOORS=f'{TOTAL_FLOORS}'),'']
    equipment=InlineKeyboardButton(text=editorial_text('navigation.equipment'),callback_data=personal_callback('equipment',world,user)+':home.0~t')
    if enemy:
        lines+=enemy_lines(enemy)
    if attempt:
        hero=json.loads(attempt['hero_json'])
        lines += ['',editorial_text('tower.render_state.line_03', current_hp=f"{attempt['current_hp']}", max_hp=f"{enemy['max_hp']}"),
                  editorial_text('tower.render_state.line_04', value_1=f"{hero_button_text(hero, attempt['equipment_bonus'])}"),
                  editorial_text('tower.render_state.line_05', value_1=f"{faction_multiplier_percent(hero['faction'], enemy['faction']) / 100:g}"),
                  editorial_text('tower.render_state.line_06', shields=f"{attempt['shields']}")]
        lines += [e.get('message','') for e in json.loads(attempt['events_json'])]
        if attempt['status']=='active':
            rows.append([button(editorial_text('tower.render_state.line_19'),world,user,f"hit.{attempt['id']}.{attempt['turn']}")])
        elif attempt['status']=='lost':
            lines.append(editorial_text('tower.render_state.line_13'))
            rows.append([button(editorial_text('tower.render_state.line_21'),world,user,f"heroes.{attempt['floor']}.0"),equipment])
            rows.append([button(editorial_text('tower.render_state.line_22'),world,user,f"retry.{attempt['id']}")])
        else:
            lines.append(editorial_text('tower.render_state.line_14'))
            if state['completed']: lines.append(editorial_text('tower.render_state.line_20'))
            else: rows.append([button(editorial_text('tower.render_state.line_26'),world,user)])
    elif state['completed']:
        lines.append(editorial_text('tower.render_state.line_08'))
    else:
        lines += ['',editorial_text('tower.render_state.line_09', equipment_bonus=f"{state['equipment_bonus']}"),editorial_text('tower.render_state.line_10'),
                  editorial_text('tower.render_state.line_11')]
        if enemy.get('response')=='prepare':
            lines.append(editorial_text('tower.render_state.line_15'))
        selected=state.get('selected_hero')
        if selected:
            lines += ['', editorial_text('tower.render_state.line_16'), hero_label(selected)]
            rows.append([button(editorial_text('tower.render_state.line_23'),world,user,f"heroes.{enemy['floor']}.0"),equipment])
            rows.append([button(editorial_text('tower.render_state.line_24'),world,user,f"start.{enemy['floor']}.{selected['id']}")])
        else:
            rows.append([button(editorial_text('tower.render_state.line_25'),world,user,f"heroes.{enemy['floor']}.0"),equipment])
    if reward:
        lines.append(editorial_text('tower.render_state.line_07', shards=f"{reward['shards']}"))
        if reward['equipment']:
            e=reward['equipment'];lines.append(editorial_text('tower.render_state.line_12', name=f"{e['name']}", attack_bonus=f"{e['attack_bonus']}"))
    if not any(equipment in row for row in rows):rows.append([equipment])
    rows.append([InlineKeyboardButton(text=editorial_text('navigation.home'),callback_data=personal_callback('home',world,user)),InlineKeyboardButton(text=editorial_text('navigation.back'),callback_data=personal_callback('adventures',world,user))])
    return '\n'.join(lines),InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:tower:'))
@editorial_screen
async def tower_callback(callback):
    context=await shop_context(callback,'tower')
    if context is None: return
    world,player,tail=context
    if not is_mini_topic(world['chat_id'],world['thread_id']):
        await callback.answer(editorial_text('tower.tower_callback.line_01'),show_alert=True);return
    w,u=world['id'],callback.from_user.id
    try:
        state=get_state(player['id']);reward=None;card=None
        parts=tail.split('.')
        if parts[0] in ('card','choose') and len(parts)==4:parts.append('0')
        if parts[0] in {'heroes','pick','start','rarity','card','choose'}:
            floor=int(parts[1])
            if state['completed'] or floor!=state['floor']['floor'] or state['attempt']:
                raise ValueError(editorial_text('errors.tower.handlers.tower_callback.01'))
        if parts[0] in ('heroes','rarity','card','choose'):
            heroes=list_heroes(player['id'])
            if parts[0]=='heroes' and len(parts)==3:
                text,markup,card=render_selection(state,heroes,w,u)
            elif parts[0]=='rarity' and len(parts)==4:
                text,markup,card=render_selection(state,heroes,w,u,stage='list',rarity=int(parts[2]),page=int(parts[3]))
            elif parts[0] in ('card','choose') and len(parts)==5:
                if parts[0]=='choose':
                    hid=int(parts[3]);rarity=int(parts[2])
                    from app.mini.tower.selection import RARITIES
                    if not 0<=rarity<len(RARITIES) or not any(h['id']==hid and h['rarity']==RARITIES[rarity] for h in heroes):
                        raise ValueError(editorial_text('errors.tower.handlers.tower_callback.03'))
                    select_hero(player['id'],hid,expected_floor=floor);state=get_state(player['id'])
                text,markup,card=render_selection(state,heroes,w,u,stage='card',rarity=int(parts[2]),page=int(parts[4]),hero_id=int(parts[3]))
            else:raise ValueError(editorial_text('errors.tower.handlers.tower_callback.02'))
        elif parts[0]=='pick' and len(parts)==3:
            # Safe legacy card selection. New UI exposes only the three stages.
            select_hero(player['id'],int(parts[2]),expected_floor=floor)
            state=get_state(player['id']);text,markup=render_state(state,w,u)
        else:
            if parts[0]=='start' and len(parts)==3:
                state['attempt']=start_selected_attempt(player['id'],floor,int(parts[2]))
            elif parts[0]=='retry' and len(parts)==2:
                attempt=retry_attempt(player['id'],int(parts[1]))
                state=get_state(player['id']);state['attempt']=attempt
            elif parts[0]=='hit' and len(parts)==3:
                result=attack(player['id'],int(parts[1]),int(parts[2]))
                state=get_state(player['id']);state['attempt']=result['attempt'];reward=result['reward']
            elif tail:raise ValueError(editorial_text('errors.tower.handlers.tower_callback.04'))
            # Home opens Tower state; hero selection is an explicit button.
            text,markup=render_state(state,w,u,reward)
    except (ValueError,IndexError) as error:
        await callback.answer(str(error) or editorial_text('tower.tower_callback.line_02'),show_alert=True);return
    await callback.answer()
    if card:await send_hero_card(callback,world,card,text,markup)
    else:await send_private_text_from_callback(callback,world,text,markup)
