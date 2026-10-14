"""Owner-bound expiring private-chat requests, persistent across restarts."""
from app.mini.editorial import text as editorial_text
import secrets
import sqlite3
import time
from app.config import DB_PATH
from app.topic_guard import is_mini_topic
from app.mini.db import connect_mini_db
from app.mini.players import validate_character_name, get_mini_player


def begin(world_id,user_id,db_path=DB_PATH,*,now=None):
    when=int(time.time() if now is None else now)
    token=secrets.token_urlsafe(18)
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        world=conn.execute('SELECT enabled,chat_id,thread_id FROM mini_worlds WHERE id=?',(world_id,)).fetchone()
        if not world or not world[0] or not is_mini_topic(world[1],world[2]):raise ValueError(editorial_text('errors.registration.begin.01'))
        conn.execute('DELETE FROM mini_registration_requests WHERE expires_at<=? AND player_id IS NULL',(when,))
        conn.execute('INSERT INTO mini_registration_requests(token,world_id,telegram_user_id,expires_at) VALUES(?,?,?,?)',
                     (token,world_id,user_id,when+1800))
    return token


def activate(token,user_id,db_path=DB_PATH,*,now=None):
    when=int(time.time() if now is None else now)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT r.* FROM mini_registration_requests r JOIN mini_worlds w ON w.id=r.world_id WHERE token=? AND telegram_user_id=? AND expires_at>? AND w.enabled=1',
                         (token,user_id,when)).fetchone()
        if row:
            world=conn.execute('SELECT chat_id,thread_id FROM mini_worlds WHERE id=?',(row['world_id'],)).fetchone()
            if not is_mini_topic(world[0],world[1]):row=None
        if not row:raise ValueError(editorial_text('errors.registration.activate.01'))
        existing=conn.execute('SELECT id FROM mini_players WHERE world_id=? AND telegram_user_id=?',(row['world_id'],user_id)).fetchone()
        if existing:
            conn.execute('UPDATE mini_registration_requests SET waiting=0,player_id=? WHERE token=?',(existing['id'],token))
        elif row['player_id'] is None:
            conn.execute('UPDATE mini_registration_requests SET waiting=0 WHERE telegram_user_id=?',(user_id,))
            conn.execute('UPDATE mini_registration_requests SET waiting=1 WHERE token=?',(token,))
        return dict(row)


def pending(user_id,db_path=DB_PATH,*,now=None):
    when=int(time.time() if now is None else now)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        row=conn.execute('SELECT r.* FROM mini_registration_requests r JOIN mini_worlds w ON w.id=r.world_id WHERE telegram_user_id=? AND waiting=1 AND player_id IS NULL AND expires_at>? AND w.enabled=1 ORDER BY expires_at DESC LIMIT 1',
                         (user_id,when)).fetchone()
        return dict(row) if row else None


def complete(token,user_id,username,name,db_path=DB_PATH,*,now=None):
    when=int(time.time() if now is None else now)
    name=validate_character_name(name)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        request=conn.execute('SELECT r.* FROM mini_registration_requests r JOIN mini_worlds w ON w.id=r.world_id WHERE token=? AND telegram_user_id=? AND expires_at>? AND w.enabled=1',
                             (token,user_id,when)).fetchone()
        if request:
            scoped=conn.execute('SELECT chat_id,thread_id FROM mini_worlds WHERE id=?',(request['world_id'],)).fetchone()
            if not is_mini_topic(scoped[0],scoped[1]):request=None
        if not request:raise ValueError(editorial_text('errors.registration.complete.01'))
        world=request['world_id']
        existing=conn.execute('SELECT id FROM mini_players WHERE world_id=? AND telegram_user_id=?',(world,user_id)).fetchone()
        granted=False
        if existing: pid=existing['id']
        else:
            if not request['waiting']:raise ValueError(editorial_text('errors.registration.complete.02'))
            pid=conn.execute('INSERT INTO mini_players(world_id,telegram_user_id,username,character_name,coins,last_seen_at) VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)',
                             (world,user_id,username.strip().lower(),name)).lastrowid
            from app.mini.onboarding import grant_starter_in_transaction
            granted=grant_starter_in_transaction(conn,pid,world,user_id,name,announce=False)
        conn.execute('UPDATE mini_registration_requests SET waiting=0,player_id=? WHERE token=?',(pid,token))
    player=get_mini_player(world,user_id,db_path)
    return {**player,'onboarding_granted':granted,'newly_created':not bool(existing)}


def cancel(user_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute('UPDATE mini_registration_requests SET waiting=0 WHERE telegram_user_id=?',(user_id,))
