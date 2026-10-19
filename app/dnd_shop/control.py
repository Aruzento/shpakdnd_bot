"""Topic lifecycle and recoverable publication. Lock order: catalog EX -> SQLite."""
import hashlib
import json
from fractions import Fraction
from pathlib import Path

from app.context import normalize_username
from app.dnd_shop.builder import build, encode_products, index, read_map, read_master
from app.dnd_shop.catalog import CatalogError, atomic_bytes, bounded_bytes, parse_catalog, snapshot
from app.dnd_shop.pricing import preflight

PLAN_KEYS = ('pricing_mode', 'factor', 'base_item_id', 'currency_signature', 'currency_units')


def admin(shop, actor):
    _, username, chat, thread = actor
    topic = shop.scope(chat, thread)
    if not username or not topic.get('admin') or normalize_username(username) != normalize_username(topic['admin']):
        raise CatalogError('Недостаточно прав: требуется администратор текущей темы.')
    return chat, thread


def state(conn, scope):
    return conn.execute('SELECT * FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?', scope).fetchone()


def enabled(shop, conn, scope):
    shop.scope(*scope)
    row = state(conn, scope)
    if row and row['pricing_mode'] == 'm' and row['settings_version'] != 2:
        raise CatalogError('Магазин временно закрыт: прежний -m требует отдельной миграции исходных цен и обязательств.')
    # Pre-control eight-column catalogs already contain base-unit prices.
    if row and not row['enabled']:
        raise CatalogError('Магазин временно закрыт.')
    if conn.execute('SELECT 1 FROM dnd_shop_publications WHERE source_path=?', (str(shop.csv_path),)).fetchone():
        raise CatalogError('Создание каталога не завершено. Администратору нужно повторить /shopcreate.')
    if row:
        if row['pricing_mode'] not in ('b', 'm') or not row['base_item_id'] or not row['currency_signature']:
            raise CatalogError('Повреждены настройки магазина; требуется проверка, торговля заблокирована.')
        plan = preflight(shop, conn, scope, row['pricing_mode'], read_master(shop.loot_path))
        if any(plan[k] != row[k] for k in PLAN_KEYS):
            raise CatalogError('Настройки валют изменились. Нужна проверка обязательств и отдельная миграция.')


def stop(shop, actor):
    scope = admin(shop, actor)
    with shop.transaction(catalog=False) as (conn, _):
        old = state(conn, scope)
        already = bool(old and not old['enabled'])
        conn.execute('''INSERT INTO dnd_shop_settings(chat_id,thread_id,enabled) VALUES(?,?,0)
            ON CONFLICT(chat_id,thread_id) DO UPDATE SET enabled=0,updated_at=CURRENT_TIMESTAMP''', scope)
        # Re-enabling must not resurrect pending buttons made before the stop.
        conn.execute('UPDATE dnd_shop_sessions SET closed=1 WHERE chat_id=? AND thread_id=?', scope)
        pending = conn.execute('SELECT plan FROM dnd_shop_publications WHERE source_path=? AND chat_id=? AND thread_id=?',
                               (str(shop.csv_path), *scope)).fetchone()
        if pending:
            plan = json.loads(pending[0])
            plan['cancelled'] = True
            conn.execute('UPDATE dnd_shop_publications SET plan=? WHERE source_path=?',
                         (json.dumps(plan, ensure_ascii=False, sort_keys=True), str(shop.csv_path)))
        return {'already': already, 'enabled': False}


def persist_state(conn, scope, plan, enabled_value):
    conn.execute('''INSERT INTO dnd_shop_settings
        (chat_id,thread_id,enabled,pricing_mode,factor,base_item_id,currency_signature,currency_units,settings_version)
        VALUES(?,?,?,?,?,?,?,?,2)
        ON CONFLICT(chat_id,thread_id) DO UPDATE SET enabled=excluded.enabled,
        pricing_mode=excluded.pricing_mode,factor=excluded.factor,base_item_id=excluded.base_item_id,
        currency_signature=excluded.currency_signature,currency_units=excluded.currency_units,
        settings_version=2,updated_at=CURRENT_TIMESTAMP''', (*scope, int(enabled_value), *(plan[k] for k in PLAN_KEYS)))


