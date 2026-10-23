"""V1.4.4: migrate disposable old SQLite copies and boot old code afterwards.

With --source the supplied database is opened read-only and backed up first.
The original is never opened for writing. No Telegram network / polling.
"""
import argparse
import ast
import hashlib
import json
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
MAIN_BASE = 'd9856b2038a36490f93a4f5613ba7887698ac7e8'
SHOP_BASE = '96ff183c08101c9a3bed079c3eeefac95c22cc2b'
CONTROL_BASE = '6165e5137d34fb0efa7c39bb7fd61fcbad3524a4'
PRICING_BASE = '07170497d1bd375b5bd9f265112eddc7e024d08c'
FORMULA_BASE = '6ef5d8bbac29f2b938b9c21448e1466abe676dc1'
COINS_BASE = 'db0d5f5aa13554e15bf5cf1ffa89b13df8a6e0f8'
THREE_COIN_BASE = '7a2fcd30ca59478a97cbe6e8bb51c2b7770415f1'
DEFAULT_OLD_REFS = (BASE, MAIN_BASE, SHOP_BASE, CONTROL_BASE, PRICING_BASE, FORMULA_BASE, COINS_BASE, THREE_COIN_BASE)
sys.path.insert(0, str(ROOT))
# Only these additive V1.4.4 tables may appear during startup. Existing
# tables (including older shop tables) are historical data, never exempted.
SHOP_TABLES = frozenset((
    'dnd_shop_skus', 'dnd_shop_owners', 'dnd_shop_sessions', 'dnd_shop_operations',
    'dnd_shop_trades', 'dnd_shop_money', 'dnd_shop_catalog_versions',
    'dnd_shop_catalog_entries', 'dnd_shop_credit', 'dnd_shop_credit_moves',
    'dnd_shop_credit_redemptions', 'dnd_shop_credit_coins', 'dnd_shop_settings',
    'dnd_shop_publications', 'dnd_shop_test_migrations', 'dnd_coin_migrations',
))


def typed_rows(rows):
    # SQLite INTEGER/REAL and BLOB/TEXT must not compare equal accidentally.
    return [tuple((type(value), value) for value in row) for row in rows]


def publication_additions(before):
    """Independent model of the existing v1_4_2_ui INSERT OR IGNORE contract."""
    columns, rows = before.get('mini_ui_publications',
                               (['player_id', 'operation_key', 'status'], []))
    assert columns == ['player_id', 'operation_key', 'status'], 'mini_ui_publications schema'
    existing = {(row[0], row[1]) for row in rows}
    source_columns, shares = before.get('mini_public_shares', ([], []))
    expected = set()
    for share in shares:
        player = share[source_columns.index('player_id')]
        key = share[source_columns.index('operation_key')]
        keys = {key}
        parts = key.split(':')
        if len(parts) == 3 and parts[0] == 'share':
            keys.add('share:' + parts[1] + '.' + parts[2])
        for compatible in keys:
            if (player, compatible) not in existing:
                expected.add((player, compatible, 'sending'))
    return expected


def assert_publications(actual, original, additions):
    # INSERT appends; every pre-existing row, status and SQLite type is immutable.
    assert typed_rows(actual[:len(original)]) == typed_rows(original), 'mini_ui_publications existing rows'
    inserted = typed_rows(actual[len(original):])
    expected = typed_rows(additions)
    assert len(inserted) == len(expected) and set(inserted) == set(expected), 'mini_ui_publications unexpected inserts'


def new_shop_tables(before, after):
    v15_tables={'mini_mythic_ledger','mini_mythic_operations','mini_mythic_pets','mini_mythic_pet_days','mini_mythic_raids','mini_mythic_raid_attacks','mini_mythic_raid_damage','mini_mythic_raid_rewards','mini_mythic_raid_audit','mini_mythic_raid_publications','mini_tower_lunar_choices','mini_combat_arise','mini_boss_admin_audit','mini_boss_timeline','story_sessions','story_participants','story_votes','story_steps','story_publications','story_operations','story_enrollment'}
    introduced = after.keys() - before.keys()
    assert introduced <= SHOP_TABLES | v15_tables, 'Unexpected new tables: ' + repr(introduced - SHOP_TABLES - v15_tables)
    assert before.keys() <= after.keys(), 'Historical table removed'
    # Startup creates schema, never economic rows in newly introduced tables.
    result = {name: after[name] for name in introduced}
    assert all(not rows for _, rows in result.values()), 'Unexpected new shop data'
    return result


