"""Durable personal-window generations; Telegram may reuse ephemeral IDs."""
import json
import logging
import sqlite3
import time
from contextvars import ContextVar
from types import SimpleNamespace
from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.ui.timing import run_sync

log = logging.getLogger(__name__)
retired_ephemeral = ContextVar("retired_mini_ephemeral", default=None)
emitted_ephemerals = ContextVar("emitted_mini_ephemerals", default=frozenset())

def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_private_windows (
        bot_id INTEGER NOT NULL, world_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        generation INTEGER NOT NULL, chat_id INTEGER, message_id INTEGER, ephemeral_id INTEGER,
        callbacks_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(bot_id,world_id,user_id))""")
    if "callbacks_json" not in {r[1] for r in conn.execute("PRAGMA table_info(mini_private_windows)")}:
        conn.execute("ALTER TABLE mini_private_windows ADD COLUMN callbacks_json TEXT NOT NULL DEFAULT '[]'")

def personal(data):
    parts = (data or "").split(":")
    if len(parts) < 4 or parts[0] not in ("mini", "miniboss"):
        return None
    if (parts[0]=="miniboss" and parts[1] in {"join","leave","list","hit","history","skip","skipyes"}) or parts[1]=="raidopen":
        return None
    try:
        world, user = int(parts[2]), int(parts[3])
        return (world, user) if world > 0 and user > 0 else None
    except ValueError:
        return None

def window(bot, world, user, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM mini_private_windows WHERE bot_id=? AND world_id=? AND user_id=?",
                           (bot, world, user)).fetchone()
        return dict(row) if row else None

def reserve(bot, world, user, db_path=DB_PATH, *, actions=None):
    actions = [] if actions is None else actions
    if not isinstance(actions,list) or any(not isinstance(v,str) for v in actions):
        raise ValueError("Invalid personal actions")
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM mini_private_windows WHERE bot_id=? AND world_id=? AND user_id=?",
                           (bot, world, user)).fetchone()
        generation = old["generation"] + 1 if old else 1
        conn.execute("""INSERT INTO mini_private_windows VALUES(?,?,?,?,NULL,NULL,NULL,?)
            ON CONFLICT(bot_id,world_id,user_id) DO UPDATE SET
            generation=excluded.generation,chat_id=NULL,message_id=NULL,ephemeral_id=NULL,
            callbacks_json=excluded.callbacks_json""",
            (bot, world, user, generation,json.dumps(actions,ensure_ascii=False)))
        return generation, dict(old) if old else None

def activate(bot, world, user, generation, chat, message, ephemeral, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        return conn.execute("""UPDATE mini_private_windows SET chat_id=?,message_id=?,ephemeral_id=?
            WHERE bot_id=? AND world_id=? AND user_id=? AND generation=?""",
            (chat,message,ephemeral,bot,world,user,generation)).rowcount == 1

def matches(row, message):
    if row["chat_id"] != message.chat.id:
        return False
    ephemeral = getattr(message, "ephemeral_message_id", None)
    return (row["ephemeral_id"] == ephemeral if ephemeral is not None else
            row["ephemeral_id"] is None and row["message_id"] == message.message_id)

def _number(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Invalid personal action number")
    result=""
    while value:
        value, digit=divmod(value,36)
        result="0123456789abcdefghijklmnopqrstuvwxyz"[digit]+result
    return result or "0"

def packed(world,user,generation,index):
    value="mini:win:"+".".join(_number(v) for v in (world,user,generation,index))
    if len(value.encode())>64:
        raise ValueError("Personal callback exceeds 64 bytes")
    return value

def unpacked(value):
    if not value.startswith("mini:win:"):
        raise ValueError("Invalid personal callback")
    parts=value.removeprefix("mini:win:").split(".")
    if len(parts)!=4 or any(not v or len(v)>13 or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz" for c in v) for v in parts):
        raise ValueError("Invalid personal callback")
    result=tuple(int(v,36) for v in parts)
    if any(v>=2**63 for v in result) or any(v<=0 for v in result[:3]):
        raise ValueError("Invalid personal callback")
    return result

def resolve(bot,world,user,generation,index,message,db_path=DB_PATH):
    if message is None:
        return None
    row=window(bot,world,user,db_path)
    if not row or row["generation"]!=generation or not matches(row,message):
        return None
    try:
        actions=json.loads(row["callbacks_json"])
    except (ValueError,TypeError):
        return None
    if not isinstance(actions,list) or not 0<=index<len(actions) or not isinstance(actions[index],str):
        return None
    return actions[index]

def _copy(model, **updates):
    if hasattr(model,"model_copy"):
        return model.model_copy(update=updates)
    return SimpleNamespace(**dict(vars(model),**updates))

class WindowGuard(BaseMiddleware):
    """Decode only the current owner's persisted generation before router filters."""
    async def __call__(self, handler, event, data):
        began=time.perf_counter()
        data["_mini_received"]=began
        emitted=emitted_ephemerals.set(frozenset())
        retired=retired_ephemeral.set(None)
        try:
            cb = getattr(event, "callback_query", None)
            payload=getattr(cb,"data",None) or ""
            if cb and payload.startswith("mini:win:"):
                try:
                    world,user,generation,index=unpacked(payload)
                except ValueError:
                    await cb.answer("Некорректная кнопка.",show_alert=True)
                    return
                if cb.from_user.id!=user:
                    await cb.answer("Это личное меню другого игрока.",show_alert=True)
                    return
                original=await run_sync("ui_window",resolve,cb.bot.id,world,user,generation,index,cb.message)
                if original is None:
                    await cb.answer("Это окно устарело. Откройте D&D Mini заново.",show_alert=True)
                    return
                event=_copy(event,callback_query=_copy(cb,data=original))
            else:
                scope = personal(payload)
                if cb and cb.message and scope is None:
                    parts=payload.split(":")
                    private=(getattr(cb.message,"ephemeral_message_id",None) is not None or cb.message.chat.type=="private")
                    if private and len(parts)>=4 and parts[0]=="miniboss" and parts[2].isdigit():
                        scope=(int(parts[2]),cb.from_user.id)
                if scope and cb.message:
                    world,user=scope
                    if cb.from_user.id!=user:
                        await cb.answer("Это личное меню другого игрока.",show_alert=True)
                        return
                    row=await run_sync("ui_window",window,cb.bot.id,world,user)
                    # Legacy raw callbacks cannot bypass a newly generated action map.
                    if row is not None and (row["callbacks_json"]!="[]" or not matches(row,cb.message)):
                        await cb.answer("Это окно устарело. Откройте D&D Mini заново.",show_alert=True)
                        return
            data["_mini_window_ms"]=(time.perf_counter()-began)*1000
            return await handler(event,data)
        finally:
            emitted_ephemerals.reset(emitted)
            retired_ephemeral.reset(retired)

