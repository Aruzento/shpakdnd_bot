"""Ephemeral selection; a challenge advertises only a defender-owned open button."""
from app.mini.editorial import text as editorial_text
from app.mini.editorial import icon, text as editorial_text, screen as editorial_screen
from app.mini.editorial.buttons import button as Button
from html import escape
import re
import json
import secrets
import sqlite3
from aiogram import F,Router
from aiogram.types import InlineKeyboardMarkup
from aiogram.exceptions import TelegramAPIError
from app.mini.ui.context import load_personal_context,load_extended_context,personal_callback
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.ui.callbacks import number, integer
from app.mini.heroes import get_player_heroes
from app.mini.availability import filter_heroes
from app.mini.db import connect_mini_db
from app.mini.duels import service


router=Router(name='mini_duels')
SIZE=8


@editorial_screen
def public_challenge_text(challenger, defender, duel_id):
    username = str(defender.get('username') or '').strip().lstrip('@')
    target = '@'+username if re.fullmatch(r'[A-Za-z0-9_]+', username) else str(defender.get('character_name') or editorial_text("general.player"))
    uid = int(defender.get('telegram_user_id') or 0)
    if not username and uid:
        mention = editorial_text("duels.mention", uid=uid, name=target)
    elif not re.fullmatch(r'[A-Za-z0-9_]+', username) and uid:
        mention = editorial_text("duels.mention", uid=uid, name=target)
    else:
        mention = escape(target)
    # The body renderer escapes player values; trusted mention markup is appended separately.
    lead = editorial_text("duels.challenge", challenger=challenger.get('character_name') or editorial_text("general.player"))
    tail = editorial_text("duels.challenge_details", duel_id=int(duel_id))
    return icon("icon.section.duels", html_mode=True)+" "+lead+" "+mention+" "+tail


@editorial_screen
def public_challenge_menu(world_id,owner_id):
    return InlineKeyboardMarkup(inline_keyboard=[[Button(text=editorial_text('duels.public_challenge_menu.line_01'),callback_data=f'mini:duel:{world_id}:{owner_id}:home')]])


