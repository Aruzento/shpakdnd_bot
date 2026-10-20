"""Real 4450-item master, two synthetic scopes; disposable SQLite only.

Outputs reproducible latency/SQL/memory metrics; never connects to Telegram.
critical_wall_s includes lock wait and is a conservative critical-section bound.
"""
import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import schema, inventory
from app.db.dnd_items import create_currency
from app.dnd_shop.audit import audit
from app.dnd_shop.builder import (MAP_COLUMNS, OFFER_COLUMNS, build, csv_bytes,
                                   index, read_master, REFERENCE_SHA256)
from app.dnd_shop.catalog import publish
from app.dnd_shop.service import Shop

SETTINGS = {-991144: {0: {'admin': '@benchmark'}, 2: {'admin': '@benchmark'}}}
ACTOR = 991144, 'benchmark', -991144, 0


def benchmark(master_path):
    master = read_master(master_path)
    assert master.digest == REFERENCE_SHA256 and len(master.rows) == 4450
    mapping = index(master)
    sword = next(code for code, name in mapping.items() if name == 'Короткий меч')
    counters = Counter()
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        def trace(sql):
            counters['sql'] += 1
            if sql == 'SELECT * FROM dnd_items':
                counters['full_registry_scan'] += 1
            if sql.startswith('INSERT INTO dnd_shop_catalog_versions'):
                counters['full_sync'] += 1
        conn.set_trace_callback(trace)
        return conn

    with tempfile.TemporaryDirectory(prefix='dnd-loot-benchmark-') as folder:
        temp = Path(folder)
        db, runtime, stage, offers = [temp / name for name in ('test.db', 'shop.csv', 'stage.csv', 'offers.csv')]
        with patch.object(schema, 'DB_PATH', db), patch.object(schema, 'LEGACY_DB_PATH', temp / 'unused.db'):
            schema.init_db()
        with patch('app.db.dnd_items.TOPIC_SETTINGS', SETTINGS):
            create_currency(-991144, 0, 'Benchmark base', 1, '1', is_base=True, db_path=db)
        with patch.object(inventory, 'DB_PATH', db):
            inventory.add_inventory_item(-991144, 0, '@benchmark', 'Benchmark base', 35000)
        with closing(original_connect(db)) as conn, conn:
            conn.execute("INSERT INTO characters VALUES(-991144,0,'@benchmark','Benchmark')")
        offers.write_bytes(csv_bytes(OFFER_COLUMNS, [dict(chat_id=-991144, thread_id=0,
            code=sword, buy_enabled='true', stock=5)]))  # Explicit synthetic benchmark offer only.
        data, imported = build(master, mapping, offers, [(-991144, 0), (-991144, 2)], SETTINGS)
        stage.write_bytes(data)
        shop = Shop(db, runtime, SETTINGS, loot_path=master_path)
        tracemalloc.start()
        with patch('app.dnd_shop.service.sqlite3.connect', side_effect=connect):
            started = time.perf_counter()
            publish(stage, runtime, shop.validate_staged)
            validation_s = time.perf_counter() - started
            counters.clear()
            started = time.perf_counter()
            session = shop.open(*ACTOR)
            cold = dict(critical_wall_s=time.perf_counter() - started, **counters)
            samples = []
            for _ in range(10):
                counters.clear()
                started = time.perf_counter()
                listing = shop.view(session, ACTOR, 'buy')
                card = shop.view(session, ACTOR, 'buy_card', listing['buttons'][0][1])
                shop.view(session, ACTOR, 'sell')
                samples.append(dict(critical_wall_s=time.perf_counter() - started, **counters))
                assert counters['full_sync'] == counters['full_registry_scan'] == 0
                assert counters['sql'] < 100
            counters.clear()
            started = time.perf_counter()
            bought = shop.execute(session, card['token'], ACTOR)
            buy_metric = dict(critical_wall_s=time.perf_counter() - started, **counters)
            listing = shop.view(session, ACTOR, 'sell')
            sale = shop.view(session, ACTOR, 'sell_card', listing['buttons'][0][1])
            counters.clear()
            started = time.perf_counter()
            sold = shop.execute(session, sale['token'], ACTOR)
            sell_metric = dict(critical_wall_s=time.perf_counter() - started, **counters)
            assert Fraction(bought['value']) == 35000 and Fraction(sold['value']) == 1050
            assert shop.execute(session, sale['token'], ACTOR) == sold
            # Process-like restart parses file but SQLite version prevents any resync.
            import app.dnd_shop.catalog as catalogs
            catalogs._cache = None
            counters.clear()
            Shop(db, runtime, SETTINGS, loot_path=master_path).open(*ACTOR)
            restart = dict(counters)
            assert counters['full_sync'] == 0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with closing(original_connect(db)) as conn:
            reconciliation = audit(conn)
            assert conn.execute('SELECT COUNT(*) FROM dnd_shop_skus').fetchone()[0] == 8900
            assert conn.execute("SELECT sold FROM dnd_shop_skus WHERE chat_id=-991144 AND thread_id=0 AND code=?", (sword,)).fetchone()[0] == 1
        return dict(platform=platform.platform(), python=platform.python_version(), master_sha256=master.digest,
            import_report=imported, snapshot_bytes=len(data), validation_s=validation_s, cold=cold,
            warm=samples, buy=buy_metric, sell=sell_metric, restart=restart,
            traced_peak_bytes=peak, reconciliation=reconciliation, errors=0)



