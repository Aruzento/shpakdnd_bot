"""Persistent story enrollment and real D&D quick creation."""

import json
from app.config import DB_PATH
from app.stories.repository import transaction
from app.stories.service import _session


def begin(sid, chat, thread, user_id, mode, db_path=DB_PATH, *, input_scope=None):
    if mode not in ("choose", "dnd", "mini"):
        raise ValueError("Invalid enrollment mode")
    data = (
        {"input_chat": input_scope[0], "input_thread": input_scope[1] or 0}
        if input_scope
        else {}
    )
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        if session["status"] != "registration":
            raise ValueError("Регистрация закрыта.")
        if input_scope:
            # One private prompt per user; starting another does not alter membership.
            for old in conn.execute(
                "SELECT * FROM story_enrollment WHERE user_id=? AND mode='dnd'",
                (user_id,),
            ).fetchall():
                previous = json.loads(old["data_json"])
                if (
                    previous.get("input_chat"),
                    previous.get("input_thread", 0),
                ) == tuple(input_scope):
                    conn.execute(
                        "UPDATE story_enrollment SET mode='choose',stage='choose' WHERE chat_id=? AND thread_id=? AND user_id=?",
                        (old["chat_id"], old["thread_id"], user_id),
                    )
        conn.execute(
            "INSERT INTO story_enrollment VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,thread_id,user_id) DO UPDATE SET session_id=excluded.session_id,mode=excluded.mode,stage=excluded.stage,data_json=excluded.data_json",
            (
                chat,
                thread or 0,
                user_id,
                sid,
                mode,
                "name" if mode == "dnd" else "choose",
                json.dumps(data),
            ),
        )
        return session


def input_text(chat, thread, user_id, username, text, db_path=DB_PATH):
    from app.context import normalize_username

    if not username:
        raise ValueError(
            "Для обычного D&D нужен username Telegram; можно выбрать Mini."
        )
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 100
        or any(ord(c) < 32 for c in text)
        or text.startswith("/")
    ):
        raise ValueError("Введи текст длиной 1–100 символов.")
    text = text.strip()
    with transaction(db_path) as conn:
        row = _pending(conn, chat, thread, user_id)
        if row is None:
            raise ValueError("Нет активного создания персонажа.")
        chat, thread = row["chat_id"], row["thread_id"]
        session = _session(conn, row["session_id"], chat, thread)
        if session["status"] != "registration":
            raise ValueError("Регистрация закрыта.")
        data = json.loads(row["data_json"])
        stage = row["stage"]
        if stage not in ("name", "race", "class"):
            raise ValueError("Создание уже завершено.")
        data[stage] = text
        next_stage = {"name": "race", "race": "class", "class": "done"}[stage]
        if next_stage == "done":
            # The two authoritative ordinary D&D rows are created together.
            user = normalize_username(username)
            existing = conn.execute(
                "SELECT name FROM characters WHERE chat_id=? AND thread_id=? AND username=?",
                (chat, thread or 0, user),
            ).fetchone()
            if existing:
                raise ValueError(
                    "В этой теме уже есть настоящий D&D-персонаж; выбери его."
                )
            conn.execute(
                "INSERT INTO characters(chat_id,thread_id,username,name) VALUES(?,?,?,?)",
                (chat, thread or 0, user, data["name"]),
            )
            conn.execute(
                "INSERT INTO character_profiles(chat_id,thread_id,username,level,class_name,race) VALUES(?,?,?,1,?,?)",
                (chat, thread or 0, user, data["class"], data["race"]),
            )
        conn.execute(
            "UPDATE story_enrollment SET stage=?,data_json=? WHERE chat_id=? AND thread_id=? AND user_id=?",
            (
                next_stage,
                json.dumps(data, ensure_ascii=False),
                chat,
                thread or 0,
                user_id,
            ),
        )
        return {
            "stage": next_stage,
            "session_id": row["session_id"],
            "data": data,
            "chat_id": chat,
            "thread_id": thread,
        }


def _pending(conn, chat, thread, user_id):
    matches = []
    for row in conn.execute(
        "SELECT e.* FROM story_enrollment e JOIN story_sessions s ON s.id=e.session_id WHERE e.user_id=? AND e.mode='dnd' AND e.stage IN ('name','race','class') AND s.status='registration'",
        (user_id,),
    ):
        data = json.loads(row["data_json"])
        if (
            data.get("input_chat", row["chat_id"]),
            data.get("input_thread", row["thread_id"]),
        ) == (chat, thread or 0):
            matches.append(row)
    if len(matches) > 1:
        raise ValueError("Выбери историю, для которой создаётся персонаж.")
    return matches[0] if matches else None


def pending(chat, thread, user_id, db_path=DB_PATH):
    import sqlite3
    from app.mini.db import connect_mini_db

    with connect_mini_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_enrollment'"
        ).fetchone():
            return None
        row = _pending(conn, chat, thread, user_id)
        return dict(row) if row else None