def remember_sent(bot,method,sent):
    receiver=getattr(getattr(method,"ephemeral_message_parameters",None),"receiver_user_id",None)
    chat=getattr(method,"chat_id",None)
    eid=getattr(sent,"ephemeral_message_id",None)
    if isinstance(receiver,int) and isinstance(chat,int) and isinstance(eid,int):
        emitted_ephemerals.set(emitted_ephemerals.get() | {(bot.id,chat,eid,receiver)})

class WindowDelivery(BaseRequestMiddleware):
    """Reserve before sending; all private callback actions carry the generation."""
    async def __call__(self, make_request, bot, method):
        async def deliver(value):
            sent=await make_request(bot,value)
            remember_sent(bot,value,sent)
            return sent
        markup=getattr(method,"reply_markup",None)
        rows=getattr(markup,"inline_keyboard",None)
        if not isinstance(rows,list):
            return await deliver(method)
        scopes={scope for row in rows for b in row if (scope:=personal(b.callback_data))}
        if len(scopes)!=1:
            return await deliver(method)
        world,user=next(iter(scopes))
        ep=getattr(method,"ephemeral_message_parameters",None)
        receiver=getattr(ep,"receiver_user_id",None)
        chat=getattr(method,"chat_id",None)
        if receiver!=user and chat!=user:
            return await deliver(method)  # Public messages are never retired here.
        actions=[b.callback_data for row in rows for b in row if b.callback_data is not None]
        generation,old=await run_sync("ui_window",reserve,bot.id,world,user,actions=actions)
        index=0;wrapped=[]
        for row in rows:
            buttons=[]
            for button in row:
                if button.callback_data is not None:
                    button=button.model_copy(update={"callback_data":packed(world,user,generation,index)})
                    index+=1
                buttons.append(button)
            wrapped.append(buttons)
        sent=await deliver(method.model_copy(update={"reply_markup":markup.model_copy(update={"inline_keyboard":wrapped})}))
        mid=getattr(sent,"message_id",None);eid=getattr(sent,"ephemeral_message_id",None)
        if isinstance(mid,int) and isinstance(chat,int):
            await run_sync("ui_window",activate,bot.id,world,user,generation,chat,mid,eid if isinstance(eid,int) else None)
        if old and old["chat_id"] and (old["chat_id"],old["message_id"],old["ephemeral_id"])!=(chat,mid,eid):
            try:
                if old["ephemeral_id"] is not None:
                    address=(bot.id,old["chat_id"],old["ephemeral_id"],user)
                    if address not in emitted_ephemerals.get():
                        retired_ephemeral.set(address)
                        await bot.delete_ephemeral_message(chat_id=old["chat_id"],receiver_user_id=user,
                                                          ephemeral_message_id=old["ephemeral_id"])
                elif old["chat_id"]==user and old["message_id"]:
                    await bot.delete_message(chat_id=user,message_id=old["message_id"])
            except Exception as error:
                log.warning("mini_window_retire_failed type=%s",type(error).__name__)
        return sent