def create(shop, actor, mode):
    from app.dnd_shop.catalog import catalog_lock
    scope = admin(shop, actor)
    with catalog_lock(shop.csv_path, exclusive=True):
        master = read_master(shop.loot_path)
        with shop.transaction(catalog=False) as (conn, _):
            old = state(conn, scope)
            if old and old['pricing_mode'] and old['pricing_mode'] != mode:
                raise CatalogError('Режим цен отличается от ранее созданного. Требуется отдельная рассмотренная миграция.')
            plan = preflight(shop, conn, scope, mode, master)  # before any registry import
            legacy_master = bool(old and old['pricing_mode'] == 'm' and old['settings_version'] == 1)
            historical = (
                conn.execute('SELECT 1 FROM dnd_shop_trades WHERE chat_id=? AND thread_id=? LIMIT 1', scope).fetchone()
                or conn.execute("SELECT 1 FROM dnd_shop_credit WHERE chat_id=? AND thread_id=? AND balance!='0' LIMIT 1", scope).fetchone()
                or conn.execute('SELECT 1 FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND sold>0 LIMIT 1', scope).fetchone()
                or conn.execute('SELECT 1 FROM dnd_shop_credit_moves WHERE chat_id=? AND thread_id=? LIMIT 1', scope).fetchone()
            )
            if (legacy_master or not (old and old['pricing_mode'])) and historical:
                raise CatalogError('Исторический магазин требует отдельной рассмотренной миграции цен и обязательств. История и credit сохранены.')
            if old and not legacy_master and old['currency_signature'] and any(old[k] != plan[k] for k in PLAN_KEYS):
                raise CatalogError('Валютная конфигурация отличается от сохранённой. Требуется отдельная миграция.')
            pending = conn.execute('SELECT * FROM dnd_shop_publications WHERE source_path=?', (str(shop.csv_path),)).fetchone()
            if pending:
                if (pending['chat_id'], pending['thread_id']) != scope:
                    raise CatalogError('Нужно завершить /shopcreate в теме незавершённой публикации.')
                previous_plan = json.loads(pending['plan'])
                if (mode == 'm' and previous_plan.get('pricing_revision') != 2) or any(previous_plan[k] != plan[k] for k in PLAN_KEYS):
                    raise CatalogError('Незавершённая публикация имеет другие настройки; требуется рассмотренное восстановление.')
                data = bytes(pending['candidate'])
                if hashlib.sha256(data).hexdigest() != pending['digest']:
                    raise CatalogError('Повреждён журнал публикации.')
            else:
                mapping = read_map(shop.map_path)
                # Preserve historical codes, but never allocate/rewrite the permanent index here.
                if index(master, previous=mapping, conn=conn, scopes=(scope,)) != mapping:
                    raise CatalogError('Индекс не соответствует историческим SKU. Подготовьте его через CLI index.')
                previous = snapshot(shop.csv_path).products if shop.csv_path.exists() else ()
                scopes = tuple(sorted({(p.chat_id, p.thread_id) for p in previous} | {scope}))
                factors = {(r['chat_id'], r['thread_id']): Fraction(r['factor'])
                           for r in conn.execute('SELECT * FROM dnd_shop_settings')}
                factors[scope] = Fraction(plan['factor'])
                data, _ = build(master, mapping, shop.offers_path, scopes, shop.settings,
                                previous=previous, price_factors=factors)
                # Preserve other topics verbatim even if their pending offers changed.
                selected = [p for p in parse_catalog(data) if (p.chat_id, p.thread_id) == scope]
                data = encode_products([p for p in previous if (p.chat_id, p.thread_id) != scope] + selected)
            # Validate all registry bindings in the same transaction, then roll them back.
            conn.execute('SAVEPOINT candidate_validation')
            persist_state(conn, scope, plan, False)
            shop.sync(conn, shop.catalog_from_bytes(data), force=True)
            conn.execute('ROLLBACK TO candidate_validation')
            conn.execute('RELEASE candidate_validation')
            conn.execute('''INSERT INTO dnd_shop_publications(source_path,chat_id,thread_id,plan,candidate,digest)
                VALUES(?,?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET plan=excluded.plan''',
                (str(shop.csv_path), *scope, json.dumps(plan, ensure_ascii=False, sort_keys=True),
                 data, hashlib.sha256(data).hexdigest()))
        # Intent is durable before replacement. A crash here blocks trading and a
        # same-topic /shopcreate revalidates and finishes it; no gifts or counters reset.
        if shop.csv_path.exists():
            previous_data = bounded_bytes(shop.csv_path)
            if previous_data != data:
                atomic_bytes(Path(str(shop.csv_path) + '.previous'), previous_data)
        atomic_bytes(shop.csv_path, data)
        with shop.transaction(catalog=False) as (conn, _):
            current = conn.execute('SELECT plan FROM dnd_shop_publications WHERE source_path=?', (str(shop.csv_path),)).fetchone()
            if not current or json.loads(current[0]) != plan:
                raise CatalogError('Магазин остановлен во время создания. Повторите /shopcreate после проверки.')
            checked = preflight(shop, conn, scope, mode, read_master(shop.loot_path))
            if any(checked[k] != plan[k] for k in PLAN_KEYS):
                raise CatalogError('Валюты изменились во время публикации. Магазин не включён.')
            persist_state(conn, scope, plan, True)
            shop.sync(conn, snapshot(shop.csv_path), force=True)
            conn.execute('DELETE FROM dnd_shop_publications WHERE source_path=?', (str(shop.csv_path),))
            # Old offers may have been based on an implicit legacy pricing mode.
            conn.execute('UPDATE dnd_shop_sessions SET closed=1 WHERE chat_id=? AND thread_id=?', scope)
            registered, buying = conn.execute('''SELECT count(*),sum(e.buy_enabled=1 AND e.active=1 AND i.active=1 AND e.stock>s.sold)
                FROM dnd_shop_catalog_entries e JOIN dnd_shop_skus s
                ON s.chat_id=e.chat_id AND s.thread_id=e.thread_id AND s.code=e.code
                JOIN dnd_items i ON i.id=e.item_id
                WHERE e.source_path=? AND e.chat_id=? AND e.thread_id=? AND e.present=1
                AND e.is_currency=0''', (str(shop.csv_path), *scope)).fetchone()
            return dict(already=bool(old and old['enabled']), enabled=True, pricing_mode=mode,
                        base_name=plan['base_name'], registered=registered, buying=buying or 0)