def lifecycle_benchmark(master_path):
    """Real original catalog, explicit monetary mappings, distinct modes and stop/restart."""
    from app.dnd_shop.catalog import snapshot
    from app.dnd_shop.pricing import MASTER_MONEY
    from app.dnd_shop.money import exact_text
    settings = {-991145: {0: {'admin': '@benchmark'}, 2: {'admin': '@benchmark'}}}
    with tempfile.TemporaryDirectory(prefix='dnd-control-benchmark-') as folder:
        temp = Path(folder)
        db, runtime, mapping, offers = (temp / n for n in ('test.db', 'shop.csv', 'index.csv', 'offers.csv'))
        with patch.object(schema, 'DB_PATH', db), patch.object(schema, 'LEGACY_DB_PATH', temp / 'unused.db'):
            schema.init_db()
        master = read_master(master_path)
        codes = index(master)
        mapping.write_bytes(csv_bytes(MAP_COLUMNS, [dict(code=k, name=v) for k, v in codes.items()]))
        with patch('app.db.dnd_items.TOPIC_SETTINGS', settings):
            for thread in (0, 2):
                base_id = create_currency(-991145, thread, 'Золотая монета', 1, '1', is_base=True, db_path=db)
                for name, (unit, quantity) in MASTER_MONEY.items():
                    if name == 'Золотая монета':
                        continue
                    scale = ({'Медная монета': '0.0001', 'Серебряная монета': '0.01', 'Золотая монета': '1'}
                             if thread == 0 else
                             {'Медная монета': '0.01', 'Серебряная монета': '0.1', 'Золотая монета': '1'})
                    value = Fraction(scale[unit]) * quantity
                    create_currency(-991145, thread, name, 1, exact_text(value), db_path=db)
                with closing(sqlite3.connect(db)) as conn, conn:
                    unit_codes = dict(conn.execute('SELECT name,code FROM dnd_items WHERE chat_id=-991145 AND thread_id=?', (thread,)))
                    conn.execute('INSERT INTO characters VALUES(-991145,?,?,?)', (thread, '@benchmark', 'Benchmark'))
                settings[-991145][thread]['shop_currency_units'] = dict(
                    master=unit_codes, universal_copper=unit_codes['Медная монета'], universal_gold=unit_codes['Золотая монета'])
        shop = Shop(db, runtime, settings, loot_path=master_path, map_path=mapping, offers_path=offers)
        timings = {}
        for thread, mode in ((0, 'm'), (2, 'b')):
            start = time.perf_counter()
            result = shop.create((991145, 'benchmark', -991145, thread), mode)
            timings['create_' + mode + '_s'] = time.perf_counter() - start
            assert result['registered'] == 4435 and result['buying'] == 0
        sword_code = next(k for k,v in codes.items() if v=='Короткий меч')
        assert snapshot(runtime).by_key[(-991145,0,sword_code)].price == Fraction('3.5')
        assert snapshot(runtime).by_key[(-991145,2,sword_code)].price == Fraction('9.975')
        second = (991145, 'benchmark', -991145, 2)
        with closing(sqlite3.connect(db)) as conn, conn:
            # Explicit inventory workload seed using original registered items.
            conn.execute("""INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description,item_id)
                SELECT chat_id,thread_id,'@benchmark',name,1,description,id FROM dnd_items
                WHERE chat_id=-991145 AND thread_id=2 AND category='item'""")
            short_sword_inventory = conn.execute("SELECT id FROM inventory WHERE name='Короткий меч'").fetchone()[0]
        start = time.perf_counter()
        session = shop.open(*second)
        for _ in range(5):
            assert shop.view(session, second, 'buy')['buttons'] == []
            assert len(shop.view(session, second, 'sell')['buttons']) == 4
        timings['warm_10_screens_s'] = time.perf_counter() - start
        card = shop.view(session, second, 'sell_card', str(short_sword_inventory))
        sold = shop.execute(session, card['token'], second)
        assert Fraction(sold['value']) == Fraction('0.29925')
        assert Fraction(sold['credit_added']) == Fraction('0.29925')
        shop.stop(second)
        recreated = Shop(db, runtime, settings, loot_path=master_path, map_path=mapping, offers_path=offers)
        recreated.create(second, 'b')
        with closing(sqlite3.connect(db)) as conn:
            assert conn.execute('SELECT count(*) FROM dnd_shop_skus').fetchone()[0] == 8900
            assert conn.execute('SELECT count(*) FROM dnd_items WHERE category="item"').fetchone()[0] == 8870
            reconciliation = audit(conn)
        return dict(**timings, scopes=2, runtime_bytes=runtime.stat().st_size,
                    price_short_sword_m=str(snapshot(runtime).by_key[(-991145, 0, next(k for k,v in codes.items() if v=='Короткий меч'))].price),
                    price_short_sword_b=str(snapshot(runtime).by_key[(-991145, 2, next(k for k,v in codes.items() if v=='Короткий меч'))].price),
                    exact_credit=sold['credit_added'], inventory_workload=4435, reconciliation=reconciliation, errors=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loot', type=Path, default=ROOT / 'data/loot.csv')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = benchmark(args.loot)
    from app.dnd_shop.money import payment
    denominations={1:Fraction(1,10000),2:Fraction(1,100),3:Fraction(1)}
    start=time.perf_counter()
    work=[0]
    for index in range(1000):
        price=Fraction(1000000000000)+Fraction(index+1,10000)
        debit,change=payment(price,denominations,{1:2**63-1,2:2**63-1,3:2**63-1},_counter=work)
        assert sum(denominations[i]*n for i,n in debit.items())-sum(denominations[i]*n for i,n in change.items())==price
    assert work[0]==3000
    report['three_coin_large_wallet']=dict(operations=1000,seconds=time.perf_counter()-start,work=work[0],errors=0)
    report["lifecycle"] = lifecycle_benchmark(args.loot)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
