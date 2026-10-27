"""Story UX journals. Ending achievements never grant resources."""
import json
import time
from app.config import DB_PATH
from app.stories.repository import transaction

def record_ending(conn, session, ending):
    conn.execute("""INSERT OR IGNORE INTO story_achievements
        SELECT user_id,?,?,?,? FROM story_participants WHERE session_id=? AND active=1""",
        (session["code"],ending,session["id"],int(time.time()),session["id"]))

def claim_reminder(sid, chat, thread, uid, username, step, key, db_path=DB_PATH, *, now=None):
    from app.stories.service import _admin, _session
    _admin(chat,thread,uid,username)
    now = int(time.time()) if now is None else int(now)
    with transaction(db_path) as conn:
        session = _session(conn,sid,chat,thread)
        if session["status"] != "active" or session["step"] != step:
            raise ValueError("Сцена уже изменилась или история завершена.")
        if conn.execute("SELECT 1 FROM story_reminders WHERE operation_key=?", (key,)).fetchone():
            raise ValueError("Напоминание уже обработано.")
        last = conn.execute("SELECT MAX(claimed_at) FROM story_reminders WHERE session_id=? AND step=?", (sid,step)).fetchone()[0]
        if last is not None and now-last < 900:
            raise ValueError("Напоминание для этой сцены доступно раз в 15 минут.")
        remaining = [r[0] for r in conn.execute("""SELECT p.user_id FROM story_participants p
            WHERE p.session_id=? AND p.active=1 AND NOT EXISTS
            (SELECT 1 FROM story_votes v WHERE v.session_id=p.session_id AND v.step=? AND v.user_id=p.user_id)
            ORDER BY p.user_id""", (sid,step))]
        if not remaining:
            raise ValueError("Все участники уже проголосовали")
        conn.execute("INSERT INTO story_reminders VALUES(?,?,?,?,'sending',?,NULL)",
                     (sid,step,key,now,json.dumps(remaining)))
        return remaining

def finish_reminder(key, message_id=None, db_path=DB_PATH):
    with transaction(db_path) as conn:
        conn.execute("UPDATE story_reminders SET status=?,message_id=? WHERE operation_key=? AND status='sending'",
                     ("sent" if message_id is not None else "uncertain",message_id,key))

def reminder_text(recipients):
    # IDs originate from integer participant rows, never user-provided HTML.
    return "📖 Ждём ваш выбор: " + ", ".join(
        f'<a href="tg://user?id={int(uid)}">игрок {i}</a>' for i,uid in enumerate(recipients,1))