@editorial_screen
def screen(world,player,user_id,view='home',value=0,page=0):
    wid=world['id'];pid=player['id']
    def b(label,payload):return Button(text=label,callback_data=f'mini:duel:{wid}:{user_id}:{payload}')
    rows=[];text=editorial_text('duels.screen.line_01')
    if view=='home':
        duel=service.get_state(pid)
        if duel and duel['status']=='pending':
            challenger=json.loads(duel['challenger_snapshot']);defender=json.loads(duel['defender_snapshot'] or '{}')
            left=max(0,duel['expires_at']-service.timestamp())
            text=editorial_text('duels.screen.line_02', id=f"{duel['id']}", name=f"{challenger['name']}", value_3=f"{defender.get('name', 'не выбран')}", left=f'{left}')
            if pid==duel['defender_id']:
                rows.append([b(editorial_text('duels.screen.line_11'),f'pick.{duel["id"]}.0')])
                if duel['defender_hero_id']:rows.append([b(editorial_text('duels.screen.line_18'),f'accept.{duel["id"]}.{duel["defender_hero_id"]}')])
                rows.append([b(editorial_text('duels.screen.line_12'),f'refuse.{duel["id"]}')])
            else:rows.append([b(editorial_text('duels.screen.line_13'),f'cancel.{duel["id"]}')])
        else:
            if duel:
                if duel['status']=='finished':
                    outcome=json.loads(duel['result_json']);text+=editorial_text('duels.screen.line_09')+(editorial_text('duels.screen.line_14') if outcome['draw'] else (editorial_text('duels.screen.line_19') if outcome['winner_id']==pid else editorial_text('duels.screen.line_20')))
                else:text+=editorial_text('duels.screen.line_06')
            rows.append([b(editorial_text('duels.screen.line_10'),'heroes.0')])
    elif view in ('heroes','pick'):
        heroes=[h for h in filter_heroes(get_player_heroes(pid,sync_catalog=False),pid) if h['rarity']!='mythic']
        text=editorial_text('duels.screen.line_03')
        pages=max(1,(len(heroes)+SIZE-1)//SIZE);page=max(0,min(page,pages-1))
        for h in heroes[page*SIZE:(page+1)*SIZE]:
            rows.append([b(editorial_text('duels.screen.line_15', name=f"{h['name']}", attack=f"{h['attack']}"),f'select.{value}.{h["id"]}' if view=='pick' else f'rivals.{h["id"]}.0')])
        if page:rows.append([b(editorial_text('duels.screen.line_16'),f'pick.{value}.{page-1}' if view=='pick' else f'heroes.{page-1}')])
        if page+1<pages:rows.append([b(editorial_text('duels.screen.line_17'),f'pick.{value}.{page+1}' if view=='pick' else f'heroes.{page+1}')])
        if not heroes:text+=editorial_text('duels.screen.line_04')
    elif view=='rivals':
        with connect_mini_db() as conn:
            conn.row_factory=sqlite3.Row
            opponents=[dict(r) for r in conn.execute('SELECT id,character_name,username FROM mini_players WHERE world_id=? AND id!=? ORDER BY id',(wid,pid))]
        text=editorial_text('duels.screen.line_05')
        pages=max(1,(len(opponents)+SIZE-1)//SIZE);page=max(0,min(page,pages-1));token=secrets.token_hex(4)
        for p in opponents[page*SIZE:(page+1)*SIZE]:rows.append([b(p['character_name']+((' • '+p['username']) if p['username'] else ''),f'c.{number(p["id"])}.{number(value)}.{token}')])
        if page:rows.append([b(editorial_text('duels.screen.line_21'),f'rivals.{value}.{page-1}')])
        if page+1<pages:rows.append([b(editorial_text('duels.screen.line_22'),f'rivals.{value}.{page+1}')])
        if not opponents:text+=editorial_text('duels.screen.line_07')
    else:raise ValueError(editorial_text('errors.duels.handlers.screen.01'))
    rows.append([Button(text=editorial_text('general.back'),callback_data=personal_callback('adventures' if view=='home' else 'duels',wid,user_id))])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:duels:'))
@editorial_screen
async def home(callback):
    context=await load_personal_context(callback)
    if not context:return
    world,player=context;text,markup=screen(world,player,callback.from_user.id)
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:duel:'))
@editorial_screen
async def action(callback):
    context=await load_extended_context(callback,'duel')
    if not context:return
    world,player,payload=context
    try:
        parts=payload.split('.');action=parts[0];view='home';value=0;page=0;answered=False
        if action=='c':
            rival,hid=integer(parts[1]),integer(parts[2])
            result=service.challenge(player['id'],rival,hid,payload)
            if result['applied']:
                with connect_mini_db() as conn:
                    conn.row_factory = sqlite3.Row
                    defender = dict(conn.execute('SELECT * FROM mini_players WHERE id=?', (rival,)).fetchone())
                owner = defender['telegram_user_id']
                try:
                    await callback.bot.send_message(chat_id=world['chat_id'],message_thread_id=world['thread_id'] or None,
                        text=public_challenge_text(player, defender, result['id']), parse_mode='HTML',
                        reply_markup=public_challenge_menu(world['id'],owner))
                except TelegramAPIError:
                    # Escrow persists; the defender can open Duels and the challenger can cancel.
                    answered=True
                    await callback.answer(editorial_text('duels.action.line_01'),show_alert=True)
        elif action in ('select','accept','refuse','cancel'):
            service.act(int(parts[1]),player['id'],action,hero_id=int(parts[2]) if action=='select' else None,
                expected_hero_id=int(parts[2]) if action=='accept' else None)
        elif action=='pick':view='pick';value=int(parts[1]);page=int(parts[2])
        elif action=='rivals':view='rivals';value=int(parts[1]);page=int(parts[2])
        elif action=='heroes':view='heroes';page=int(parts[1])
        elif action!='home':raise ValueError(editorial_text('errors.duels.handlers.action.01'))
        text,markup=screen(world,player,callback.from_user.id,view,value,page)
    except (ValueError,IndexError) as error:await callback.answer(str(error)[:180],show_alert=True);return
    if not answered: await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
