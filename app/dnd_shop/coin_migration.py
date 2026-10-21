"""Atomic, value-preserving normalization; no exchange-rate or history changes."""
import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from app.context import normalize_username
from app.dnd_shop.catalog import CatalogError, MAX_INTEGER, catalog_lock, bounded_bytes
from app.dnd_shop.coins import configured_profile
from app.dnd_shop.audit import audit

TABLE='dnd_coin_migrations'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS dnd_coin_migrations (
        id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256)=64),
        plan TEXT NOT NULL CHECK(length(plan)<=1048576),
        result TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def snapshot(conn, *, protected=False):
    names=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    excluded={TABLE}
    if protected:
        excluded.update(('inventory','dnd_shop_settings','dnd_shop_sessions'))
    result={}
    for name in names:
        if name not in excluded:
            result[name]=[[repr(v) for v in r] for r in conn.execute('SELECT * FROM "'+name.replace('"','""')+'" ORDER BY rowid')]
    result['_schema']=[list(r) for r in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND tbl_name!=? ORDER BY type,name",(TABLE,))]
    return digest(canonical(result).encode())


def plan_wallets(shop,conn,scope):
    conn.row_factory=sqlite3.Row
    topic=shop.scope(*scope)
    money=configured_profile(conn,scope,topic)
    if not money:
        raise CatalogError('Нормализация требует подтверждённых ID/code основных монет и связок.')
    currency_rows,denominations=shop.currencies(conn,dict(chat_id=scope[0],thread_id=scope[1]))
    rows=conn.execute('SELECT * FROM inventory WHERE chat_id=? AND thread_id=? ORDER BY id',scope).fetchall()
    owners=defaultdict(list)
    for row in rows:
        owner=normalize_username(row['username'])
        if not owner or owner!=row['username']:
            raise CatalogError('Неоднозначное историческое имя владельца; нужна отдельная проверка.')
        owners[owner].append(row)
    wallets=[]
    for owner,records in sorted(owners.items()):
        groups=defaultdict(list)
        before=after=Fraction(0)
        ordinary=[]
        for row in records:
            item=shop.resolve(conn,row)
            if item is None and row['item_id'] is not None:
                raise CatalogError('Исторический item_id не существует в теме; нужна отдельная проверка.')
            if item and item['id'] in denominations:
                if type(row['quantity']) is not int or not 0<row['quantity']<=MAX_INTEGER:
                    raise CatalogError('Некорректное количество валюты у '+owner)
                value=denominations[item['id']]*row['quantity']
                before+=value
                if str(item['id']) in money['bundles']:
                    target,multiplier=money['bundles'][str(item['id'])]
                    groups[target].append((row,item['id'],multiplier))
                else:
                    after+=value
            else:
                ordinary.append(row['id'])
        changes=[]
        for target,sources in sorted(groups.items()):
            quantity=sum(r['quantity']*multiplier for r,_,multiplier in sources)
            if not 0<quantity<=MAX_INTEGER:
                raise CatalogError('Объединение основных монет переполняет SQLite INTEGER: '+owner)
            after+=quantity*denominations[target]
            keep=next((r['id'] for r,item_id,_ in sources if item_id==target),None)
            item=conn.execute('SELECT * FROM dnd_items WHERE id=?',(target,)).fetchone()
            if any(r['id'] in ordinary and r['name'].strip().casefold()==item['name_key'] for r in records):
                raise CatalogError('Название основной монеты конфликтует с обычным предметом.')
            changes.append(dict(item_id=target,name=item['name'],description=item['description'],quantity=quantity,
                                keep_id=keep,source_ids=[r['id'] for r,_,_ in sources],
                                before=[dict(id=r['id'],item_id=r['item_id'],quantity=r['quantity'],
                                             registered_id=item_id,multiplier=multiplier,row=dict(r)) for r,item_id,multiplier in sources]))
        if before!=after:
            raise CatalogError('Стоимость кошелька не сохраняется.')
        if changes:
            wallets.append(dict(username=owner,before_value=str(before),after_value=str(after),changes=changes))
    return money,wallets


def untouched_inventory(shop,conn,scope,money):
    rows=[]
    conn.row_factory=sqlite3.Row
    for row in conn.execute('SELECT * FROM inventory ORDER BY id'):
        if (row['chat_id'],row['thread_id'])==scope:
            item=shop.resolve(conn,row)
            if item and str(item['id']) in money['bundles']:
                continue
        rows.append([repr(v) for v in row])
    return digest(canonical(rows).encode())


def control_fingerprint(conn,scope):
    result={}
    conn.row_factory=sqlite3.Row
    for table,allowed in (('dnd_shop_settings',{'enabled','updated_at'}),
                          ('dnd_shop_sessions',{'closed','expires'})):
        result[table]=[]
        for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid'):
            local=(row['chat_id'],row['thread_id'])==scope
            result[table].append({k:repr(row[k]) for k in row.keys() if not local or k not in allowed})
    return digest(canonical(result).encode())


def prior_journals(conn):
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone()
    rows=[[repr(v) for v in r] for r in conn.execute('SELECT * FROM dnd_coin_migrations ORDER BY rowid')] if exists else []
    return digest(canonical(rows).encode())


def prepare(shop, conn, scope):
    if [tuple(r) for r in conn.execute('PRAGMA integrity_check')]!=[('ok',)] or conn.execute('PRAGMA foreign_key_check').fetchall():
        raise CatalogError('SQLite повреждена; нормализация запрещена.')
    audit(conn)
    state=conn.execute('SELECT * FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?',scope).fetchone()
    if not state or state['settings_version']!=2:
        raise CatalogError('Сначала должна быть завершена отдельная миграция цен; вариант B здесь не выполняется.')
    if conn.execute('SELECT 1 FROM dnd_shop_publications LIMIT 1').fetchone():
        raise CatalogError('Сначала завершите публикацию каталога.')
    from app.dnd_shop.pricing import preflight
    from app.dnd_shop.builder import read_master,verify_snapshot
    from app.dnd_shop.catalog import parse_catalog
    from app.dnd_shop.control import PLAN_KEYS
    approved=preflight(shop,conn,scope,state['pricing_mode'],read_master(shop.loot_path))
    if any(approved[k]!=state[k] for k in PLAN_KEYS):
        raise CatalogError('Валютная конфигурация не соответствует сохранённым настройкам.')
    products=parse_catalog(bounded_bytes(shop.csv_path))
    factors={(r['chat_id'],r['thread_id']):Fraction(r['factor']) for r in conn.execute('SELECT * FROM dnd_shop_settings')}
    verify_snapshot(products,read_master(shop.loot_path),factors)
    if scope not in {(p.chat_id,p.thread_id) for p in products}:
        raise CatalogError('Тема отсутствует в полном runtime каталоге.')
    aliases=json.loads(state['currency_units'])
    bindings={r['code']:r for r in conn.execute('''SELECT s.code,i.* FROM dnd_shop_skus s
        JOIN dnd_items i ON i.id=s.item_id WHERE s.chat_id=? AND s.thread_id=?''',scope)}
    for product in products:
        if (product.chat_id,product.thread_id)!=scope:
            continue
        item=bindings.get(product.code)
        if not item or (item['name_key']!=product.name.casefold() and aliases.get(product.name.casefold())!=item['id']):
            raise CatalogError('SKU/реестр runtime каталога не подтверждён.')
    money,wallets=plan_wallets(shop,conn,scope)
    result=dict(version=1,id='coins-v1:'+str(scope[0])+':'+str(scope[1]),scope=list(scope),
                source_fingerprint=snapshot(conn),protected_fingerprint=snapshot(conn,protected=True),
                profile=money,wallets=wallets,prior_journals=prior_journals(conn),control_fingerprint=control_fingerprint(conn,scope),untouched_inventory=untouched_inventory(shop,conn,scope,money),
                settings=dict(state),runtime_sha256=digest(bounded_bytes(shop.csv_path)))
    if len(canonical(result).encode())>1000000:
        raise CatalogError('План превышает безопасный размер; нужна отдельно рассмотренная пакетная миграция.')
    return result


def normalize(conn,scope,wallets):
    for wallet in wallets:
        for change in wallet['changes']:
            keep=change['keep_id']
            if keep is not None:
                conn.execute('UPDATE inventory SET quantity=?,item_id=? WHERE id=?',
                             (change['quantity'],change['item_id'],keep))
            else:
                keep=conn.execute('''INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description,item_id)
                    VALUES(?,?,?,?,?,?,?)''',(*scope,wallet['username'],change['name'],change['quantity'],
                                              change['description'],change['item_id'])).lastrowid
            for source in change['source_ids']:
                if source!=keep:
                    conn.execute('DELETE FROM inventory WHERE id=?',(source,))


def apply(shop,plan):
    """Caller is an approved guarded CLI or a disposable validator; no nested API commits."""
    scope=tuple(plan['scope'])
    plan_sha=digest(canonical(plan).encode())
    with catalog_lock(shop.csv_path,exclusive=True):
        with shop.transaction(catalog=False) as (conn,_):
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone()
            record=conn.execute('SELECT * FROM dnd_coin_migrations WHERE id=?',(plan['id'],)).fetchone() if exists else None
            if record:
                if record['plan_sha256']!=plan_sha or record['plan']!=canonical(plan):
                    raise CatalogError('Другой план нормализации уже выполнен.')
                return json.loads(record['result'])
            if snapshot(conn)!=plan['source_fingerprint'] or digest(bounded_bytes(shop.csv_path))!=plan['runtime_sha256']:
                raise CatalogError('Данные изменились после backup; нужен новый план.')
            actual=prepare(shop,conn,scope)
            # External evidence is bound by the CLI; all economic fields are recomputed.
            if any(actual[k]!=plan[k] for k in actual):
                raise CatalogError('План нормализации отличается от фактических данных.')
            migrate(conn)
            normalize(conn,scope,plan['wallets'])
            conn.execute('UPDATE dnd_shop_sessions SET closed=1,expires=0 WHERE chat_id=? AND thread_id=?',scope)
            conn.execute('UPDATE dnd_shop_settings SET enabled=0,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND thread_id=?',scope)
            _,after=plan_wallets(shop,conn,scope)
            before_values={r['username']:r['before_value'] for r in plan['wallets']}
            if {r['username']:r['before_value'] for r in after}!=before_values:
                raise CatalogError('Стоимость после нормализации отличается.')
            expected={w['username']:{c['item_id']:c['quantity'] for c in w['changes']} for w in plan['wallets']}
            observed={w['username']:{c['item_id']:c['quantity'] for c in w['changes']} for w in after}
            if expected!=observed or untouched_inventory(shop,conn,scope,plan['profile'])!=plan['untouched_inventory']:
                raise CatalogError('Количество основных монет или посторонние инвентари изменены неправильно.')
            if prior_journals(conn)!=plan['prior_journals']:
                raise CatalogError('Прежний журнал нормализации изменён.')
            if control_fingerprint(conn,scope)!=plan['control_fingerprint']:
                raise CatalogError('Настройки/сессии другой темы или защищённые поля изменены.')
            if snapshot(conn,protected=True)!=plan['protected_fingerprint']:
                raise CatalogError('Изменены исторические/реестровые данные.')
            result=dict(id=plan['id'],plan_sha256=plan_sha,enabled=False,
                        owners=len(after),value_preserved=True,history_preserved=True)
            conn.execute('INSERT INTO dnd_coin_migrations(id,chat_id,thread_id,plan_sha256,plan,result) VALUES(?,?,?,?,?,?)',
                         (plan['id'],*scope,plan_sha,canonical(plan),canonical(result)))
            audit(conn)
            return result
