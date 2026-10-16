"""V1.4.4: migrate disposable old SQLite copies and boot old code afterwards.

With --source the supplied database is opened read-only and backed up first.
The original is never opened for writing. No Telegram network / polling.
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = '5e4d4495a189380542cc643c5c8d723b9351b36b'
sys.path.insert(0, str(ROOT))
TABLES = ('inventory', 'characters', 'character_profiles', 'dnd_items', 'dnd_currencies')


def snapshot(path):
    result = {}
    with closing(sqlite3.connect(path)) as conn:
        for table in TABLES:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                columns = [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
                result[table] = columns, conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
    return result


def assert_preserved(path, before):
    with closing(sqlite3.connect(path)) as conn:
        for table, (columns, rows) in before.items():
            projection = ','.join('"' + col + '"' for col in columns)
            assert conn.execute(f'SELECT {projection} FROM {table} ORDER BY rowid').fetchall() == rows, table
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def run_old(code, path, script):
    env = dict(os.environ, BOT_TOKEN='123456:offline-shop-migration', PYTHONIOENCODING='utf-8')
    env.pop('PYTHONPATH', None)
    prelude = ("import sys;from pathlib import Path;import app.config as config;"
               "config.DB_PATH=Path(sys.argv[1]);config.LEGACY_DB_PATH=config.DB_PATH.with_name('unused.db');")
    subprocess.run([sys.executable, '-c', prelude + script, str(path)], cwd=code, env=env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, help='Read-only source, copied with SQLite backup')
    parser.add_argument('--old-ref', default=BASE)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='dnd-shop-migration-') as folder:
        temp = Path(folder)
        archive = temp / 'old.zip'
        old_code = temp / 'old'
        old_code.mkdir()
        subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(archive),
                        args.old_ref, 'app', 'check_bot.py'], cwd=ROOT, check=True)
        with zipfile.ZipFile(archive) as source:
            source.extractall(old_code)
        seed = temp / 'seed.db'
        run_old(old_code, seed, """
from app.db.schema import init_db
init_db()
from app.db.inventory import add_inventory_item
from app.db.dnd_items import create_currency
from app.db.character_registry import create_character
create_character(694384548,0,'@migration_fixture','Fixture')
create_currency(694384548,0,'Fixture gold',1,'1',is_base=True,db_path=config.DB_PATH)
create_currency(694384548,0,'Fixture third',3,'1',db_path=config.DB_PATH)
add_inventory_item(694384548,0,'@migration_fixture','Fixture gold',50)
import sqlite3
with sqlite3.connect(config.DB_PATH) as conn:
    for name,qty in [('Legacy null','3'),('Legacy real','2.5'),('Legacy text','historical')]:
        conn.execute("INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description) VALUES(694384548,0,'@migration_fixture',?,?,'Historical')",(name,qty))
""")
        sources = [seed]
        if args.source:
            if not args.source.is_file():
                raise ValueError('--source does not exist')
            sources.append(args.source.resolve())
        for index, source_path in enumerate(sources):
            copy = temp / f'copy-{index}.db'
            with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(copy)) as target:
                source.backup(target)
            before = snapshot(copy)
            from unittest.mock import patch
            from app.db import schema
            with patch.object(schema, 'DB_PATH', copy), patch.object(schema, 'LEGACY_DB_PATH', temp / 'unused.db'):
                for _ in range(3):
                    schema.init_db()
            assert_preserved(copy, before)
            from app.db.dnd_shop import migrate
            with closing(sqlite3.connect(copy)) as conn, conn:
                conn.execute('DROP TABLE dnd_shop_money')
                conn.execute('DROP TABLE dnd_shop_trades')
                conn.execute('DROP TABLE dnd_shop_operations')
                conn.execute('DROP TABLE dnd_shop_sessions')
                conn.execute('DROP TABLE dnd_shop_owners')
                conn.execute('DROP TABLE dnd_shop_skus')
            # Fault injection confirms additive DDL rolls back as one transaction.
            with closing(sqlite3.connect(copy)) as conn:
                conn.set_authorizer(lambda action, *rest: sqlite3.SQLITE_DENY
                                    if action == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK)
                try:
                    with conn:
                        migrate(conn)
                except sqlite3.DatabaseError:
                    pass
                else:
                    raise AssertionError('Migration fault was not injected')
                conn.set_authorizer(None)
                assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'dnd_shop_%'").fetchall()
            with patch.object(schema, 'DB_PATH', copy):
                schema.init_db()
            assert_preserved(copy, before)
            run_old(old_code, copy, """
from app.db.schema import init_db
init_db()
from app.db.inventory import get_inventory
get_inventory(694384548,0,'@migration_fixture')
import check_bot
check_bot.main()
""")
            assert_preserved(copy, before)
            print(f'OK: source {index}, historical values preserved, repeated migration, DDL rollback, old startup, integrity and foreign keys')
    print('OK: ordinary D&D shop migration checks')


if __name__ == '__main__':
    main()
