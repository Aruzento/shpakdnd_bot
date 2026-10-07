"""Timestamp accrual in 1/(32*3600) resource units; no floats or scheduler."""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from app.config import DB_PATH
from app.mini.db import connect_mini_db
from app.mini.wallet import change_balance_in_transaction

PRICES = (10, 50, 100, 200, 300)
CAP_SECONDS = 8 * 3600
UNITS = 32 * 3600
RATE_UNITS = {'common':4, 'uncommon':8, 'rare':20, 'legendary':40, 'shadow':40, 'mythic':40}
BUILDINGS = ('mine', 'market', 'hunt')


def timestamp(now=None):
    if now is None: return int(datetime.now(timezone.utc).timestamp())
    if isinstance(now, datetime):
        if now.tzinfo is None: raise ValueError('Требуется datetime с часовым поясом.')
        return int(now.timestamp())
    if type(now) is not int: raise ValueError('Требуется целое время UTC.')
    return now


def ensure(conn, player_id, now):
    conn.execute('INSERT OR IGNORE INTO mini_villages(player_id,cycle_started,settled_at) VALUES(?,?,?)', (player_id,now,now))
    return dict(conn.execute('SELECT * FROM mini_villages WHERE player_id=?',(player_id,)).fetchone())


def production(conn, player_id, houses):
    from app.mini.village.balance import load_balance
    rarity_rates = {**RATE_UNITS, "mythic": load_balance()["mythic_rate_units"]}
    rates = dict.fromkeys(BUILDINGS, 0)
    for row in conn.execute('''SELECT v.building, h.rarity, ph.stars FROM mini_village_residents v
            JOIN mini_heroes h ON h.id=v.hero_id
            JOIN mini_player_heroes ph ON ph.player_id=v.player_id AND ph.hero_id=v.hero_id
            WHERE v.player_id=? AND v.building IS NOT NULL''',(player_id,)):
        rates[row['building']] += rarity_rates[row['rarity']] * min(row['stars'],6)
    rates['consumption'] = houses * 16
    rates['fed'] = rates['hunt'] >= rates['consumption']
    if not rates['fed']: rates['mine']=rates['market']=0
    return rates


def settle(conn, player_id, now):
    if not conn.in_transaction: raise RuntimeError('Village settlement requires a transaction.')
    village = ensure(conn, player_id, now)
    if now < village['settled_at']: raise ValueError('Время деревни не может идти назад.')
    start = village['settled_at']
    end = max(start, min(now,village['cycle_started']+CAP_SECONDS))
    rates = production(conn,player_id,village['houses'])
    for building, resource in (('market','coins'),('mine','shards')):
        rate = rates[building]
        value = rate * (end-start)
        boost = conn.execute('SELECT starts_at,ends_at FROM mini_village_boosts WHERE player_id=? AND resource=?',(player_id,resource)).fetchone()
        if boost:
            overlap = max(0,min(end,boost['ends_at'])-max(start,boost['starts_at']))
            value += rate * overlap // 4
        village[resource+'_units'] += value
    conn.execute('UPDATE mini_villages SET settled_at=?,coins_units=?,shards_units=? WHERE player_id=?',
        (end,village['coins_units'],village['shards_units'],player_id))
    village['settled_at']=end
    return village


def _state(conn,player_id,now):
    v=settle(conn,player_id,now)
    rates=production(conn,player_id,v['houses'])
    residents=[dict(r) for r in conn.execute('''SELECT v.*,h.code,h.name,h.rarity,ph.stars
        FROM mini_village_residents v JOIN mini_heroes h ON h.id=v.hero_id
        JOIN mini_player_heroes ph ON ph.player_id=v.player_id AND ph.hero_id=v.hero_id
        WHERE v.player_id=? ORDER BY h.name''',(player_id,))]
    hourly={k:Decimal(value)/32 for k,value in rates.items() if k!='fed'}
    boosts={r['resource']:r['ends_at'] for r in conn.execute(
        'SELECT resource,ends_at FROM mini_village_boosts WHERE player_id=? AND starts_at<=? AND ends_at>?',
        (player_id,now,now))}
    for building,resource in (('market','coins'),('mine','shards')):
        if resource in boosts: hourly[building] *= Decimal('1.25')
    return {**v,'capacity' :v['houses']*10,'residents':residents,'rates':rates,
        'hourly':hourly,'boosts':boosts,
        'fed':rates['fed'],'next_price':PRICES[v['houses']] if v['houses']<5 else None,
        'seconds_left':max(0,v['cycle_started']+CAP_SECONDS-now),
        'coins':Decimal(v['coins_units'])/UNITS,'shards':Decimal(v['shards_units'])/UNITS}


