"""One durable send claim per challenge; known messages are edited idempotently."""
import asyncio
from aiogram.exceptions import TelegramBadRequest
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.editorial import screen
from app.mini.events import pvp
from app.mini.village.service import timestamp

def recover(db_path=DB_PATH):
    """Startup only. A crashed send may have reached Telegram: never resend."""
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        rows=conn.execute("SELECT * FROM mini_event_pvp WHERE publication='sending'").fetchall()
        for row in rows:
            conn.execute("""UPDATE mini_event_pvp SET publication='uncertain',
                last_error='Interrupted before acknowledgment' WHERE id=?""",(row["id"],))
            pvp._close(conn,row,"publication_failed",outcome="publication")
        conn.execute("UPDATE mini_event_pvp SET editing_until=0")
        pvp.expire_in_transaction(conn,timestamp())

def claim_send(sid,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        row=conn.execute("""SELECT s.*,w.chat_id,w.thread_id FROM mini_event_pvp s
            JOIN mini_worlds w ON w.id=s.world_id WHERE s.id=? AND s.publication='pending'
            AND s.status='open' AND w.enabled=1""",(sid,)).fetchone()
        if row is None:return None
        if not conn.execute("UPDATE mini_event_pvp SET publication='sending' WHERE id=? AND publication='pending'",(sid,)).rowcount:
            return None
        return dict(row)

def acknowledge(sid,message_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        conn.execute("""UPDATE mini_event_pvp SET publication='sent',message_id=?,
            published_revision=0 WHERE id=? AND publication='sending'""",(message_id,sid))

def failed_send(sid,error,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        row=conn.execute("SELECT * FROM mini_event_pvp WHERE id=? AND publication='sending'",(sid,)).fetchone()
        if row:
            conn.execute("UPDATE mini_event_pvp SET publication='uncertain',last_error=? WHERE id=?",(str(error)[:500],sid))
            pvp._close(conn,row,"publication_failed",outcome="publication")

def claim_edit(sid,db_path=DB_PATH):
    now=timestamp()
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        row=conn.execute("""SELECT s.*,w.chat_id,w.thread_id FROM mini_event_pvp s
            JOIN mini_worlds w ON w.id=s.world_id WHERE s.id=? AND s.message_id IS NOT NULL
            AND s.publication='sent' AND s.published_revision<s.revision
            AND s.editing_until<=? AND w.enabled=1""",(sid,now)).fetchone()
        if row is None:return None
        conn.execute("UPDATE mini_event_pvp SET editing_until=? WHERE id=?",(now+60,sid))
        return dict(row)

def finish_edit(sid,revision,*,ok,error="",db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute("""UPDATE mini_event_pvp SET editing_until=0,
            published_revision=CASE WHEN ? THEN ? ELSE MIN(published_revision,?-1) END,
            last_error=? WHERE id=?""",(ok,revision,revision,str(error)[:500],sid))

@screen
async def publish(bot,sid,db_path=DB_PATH):
    from app.mini.events.pvp_handlers import public_screen
    row=claim_send(sid,db_path)
    if row:
        session=pvp.get_session(row["world_id"],sid,db_path)
        body,markup=public_screen(session)
        try:
            sent=await bot.send_message(chat_id=row["chat_id"],message_thread_id=row["thread_id"] or None,
                text=body,reply_markup=markup)
        except asyncio.CancelledError:
            failed_send(sid,"Publication interrupted",db_path)
            raise
        except Exception as error:
            # Even an apparent API error can follow a processed request. Refund
            # escrow, expose recovery in the lobby, and never issue another send.
            failed_send(sid,error,db_path)
        else:
            acknowledge(sid,sent.message_id,db_path)
    row=claim_edit(sid,db_path)
    if row:
        session=pvp.get_session(row["world_id"],sid,db_path)
        body,markup=public_screen(session)
        try:
            await bot.edit_message_text(chat_id=row["chat_id"],message_id=row["message_id"],
                                        text=body,reply_markup=markup)
        except TelegramBadRequest as error:
            finish_edit(sid,session["revision"],ok="message is not modified" in str(error).lower(),
                        error=error,db_path=db_path)
        except asyncio.CancelledError:
            finish_edit(sid,session["revision"],ok=False,error="Edit interrupted",db_path=db_path)
            raise
        except Exception as error:
            finish_edit(sid,session["revision"],ok=False,error=error,db_path=db_path)
        else:
            finish_edit(sid,session["revision"],ok=True,db_path=db_path)

async def tick(bot,db_path=DB_PATH):
    pvp.expire(db_path)
    with connect_mini_db(db_path) as conn:
        ids=[r[0] for r in conn.execute("""SELECT id FROM mini_event_pvp WHERE
            (publication='pending' AND status='open') OR
            (publication='sent' AND published_revision<revision)""")]
    for sid in ids:
        await publish(bot,sid,db_path)

async def watch_loop(bot,*,interval_seconds=10):
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Events PvP watcher: {type(error).__name__}: {error}")
        await asyncio.sleep(max(1,interval_seconds))
