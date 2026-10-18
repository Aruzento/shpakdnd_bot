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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loot', type=Path, default=ROOT / 'data/loot.csv')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = benchmark(args.loot)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
