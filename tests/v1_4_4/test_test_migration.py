"""Synthetic original-catalog fixture; real private TEST copies use the release validator."""
import csv
import hashlib
import json
import shutil
import sqlite3
import tempfile
import subprocess
import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from contextlib import closing, nullcontext, contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from app.db import schema, inventory
from app.db.dnd_items import create_currency
from app.dnd_shop import builder
from app.dnd_shop.audit import audit
from app.dnd_shop.catalog import CatalogError, CatalogBusy, snapshot
from app.dnd_shop.money import CalculationLimit, exact_text
from app.dnd_shop.pricing import MASTER_MONEY, GOLD_FACTOR, preflight
from app.dnd_shop.service import Shop
from app.dnd_shop import test_migration as tm

ROOT=Path(__file__).resolve().parents[2]


def seed(folder, *, credit_last=False):
    """All master rows are original; actors, offers and 11 sales are explicit synthetic data."""
    db=folder/'seed.db'
    runtime=folder/'seed.csv'
    mapping=folder/'index.csv'
    offers=folder/'offers.csv'
    loot=ROOT/'data/loot.csv'
    topics={tm.SCOPE[0]:{0:{'admin':'@fixture'}}}
    with patch.object(schema,'DB_PATH',db),patch.object(schema,'LEGACY_DB_PATH',folder/'unused.db'):
        schema.init_db()
    with patch('app.db.dnd_items.TOPIC_SETTINGS',topics):
        for name,(unit,quantity) in sorted(MASTER_MONEY.items(),key=lambda row:row[0]!='Золотая монета'):
            value=Fraction({'Медная монета':'0.01','Серебряная монета':'0.1','Золотая монета':'1'}[unit])*quantity
            create_currency(*tm.SCOPE,name,1,exact_text(value),is_base=name=='Золотая монета',db_path=db)
    with closing(sqlite3.connect(db)) as conn,conn:
        codes=dict(conn.execute('SELECT name,code FROM dnd_items'))
        conn.execute('INSERT INTO characters VALUES(?,?,?,?)',(*tm.SCOPE,'@fixture','Fixture'))
    topics[tm.SCOPE[0]][0]['shop_currency_units']=dict(master=codes,universal_copper=codes['Медная монета'],
                                                    universal_gold=codes['Золотая монета'])
    master=builder.read_master(loot)
    code_map=builder.index(master,previous={'short_sword':'Короткий меч'})
    mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS,[dict(code=k,name=v) for k,v in code_map.items()]))
    offers.write_bytes(builder.csv_bytes(builder.OFFER_COLUMNS,[dict(chat_id=tm.SCOPE[0],thread_id=0,
                               code='short_sword',buy_enabled='true',stock=4)]))
    data,_=builder.build(master,code_map,offers,[tm.SCOPE],topics)
    runtime.write_bytes(data)
    shop=Shop(db,runtime,topics,loot_path=loot,map_path=mapping,offers_path=offers)
    actor=(991144,'fixture',*tm.SCOPE)
    session=shop.open(*actor)
    with patch.object(inventory,'DB_PATH',db):
        inventory.add_inventory_item(*tm.SCOPE,'@fixture','Короткий меч',12)
        inventory.add_inventory_item(*tm.SCOPE,'@fixture','Серебряная монета',5595)
        inventory.add_inventory_item(*tm.SCOPE,'@fixture','Золотая монета',4)
    with closing(sqlite3.connect(db)) as conn:
        item_id=conn.execute("SELECT id FROM inventory WHERE name='Короткий меч'").fetchone()[0]
    for index in range(11):
        card=shop.view(session,actor,'sell_card',str(item_id))
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('synthetic old credit')) if credit_last and index==10 else nullcontext():
            shop.execute(session,card['token'],actor)
    # A pre-upgrade stopped m state. Same approved old profile as TEST, but
    # synthetic actors/transactions, never copied user data in Git or CI.
    with closing(sqlite3.connect(db)) as conn,conn:
        conn.row_factory=sqlite3.Row
        profile=preflight(shop,conn,tm.SCOPE,'b',master)
        conn.execute('''INSERT INTO dnd_shop_settings(chat_id,thread_id,enabled,pricing_mode,
            factor,base_item_id,currency_signature,currency_units) VALUES(?,?,0,'m','1',?,?,?)''',
            (*tm.SCOPE,profile['base_item_id'],profile['currency_signature'],profile['currency_units']))
    source='TOPIC_SETTINGS = '+repr(topics)+'\n'
    metadata=folder/'capture.json'
    meta=dict(hostname=tm.HOST,scope=list(tm.SCOPE),git_sha='07170497d1bd375b5bd9f265112eddc7e024d08c',
              db_path=tm.DB.as_posix(),db_resolved=tm.DB.as_posix(),
              sha256=tm.digest(db.read_bytes()),runtime_sha256=tm.digest(runtime.read_bytes()),
              master_sha256=master.digest,topics_source=source,topics_sha256=tm.digest(source.encode()),
              captured_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    metadata.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
    return db,runtime,metadata,topics,mapping,offers


class TestOptionBMigrator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=tempfile.TemporaryDirectory(prefix='test-b-seed-')
        cls.fixture=seed(Path(cls.template.name))

    @classmethod
    def tearDownClass(cls):
        cls.template.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='test-b-case-')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        backup,runtime,meta,self.topics,self.mapping,self.offers=self.fixture
        self.backup=self.folder/'backup.db'
        self.original=self.folder/'snapshot.csv'
        self.meta=self.folder/'capture.json'
        for source,target in ((backup,self.backup),(runtime,self.original),(meta,self.meta)):
            shutil.copyfile(source,target)
        self.db=self.folder/'working.db'
        shutil.copyfile(self.backup,self.db)
        self.runtime=self.folder/'shop.csv'
        shutil.copyfile(self.original,self.runtime)
        self.loot=ROOT/'data/loot.csv'
        self.shop=Shop(self.db,self.runtime,self.topics,loot_path=self.loot,map_path=self.mapping,offers_path=self.offers)
        self.plan,self.candidate=tm.prepare(self.backup,self.meta,self.original,self.loot)
        self.sha=tm.digest(tm.canonical(self.plan).encode())
        self.actor=(991144,'fixture',*tm.SCOPE)

    def sql(self,query,args=()):
        with closing(sqlite3.connect(self.db)) as conn,conn:
            return conn.execute(query,args).fetchall()

    def apply_copy(self):
        # This mock exists only in tests. The actual CLI exposes no host bypass.
        self.assertNotEqual(self.db.resolve(),tm.DB)
        with patch.object(tm,'require_target'):
            return tm.apply(self.shop,self.backup,self.meta,self.original,self.plan,self.sha)

    def test_dry_run_preserves_inputs_and_exact_option_b_loss(self):
        before=[p.read_bytes() for p in (self.backup,self.original,self.meta)]
        plan,candidate=tm.prepare(self.backup,self.meta,self.original,self.loot)
        self.assertEqual(plan,self.plan)
        self.assertEqual([p.read_bytes() for p in (self.backup,self.original,self.meta)],before)
        self.assertEqual(Fraction(plan['wallets'][0]['delta']),Fraction('-503.55'))
        self.assertEqual(len(plan['currencies']),15)
        sword=next(p for p in tm.parse_catalog(candidate) if p.code=='short_sword')
        self.assertEqual(sword.price,Fraction('3.5'))
        self.assertEqual(sword.price*Fraction(3,100),Fraction('0.105'))
        self.assertEqual(35000*GOLD_FACTOR,Fraction('9.975'))

    def test_all_values_history_quantities_and_one_shot_receipt_preserved(self):
        inventory_before=self.sql('SELECT * FROM inventory')
        trades=self.sql('SELECT * FROM dnd_shop_trades')
        money=self.sql('SELECT * FROM dnd_shop_money')
        quotes=self.sql('SELECT * FROM dnd_shop_operations')
        result=self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM inventory'),inventory_before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'),trades)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_money'),money)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_operations'),quotes)
        self.assertEqual(self.apply_copy(),result)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_test_migrations'),[(1,)])
        self.assertEqual(self.sql('SELECT DISTINCT closed,expires FROM dnd_shop_sessions'),[(1,0)])
        self.assertEqual(self.sql('SELECT enabled,factor,settings_version FROM dnd_shop_settings'),[(0,'1/10000',2)])
        for row in self.plan['currencies']:
            value,quantity=self.sql('SELECT base_value,currency_quantity FROM dnd_currencies WHERE item_id=?',(row['item_id'],))[0]
            self.assertEqual(quantity,row['currency_quantity'])
            self.assertEqual(Fraction(value)/quantity,Fraction(row['after_nominal']))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(audit(conn)['errors'],0)
            self.assertEqual([tuple(r) for r in conn.execute('PRAGMA integrity_check')],[('ok',)])
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_hostname_path_and_plan_tampering_denied(self):
        with patch.object(tm.socket,'gethostname',return_value='VM-436996'):
            with self.assertRaisesRegex(CatalogError,'VM-338333'):
                tm.require_target(self.shop,{},self.plan)
        with patch.object(tm.socket,'gethostname',return_value=tm.HOST):
            with self.assertRaisesRegex(CatalogError,'paths'):
                tm.require_target(self.shop,{},self.plan)
        old=self.db.read_bytes()
        with self.assertRaises(CatalogError):
            tm.apply(self.shop,self.backup,self.meta,self.original,self.plan,'0'*64)
        altered=dict(self.plan,keep_inventory_quantities=False)
        with patch.object(tm,'require_target'),self.assertRaises(CatalogError):
            tm.apply(self.shop,self.backup,self.meta,self.original,altered,tm.digest(tm.canonical(altered).encode()))
        self.assertEqual(self.db.read_bytes(),old)

    def test_new_operation_or_inventory_drift_rejects_before_writes(self):
        self.sql("UPDATE inventory SET quantity=quantity+1 WHERE name='Серебряная монета'")
        before=self.sql('SELECT * FROM dnd_currencies')
        with self.assertRaisesRegex(CatalogError,'changed state'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_test_migrations'),[])

    def test_historical_credit_is_preserved_at_the_same_gold_base(self):
        folder=self.folder/'credit-fixture'
        folder.mkdir()
        backup,runtime,metadata,topics,mapping,offers=seed(folder,credit_last=True)
        db=self.folder/'credit-working.db'
        shutil.copyfile(backup,db)
        current=self.folder/'credit-runtime.csv'
        shutil.copyfile(runtime,current)
        shop=Shop(db,current,topics,loot_path=self.loot,map_path=mapping,offers_path=offers)
        plan,_=tm.prepare(backup,metadata,runtime,self.loot)
        with closing(sqlite3.connect(db)) as conn:
            before=tm.scoped_rows(conn)
            self.assertTrue(conn.execute('SELECT 1 FROM dnd_shop_credit').fetchone())
        approved=tm.digest(tm.canonical(plan).encode())
        with patch.object(tm,'require_target'):
            result=tm.apply(shop,backup,metadata,runtime,plan,approved)
            with patch.object(tm,'catalog_lock',side_effect=CatalogBusy('synthetic busy')):
                self.assertEqual(tm.apply(shop,backup,metadata,runtime,plan,approved),result)
        with closing(sqlite3.connect(db)) as conn:
            after=tm.scoped_rows(conn)
            for table in ('dnd_shop_credit','dnd_shop_credit_moves','dnd_shop_trades','dnd_shop_money','inventory'):
                self.assertEqual(after[table],before[table],table)
            self.assertEqual(audit(conn)['errors'],0)

    def test_schema_drift_is_not_the_approved_source(self):
        self.sql('CREATE TABLE unexpected_change(value TEXT)')
        before=self.sql('SELECT * FROM dnd_currencies')
        with self.assertRaisesRegex(CatalogError,'changed state'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),before)

    def test_cli_default_is_readonly_and_plan_file_hash_is_exact(self):
        output=self.folder/'plan.json'
        before=self.backup.read_bytes(),self.original.read_bytes()
        command=[sys.executable,str(ROOT/'tools/migrate_dnd_shop_test_b.py'),
                 '--backup',str(self.backup),'--metadata',str(self.meta),
                 '--runtime-snapshot',str(self.original),'--output',str(output)]
        result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,BOT_TOKEN='123456:offline-cli'),
                              capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(tm.digest(output.read_bytes()),json.loads(result.stdout)['plan_sha256'])
        self.assertEqual((self.backup.read_bytes(),self.original.read_bytes()),before)
        # Never overwrite evidence or an already approved plan.
        result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,BOT_TOKEN='123456:offline-cli'),capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.backup.read_bytes(),self.original.read_bytes()),before)

    def test_parallel_apply_keeps_one_journal_and_no_double_resources(self):
        before=self.sql('SELECT * FROM inventory')
        # One shared mock, so concurrent contexts never restore a real guard.
        with patch.object(tm,'require_target'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:tm.apply(self.shop,self.backup,self.meta,self.original,self.plan,self.sha),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_test_migrations'),[(1,)])
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)

    def test_candidate_parsing_is_outside_lock_and_live_sync_is_not_skipped(self):
        locked = False
        real_lock, parse = tm.catalog_lock, self.shop.catalog_from_bytes
        @contextmanager
        def tracked_lock(*args, **kwargs):
            nonlocal locked
            with real_lock(*args, **kwargs):
                locked = True
                try:
                    yield
                finally:
                    locked = False
        def parse_once(data):
            self.assertFalse(locked, 'Full candidate parsing must precede EX lock')
            return parse(data)
        with patch.object(tm,'catalog_lock',tracked_lock), \
                patch.object(self.shop,'catalog_from_bytes',side_effect=parse_once) as parsed, \
                patch.object(self.shop,'sync',wraps=self.shop.sync) as synced:
            self.apply_copy()
        self.assertEqual(parsed.call_count,1)
        self.assertEqual(synced.call_count,2)  # validate before publish AND finalize
        self.assertIs(synced.call_args_list[0].args[1],synced.call_args_list[1].args[1])
        self.assertTrue(all(call.kwargs == {'force':True} for call in synced.call_args_list))

    def test_parsed_candidate_cannot_be_reused_for_other_bytes(self):
        old = self.shop.catalog_from_bytes(self.original.read_bytes())
        with self.assertRaisesRegex(CatalogError,'Parsed candidate differs'):
            tm.sync_existing(self.shop,None,self.candidate,catalog=old)

    def test_master_change_before_lock_rejects_without_economic_changes(self):
        master = self.folder/'private-loot.csv'
        master.write_bytes(self.loot.read_bytes())
        self.shop.loot_path = master
        before = self.sql('SELECT * FROM dnd_currencies')
        inventory_before = self.sql('SELECT * FROM inventory')
        real_lock = tm.catalog_lock
        @contextmanager
        def changed_master(*args, **kwargs):
            with real_lock(*args, **kwargs):
                master.write_bytes(master.read_bytes()+b'\n')
                yield
        with patch.object(tm,'catalog_lock',changed_master), \
                patch.object(tm,'wait_for_receipt',side_effect=AssertionError('do not mask acquired errors')), \
                self.assertRaisesRegex(CatalogError,'Master changed'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),before)
        self.assertEqual(self.sql('SELECT * FROM inventory'),inventory_before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_test_migrations'),[])
        self.assertEqual(self.runtime.read_bytes(),self.original.read_bytes())

    def test_master_change_after_publication_stays_closed_and_recovers(self):
        master = self.folder/'private-loot.csv'
        original_master = self.loot.read_bytes()
        master.write_bytes(original_master)
        self.shop.loot_path = master
        before = self.sql('SELECT * FROM inventory')
        trades = self.sql('SELECT * FROM dnd_shop_trades')
        money = self.sql('SELECT * FROM dnd_shop_money')
        original_publish = tm.publish_locked
        def changed_master(shop,data):
            original_publish(shop,data)
            master.write_bytes(original_master+b'\n')
        with patch.object(tm,'publish_locked',side_effect=changed_master), \
                self.assertRaisesRegex(CatalogError,'Master changed'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('prepared',)])
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])
        master.write_bytes(original_master)
        result = self.apply_copy()
        self.assertEqual(self.apply_copy(),result)
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'),trades)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_money'),money)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_test_migrations'),[(1,)])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(audit(conn)['errors'],0)
            self.assertEqual([tuple(r) for r in conn.execute('PRAGMA integrity_check')],[('ok',)])
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_lock_timeout_joins_only_identical_durable_operation(self):
        entered,waiting,release = threading.Event(),threading.Event(),threading.Event()
        real_lock,real_publish,real_sleep = tm.catalog_lock,tm.publish_locked,tm.time.sleep
        before = self.sql('SELECT * FROM inventory')
        trades = self.sql('SELECT * FROM dnd_shop_trades')
        money = self.sql('SELECT * FROM dnd_shop_money')
        def paused_publish(shop,data):
            entered.set()
            if not release.wait(30):
                raise RuntimeError('synthetic publication barrier expired')
            real_publish(shop,data)
        def joined_sleep(seconds):
            waiting.set()  # a matching prepared intent was already proved
            real_sleep(seconds)
        def immediate_lock(*args,**kwargs):
            return real_lock(*args,**kwargs,timeout=0)  # force the waiter path, not a longer deadline
        with patch.object(tm,'require_target'),patch.object(tm,'catalog_lock',immediate_lock), \
                patch.object(tm,'publish_locked',side_effect=paused_publish), \
                patch.object(tm.time,'sleep',side_effect=joined_sleep),ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(tm.apply,self.shop,self.backup,self.meta,self.original,self.plan,self.sha)
            try:
                self.assertTrue(entered.wait(30))  # intent committed; owner holds EX
                second=pool.submit(tm.apply,self.shop,self.backup,self.meta,self.original,self.plan,self.sha)
                self.assertTrue(waiting.wait(30))
            finally:
                release.set()
            self.assertEqual(first.result(30),second.result(30))
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'),trades)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_money'),money)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_test_migrations'),[(1,)])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(audit(conn)['errors'],0)

    def test_unknown_catalog_holder_does_not_get_extra_wait_or_writes(self):
        before=self.sql('SELECT * FROM dnd_currencies')
        with patch.object(tm,'catalog_lock',side_effect=CatalogBusy('synthetic busy')), \
                patch.object(tm.time,'sleep',side_effect=AssertionError('unknown holder must not be joined')), \
                self.assertRaisesRegex(CatalogError,'synthetic busy'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_test_migrations'),[])

    def test_unreadable_lock_is_not_a_receipt_wait_even_when_complete(self):
        self.apply_copy()
        with patch.object(tm,'catalog_lock',side_effect=CatalogError('unreadable lock')), \
                patch.object(tm,'wait_for_receipt',side_effect=AssertionError('do not mask I/O errors')), \
                self.assertRaisesRegex(CatalogError,'unreadable lock'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_test_migrations'),[(1,)])

    def test_join_of_interrupted_intent_is_bounded_and_never_resumes_writes(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('synthetic crash')),self.assertRaises(OSError):
            self.apply_copy()
        before=self.sql('SELECT * FROM inventory')
        journal=self.sql('SELECT * FROM dnd_shop_test_migrations')
        with patch.object(tm.time,'monotonic',side_effect=[0,tm.RECEIPT_WAIT+1]), \
                patch.object(tm.time,'sleep',side_effect=AssertionError('expired budget')):
            self.assertIsNone(tm.wait_for_receipt(self.shop,self.candidate,self.plan,self.sha))
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_test_migrations'),journal)
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])
        self.assertEqual(self.apply_copy(),self.apply_copy())  # explicit resume, one receipt

    def test_receipt_reader_retries_sqlite_busy_without_writes_or_extra_budget(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('synthetic crash')),self.assertRaises(OSError):
            self.apply_copy()
        with closing(sqlite3.connect(self.db)) as conn:
            before=tm.scoped_rows(conn)
        real_connect=sqlite3.connect
        errors=[]
        class Reader(sqlite3.Connection):
            def execute(reader,sql,*args,**kwargs):
                try:
                    return super().execute(sql,*args,**kwargs)
                except sqlite3.OperationalError as error:
                    errors.append(error.sqlite_errorcode)
                    raise
        with closing(real_connect(self.db,isolation_level=None)) as writer:
            sleeps=[]
            def next_read(seconds):
                sleeps.append(seconds)
                if len(sleeps)==1:
                    writer.execute('BEGIN EXCLUSIVE')
                elif len(sleeps)==2:
                    writer.execute('ROLLBACK')
                else:
                    self.fail('receipt budget must not restart after SQLITE_BUSY')
            try:
                with patch.object(tm.sqlite3,'connect',side_effect=lambda *a,**kw: real_connect(*a,**kw,factory=Reader)), \
                        patch.object(tm.time,'monotonic',side_effect=[0,0,0,tm.RECEIPT_WAIT+1]), \
                        patch.object(tm.time,'sleep',side_effect=next_read):
                    self.assertIsNone(tm.wait_for_receipt(self.shop,self.candidate,self.plan,self.sha))
            finally:
                if writer.in_transaction:
                    writer.execute('ROLLBACK')
        self.assertEqual(errors,[sqlite3.SQLITE_BUSY])
        self.assertEqual(len(sleeps),2)  # blocked read retried; prepared record still cannot return a receipt
        with closing(real_connect(self.db)) as conn:
            self.assertEqual(tm.scoped_rows(conn),before)
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('prepared',)])
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])

    def test_receipt_proof_read_handles_exclusive_commit_within_the_same_budget(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('synthetic crash')),self.assertRaises(OSError):
            self.apply_copy()
        with closing(sqlite3.connect(self.db)) as conn:
            before=tm.scoped_rows(conn)
        with closing(sqlite3.connect(self.db,isolation_level=None)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            try:
                with patch.object(tm.time,'monotonic',side_effect=[0,0,tm.RECEIPT_WAIT+1]), \
                        patch.object(tm.time,'sleep',side_effect=lambda seconds: writer.execute('ROLLBACK')) as sleep:
                    self.assertIsNone(tm.wait_for_receipt(self.shop,self.candidate,self.plan,self.sha))
                self.assertEqual(sleep.call_count,1)  # first proof SELECT was blocked, then read successfully
            finally:
                if writer.in_transaction:
                    writer.execute('ROLLBACK')
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(tm.scoped_rows(conn),before)

    def test_receipt_reader_does_not_retry_sqlite_io_or_corruption(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('synthetic crash')),self.assertRaises(OSError):
            self.apply_copy()
        real_connect=sqlite3.connect
        with closing(real_connect(self.db)) as conn:
            before=tm.scoped_rows(conn)
        for code in (sqlite3.SQLITE_IOERR,sqlite3.SQLITE_CORRUPT):
            class Reader(sqlite3.Connection):
                def execute(reader,sql,*args,**kwargs):
                    if sql.startswith('SELECT * FROM dnd_shop_test_migrations'):
                        error=sqlite3.OperationalError('synthetic unrecoverable read')
                        error.sqlite_errorcode=code
                        raise error
                    return super().execute(sql,*args,**kwargs)
            with self.subTest(sqlite_errorcode=code), \
                    patch.object(tm.sqlite3,'connect',side_effect=lambda *a,**kw: real_connect(*a,**kw,factory=Reader)), \
                    patch.object(tm.time,'sleep') as sleep, \
                    self.assertRaisesRegex(sqlite3.OperationalError,'unrecoverable'):
                tm.wait_for_receipt(self.shop,self.candidate,self.plan,self.sha)
            self.assertEqual(sleep.call_count,1)  # no second read after non-contention errors
        with closing(real_connect(self.db)) as conn:
            self.assertEqual(tm.scoped_rows(conn),before)

    def test_join_rejects_other_plan_and_damaged_publication(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('synthetic crash')),self.assertRaises(OSError):
            self.apply_copy()
        with patch.object(tm.time,'sleep',side_effect=AssertionError('unproven intent')):
            self.assertIsNone(tm.wait_for_receipt(self.shop,self.candidate,self.plan,'0'*64))
            self.sql("UPDATE dnd_shop_publications SET digest=?",('0'*64,))
            self.assertIsNone(tm.wait_for_receipt(self.shop,self.candidate,self.plan,self.sha))
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('prepared',)])
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])

    def test_expired_snapshot_is_not_an_apply_authorization(self):
        meta=json.loads(self.meta.read_text(encoding='utf-8'))
        meta['captured_utc']='2026-01-01T00:00:00Z'
        self.meta.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
        with self.assertRaisesRegex(CatalogError,'fresh'):
            tm.prepare(self.backup,self.meta,self.original,self.loot)

    def test_sql_failure_rolls_back_nominals_and_settings(self):
        before=self.sql('SELECT * FROM dnd_currencies')
        original=tm.change_nominals
        def fail(conn,changes):
            original(conn,changes)
            if conn.execute('PRAGMA database_list').fetchone()[2]:
                raise sqlite3.OperationalError('injected SQL failure in actual target transaction')
        with patch.object(tm,'change_nominals',side_effect=fail),self.assertRaises(sqlite3.Error):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),before)
        self.assertEqual(self.sql('SELECT settings_version,enabled FROM dnd_shop_settings'),[(1,0)])
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_test_migrations'),[])
        self.assertEqual(self.runtime.read_bytes(),self.original.read_bytes())

    def test_crash_before_file_and_after_file_recover_without_double_resources(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('power loss')),self.assertRaises(OSError):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('prepared',)])
        self.assertEqual(self.runtime.read_bytes(),self.original.read_bytes())
        with self.assertRaises(CatalogError):
            self.shop.open(*self.actor)
        with self.assertRaises(CatalogError):
            self.shop.create(self.actor,'m')
        before=self.sql('SELECT * FROM inventory')
        original=tm.publish_locked
        def after_file(shop,data):
            original(shop,data)
            raise OSError('crash after replace')
        with patch.object(tm,'publish_locked',side_effect=after_file),self.assertRaises(OSError):
            self.apply_copy()
        self.assertEqual(self.runtime.read_bytes(),self.candidate)
        result=self.apply_copy()
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertFalse(result['enabled'])
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_publications'),[])

    def test_stop_during_interruption_cancels_resume_and_never_reopens(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('power loss')),self.assertRaises(OSError):
            self.apply_copy()
        self.shop.stop(self.actor)
        with self.assertRaises(CatalogError):
            self.apply_copy()
        with self.assertRaises(CatalogError):
            self.shop.create(self.actor,'m')
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('prepared',)])

    def test_drift_during_interruption_stays_closed(self):
        with patch.object(tm,'publish_locked',side_effect=OSError('power loss')),self.assertRaises(OSError):
            self.apply_copy()
        self.sql("UPDATE inventory SET quantity=quantity+1 WHERE name='Золотая монета'")
        with self.assertRaisesRegex(CatalogError,'State changed'):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])

    def test_final_sql_failure_keeps_publication_recoverable(self):
        original=tm.audit
        def fail(conn):
            row=conn.execute('SELECT phase FROM dnd_shop_test_migrations').fetchone()
            if row and row[0]=='complete':
                raise sqlite3.OperationalError('injected final audit failure')
            return original(conn)
        with patch.object(tm,'audit',side_effect=fail),self.assertRaises(sqlite3.Error):
            self.apply_copy()
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('prepared',)])
        self.assertEqual(self.runtime.read_bytes(),self.candidate)
        self.assertEqual(self.sql('SELECT enabled FROM dnd_shop_settings'),[(0,)])
        self.apply_copy()
        self.assertEqual(self.sql('SELECT phase FROM dnd_shop_test_migrations'),[('complete',)])

    def test_journal_ddl_failure_and_repeated_init_are_additive(self):
        with closing(sqlite3.connect(self.db)) as conn,conn:
            conn.execute('DROP TABLE dnd_shop_test_migrations')
        with closing(sqlite3.connect(self.db)) as conn:
            before=tm.fingerprint(conn)
            tables=conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            conn.set_authorizer(lambda action,name,*rest:sqlite3.SQLITE_DENY
                                if action==sqlite3.SQLITE_CREATE_TABLE and name=='dnd_shop_test_migrations'
                                else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.Error):
                with conn:
                    tm.migrate(conn)
            conn.set_authorizer(None)
            self.assertEqual(tm.fingerprint(conn),before)
            self.assertEqual(conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall(),tables)
            data_before=tm.scoped_rows(conn)
            with conn:
                tm.migrate(conn)
            # The first successful migration adds the journal schema, never data.
            migrated=tm.fingerprint(conn)
            self.assertEqual(tm.scoped_rows(conn),data_before)
            for _ in range(3):
                with conn:
                    tm.migrate(conn)
                self.assertEqual(tm.fingerprint(conn),migrated)

    def test_post_migration_trade_change_credit_duplicate_and_restart(self):
        self.apply_copy()
        self.shop.create(self.actor,'m')
        session=self.shop.open(*self.actor)
        # Small explicitly synthetic wallet workload after preservation was proved.
        self.sql("DELETE FROM inventory WHERE item_id IN (SELECT item_id FROM dnd_currencies)")
        with patch.object(inventory,'DB_PATH',self.db):
            inventory.add_inventory_item(*tm.SCOPE,'@fixture','Золотая монета',4)
        listing=self.shop.view(session,self.actor,'buy')
        card=self.shop.view(session,self.actor,'buy_card',listing['buttons'][0][1])
        bought=self.shop.execute(session,card['token'],self.actor)
        self.assertEqual(Fraction(bought['value']),Fraction('3.5'))
        self.assertEqual(self.shop.execute(session,card['token'],self.actor),bought)
        item=self.sql("SELECT id FROM inventory WHERE name='Короткий меч'")[0][0]
        sale=self.shop.view(session,self.actor,'sell_card',str(item))
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('safe limit')):
            sold=self.shop.execute(session,sale['token'],self.actor)
        self.assertEqual(Fraction(sold['credit_added']),Fraction('0.105'))
        restarted=Shop(self.db,self.runtime,self.topics,loot_path=self.loot,map_path=self.mapping,offers_path=self.offers)
        self.assertEqual(restarted.execute(session,sale['token'],self.actor),sold)
        token=restarted.view(session,self.actor,'credit')['token']
        redeemed=restarted.redeem(session,token,self.actor)
        self.assertEqual(restarted.redeem(session,token,self.actor),redeemed)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(audit(conn)['errors'],0)
