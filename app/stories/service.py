"""Persistent shared registration/voting, immutable scenarios and admin kick."""

import json
import logging
import secrets
from datetime import datetime, timezone
from app.config import DB_PATH
from app.stories.repository import transaction
from app.stories.catalog import discover
from app.stories.routing import available_choices

logger = logging.getLogger(__name__)


def _admin(chat, thread, user_id, username):
    from app.context import get_topic_admin, normalize_username

    required = get_topic_admin(chat, thread or 0)
    if not required or not username or normalize_username(username) != required:
        raise ValueError("Только администратор этой темы может управлять историей.")


def _session(conn, sid, chat, thread):
    row = conn.execute(
        "SELECT * FROM story_sessions WHERE id=? AND chat_id=? AND thread_id=?",
        (sid, chat, thread or 0),
    ).fetchone()
    if row is None:
        raise ValueError("История не найдена в этой теме.")
    return dict(row)


def _prior(conn, key, payload):
    if not key:
        raise ValueError("Operation key required")
    encoded = json.dumps(payload, sort_keys=True)
    row = conn.execute(
        "SELECT * FROM story_operations WHERE operation_key=?", (key,)
    ).fetchone()
    if row:
        if row["payload"] != encoded:
            raise ValueError("Ключ операции уже использован.")
        return json.loads(row["result_json"])


def _save(conn, key, payload, result):
    conn.execute(
        "INSERT INTO story_operations VALUES(?,?,?)",
        (key, json.dumps(payload, sort_keys=True), json.dumps(result)),
    )
    return result


def create(
    chat, thread, user_id, username, code, key, db_path=DB_PATH, *, catalog_dir=None
):
    thread = thread or 0
    _admin(chat, thread, user_id, username)
    stories, errors = discover(catalog_dir)
    if code not in stories:
        raise ValueError("Сценарий отсутствует или не прошёл проверку JSON.")
    payload = ["create", chat, thread, user_id, code]
    with transaction(db_path) as conn:
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if conn.execute(
            "SELECT 1 FROM story_sessions WHERE chat_id=? AND thread_id=? AND status IN ('registration','active')",
            (chat, thread),
        ).fetchone():
            raise ValueError("В этой теме уже открыта история.")
        sid = conn.execute(
            "INSERT INTO story_sessions(chat_id,thread_id,code,creator_id,created_at) VALUES(?,?,?,?,?)",
            (chat, thread, code, user_id, int(datetime.now(timezone.utc).timestamp())),
        ).lastrowid
        conn.execute(
            "INSERT INTO story_publications(session_id,step) VALUES(?,0)", (sid,)
        )
        return _save(
            conn,
            key,
            payload,
            {"session_id": sid, "code": code, "title": stories[code]["title"]},
        )


def join(
    sid, chat, thread, user_id, mode, entity, key, db_path=DB_PATH, *, username=None
):
    payload = ["join", sid, chat, thread or 0, user_id, mode, entity]
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if session["status"] != "registration":
            raise ValueError("Состав истории уже закрыт.")
        if mode == "mini":
            row = conn.execute(
                "SELECT p.id,p.character_name FROM mini_players p JOIN mini_worlds w ON w.id=p.world_id WHERE p.id=? AND p.telegram_user_id=? AND w.chat_id=?",
                (entity.get("id"), user_id, chat),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Выбери собственный существующий Mini-аккаунт в этом чате."
                )
            entity = {"id": row["id"], "name": row["character_name"]}
        elif mode == "dnd":
            from app.context import normalize_username

            if not username:
                raise ValueError(
                    "Для D&D-персонажа нужен Telegram username; можно выбрать Mini."
                )
            scope = entity.get("thread_id", thread or 0)
            row = conn.execute(
                "SELECT * FROM characters WHERE chat_id=? AND thread_id=? AND username=?",
                (chat, scope, normalize_username(username)),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Сначала создай настоящего D&D-персонажа: имя → раса → класс."
                )
            entity = {
                "chat_id": chat,
                "thread_id": scope,
                "username": row["username"],
                "name": row["name"],
            }
        else:
            raise ValueError("Выбери D&D или Mini.")
        existing = conn.execute(
            "SELECT * FROM story_participants WHERE session_id=? AND user_id=?",
            (sid, user_id),
        ).fetchone()
        if existing and existing["active"]:
            return _save(conn, key, payload, {"joined": True, "session_id": sid})
        conn.execute(
            "INSERT INTO story_participants VALUES(?,?,?,?,1) ON CONFLICT(session_id,user_id) DO UPDATE SET mode=excluded.mode,entity_json=excluded.entity_json,active=1",
            (sid, user_id, mode, json.dumps(entity, ensure_ascii=False)),
        )
        return _save(conn, key, payload, {"joined": True, "session_id": sid})