def schema_snapshot(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()


def assert_repeated(path, migrated, introduced, expected_schema):
    """Freeze all columns after first startup, checking new tables separately."""
    actual = snapshot(path)
    assert schema_snapshot(path) == expected_schema, 'Schema changed after first startup'
    for table, expected in introduced.items():
        assert actual.get(table) == expected, 'New shop table changed: ' + table
    assert actual.keys() == migrated.keys(), 'Tables changed after first startup'
    for table, (columns, rows) in migrated.items():
        assert actual[table][0] == columns, 'Columns changed: ' + table
        assert typed_rows(actual[table][1]) == typed_rows(rows), 'Repeated startup changed: ' + table


def snapshot(path):
    result = {}
    with closing(sqlite3.connect(path)) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                columns = [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
                result[table] = columns, conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
    return result


def assert_preserved(path, before, *, upgrading_settings=False):
    additions = publication_additions(before)
    with closing(sqlite3.connect(path)) as conn:
        for table, (columns, rows) in before.items():
            projection = ','.join('"' + col + '"' for col in columns)
            actual = conn.execute(f'SELECT {projection} FROM {table} ORDER BY rowid').fetchall()
            if table == 'mini_ui_publications':
                assert_publications(actual, rows, additions)
            elif table == 'dnd_shop_settings':
                mode_index, enabled_index = columns.index('pricing_mode'), columns.index('enabled')
                expected = []
                for row in rows:
                    row = list(row)
                    if upgrading_settings and row[mode_index] == 'm':
                        row[enabled_index] = 0
                    expected.append(tuple(row))
                assert typed_rows(actual) == typed_rows(expected), table
            elif table == 'dnd_shop_sessions':
                closed = columns.index('closed')
                # Only the legacy CHECK rebuild closes sessions. A database
                # already using formula-aware settings must preserve every row.
                master_scopes = set(conn.execute("SELECT chat_id,thread_id FROM dnd_shop_settings WHERE pricing_mode='m' AND settings_version=1")) if upgrading_settings else set()
                expected = []
                for row in rows:
                    row = list(row)
                    if (row[columns.index('chat_id')], row[columns.index('thread_id')]) in master_scopes:
                        row[closed] = 1
                    expected.append(tuple(row))
                assert typed_rows(actual) == typed_rows(expected), table
            else:
                assert typed_rows(actual) == typed_rows(rows), table
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def run_old(code, path, script, topics_source=None):
    env = dict(os.environ, BOT_TOKEN='123456:offline-shop-migration', PYTHONIOENCODING='utf-8')
    env.pop('PYTHONPATH', None)
    prelude = ("import sys;from pathlib import Path;import app.config as config;"
               "config.DB_PATH=Path(sys.argv[1]);config.LEGACY_DB_PATH=config.DB_PATH.with_name('unused.db');")
    if topics_source:
        prelude += "import app.topics as scoped_topics;exec(" + repr(topics_source) + ",scoped_topics.__dict__);"
    subprocess.run([sys.executable, '-c', prelude + script, str(path)], cwd=code, env=env, check=True)


def verify_ref(old_ref, source_path=None, topics_source=None):
    print(f'CHECK: shop migration from old_ref={old_ref}', flush=True)
    with tempfile.TemporaryDirectory(prefix='dnd-shop-migration-') as folder:
        temp = Path(folder)
        archive = temp / 'old.zip'
        old_code = temp / 'old'
        old_code.mkdir()
        subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(archive),
                        old_ref, 'app', 'check_bot.py'], cwd=ROOT, check=True)
        with zipfile.ZipFile(archive) as source:
            source.extractall(old_code)
        seed = temp / 'seed.db'
        run_old(old_code, seed, """
from app.db.schema import init_db
init_db()
import check_bot
check_bot.main()
from app.db.inventory import add_inventory_item
from app.db.dnd_items import create_currency
from app.db.character_registry import create_character
create_character(694384548,0,'@migration_fixture','Fixture')
create_currency(694384548,0,'Fixture gold',1,'1',is_base=True,db_path=config.DB_PATH)
create_currency(694384548,0,'Fixture third',3,'1',db_path=config.DB_PATH)
add_inventory_item(694384548,0,'@migration_fixture','Fixture gold',50)
# The V1.4.4 baseline already has receipts, sold counters and pending buttons.
if Path('app/dnd_shop/service.py').is_file():
    from app.dnd_shop.service import Shop
    import csv
    catalog = config.DB_PATH.with_suffix('.csv')
    with catalog.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['chat_id','thread_id','code','name','description','price','stock','active'])
        writer.writerow([694384548,0,'legacy_sword','Legacy sword','Original',10,5,'true'])
    shop = Shop(config.DB_PATH, catalog, {694384548:{0:{'admin':'@fixture'}}})
    actor = 934568, 'migration_fixture', 694384548, 0
    session = shop.open(*actor)
    listing = shop.view(session, actor, 'buy')
    card = shop.view(session, actor, 'buy_card', listing['buttons'][0][1])
    result = shop.execute(session, card['token'], actor)
    assert shop.execute(session, card['token'], actor)['trade_id'] == result['trade_id']
    shop.view(session, actor, 'buy')
import sqlite3
with sqlite3.connect(config.DB_PATH) as conn:
    # 071 has factor=1 constrained settings. Freeze economic rows, not new prices.
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'dnd_shop_settings' in tables:
        item_id = conn.execute("SELECT id FROM dnd_items WHERE name='Fixture gold'").fetchone()[0]
        settings_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='dnd_shop_settings'").fetchone()[0]
        enabled = int('CHECK(settings_version=1)' in settings_sql)
        conn.execute("INSERT INTO dnd_shop_settings(chat_id,thread_id,enabled,pricing_mode,base_item_id,currency_signature) VALUES(694384548,0,?,'m',?,?)", (enabled,item_id,'0'*64))
    for name,qty in [('Legacy null','3'),('Legacy real','2.5'),('Legacy text','historical')]:
        conn.execute("INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description) VALUES(694384548,0,'@migration_fixture',?,?,'Historical')",(name,qty))
""")
        sources = [seed]
        if source_path:
            if not source_path.is_file():
                raise ValueError('--source does not exist')
            sources.append(source_path.resolve())
        for index, source_path in enumerate(sources):
            copy = temp / f'copy-{index}.db'
            with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(copy)) as target:
                source.backup(target)
            before = snapshot(copy)
            with closing(sqlite3.connect(copy)) as conn:
                original_settings=conn.execute("SELECT sql FROM sqlite_master WHERE name='dnd_shop_settings'").fetchone()
                upgrading_settings=bool(original_settings and 'CHECK(settings_version=1)' in original_settings[0])
            from unittest.mock import patch
            from app.db import schema
            from app.db.dnd_shop import migrate
            from app.mini.schema import init_mini_db
            fault = temp / f'fault-{index}.db'
            with closing(sqlite3.connect(copy)) as origin, closing(sqlite3.connect(fault)) as target:
                origin.backup(target)
            with closing(sqlite3.connect(fault)) as conn:
                schema_before = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
                has_settings = conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_shop_settings'").fetchone()
                settings_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='dnd_shop_settings'").fetchone()
                upgrading = settings_sql and 'CHECK(settings_version=1)' in settings_sql[0]
                if not has_settings or upgrading:
                    conn.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_CREATE_TABLE and name == 'dnd_shop_settings' else sqlite3.SQLITE_OK)
                    try:
                        with conn:
                            migrate(conn)
                    except sqlite3.DatabaseError:
                        pass
                    else:
                        raise AssertionError('Migration fault was not injected')
                    conn.set_authorizer(None)
                    assert conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall() == schema_before
            with closing(sqlite3.connect(fault)) as conn:
                has_coin_journal=conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_coin_migrations'").fetchone()
                if not has_coin_journal:
                    original_schema=conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
                    conn.set_authorizer(lambda action,name,*rest: sqlite3.SQLITE_DENY
                        if action==sqlite3.SQLITE_CREATE_TABLE and name=='dnd_coin_migrations' else sqlite3.SQLITE_OK)
                    try:
                        with conn:
                            migrate(conn)
                    except sqlite3.DatabaseError:
                        pass
                    else:
                        raise AssertionError('Coin journal DDL fault was not injected')
                    conn.set_authorizer(None)
                    assert conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()==original_schema
            assert snapshot(fault) == before
            with closing(sqlite3.connect(fault)) as conn:
                assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
                assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            with patch.object(schema, 'DB_PATH', copy), patch.object(schema, 'LEGACY_DB_PATH', temp / 'unused.db'):
                schema.init_db()
                init_mini_db(copy)
                assert_preserved(copy, before, upgrading_settings=upgrading_settings)
                migrated = snapshot(copy)
                introduced = new_shop_tables(before, migrated)
                migrated_schema = schema_snapshot(copy)
                for _ in range(2):
                    schema.init_db()
                    init_mini_db(copy)
                    assert_preserved(copy, before, upgrading_settings=upgrading_settings)
                    assert_repeated(copy, migrated, introduced, migrated_schema)
            print('Expected Mini publication inserts:', len(publication_additions(before)))
            from app.dnd_shop.audit import audit
            with closing(sqlite3.connect(copy)) as conn:
                print('Journal reconciliation:', audit(conn))
            if index and topics_source:
                # Same private configuration, current source and migrated COPY.
                # Never write this configuration into the working checkout.
                run_old(ROOT, copy, "import check_bot;check_bot.main()", topics_source=topics_source)
                assert_preserved(copy, before, upgrading_settings=upgrading_settings)
                assert_repeated(copy, migrated, introduced, migrated_schema)
            run_old(old_code, copy, """
from app.db.schema import init_db
init_db()
from app.db.inventory import get_inventory
get_inventory(694384548,0,'@migration_fixture')
import check_bot
check_bot.main()
""", topics_source=topics_source if index else None)
            assert_preserved(copy, before, upgrading_settings=upgrading_settings)
            assert_repeated(copy, migrated, introduced, migrated_schema)
            print(f'OK: source {index}, historical values preserved, repeated migration, DDL rollback, old startup, integrity and foreign keys')
    print(f'OK: ordinary D&D shop migration checks old_ref={old_ref}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Read-only source, copied with SQLite backup')
    parser.add_argument('--source-metadata', type=Path, help='Private backup metadata with captured literal TOPIC_SETTINGS')
    parser.add_argument('--old-ref', action='append',
                        help='Repeatable baseline override; default: V1.4.3, V1.4.3.1, and checked V1.4.4 shop baseline')
    args = parser.parse_args()
    topics_source = None
    if args.source_metadata:
        if not args.source:
            parser.error('--source-metadata requires --source')
        metadata = json.loads(args.source_metadata.read_text(encoding='utf-8'))
        topics_source = metadata['topics_source']
        if (hashlib.sha256(args.source.read_bytes()).hexdigest()!=metadata['sha256']
                or hashlib.sha256(topics_source.encode('utf-8')).hexdigest()!=metadata['topics_sha256']):
            parser.error('Snapshot / configuration SHA mismatch')
        # Permit only literal configuration assignments; never execute arbitrary
        # remote Python. The archived process receives this validated config.
        for node in ast.parse(topics_source).body:
            if not isinstance(node, ast.Assign):
                parser.error('Captured config needs separate review: non-literal statement')
            ast.literal_eval(node.value)
            for target in node.targets:
                while isinstance(target, ast.Subscript):
                    ast.literal_eval(target.slice)
                    target = target.value
                if not isinstance(target, ast.Name) or target.id!='TOPIC_SETTINGS':
                    parser.error('Unexpected configuration assignment target')
    for old_ref in dict.fromkeys(args.old_ref or DEFAULT_OLD_REFS):
        verify_ref(old_ref, args.source, topics_source)


if __name__ == '__main__':
    main()
