"""One persistent favorites list per scoped Mini player, capped by schema."""
import sqlite3
from app.config import DB_PATH
from app.mini.db import connect_mini_db


def _clean(conn, player_id):
    conn.execute("""DELETE FROM mini_hero_favorites WHERE player_id=? AND hero_id NOT IN (
        SELECT ph.hero_id FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
        WHERE ph.player_id=?)""", (player_id,player_id))


def _valid(conn, player_id, hero_id):
    if not conn.execute("""SELECT 1 FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id
        WHERE ph.player_id=? AND ph.hero_id=?""",(player_id,hero_id)).fetchone():
        raise ValueError('Этот герой больше недоступен в коллекции.')


def get_favorites(player_id, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        _clean(conn,player_id)
        return [r[0] for r in conn.execute('SELECT hero_id FROM mini_hero_favorites WHERE player_id=? ORDER BY position',(player_id,))]


def is_favorite(player_id, hero_id, db_path=DB_PATH):
    return hero_id in get_favorites(player_id,db_path)


def add_favorite(player_id, hero_id, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        _clean(conn,player_id);_valid(conn,player_id,hero_id)
        rows=conn.execute('SELECT hero_id,position FROM mini_hero_favorites WHERE player_id=?',(player_id,)).fetchall()
        if hero_id in [r[0] for r in rows]: return False
        if len(rows)==3: raise ValueError('Выбери, кого заменить в избранном.')
        position=next(n for n in range(3) if n not in [r[1] for r in rows])
        conn.execute('INSERT INTO mini_hero_favorites VALUES(?,?,?)',(player_id,hero_id,position))
        return True


def remove_favorite(player_id, hero_id, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        return bool(conn.execute('DELETE FROM mini_hero_favorites WHERE player_id=? AND hero_id=?',(player_id,hero_id)).rowcount)


def replace_favorite(player_id, old_id, new_id, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        _clean(conn,player_id);_valid(conn,player_id,new_id)
        rows=dict(conn.execute('SELECT hero_id,position FROM mini_hero_favorites WHERE player_id=?',(player_id,)))
        if new_id in rows: return False
        if old_id not in rows: raise ValueError('Кнопка замены устарела. Открой карточку заново.')
        conn.execute('UPDATE mini_hero_favorites SET hero_id=? WHERE player_id=? AND hero_id=?',(new_id,player_id,old_id))
        return True


def save_order(player_id, hero_ids, db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        _clean(conn,player_id)
        current={r[0] for r in conn.execute('SELECT hero_id FROM mini_hero_favorites WHERE player_id=?',(player_id,))}
        if len(hero_ids)>3 or len(set(hero_ids))!=len(hero_ids) or set(hero_ids)!=current:
            raise ValueError('Список избранных изменился. Открой его заново.')
        conn.execute('DELETE FROM mini_hero_favorites WHERE player_id=?',(player_id,))
        conn.executemany('INSERT INTO mini_hero_favorites VALUES(?,?,?)',[(player_id,h,n) for n,h in enumerate(hero_ids)])
