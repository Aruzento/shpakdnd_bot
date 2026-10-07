"""Pending locks, escrow, resolution and payout share one SQLite transaction."""
import json
import sqlite3
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.wallet import change_balance_in_transaction
from app.mini.availability import assert_available
from app.mini.hero_upgrades import calculate_attack
from app.mini.village.service import timestamp
from app.mini.duels.combat import resolve

STAKE=10
EXPIRY=120


def _hero(conn,pid,hid):
    assert_available(conn,pid,hid)
    row=conn.execute('SELECT h.*,ph.stars FROM mini_player_heroes ph JOIN mini_heroes h ON h.id=ph.hero_id WHERE ph.player_id=? AND ph.hero_id=?',(pid,hid)).fetchone()
    return {'id':hid,'code':row['code'],'name':row['name'],'attack':calculate_attack(row['attack'],row['stars'])}


def _close(conn,row,status,*,refund=True,result=None):
    if refund:
        change_balance_in_transaction(conn,row['challenger_id'],STAKE,'Возврат ставки дуэли','duel',row['id'],f"duel:{row['id']}:refund")
    conn.execute('UPDATE mini_duels SET status=?,result_json=? WHERE id=? AND status=\'pending\'',(status,json.dumps(result) if result else None,row['id']))
    conn.execute('DELETE FROM mini_duel_locks WHERE duel_id=?',(row['id'],))


def expire_in_transaction(conn,now):
    rows=conn.execute("SELECT * FROM mini_duels WHERE status='pending' AND expires_at<=?",(now,)).fetchall()
    for row in rows: _close(conn,row,'expired')
    return len(rows)


def expire(db_path=DB_PATH,*,now=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
        return expire_in_transaction(conn,timestamp(now))


def challenge(challenger_id,defender_id,hero_id,operation_key,db_path=DB_PATH,*,now=None):
    if challenger_id==defender_id: raise ValueError('Нельзя вызвать себя на дуэль.')
    if not operation_key: raise ValueError('Нужен ключ вызова.')
    now=timestamp(now)
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        expire_in_transaction(conn,now)
        old=conn.execute('SELECT * FROM mini_duels WHERE challenger_id=? AND operation_key=?',(challenger_id,operation_key)).fetchone()
        if old:
            if (old['defender_id'],old['challenger_hero_id'])!=(defender_id,hero_id): raise ValueError('Ключ вызова уже использован.')
            return {**dict(old),'applied':False}
        players=conn.execute('SELECT id,world_id FROM mini_players WHERE id IN (?,?)',(challenger_id,defender_id)).fetchall()
        if len(players)!=2 or players[0]['world_id']!=players[1]['world_id']: raise ValueError('Игроки должны находиться в одном Mini-мире.')
        if conn.execute('SELECT 1 FROM mini_duel_locks WHERE player_id IN (?,?)',(challenger_id,defender_id)).fetchone():
            raise ValueError('Один из игроков уже участвует в дуэли.')
        hero=_hero(conn,challenger_id,hero_id)
        cursor=conn.execute('''INSERT INTO mini_duels(challenger_id,defender_id,challenger_hero_id,challenger_snapshot,expires_at,operation_key)
            VALUES(?,?,?,?,?,?)''',(challenger_id,defender_id,hero_id,json.dumps(hero),now+EXPIRY,operation_key))
        did=cursor.lastrowid
        change_balance_in_transaction(conn,challenger_id,-STAKE,'Ставка дуэли','duel',did,f'duel:{did}:stake:challenger')
        conn.executemany('INSERT INTO mini_duel_locks VALUES(?,?)',((challenger_id,did),(defender_id,did)))
        return {**dict(conn.execute('SELECT * FROM mini_duels WHERE id=?',(did,)).fetchone()),'applied':True}


def get_state(player_id,db_path=DB_PATH,*,now=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('BEGIN IMMEDIATE')
        expire_in_transaction(conn,timestamp(now))
        row=conn.execute('SELECT * FROM mini_duels WHERE challenger_id=? OR defender_id=? ORDER BY id DESC LIMIT 1',(player_id,player_id)).fetchone()
        return dict(row) if row else None


def act(duel_id,actor_id,action,db_path=DB_PATH,*,hero_id=None,expected_hero_id=None,now=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        expire_in_transaction(conn,timestamp(now))
        row=conn.execute('SELECT * FROM mini_duels WHERE id=?',(duel_id,)).fetchone()
        if not row or actor_id not in (row['challenger_id'],row['defender_id']): raise ValueError('Это чужая дуэль.')
        defender=actor_id==row['defender_id']
        if action in ('select','accept','refuse') and not defender: raise ValueError('Действие доступно только защитнику.')
        if action=='cancel' and defender: raise ValueError('Отменить вызов может только инициатор.')
        if row['status']!='pending': return {**dict(row),'applied':False}
        if action=='select':
            hero=_hero(conn,actor_id,hero_id)
            conn.execute('UPDATE mini_duels SET defender_hero_id=?,defender_snapshot=? WHERE id=?',(hero_id,json.dumps(hero),duel_id))
        elif action in ('refuse','cancel'): _close(conn,row,'refused' if action=='refuse' else 'cancelled')
        elif action=='accept':
            hid=row['defender_hero_id']
            if hid is None: raise ValueError('Сначала выбери героя.')
            if expected_hero_id is not None and hid!=expected_hero_id: raise ValueError('Выбор героя изменился. Открой дуэль заново.')
            defender_hero=_hero(conn,actor_id,hid)
            assert_available(conn,row['challenger_id'],row['challenger_hero_id'])
            balance=conn.execute('SELECT coins FROM mini_players WHERE id=?',(actor_id,)).fetchone()[0]
            if balance<STAKE: _close(conn,row,'insufficient')
            else:
                change_balance_in_transaction(conn,actor_id,-STAKE,'Ставка дуэли','duel',duel_id,f'duel:{duel_id}:stake:defender')
                result=resolve(json.loads(row['challenger_snapshot']),defender_hero)
                if result['draw']:
                    for pid in (row['challenger_id'],row['defender_id']):
                        change_balance_in_transaction(conn,pid,STAKE,'Ничья в дуэли','duel',duel_id,f'duel:{duel_id}:draw:{pid}')
                else:
                    winner=row['challenger_id'] if result['winner']=='challenger' else row['defender_id']
                    loser=row['defender_id'] if winner==row['challenger_id'] else row['challenger_id']
                    change_balance_in_transaction(conn,winner,17,'Победа в дуэли','duel',duel_id,f'duel:{duel_id}:winner')
                    from app.mini.shards import change_shards_in_transaction
                    change_shards_in_transaction(conn,loser,3)
                    result.update(winner_id=winner,loser_id=loser)
                conn.execute('UPDATE mini_duels SET defender_snapshot=? WHERE id=?',(json.dumps(defender_hero),duel_id))
                _close(conn,row,'finished',refund=False,result=result)
        else: raise ValueError('Неизвестное действие дуэли.')
        return {**dict(conn.execute('SELECT * FROM mini_duels WHERE id=?',(duel_id,)).fetchone()),'applied':True}
