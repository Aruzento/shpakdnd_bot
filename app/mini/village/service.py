"""Timestamp accrual in 1/(32*3600) resource units; no floats or scheduler."""
from app.mini.editorial import text as editorial_text
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
        if now.tzinfo is None: raise ValueError(editorial_text('errors.village.service.timestamp.02'))
        return int(now.timestamp())
    if type(now) is not int: raise ValueError(editorial_text('errors.village.service.timestamp.01'))
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
        rates[row['building']] += rarity_rates[row['rarity']] * (row['stars'] + 1)
    rates['consumption'] = houses * 16
    rates['fed'] = rates['hunt'] >= rates['consumption']
    if not rates['fed']: rates['mine']=rates['market']=0
    return rates


def settle(conn, player_id, now):
    if not conn.in_transaction: raise RuntimeError('Village settlement requires a transaction.')
    village = ensure(conn, player_id, now)
    if now < village['settled_at']: raise ValueError(editorial_text('errors.village.service.settle.01'))
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
    from app.mini.village.balance import load_balance
    rarity_rates={**RATE_UNITS,'mythic':load_balance()['mythic_rate_units']}
    for resident in residents:
        building=resident['building']
        rate=Decimal(rarity_rates[resident['rarity']]*(resident['stars']+1))/32 if building else Decimal(0)
        if building in ('mine','market') and not rates['fed']:rate=Decimal(0)
        resource={'mine':'shards','market':'coins'}.get(building)
        if resource in boosts:rate*=Decimal('1.25')
        resident['hourly_income']=rate
    unsettled=conn.execute('SELECT COUNT(*) FROM mini_player_heroes ph WHERE ph.player_id=? AND NOT EXISTS (SELECT 1 FROM mini_village_residents r WHERE r.player_id=ph.player_id AND r.hero_id=ph.hero_id)',(player_id,)).fetchone()[0]
    return {**v,'unsettled_count':unsettled,'capacity' :v['houses']*10,'residents':residents,'rates':rates,
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
    if not isinstance(key,str) or not key.strip(): raise ValueError(editorial_text('errors.village.service.previous.01'))
    row=conn.execute('SELECT * FROM mini_village_operations WHERE player_id=? AND operation_key=?',(pid,key)).fetchone()
    if row:
        if row['payload'] != payload: raise ValueError(editorial_text('errors.village.service.previous.02'))
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
            raise ValueError(editorial_text('errors.village.service.mutate.01'))
        if action=='buy':
            if expected_houses is None or expected_houses != v['houses']: raise ValueError(editorial_text('errors.village.service.mutate.02'))
            if v['houses']>=5: raise ValueError(editorial_text('errors.village.service.mutate.03'))
            change_balance_in_transaction(conn,player_id,-PRICES[v['houses']], 'Покупка дома','village',player_id,'village:'+operation_key)
            conn.execute('UPDATE mini_villages SET houses=houses+1 WHERE player_id=?',(player_id,))
        elif action=='settle':
            from app.mini.availability import assert_available, assert_not_committed
            assert_available(conn,player_id,hero_id)
            assert_not_committed(conn,player_id,hero_id,now=now)
            count=conn.execute('SELECT COUNT(*) FROM mini_village_residents WHERE player_id=?',(player_id,)).fetchone()[0]
            if count>=v['houses']*10: raise ValueError(editorial_text('errors.village.service.mutate.04'))
            conn.execute('INSERT INTO mini_village_residents(player_id,hero_id) VALUES(?,?)',(player_id,hero_id))
        elif action=='evict':
            if not conn.execute('DELETE FROM mini_village_residents WHERE player_id=? AND hero_id=?',(player_id,hero_id)).rowcount:
                raise ValueError(editorial_text('errors.village.service.mutate.05'))
        elif action=='assign':
            if building not in (*BUILDINGS,None): raise ValueError(editorial_text('errors.village.service.mutate.06'))
            resident=conn.execute('''SELECT v.building FROM mini_village_residents v
                JOIN mini_player_heroes ph ON ph.player_id=v.player_id AND ph.hero_id=v.hero_id
                WHERE v.player_id=? AND v.hero_id=?''',(player_id,hero_id)).fetchone()
            if not resident: raise ValueError(editorial_text('errors.village.service.mutate.07'))
            if building is not None and resident['building'] is not None and resident['building']!=building:
                raise ValueError(editorial_text('errors.village.service.mutate.08'))
            if resident['building']==building:
                result={'applied':False,'action':action,'coins':0,'shards':0,'status':'Герой уже работает здесь.' if building else 'Герой уже не работает.'}
                conn.execute('INSERT INTO mini_village_operations VALUES(?,?,?,?)',(player_id,operation_key,payload,json.dumps(result)))
                return result
            if not conn.execute('UPDATE mini_village_residents SET building=? WHERE player_id=? AND hero_id=?',(building,player_id,hero_id)).rowcount:
                raise ValueError(editorial_text('errors.village.service.mutate.09'))
        elif action=='collect':
            coins,shards=v['coins_units']//UNITS,v['shards_units']//UNITS
            if coins: change_balance_in_transaction(conn,player_id,coins,'Производство деревни','village',player_id,'village:'+operation_key)
            if shards:
                from app.mini.shards import change_shards_in_transaction
                change_shards_in_transaction(conn,player_id,shards)
            conn.execute('UPDATE mini_villages SET coins_units=?,shards_units=?,cycle_started=?,settled_at=? WHERE player_id=?',
                (v['coins_units']%UNITS,v['shards_units']%UNITS,now,now,player_id))
        else: raise ValueError(editorial_text('errors.village.service.mutate.10'))
        conn.execute('UPDATE mini_villages SET revision=revision+1 WHERE player_id=?',(player_id,))
        result={'applied':True,'action':action,'coins':coins if action=='collect' else 0,'shards':shards if action=='collect' else 0}
        conn.execute('INSERT INTO mini_village_operations VALUES(?,?,?,?)',(player_id,operation_key,payload,json.dumps(result)))
        return result


def activate_boost(conn,player_id,resource,*,now=None):
    now=timestamp(now)
    if resource not in ('coins','shards'): raise ValueError(editorial_text('errors.village.service.activate_boost.01'))
    settle(conn,player_id,now)
    old=conn.execute('SELECT * FROM mini_village_boosts WHERE player_id=? AND resource=?',(player_id,resource)).fetchone()
    start=old['starts_at'] if old and old['ends_at']>now else now
    end=max(now,old['ends_at'] if old else now)+CAP_SECONDS
    conn.execute('INSERT INTO mini_village_boosts VALUES(?,?,?,?) ON CONFLICT(player_id,resource) DO UPDATE SET starts_at=excluded.starts_at,ends_at=excluded.ends_at',
        (player_id,resource,start,end))
    return end
