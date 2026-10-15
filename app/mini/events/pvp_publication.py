"""Single send claim, durable bounded edit retries, safe structured diagnostics."""
import asyncio
import json
import logging
import secrets
from aiogram.exceptions import (TelegramBadRequest,TelegramForbiddenError,TelegramUnauthorizedError,
                                TelegramRetryAfter)
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.editorial import screen
from app.mini.events import pvp
from app.mini.village.service import timestamp

log=logging.getLogger(__name__)
BACKOFF=(10,30,60,120,300,900,900)
MAX_EDIT_FAILURES=8
LEASE_SECONDS=45
REQUEST_TIMEOUT=25

def error_info(error):
    # Never persist str(error): Telegram/HTTP exceptions may contain token URLs,
    # player text, request bodies or local paths. Classification uses it in memory.
    kind=type(error).__name__ if isinstance(error,BaseException) else "Interrupted"
    kind="".join(c for c in kind if c.isalnum() or c=="_")[:64]
    if isinstance(error,TelegramRetryAfter):
        delay=getattr(error,"retry_after",0)
        return "temporary","Telegram rate limit",kind,max(0,int(delay))
    if isinstance(error,(TelegramBadRequest,TelegramForbiddenError,TelegramUnauthorizedError)):
        return "permanent","Telegram rejected request",kind,0
    return "temporary","Transport result unknown",kind,0

def _diagnostic(conn,sid,event):
    row=conn.execute("""SELECT s.*,w.chat_id,w.thread_id FROM mini_event_pvp s
        JOIN mini_worlds w ON w.id=s.world_id WHERE s.id=?""",(sid,)).fetchone()
    # Fixed fields only: no names, choices, free-form exception messages or tokens.
    refunds=conn.execute("""SELECT COUNT(*) FROM mini_wallet_transactions
        WHERE reference_type='mini_event_pvp' AND reference_id=?
        AND operation_key LIKE ? AND amount=5""",(sid,f"pvp:{sid}:refund:%")).fetchone()[0]
    return dict(event=event,session_id=sid,world_id=row["world_id"],chat_id=row["chat_id"],
        topic_id=row["thread_id"],status=row["status"],publication=row["publication"],
        has_message_id=bool(row["message_id"]),send_attempt_at=row["send_attempt_at"],
        edit_attempt_at=row["edit_attempt_at"],error_type=row["last_error_type"],
        error=row["last_error"],last_error_at=row["last_error_at"],
        retry_count=row["retry_count"],next_retry_at=row["next_retry_at"],
        edit_status=row["edit_status"],delivery_uncertain=bool(row["delivery_uncertain"]),
        refund_transactions=refunds,refunds_confirmed=(row["status"] not in pvp.ACTIVE
            and row["outcome"]!="win" and refunds==(2 if row["acceptor_id"] is not None else 1)))

def _emit(record):
    log.warning("pvp_publication %s",json.dumps(record,sort_keys=True))

def _fail_edit(conn,row,now,error,*,permanent=False):
    category,description,kind,retry_after=error_info(error)
    count=row["retry_count"]+1
    state="permanent" if permanent or category=="permanent" else "exhausted" if count>=MAX_EDIT_FAILURES else "retrying"
    delay=max(BACKOFF[min(count-1,len(BACKOFF)-1)],retry_after)
    conn.execute("""UPDATE mini_event_pvp SET retry_count=?,next_retry_at=?,edit_status=?,
        editing_until=0,edit_token='',last_error=?,last_error_type=?,last_error_at=?
        WHERE id=?""",(count,now+delay if state=="retrying" else 0,state,description,kind,now,row["id"]))

def _recover(conn,now):
    records=[]
    for row in conn.execute("""SELECT * FROM mini_event_pvp
        WHERE publication='sending' AND send_until<=?""",(now,)).fetchall():
        conn.execute("""UPDATE mini_event_pvp SET publication='uncertain',delivery_uncertain=1,
            last_error='Send lease ended without acknowledgment',last_error_type='InterruptedSend',
            last_error_at=? WHERE id=?""",(now,row["id"]))
        pvp._close(conn,row,"publication_failed",outcome="publication")
        records.append(_diagnostic(conn,row["id"],"send_lease_expired"))
    for row in conn.execute("""SELECT * FROM mini_event_pvp
        WHERE edit_status='editing' AND editing_until<=?""",(now,)).fetchall():
        _fail_edit(conn,row,now,TimeoutError())
        records.append(_diagnostic(conn,row["id"],"edit_lease_expired"))
    return records

