"""Owner-bound private name sessions. No Telegram calls inside transactions."""
import secrets
import sqlite3
import time
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.onboarding import grant_starter_in_transaction
from app.topic_guard import is_mini_topic


def begin(world_id, user_id, db_path=DB_PATH):
    token=secrets.token_hex(12)
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('INSERT INTO mini_registration_sessions(token,world_id,telegram_user_id,expires_at) VALUES(?,?,?,?)',
                     (token,world_id,user_id,int(time.time())+900))
    return token


def _world(conn,session):
    world=conn.execute('SELECT * FROM mini_worlds WHERE id=?',(session['world_id'],)).fetchone()
    if not world or not world['enabled'] or not is_mini_topic(world['chat_id'],world['thread_id']):
        raise ValueError('Мир Mini недоступен.')
    return dict(world)


def activate(token,user_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM mini_registration_sessions WHERE token=? AND telegram_user_id=? AND expires_at>?',
                         (token,user_id,int(time.time()))).fetchone()
        if not row or row['completed']: raise ValueError('Ссылка регистрации устарела. Открой панель Mini заново.')
        world=_world(conn,row)
        conn.execute('UPDATE mini_registration_sessions SET active=0 WHERE telegram_user_id=?',(user_id,))
        conn.execute('UPDATE mini_registration_sessions SET active=1 WHERE token=?',(token,))
        return world


def pending(user_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        row=conn.execute('SELECT token FROM mini_registration_sessions WHERE telegram_user_id=? AND active=1 AND completed=0 AND expires_at>? ORDER BY rowid DESC LIMIT 1',
                         (user_id,int(time.time()))).fetchone()
    return row[0] if row else None


def complete(token,user_id,username,name,db_path=DB_PATH):
    name=' '.join(str(name).split())
    if not name or len(name)>64 or name.startswith('/'):
        raise ValueError('Введи имя персонажа от 1 до 64 символов.')
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        session=conn.execute('SELECT * FROM mini_registration_sessions WHERE token=? AND telegram_user_id=? AND expires_at>?',
                             (token,user_id,int(time.time()))).fetchone()
        if not session or not session['active']: raise ValueError('Сессия регистрации устарела.')
        world=_world(conn,session)
        player=conn.execute('SELECT * FROM mini_players WHERE world_id=? AND telegram_user_id=?',(world['id'],user_id)).fetchone()
        created=player is None
        granted=False
        if created:
            cursor=conn.execute('INSERT INTO mini_players(world_id,telegram_user_id,username,character_name,coins,last_seen_at) VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)',
                                (world['id'],user_id,username.strip().lower(),name))
            granted=grant_starter_in_transaction(conn,cursor.lastrowid,world['id'],user_id,name)
            player=conn.execute('SELECT * FROM mini_players WHERE id=?',(cursor.lastrowid,)).fetchone()
        conn.execute('UPDATE mini_registration_sessions SET completed=1 WHERE token=?',(token,))
        return world,{**dict(player),'onboarding_granted':granted},created
