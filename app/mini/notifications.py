"""Durable announcement intents; uncertain Telegram sends are never repeated.

Telegram sendMessage has no idempotency key. Reserve before sending so a restart
cannot duplicate an announcement. Confirmed API rejections can be retried; a
crash or an ambiguous timeout leaves a durable uncertain/sending record.
"""
import asyncio
import json
import sqlite3
import time
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from app import config
from app.mini.db import connect_mini_db
from app.mini.presentation import format_player_mention


def enqueue_expired_titles(db_path=None, *, now=None):
    db_path=config.DB_PATH if db_path is None else db_path
    when=int(time.time() if now is None else now)
    with connect_mini_db(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        rows=conn.execute("""SELECT t.id,t.player_id,t.text,p.world_id FROM mini_titles t
            JOIN mini_players p ON p.id=t.player_id WHERE t.status='active' AND t.expires_at<=?""",(when,)).fetchall()
        for title_id,player_id,text,world_id in rows:
            conn.execute("UPDATE mini_titles SET status='expired' WHERE id=? AND status='active'",(title_id,))
            conn.execute("""INSERT OR IGNORE INTO mini_public_notifications
                (event_key,world_id,player_id,kind,payload_json) VALUES(?,?,?,'title_expired',?)""",
                (f'title_expired:{title_id}',world_id,player_id,json.dumps({'title':text},ensure_ascii=False)))
    return len(rows)


def claim_notification(notification_id, db_path=None):
    db_path=config.DB_PATH if db_path is None else db_path
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("""SELECT n.*,p.username,p.character_name,p.telegram_user_id,
            w.chat_id,w.thread_id FROM mini_public_notifications n
            JOIN mini_players p ON p.id=n.player_id JOIN mini_worlds w ON w.id=n.world_id
            WHERE n.id=? AND n.status='pending' AND w.enabled=1""",(notification_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE mini_public_notifications SET status='sending' WHERE id=?",(notification_id,))
        return dict(row)


def finish_notification(notification_id,status,db_path=None, *, message_id=None,error=''):
    db_path=config.DB_PATH if db_path is None else db_path
    with connect_mini_db(db_path) as conn:
        conn.execute("""UPDATE mini_public_notifications SET status=?,message_id=?,last_error=?
            WHERE id=? AND status='sending'""",(status,message_id,error[:500],notification_id))


async def publish_pending_notifications(bot, db_path=None, *, player_id=None):
    db_path=config.DB_PATH if db_path is None else db_path
    with connect_mini_db(db_path) as conn:
        rows=conn.execute("""SELECT id FROM mini_public_notifications WHERE status='pending'
            AND (? IS NULL OR player_id=?) ORDER BY id""",(player_id,player_id)).fetchall()
    for (notification_id,) in rows:
        row=claim_notification(notification_id,db_path)
        if row is None:
            continue
        payload=json.loads(row['payload_json'])
        if row['kind']=='welcome':
            text=f"🎉 Приветствуем нового героя: {payload['character_name']} — {format_player_mention(row,db_path)}"
        else:
            # The title that just ended must not prefix its own expiration notice.
            text=f"{format_player_mention(row,db_path,include_title=False)} больше не \"{payload['title']}\". Какая жалость.."
        try:
            sent=await bot.send_message(chat_id=row['chat_id'],message_thread_id=row['thread_id'] or None,text=text)
        except (TelegramBadRequest,TelegramForbiddenError,TelegramRetryAfter) as error:
            finish_notification(notification_id,'pending',db_path,error=str(error))
        except asyncio.CancelledError:
            finish_notification(notification_id,'uncertain',db_path,error='Publication interrupted')
            raise
        except Exception as error:
            finish_notification(notification_id,'uncertain',db_path,error=str(error))
            print(f'Mini notification {notification_id}: {type(error).__name__}: {error}')
        else:
            finish_notification(notification_id,'sent',db_path,message_id=sent.message_id)


async def mini_notice_watch_loop(bot, *, interval_seconds=60):
    while True:
        try:
            enqueue_expired_titles()
            await publish_pending_notifications(bot)
            from app.mini.duels.service import expire
            expire()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f'Mini notice watcher: {type(error).__name__}: {error}')
        await asyncio.sleep(max(10,interval_seconds))


def recover_interrupted_notifications(db_path=None):
    """Called before polling: never re-send an unacknowledged prior dispatch."""
    db_path=config.DB_PATH if db_path is None else db_path
    with connect_mini_db(db_path) as conn:
        return conn.execute("""UPDATE mini_public_notifications SET status='uncertain',
            last_error='Bot stopped before send acknowledgment' WHERE status='sending'""").rowcount