def recover(db_path=DB_PATH,*,now=None):
    """Safe in multiple processes: do not steal live leases or reset backoff."""
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        records=_recover(conn,timestamp(now))
        pvp.expire_in_transaction(conn,timestamp(now))
    for record in records:_emit(record)

def claim_send(sid,db_path=DB_PATH,*,now=None):
    now=timestamp(now)
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        pvp.expire_in_transaction(conn,now)
        row=conn.execute("""SELECT s.*,w.chat_id,w.thread_id FROM mini_event_pvp s
            JOIN mini_worlds w ON w.id=s.world_id WHERE s.id=? AND s.publication='pending'
            AND s.status='open' AND w.enabled=1""",(sid,)).fetchone()
        if row is None:return None
        token=secrets.token_hex(16)
        conn.execute("""UPDATE mini_event_pvp SET publication='sending',send_token=?,
            send_attempt_at=?,send_until=? WHERE id=?""",(token,now,now+LEASE_SECONDS,sid))
        return {**pvp._snapshot(conn,row),"send_token":token}

def acknowledge(sid,message_id,db_path=DB_PATH,*,token=None,revision=0,now=None):
    if type(message_id) is not int or message_id<=0:
        return failed_send(sid,RuntimeError("Missing acknowledgment"),db_path,token=token,now=now)
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        pvp.expire_in_transaction(conn,timestamp(now))
        row=conn.execute("SELECT * FROM mini_event_pvp WHERE id=?",(sid,)).fetchone()
        if row is None or (token is not None and row["send_token"]!=token):return
        if row["publication"] not in ("sending","uncertain"):return
        # A late acknowledgment can recover a known ID, but never resurrect a
        # cancelled/expired/refunded game. Acceptance was blocked while sending.
        conn.execute("""UPDATE mini_event_pvp SET publication='sent',message_id=?,
            delivery_uncertain=0,published_revision=?,send_until=0 WHERE id=?""",
            (message_id,revision,sid))

def failed_send(sid,error,db_path=DB_PATH,*,token=None,now=None,not_dispatched=False):
    now=timestamp(now)
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        row=conn.execute("SELECT * FROM mini_event_pvp WHERE id=? AND publication='sending'",(sid,)).fetchone()
        if row is None or (token is not None and token!=row["send_token"]):return
        category,description,kind,_=error_info(error)
        uncertain=not (not_dispatched or category=="permanent" or isinstance(error,TelegramRetryAfter))
        conn.execute("""UPDATE mini_event_pvp SET publication=?,delivery_uncertain=?,
            last_error=?,last_error_type=?,last_error_at=?,send_until=0 WHERE id=?""",
            ("uncertain" if uncertain else "failed",int(uncertain),description,kind,now,sid))
        pvp._close(conn,row,"publication_failed",outcome="publication")
        record=_diagnostic(conn,sid,"send_failed")
    _emit(record)

def claim_edit(sid,db_path=DB_PATH,*,now=None):
    now=timestamp(now)
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        row=conn.execute("""SELECT s.*,w.chat_id,w.thread_id FROM mini_event_pvp s
            JOIN mini_worlds w ON w.id=s.world_id WHERE s.id=? AND s.message_id IS NOT NULL
            AND s.publication='sent' AND s.published_revision<s.revision
            AND s.edit_status IN ('idle','synced','retrying') AND s.next_retry_at<=?
            AND s.editing_until<=? AND w.enabled=1""",(sid,now,now)).fetchone()
        if row is None:return None
        token=secrets.token_hex(16)
        conn.execute("""UPDATE mini_event_pvp SET editing_until=?,edit_token=?,
            edit_revision=?,edit_attempt_at=?,edit_status='editing' WHERE id=?""",
            (now+LEASE_SECONDS,token,row["revision"],now,sid))
        return {**pvp._snapshot(conn,row),"edit_token":token}

