"""Fragments are per player/hero; crafting is an atomic one-time unlock."""
import sqlite3
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.catalog import load_hero_catalog


def _mythic(code):
    hero=next((h for h in load_hero_catalog()['heroes'] if h['code']==code),None)
    if not hero or hero['rarity']!='mythic': raise ValueError('Нужен мифический герой.')
    return hero


def grant_mythic_fragments(player_id,hero_code,amount,source,idempotency_key,db_path=DB_PATH):
    _mythic(hero_code)
    if type(amount) is not int or amount<=0 or not isinstance(source,str) or not source.strip() or not isinstance(idempotency_key,str) or not idempotency_key.strip(): raise ValueError('Некорректное начисление фрагментов.')
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT * FROM mini_mythic_grants WHERE player_id=? AND operation_key=?',(player_id,idempotency_key)).fetchone()
        if old:
            if (old['hero_code'],old['amount'],old['source'])!=(hero_code,amount,source): raise ValueError('Ключ операции уже использован.')
            return {'applied':False}
        conn.execute('INSERT INTO mini_mythic_grants VALUES(?,?,?,?,?)',(player_id,idempotency_key,hero_code,amount,source))
        conn.execute('''INSERT INTO mini_mythic_fragments VALUES(?,?,?) ON CONFLICT(player_id,hero_code)
            DO UPDATE SET amount=mini_mythic_fragments.amount+excluded.amount''',(player_id,hero_code,amount))
        return {'applied':True,'amount':conn.execute('SELECT amount FROM mini_mythic_fragments WHERE player_id=? AND hero_code=?',(player_id,hero_code)).fetchone()[0]}


def craft(player_id,hero_code,db_path=DB_PATH):
    content=_mythic(hero_code)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        hero=conn.execute("SELECT id FROM mini_heroes WHERE code=? AND rarity='mythic'",(hero_code,)).fetchone()
        if not hero: raise ValueError('Каталог мифических героев ещё не загружен.')
        if conn.execute('SELECT 1 FROM mini_player_heroes WHERE player_id=? AND hero_id=?',(player_id,hero['id'])).fetchone():
            raise ValueError('Этот мифический герой уже создан.')
        updated=conn.execute('UPDATE mini_mythic_fragments SET amount=amount-? WHERE player_id=? AND hero_code=? AND amount>=?',
            (content['fragment_cost'],player_id,hero_code,content['fragment_cost']))
        if not updated.rowcount: raise ValueError('Недостаточно фрагментов.')
        conn.execute('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(player_id,hero['id']))
        return {'code':hero_code,'crafted':True}


def list_heroes(player_id,db_path=DB_PATH):
    content=[h for h in load_hero_catalog()['heroes'] if h['rarity']=='mythic']
    with connect_mini_db(db_path) as conn:
        ids=dict(conn.execute("SELECT code,id FROM mini_heroes WHERE rarity='mythic'"))
        amounts=dict(conn.execute('SELECT hero_code,amount FROM mini_mythic_fragments WHERE player_id=?',(player_id,)))
        owned={r[0] for r in conn.execute('SELECT h.code FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=?',(player_id,))}
    return [{**h,'id':ids.get(h['code']),'fragments':amounts.get(h['code'],0),'owned':h['code'] in owned} for h in content]


def craft_by_id(player_id,hero_id,db_path=DB_PATH):
    with connect_mini_db(db_path) as conn:
        row=conn.execute("SELECT code FROM mini_heroes WHERE id=? AND rarity='mythic'",(hero_id,)).fetchone()
    if not row: raise ValueError('Мифический герой не найден.')
    return craft(player_id,row[0],db_path)
