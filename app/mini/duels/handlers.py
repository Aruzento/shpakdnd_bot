"""Ephemeral selection; a challenge advertises only a defender-owned open button."""
import json
import secrets
import sqlite3
from aiogram import F,Router
from aiogram.types import InlineKeyboardButton as Button,InlineKeyboardMarkup
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


def screen(world,player,user_id,view='home',value=0,page=0):
    wid=world['id'];pid=player['id']
    def b(label,payload):return Button(text=label,callback_data=f'mini:duel:{wid}:{user_id}:{payload}')
    rows=[];text='⚔️ Дуэли\n\nСтавка: 10 монет с каждого. Победитель получает 17 монет, проигравший — 3 осколка. При ничьей ставки возвращаются.'
    if view=='home':
        duel=service.get_state(pid)
        if duel and duel['status']=='pending':
            challenger=json.loads(duel['challenger_snapshot']);defender=json.loads(duel['defender_snapshot'] or '{}')
            left=max(0,duel['expires_at']-service.timestamp())
            text=f"⚔️ Вызов #{duel['id']}\n\nИнициатор: {challenger['name']}\nЗащитник: {defender.get('name','не выбран')}\nДо истечения: {left} сек.\nСтавка: 10 монет с каждого."
            if pid==duel['defender_id']:
                rows.append([b('Выбрать / сменить героя',f'pick.{duel["id"]}.0')])
                if duel['defender_hero_id']:rows.append([b('Согласен',f'accept.{duel["id"]}.{duel["defender_hero_id"]}')])
                rows.append([b('Отказ',f'refuse.{duel["id"]}')])
            else:rows.append([b('Отменить вызов',f'cancel.{duel["id"]}')])
        else:
            if duel:
                if duel['status']=='finished':
                    outcome=json.loads(duel['result_json']);text+='\n\nПоследняя дуэль: '+('🤝 Ничья' if outcome['draw'] else ('🏆 Победа' if outcome['winner_id']==pid else 'Поражение • +3 осколка'))
                else:text+='\n\nПоследний вызов завершён: ставка возвращена.'
            rows.append([b('Вызвать игрока','heroes.0')])
    elif view in ('heroes','pick'):
        heroes=filter_heroes(get_player_heroes(pid,sync_catalog=False),pid)
        text='🎴 Выбери героя для дуэли.'
        pages=max(1,(len(heroes)+SIZE-1)//SIZE);page=max(0,min(page,pages-1))
        for h in heroes[page*SIZE:(page+1)*SIZE]:
            rows.append([b(f"{h['name']} • атака {h['attack']}",f'select.{value}.{h["id"]}' if view=='pick' else f'rivals.{h["id"]}.0')])
        if page:rows.append([b('←',f'pick.{value}.{page-1}' if view=='pick' else f'heroes.{page-1}')])
        if page+1<pages:rows.append([b('→',f'pick.{value}.{page+1}' if view=='pick' else f'heroes.{page+1}')])
        if not heroes:text+='\nНет героев, доступных для боя.'
    elif view=='rivals':
        with connect_mini_db() as conn:
            conn.row_factory=sqlite3.Row
            opponents=[dict(r) for r in conn.execute('SELECT id,character_name,username FROM mini_players WHERE world_id=? AND id!=? ORDER BY id',(wid,pid))]
        text='⚔️ Выбери соперника. Выбранный герой закрепится после отправки вызова.'
        pages=max(1,(len(opponents)+SIZE-1)//SIZE);page=max(0,min(page,pages-1));token=secrets.token_hex(4)
        for p in opponents[page*SIZE:(page+1)*SIZE]:rows.append([b(p['character_name']+((' • '+p['username']) if p['username'] else ''),f'c.{number(p["id"])}.{number(value)}.{token}')])
        if page:rows.append([b('←',f'rivals.{value}.{page-1}')])
        if page+1<pages:rows.append([b('→',f'rivals.{value}.{page+1}')])
        if not opponents:text+='\nВ этом мире пока нет других игроков.'
    else:raise ValueError('Неизвестный экран дуэли.')
    rows.append([Button(text='Назад',callback_data=personal_callback('adventures' if view=='home' else 'duels',wid,user_id))])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('mini:duels:'))
async def home(callback):
    context=await load_personal_context(callback)
    if not context:return
    world,player=context;text,markup=screen(world,player,callback.from_user.id)
    await callback.answer();await send_private_text_from_callback(callback,world,text,markup)


@router.callback_query(F.data.startswith('mini:duel:'))
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
                with connect_mini_db() as conn:owner=conn.execute('SELECT telegram_user_id FROM mini_players WHERE id=?',(rival,)).fetchone()[0]
                try:
                    await callback.bot.send_message(chat_id=world['chat_id'],message_thread_id=world['thread_id'] or None,
                        text=f"⚔️ {player['character_name']} вызывает на дуэль! Вызов #{result['id']} • срок 2 минуты • ставка 10 монет.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[Button(text='Открыть вызов',callback_data=f'mini:duel:{world["id"]}:{owner}:home')]]))
                except TelegramAPIError:
                    # Escrow persists; the defender can open Duels and the challenger can cancel.
                    answered=True
                    await callback.answer('Вызов сохранён. Не удалось опубликовать: соперник может открыть «Дуэли».',show_alert=True)
        elif action in ('select','accept','refuse','cancel'):
            service.act(int(parts[1]),player['id'],action,hero_id=int(parts[2]) if action=='select' else None,
                expected_hero_id=int(parts[2]) if action=='accept' else None)
        elif action=='pick':view='pick';value=int(parts[1]);page=int(parts[2])
        elif action=='rivals':view='rivals';value=int(parts[1]);page=int(parts[2])
        elif action=='heroes':view='heroes';page=int(parts[1])
        elif action!='home':raise ValueError('Неизвестное действие.')
        text,markup=screen(world,player,callback.from_user.id,view,value,page)
    except (ValueError,IndexError) as error:await callback.answer(str(error)[:180],show_alert=True);return
    if not answered: await callback.answer()
    await send_private_text_from_callback(callback,world,text,markup)