def finish_edit(sid,revision,*,ok,error=None,db_path=DB_PATH,token=None,now=None):
    now=timestamp(now)
    record=None
    with connect_mini_db(db_path) as conn:
        pvp._begin(conn)
        row=conn.execute("SELECT * FROM mini_event_pvp WHERE id=?",(sid,)).fetchone()
        if row is None:return
        if token is not None and (row["edit_token"]!=token or row["edit_status"]!="editing"):
            if ok:
                # A very late successful request may have overwritten a newer
                # message. Require repair without releasing another worker's
                # lease or bypassing its persisted backoff/terminal error.
                conn.execute("UPDATE mini_event_pvp SET published_revision=MIN(published_revision,?) WHERE id=?",
                             (max(0,revision-1),sid))
            return
        if ok:
            conn.execute("""UPDATE mini_event_pvp SET editing_until=0,edit_token='',
                published_revision=?,retry_count=0,next_retry_at=0,
                edit_status=CASE WHEN revision<=? THEN 'synced' ELSE 'idle' END,
                last_error='',last_error_type='',last_error_at=0 WHERE id=?""",
                (revision,revision,sid))
        else:
            _fail_edit(conn,row,now,error or TimeoutError())
            record=_diagnostic(conn,sid,"edit_failed")
    if record:_emit(record)

@screen
async def publish(bot,sid,db_path=DB_PATH):
    from app.mini.events.pvp_handlers import public_screen
    row=claim_send(sid,db_path)
    if row:
        dispatched=False
        try:
            body,markup=public_screen(row)
            dispatched=True
            sent=await bot.send_message(chat_id=row["chat_id"],message_thread_id=row["thread_id"] or None,
                text=body,reply_markup=markup,request_timeout=REQUEST_TIMEOUT)
        except asyncio.CancelledError as error:
            failed_send(sid,error,db_path,token=row["send_token"],not_dispatched=not dispatched)
            raise
        except Exception as error:
            failed_send(sid,error,db_path,token=row["send_token"],not_dispatched=not dispatched)
        else:
            acknowledge(sid,getattr(sent,"message_id",None),db_path,token=row["send_token"],revision=row["revision"])
    row=claim_edit(sid,db_path)
    if row:
        try:
            body,markup=public_screen(row)
            await bot.edit_message_text(chat_id=row["chat_id"],message_id=row["message_id"],
                text=body,reply_markup=markup,request_timeout=REQUEST_TIMEOUT)
        except TelegramBadRequest as error:
            # This applies only to this captured payload/revision, never a
            # revision that appeared while the HTTP request was in flight.
            finish_edit(sid,row["revision"],ok="message is not modified" in error.message.lower(),
                error=error,db_path=db_path,token=row["edit_token"])
        except asyncio.CancelledError as error:
            finish_edit(sid,row["revision"],ok=False,error=error,db_path=db_path,token=row["edit_token"])
            raise
        except Exception as error:
            finish_edit(sid,row["revision"],ok=False,error=error,db_path=db_path,token=row["edit_token"])
        else:
            finish_edit(sid,row["revision"],ok=True,db_path=db_path,token=row["edit_token"])

async def tick(bot,db_path=DB_PATH):
    recover(db_path)
    now=timestamp()
    with connect_mini_db(db_path) as conn:
        ids=[r[0] for r in conn.execute("""SELECT id FROM mini_event_pvp WHERE
            (publication='pending' AND status='open') OR
            (publication='sent' AND published_revision<revision AND
             edit_status IN ('idle','synced','retrying') AND next_retry_at<=? AND editing_until<=?)""",
            (now,now))]
    for sid in ids:
        await publish(bot,sid,db_path)

async def watch_loop(bot,*,interval_seconds=10):
    while True:
        try:await tick(bot)
        except asyncio.CancelledError:raise
        except Exception as error:
            # Exception class only; no potentially secret HTTP exception text.
            log.error("pvp_watcher_failure type=%s",type(error).__name__)
        await asyncio.sleep(max(1,interval_seconds))
