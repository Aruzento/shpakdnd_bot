"""Exercise actual index/build/validate/publish commands on disposable files/DB."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import schema
from app.dnd_shop.builder import OFFER_COLUMNS, csv_bytes, read_map, read_master
from app.dnd_shop.catalog import read_catalog
from app.dnd_shop.service import Shop
from app.topics import TOPIC_SETTINGS


def main():
    scope = next((chat, thread) for chat, topics in TOPIC_SETTINGS.items()
                 for thread, settings in topics.items() if settings.get('admin') and not settings.get('mini'))
    with tempfile.TemporaryDirectory(prefix='loot-cli-') as folder:
        temp = Path(folder)
        db, runtime, master, mapping, offers, staged = [temp / name for name in
            ('test.db', 'shop.csv', 'loot.csv', 'map.csv', 'offers.csv', 'staged.csv')]
        shutil.copyfile(ROOT / 'data/loot.csv', master)
        offers.write_bytes(csv_bytes(OFFER_COLUMNS, []))
        with patch.object(schema, 'DB_PATH', db), patch.object(schema, 'LEGACY_DB_PATH', temp / 'unused.db'):
            schema.init_db()
        env = dict(os.environ, BOT_TOKEN='123456:offline-loot-cli')
        common = ['--target', str(runtime), '--loot', str(master), '--db', str(db)]
        def cli(command, *args, success=True):
            result = subprocess.run([sys.executable, '-m', 'app.dnd_shop.cli', command, *args, *common],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
            if success and result.returncode or not success and not result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            if success:
                print(result.stdout.strip(), flush=True)
            return result
        # Negative chat IDs need = form for argparse.
        selected = [f'--scope={scope[0]}:{scope[1]}']
        cli('index', '--map', str(mapping), *selected)
        cli('build', '--map', str(mapping), '--offers', str(offers), '--output', str(staged), *selected)
        cli('validate', str(staged))
        cli('publish', str(staged))
        products = read_catalog(runtime)
        assert len(products) == 4450 and not any(p.buy_enabled for p in products)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('INSERT INTO characters VALUES(?,?,?,?)', (*scope, '@cli_fixture', 'CLI fixture'))
        shop = Shop(db, runtime, loot_path=master)
        actor = 998844, 'cli_fixture', *scope
        session = shop.open(*actor)
        assert shop.view(session, actor, 'buy')['buttons'] == []
        first = runtime.read_bytes()
        index_before = mapping.read_bytes()
        cli('index', '--map', str(mapping), *selected)
        assert mapping.read_bytes() == index_before
        code = next(k for k, name in read_map(mapping).items() if name == 'Короткий меч')
        offers.write_bytes(csv_bytes(OFFER_COLUMNS, [dict(chat_id=scope[0], thread_id=scope[1],
            code=code, buy_enabled='true', stock=1)]))  # Explicit synthetic CLI offer only.
        cli('build', '--map', str(mapping), '--offers', str(offers), '--output', str(staged), *selected)
        cli('publish', str(staged))
        assert Path(str(runtime) + '.previous').read_bytes() == first
        current = runtime.read_bytes()
        staged.write_bytes(b'\xff')
        cli('publish', str(staged), success=False)
        assert runtime.read_bytes() == current
        # Reject a legacy downgrade after a persisted master version.
        staged.write_text('chat_id,thread_id,code,name,description,price,stock,active\n', encoding='utf-8')
        cli('publish', str(staged), success=False)
        cli('audit')
        cli('rollback-check', success=False)
        cli('rollback-check', '--target-ref', '96ff183c08101c9a3bed079c3eeefac95c22cc2b', success=False)
        # A reviewed no-shop target retains all additive tables and liabilities.
        admin = TOPIC_SETTINGS[scope[0]][scope[1]]['admin']
        shop.stop((999, admin, *scope))
        cli('rollback-check', '--target-ref', 'd9856b2038a36490f93a4f5613ba7887698ac7e8')
        cli('rollback-check', '--target-ref', 'HEAD')
        assert read_master(master).digest == read_master(ROOT / 'data/loot.csv').digest
        with closing(sqlite3.connect(db)) as conn:
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        print(json.dumps(dict(real_master=4450, initial_buy_enabled=0, explicit_test_offers=1,
                              previous_retained=True, invalid_publish_preserved=True, errors=0)))


if __name__ == '__main__':
    main()
