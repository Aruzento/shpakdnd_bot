import re
import shlex
import sqlite3
import time
from app.config import DB_PATH
from app.mini.db import connect_mini_db

MAX_TITLE_LENGTH = 48
MAX_DAYS = 365


def parse_duration(value):
    match = re.fullmatch(r'([1-9]\d*)d', value)
    if not match or int(match[1]) > MAX_DAYS:
        raise ValueError('Срок: от 1d до 365d, например 7d.')
    return int(match[1]) * 86400


def validate_title(text):
    text = text.strip()
    if not text or len(text) > MAX_TITLE_LENGTH:
        raise ValueError('Титул должен содержать от 1 до 48 символов.')
    if any(ord(c)<32 or c in '[]\u2028\u2029' for c in text):
        raise ValueError('Титул должен быть одной строкой без квадратных скобок.')
    return text


def parse_command(text):
    try:
        args = shlex.split(text or '')
    except ValueError as error:
        raise ValueError('Проверь кавычки титула.') from error
    if len(args)!=4 or args[0].split('@')[0]!='/supertitle' or not re.fullmatch(r'@[A-Za-z0-9_]{1,32}', args[1]):
        raise ValueError('Использование: /supertitle @user 7d "Титул"')
    return args[1].lower(), parse_duration(args[2]), validate_title(args[3])


def get_active_title(player_id, db_path=DB_PATH, *, now=None, conn=None):
    when = int(time.time() if now is None else now)
    def lookup(connection):
        # Legacy pre-migration presentation remains usable during recovery.
        exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mini_titles'").fetchone()
        if not exists:
            return None
        row = connection.execute("""SELECT text FROM mini_titles
            WHERE player_id=? AND status='active' AND expires_at>?""",(player_id,when)).fetchone()
        return row[0] if row else None
    if conn is not None:
        return lookup(conn)
    with connect_mini_db(db_path) as connection:
        return lookup(connection)


def issue_title(player_id, text, duration_seconds, admin_user_id, operation_key,
                db_path=DB_PATH, *, now=None):
    text=validate_title(text)
    if type(duration_seconds) is not int or not 86400<=duration_seconds<=MAX_DAYS*86400:
        raise ValueError('Недопустимый срок титула.')
    if not operation_key:
        raise ValueError('Не указан ключ выдачи титула.')
    when=int(time.time() if now is None else now)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT * FROM mini_titles WHERE operation_key=?',(operation_key,)).fetchone()
        if old:
            return dict(old)
        player=conn.execute('SELECT world_id FROM mini_players WHERE id=?',(player_id,)).fetchone()
        if not player:
            raise ValueError('Mini-игрок не найден.')
        conn.execute("UPDATE mini_titles SET status='replaced' WHERE player_id=? AND status='active'",(player_id,))
        conn.execute("""UPDATE mini_public_notifications SET status='cancelled'
            WHERE player_id=? AND kind='title_expired' AND status='pending'""",(player_id,))
        cursor=conn.execute("""INSERT INTO mini_titles
            (player_id,text,expires_at,granted_by,operation_key,created_at) VALUES(?,?,?,?,?,?)""",
            (player_id,text,when+duration_seconds,admin_user_id,operation_key,when))
        return dict(conn.execute('SELECT * FROM mini_titles WHERE id=?',(cursor.lastrowid,)).fetchone())


def get_title_target(world_id, username, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        rows=conn.execute('SELECT * FROM mini_players WHERE world_id=? AND lower(username)=?',
                          (world_id,username.lower())).fetchall()
    if len(rows)!=1:
        raise ValueError('Mini-игрок не найден или имя пользователя неоднозначно.')
    return dict(rows[0])
