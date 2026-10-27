"""Durable personal-window generations; public messages are never retired here."""
import sqlite3
import logging
from contextvars import ContextVar
from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.ui.timing import run_sync

log = logging.getLogger(__name__)
retired_ephemeral = ContextVar("retired_mini_ephemeral", default=None)

def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS mini_private_windows (
        bot_id INTEGER NOT NULL, world_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        generation INTEGER NOT NULL, chat_id INTEGER, message_id INTEGER, ephemeral_id INTEGER,
        PRIMARY KEY(bot_id,world_id,user_id))""")

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

def reserve(bot, world, user, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM mini_private_windows WHERE bot_id=? AND world_id=? AND user_id=?",
                           (bot, world, user)).fetchone()
        generation = old["generation"] + 1 if old else 1
        conn.execute("""INSERT INTO mini_private_windows VALUES(?,?,?,?,NULL,NULL,NULL)
            ON CONFLICT(bot_id,world_id,user_id) DO UPDATE SET
            generation=excluded.generation,chat_id=NULL,message_id=NULL,ephemeral_id=NULL""",
            (bot, world, user, generation))
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

class WindowGuard(BaseMiddleware):
    """Runs before router filters. Existing handler ownership checks remain authoritative."""
    async def __call__(self, handler, event, data):
        cb = getattr(event, "callback_query", None)
        scope = personal(getattr(cb, "data", None))
        # Boss public entry buttons carry boss_id, not owner_id. Their personal
        # copies still obey window lifetime, while shared public launchers stay usable.
        if cb and cb.message and scope is None:
            parts = (cb.data or "").split(":")
            personal_message = (getattr(cb.message,"ephemeral_message_id",None) is not None
                                or cb.message.chat.type=="private")
            if personal_message and len(parts)>=4 and parts[0]=="miniboss" and parts[2].isdigit():
                scope=(int(parts[2]),cb.from_user.id)
        if scope and cb.message:
            world, user = scope
            if cb.from_user.id != user:
                await cb.answer("Это личное меню другого игрока.", show_alert=True)
                return
            row = await run_sync("ui_window", window, cb.bot.id, world, user)
            if row is not None and not matches(row, cb.message):
                await cb.answer("Это окно устарело. Откройте D&D Mini заново.", show_alert=True)
                return
        return await handler(event, data)

class WindowDelivery(BaseRequestMiddleware):
    """Reserve before sending; a failed/uncertain send cannot revive old buttons."""
    async def __call__(self, make_request, bot, method):
        markup = getattr(method, "reply_markup", None)
        rows = getattr(markup, "inline_keyboard", None)
        if not isinstance(rows, list):
            return await make_request(bot, method)
        scopes = {scope for row in rows for b in row if (scope := personal(b.callback_data))}
        if len(scopes) != 1:
            return await make_request(bot, method)
        world, user = next(iter(scopes))
        ep = getattr(method, "ephemeral_message_parameters", None)
        receiver = getattr(ep, "receiver_user_id", None)
        chat = getattr(method, "chat_id", None)
        if receiver != user and chat != user:
            # A public boss card may include personal launch links; never convert it or delete it.
            return await make_request(bot, method)
        generation, old = await run_sync("ui_window", reserve, bot.id, world, user)
        sent = await make_request(bot, method)
        mid = getattr(sent, "message_id", None)
        eid = getattr(sent, "ephemeral_message_id", None)
        if isinstance(mid, int) and isinstance(chat, int):
            await run_sync("ui_window", activate, bot.id, world, user, generation,
                           chat, mid, eid if isinstance(eid, int) else None)
        if old and old["chat_id"] and (old["chat_id"],old["message_id"],old["ephemeral_id"]) != (chat,mid,eid):
            try:
                if old["ephemeral_id"] is not None:
                    retired_ephemeral.set((bot.id,old["chat_id"],old["ephemeral_id"],user))
                    await bot.delete_ephemeral_message(chat_id=old["chat_id"], receiver_user_id=user,
                                                      ephemeral_message_id=old["ephemeral_id"])
                elif old["chat_id"] == user and old["message_id"]:
                    await bot.delete_message(chat_id=user,message_id=old["message_id"])
            except Exception as error:
                # Old buttons are already durably invalid even when Telegram cannot remove the view.
                log.warning("mini_window_retire_failed type=%s", type(error).__name__)
        return sent