def leave(sid, chat, thread, user_id, key, db_path=DB_PATH):
    payload = ["leave", sid, chat, thread or 0, user_id]
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if session["status"] != "registration":
            raise ValueError("Выйти самостоятельно можно до начала.")
        conn.execute(
            "UPDATE story_participants SET active=0 WHERE session_id=? AND user_id=?",
            (sid, user_id),
        )
        return _save(conn, key, payload, {"left": True})


def start(
    sid, chat, thread, user_id, username, key, db_path=DB_PATH, *, catalog_dir=None
):
    _admin(chat, thread, user_id, username)
    stories, _ = discover(catalog_dir)
    payload = ["start", sid, chat, thread or 0, user_id]
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if session["status"] != "registration":
            raise ValueError("История уже запущена или завершена.")
        if not conn.execute(
            "SELECT 1 FROM story_participants WHERE session_id=? AND active=1", (sid,)
        ).fetchone():
            raise ValueError("Нет участников.")
        if session["code"] not in stories:
            raise ValueError("Сценарий больше не доступен; исправь JSON перед стартом.")
        story = stories[session["code"]]
        node = story["start_node"]
        ending = bool(story["nodes"][node].get("end"))
        conn.execute(
            "UPDATE story_sessions SET snapshot_json=?,node_id=?,step=1,status=? WHERE id=?",
            (
                json.dumps(story, ensure_ascii=False),
                node,
                "finished" if ending else "active",
                sid,
            ),
        )
        conn.execute(
            "INSERT INTO story_publications(session_id,step) VALUES(?,1)", (sid,)
        )
        if ending:
            from app.stories.feedback import record_ending
            record_ending(conn, session, node)
        return _save(
            conn,
            key,
            payload,
            {"started": True, "step": 1, "node_id": node, "ending": ending},
        )


def visited_nodes(conn, session):
    """Committed group route only; read in the caller's SQLite transaction."""
    visited = {session["node_id"]} if session["node_id"] else set()
    for row in conn.execute(
        "SELECT node_id,next_node FROM story_steps WHERE session_id=? ORDER BY step",
        (session["id"],),
    ):
        visited.update(node for node in row if node is not None)
    return visited


def _choices(conn, session):
    story = json.loads(session["snapshot_json"])
    visited = visited_nodes(conn, session)
    node = story["nodes"].get(session["node_id"])
    choices = available_choices(story, session["node_id"], visited) if node else []
    if node is None or (
        session["status"] == "active"
        and story["schema_version"] == 2
        and not node.get("end")
        and not choices
    ):
        logger.error(
            "Story route invariant failed session=%s step=%s node=%s visited=%s",
            session["id"], session["step"], session["node_id"], sorted(visited),
        )
        raise ValueError(
            "Нет безопасного продолжения истории. Состояние сохранено; "
            "администратор может выполнить /story cancel."
        )
    return choices