def get_state(player_id,db_path=DB_PATH,*,now=None):
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        return _state(conn,player_id,timestamp(now))


def _previous(conn,pid,key,payload):
    if not isinstance(key,str) or not key.strip(): raise ValueError('Нужен ключ операции.')
    row=conn.execute('SELECT * FROM mini_village_operations WHERE player_id=? AND operation_key=?',(pid,key)).fetchone()
    if row:
        if row['payload'] != payload: raise ValueError('Ключ уже использован для другой операции.')
        return {**json.loads(row['result_json']),'applied':False}


def mutate(player_id,action,db_path=DB_PATH,*,hero_id=None,building=None,expected_revision=None,expected_houses=None,operation_key,now=None):
    now=timestamp(now)
    payload=json.dumps([action,hero_id,building,expected_revision,expected_houses])
    with connect_mini_db(db_path) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        prior=_previous(conn,player_id,operation_key,payload)
        if prior: return prior
        v=settle(conn,player_id,now)
        if expected_revision is not None and expected_revision != v['revision']:
            raise ValueError('Кнопка деревни устарела. Открой меню заново.')
        if action=='buy':
            if expected_houses is None or expected_houses != v['houses']: raise ValueError('Кнопка покупки устарела.')
            if v['houses']>=5: raise ValueError('Все дома уже куплены.')
            change_balance_in_transaction(conn,player_id,-PRICES[v['houses']], 'Покупка дома','village',player_id,'village:'+operation_key)
            conn.execute('UPDATE mini_villages SET houses=houses+1 WHERE player_id=?',(player_id,))
        elif action=='settle':
            from app.mini.availability import assert_available, assert_not_committed
            assert_available(conn,player_id,hero_id)
            assert_not_committed(conn,player_id,hero_id,now=now)
            count=conn.execute('SELECT COUNT(*) FROM mini_village_residents WHERE player_id=?',(player_id,)).fetchone()[0]
            if count>=v['houses']*10: raise ValueError('В деревне нет свободных мест.')
            conn.execute('INSERT INTO mini_village_residents(player_id,hero_id) VALUES(?,?)',(player_id,hero_id))
        elif action=='evict':
            if not conn.execute('DELETE FROM mini_village_residents WHERE player_id=? AND hero_id=?',(player_id,hero_id)).rowcount:
                raise ValueError('Этот герой не живёт в деревне.')
        elif action=='assign':
            if building not in (*BUILDINGS,None): raise ValueError('Неизвестное производство.')
            if not conn.execute('UPDATE mini_village_residents SET building=? WHERE player_id=? AND hero_id=?',(building,player_id,hero_id)).rowcount:
                raise ValueError('Работник должен жить в деревне.')
        elif action=='collect':
            coins,shards=v['coins_units']//UNITS,v['shards_units']//UNITS
            if coins: change_balance_in_transaction(conn,player_id,coins,'Производство деревни','village',player_id,'village:'+operation_key)
            if shards:
                from app.mini.shards import change_shards_in_transaction
                change_shards_in_transaction(conn,player_id,shards)
            conn.execute('UPDATE mini_villages SET coins_units=?,shards_units=?,cycle_started=?,settled_at=? WHERE player_id=?',
                (v['coins_units']%UNITS,v['shards_units']%UNITS,now,now,player_id))
        else: raise ValueError('Неизвестное действие деревни.')
        conn.execute('UPDATE mini_villages SET revision=revision+1 WHERE player_id=?',(player_id,))
        result={'applied':True,'action':action,'coins':coins if action=='collect' else 0,'shards':shards if action=='collect' else 0}
        conn.execute('INSERT INTO mini_village_operations VALUES(?,?,?,?)',(player_id,operation_key,payload,json.dumps(result)))
        return result


def activate_boost(conn,player_id,resource,*,now=None):
    now=timestamp(now)
    if resource not in ('coins','shards'): raise ValueError('Неизвестный ресурс усиления.')
    settle(conn,player_id,now)
    old=conn.execute('SELECT * FROM mini_village_boosts WHERE player_id=? AND resource=?',(player_id,resource)).fetchone()
    start=old['starts_at'] if old and old['ends_at']>now else now
    end=max(now,old['ends_at'] if old else now)+CAP_SECONDS
    conn.execute('INSERT INTO mini_village_boosts VALUES(?,?,?,?) ON CONFLICT(player_id,resource) DO UPDATE SET starts_at=excluded.starts_at,ends_at=excluded.ends_at',
        (player_id,resource,start,end))
    return end
