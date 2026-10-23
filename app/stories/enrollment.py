"""Persistent story enrollment and real D&D quick creation."""

import json
from app.config import DB_PATH
from app.stories.repository import transaction
from app.stories.service import _session


def begin(sid, chat, thread, user_id, mode, db_path=DB_PATH):
    if mode not in ("choose", "dnd", "mini"):
        raise ValueError("Invalid enrollment mode")
    with transaction(db_path) as conn:
        session = _session(conn, sid, chat, thread)
        if session["status"] != "registration":
            raise ValueError("Регистрация закрыта.")
        conn.execute(
            "INSERT INTO story_enrollment VALUES(?,?,?,?,?,?,'{}') ON CONFLICT(chat_id,thread_id,user_id) DO UPDATE SET session_id=excluded.session_id,mode=excluded.mode,stage=excluded.stage,data_json='{}'",
            (
                chat,
                thread or 0,
                user_id,
                sid,
                mode,
                "name" if mode == "dnd" else "choose",
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
        row = conn.execute(
            "SELECT * FROM story_enrollment WHERE chat_id=? AND thread_id=? AND user_id=? AND mode='dnd'",
            (chat, thread or 0, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("Нет активного создания персонажа.")
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
        return {"stage": next_stage, "session_id": row["session_id"], "data": data}