def _tally(conn, session):
    votes = [
        dict(r)
        for r in conn.execute(
            "SELECT v.* FROM story_votes v JOIN story_participants p ON p.session_id=v.session_id AND p.user_id=v.user_id WHERE v.session_id=? AND v.step=? AND p.active=1",
            (session["id"], session["step"]),
        )
    ]
    active = conn.execute(
        "SELECT COUNT(*) FROM story_participants WHERE session_id=? AND active=1",
        (session["id"],),
    ).fetchone()[0]
    counts = {c["id"]: 0 for _, c in _choices(conn, session)}
    for vote in votes:
        if vote["choice_id"] not in counts:
            logger.error("Unsafe persisted story vote session=%s step=%s",
                         session["id"], session["step"])
            raise ValueError("Недопустимый сохранённый голос. Администратор: /story cancel.")
        counts[vote["choice_id"]] += 1
    total = len(votes)
    percent = {c: 100 * n // total if total else 0 for c, n in counts.items()}
    if total:
        remainder = 100 - sum(percent.values())
        order = sorted(counts, key=lambda c: (-(100 * counts[c] % total), c))
        for c in order[:remainder]:
            percent[c] += 1
    return {"active": active, "voted": total, "counts": counts, "percent": percent}


def _advance(conn, session, chooser=None):
    tally = _tally(conn, session)
    if not tally["active"] or tally["voted"] != tally["active"]:
        return {"advanced": False, **tally}
    best = max(tally["counts"].values())
    leaders = [c for c, n in tally["counts"].items() if n == best]
    choice = (chooser or secrets.choice)(leaders)
    if choice not in leaders:
        raise ValueError("Invalid tie choice")
    story = json.loads(session["snapshot_json"])
    nxt = next(c["next"] for _, c in _choices(conn, session) if c["id"] == choice)
    ending = bool(story["nodes"][nxt].get("end"))
    result = {
        "advanced": True,
        "choice": choice,
        "next_node": nxt,
        "ending": ending,
        **tally,
    }
    conn.execute(
        "INSERT INTO story_steps VALUES(?,?,?,?,?,?)",
        (
            session["id"],
            session["step"],
            session["node_id"],
            choice,
            nxt,
            json.dumps(result),
        ),
    )
    conn.execute(
        "UPDATE story_sessions SET node_id=?,step=step+1,status=?,result_json=? WHERE id=?",
        (nxt, "finished" if ending else "active", json.dumps(result), session["id"]),
    )
    conn.execute(
        "INSERT INTO story_publications(session_id,step) VALUES(?,?)",
        (session["id"], session["step"] + 1),
    )
    if ending:
        from app.stories.feedback import record_ending
        record_ending(conn, session, nxt)
    return result


def vote(
    sid, chat, thread, user_id, step, choice, key, db_path=DB_PATH, *, chooser=None
):
    payload = ["vote", sid, chat, thread or 0, user_id, step, choice]
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if session["status"] != "active" or session["step"] != step:
            raise ValueError("Устаревший этап истории.")
        if not conn.execute(
            "SELECT 1 FROM story_participants WHERE session_id=? AND user_id=? AND active=1",
            (sid, user_id),
        ).fetchone():
            raise ValueError("Ты не записан в эту историю.")
        if choice not in {c["id"] for _, c in _choices(conn, session)}:
            raise ValueError("Нет такого варианта.")
        oldvote = conn.execute(
            "SELECT choice_id FROM story_votes WHERE session_id=? AND step=? AND user_id=?",
            (sid, step, user_id),
        ).fetchone()
        if oldvote:
            if oldvote[0] != choice:
                raise ValueError("Переголосовать нельзя.")
            return _save(
                conn, key, payload, {"advanced": False, **_tally(conn, session)}
            )
        conn.execute(
            "INSERT INTO story_votes VALUES(?,?,?,?)", (sid, step, user_id, choice)
        )
        return _save(conn, key, payload, _advance(conn, session, chooser))


def kick(
    sid,
    chat,
    thread,
    user_id,
    username,
    target_id,
    key,
    db_path=DB_PATH,
    *,
    chooser=None
):
    _admin(chat, thread, user_id, username)
    payload = ["kick", sid, chat, thread or 0, user_id, target_id]
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if session["status"] not in ("registration", "active"):
            raise ValueError("История завершена.")
        conn.execute(
            "UPDATE story_participants SET active=0 WHERE session_id=? AND user_id=?",
            (sid, target_id),
        )
        conn.execute(
            "DELETE FROM story_votes WHERE session_id=? AND step=? AND user_id=?",
            (sid, session["step"], target_id),
        )
        result = (
            _advance(conn, session, chooser)
            if session["status"] == "active"
            else {"kicked": True}
        )
        return _save(conn, key, payload, result)


def cancel(sid, chat, thread, user_id, username, key, db_path=DB_PATH):
    _admin(chat, thread, user_id, username)
    payload = ["cancel", sid, chat, thread or 0, user_id]
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        old = _prior(conn, key, payload)
        if old is not None:
            return old
        if session["status"] not in ("registration", "active"):
            raise ValueError("История уже завершена.")
        conn.execute("UPDATE story_sessions SET status='cancelled' WHERE id=?", (sid,))
        return _save(conn, key, payload, {"cancelled": True})


def status(chat, thread, db_path=DB_PATH, *, session_id=None):
    with transaction(db_path) as conn:
        if session_id is None:
            row = conn.execute(
                "SELECT id FROM story_sessions WHERE chat_id=? AND thread_id=? ORDER BY id DESC LIMIT 1",
                (chat, thread or 0),
            ).fetchone()
            if row is None:
                return None
            session_id = row[0]
        session = _session(conn, session_id, chat, thread)
        session["participants"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM story_participants WHERE session_id=? AND active=1 ORDER BY user_id",
                (session_id,),
            )
        ]
        if session["node_id"]:
            session["visited"] = sorted(visited_nodes(conn, session))
            try:
                session["choices"] = _choices(conn, session)
                session["tally"] = _tally(conn, session)
            except ValueError as error:
                # Preserve corrupt state, keep status/cancel usable, publish no buttons.
                session["route_error"] = str(error)
                session["choices"] = []
        return session
