"""Source copper formula and irreversible-history protection on disposable SQLite."""
import csv
import io
import json
import sqlite3
import subprocess
import types
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from unittest.mock import patch

from app.db import schema
from app.db.dnd_shop import migrate
from app.dnd_shop import builder
from app.dnd_shop.audit import audit
from app.dnd_shop.catalog import CatalogError, snapshot
from app.dnd_shop.diagnostics import analyze
from app.dnd_shop.money import CalculationLimit, exact_text
from app.dnd_shop.pricing import MASTER_MONEY, preflight
from tests.v1_4_4 import test_shop_control as control
ROOT = control.ROOT

BASE = '07170497d1bd375b5bd9f265112eddc7e024d08c'


class SourceMoneyTests(unittest.TestCase):
    # Reuse setup/helpers, not inherited test methods.
    for _name in ('setUp', 'sql', 'currency', 'write', 'write_rows', 'add', 'open',
                  'amount', 'buy_offer', 'sale_offer', 'new_shop', 'configure',
                  'thread_two', 'allow', 'balance', 'assert_clean'):
        locals()[_name] = getattr(control.ControlTests, _name)
    del _name

    def all_units(self):
        rows = builder.read_master(self.master).rows
        existing = {item.name for item in rows}
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, delimiter=';',lineterminator='\n')
        writer.writerow(builder.MASTER_COLUMNS)
        writer.writerows((r.name,r.effect,r.description,r.price,r.rarity,r.category) for r in rows)
        values = {'Медная монета': Fraction('0.0001'), 'Серебряная монета': Fraction('0.01'),
                  'Золотая монета': Fraction(1)}
        aliases = self.settings[-111][0]['shop_currency_units']['master']
        for name,(unit,quantity) in MASTER_MONEY.items():
            if name in existing:
                continue
            writer.writerow((name,'нет','Деньги', int(values[unit]*10000*quantity),'обычный','валюта'))
            item_id = self.currency(name, exact_text(values[unit]*quantity))
            aliases[name] = self.sql('SELECT code FROM dnd_items WHERE id=?',(item_id,))[0][0]
        self.master.write_text(stream.getvalue(),encoding='utf-8')
        self.codes = builder.index(builder.read_master(self.master),previous=self.codes)
        self.mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS,[dict(code=k,name=v) for k,v in self.codes.items()]))

    def old_settings(self, enabled=1):
        source = subprocess.check_output(['git','show',BASE+':app/db/dnd_shop.py'],cwd=ROOT)
        module = types.ModuleType('old_shop_schema')
        exec(compile(source,'old_schema','exec'),module.__dict__)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            plan = preflight(self.shop,conn,(-111,0),'m',builder.read_master(self.master))
            conn.execute('DROP TABLE dnd_shop_settings')
            module.migrate(conn)
            conn.execute('''INSERT INTO dnd_shop_settings(chat_id,thread_id,enabled,pricing_mode,factor,
                base_item_id,currency_signature,currency_units) VALUES(-111,0,?,'m','1',?,?,?)''',
                         (enabled,plan['base_item_id'],plan['currency_signature'],plan['currency_units']))

    def migrate_shop(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('PRAGMA foreign_keys=ON')
            migrate(conn)

    def test_three_actual_base_units_and_source_examples(self):
        for base, copper, silver, gold in (
            (self.gold,'0.0001','0.01','1'),
            (self.silver,'0.01','1','100'),
            (self.copper,'1','100','10000')):
            with self.subTest(base=base):
                self.sql('UPDATE dnd_currencies SET is_base=0')
                for item_id,value in ((self.copper,copper),(self.silver,silver),(self.gold,gold)):
                    self.sql('UPDATE dnd_currencies SET base_value=? WHERE item_id=?',(value,item_id))
                self.sql('UPDATE dnd_currencies SET is_base=1 WHERE item_id=?',(base,))
                with closing(sqlite3.connect(self.db)) as conn:
                    conn.row_factory=sqlite3.Row
                    plan=preflight(self.shop,conn,(-111,0),'m',builder.read_master(self.master))
                factor=Fraction(plan['factor'])
                for raw in (1,100,3500,35000,350000000):
                    self.assertEqual(raw*factor, Fraction(raw)*Fraction(copper))
                self.assertEqual(35000*factor, {'1':Fraction(35000),'0.01':Fraction(350),'0.0001':Fraction('3.5')}[copper])
                self.assertEqual(35000*factor*Fraction(3,100), Fraction(1050)*Fraction(copper))

    def test_source_purchase_mixed_change_three_percent_credit_restart(self):
        self.allow('Кошель с монетами')
        self.shop.create(self.owner,'m')
        self.add('Золото',3)
        self.add('Серебро',60)
        result=self.shop.execute(*self.buy_offer(),self.actor)
        self.assertEqual(Fraction(result['value']),Fraction('3.5'))
        self.assertEqual(self.amount('Кошель с монетами'),1)
        self.assert_clean()
        # Pay .105 precisely, independent of buying assortment.
        session,token=self.sale_offer()
        sold=self.shop.execute(session,token,self.actor)
        self.assertEqual(Fraction(sold['value']),Fraction('0.105'))
        self.assertEqual(self.new_shop().execute(session,token,self.actor),sold)
        self.add('Кошель с монетами',1)
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('safe budget')):
            self.shop.execute(*self.sale_offer(),self.actor)
        self.assertEqual(self.balance(),Fraction('0.105'))
        self.add('Золото',4)
        bought=self.shop.execute(*self.buy_offer(),self.actor)
        self.assertEqual(Fraction(bought['credit_spent']),Fraction('0.105'))
        self.assert_clean()

    def test_whole_gold_overpayment_exact_change_in_source_scale(self):
        self.allow('Кошель с монетами')
        self.shop.create(self.owner,'m')
        self.add('Золото',4)
        receipt=self.shop.execute(*self.buy_offer(),self.actor)
        self.assertEqual(Fraction(receipt['value']),Fraction('3.5'))
        self.assertEqual(self.amount('Серебро'),50)
        self.assertEqual(self.sql("SELECT sum(quantity) FROM dnd_shop_money WHERE direction='change'"),[(50,)])
        self.assert_clean()

    def test_all_fifteen_nominals_and_each_corrupt_bundle_fail_closed(self):
        self.all_units()
        before=self.sql('SELECT * FROM dnd_items')
        ids = self.settings[-111][0]['shop_currency_units']['master']
        for name,code in ids.items():
            item_id=self.sql('SELECT id FROM dnd_items WHERE code=?',(code,))[0][0]
            original=self.sql('SELECT base_value FROM dnd_currencies WHERE item_id=?',(item_id,))[0][0]
            # The registry CHECK prevents a non-unit base before preflight.
            if item_id == self.gold:
                with self.assertRaises(sqlite3.IntegrityError):
                    self.sql('UPDATE dnd_currencies SET base_value=? WHERE item_id=?',
                             (exact_text(Fraction(original)*2),item_id))
                continue
            self.sql('UPDATE dnd_currencies SET base_value=? WHERE item_id=?',(exact_text(Fraction(original)*2),item_id))
            with self.subTest(name=name), self.assertRaises(CatalogError):
                self.shop.create(self.owner,'m')
            self.sql('UPDATE dnd_currencies SET base_value=? WHERE item_id=?',(original,item_id))
            self.assertEqual(self.sql('SELECT * FROM dnd_items'),before)
            self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'),[])
        self.shop.create(self.owner,'m')
        self.assertEqual(self.sql("SELECT count(*) FROM dnd_shop_catalog_entries WHERE is_currency=1"),[(0,)])
        self.assert_clean()

    def test_wrong_test_scale_rejected_even_with_correct_names_and_fifteen_aliases(self):
        self.all_units()
        for name,(unit,quantity) in MASTER_MONEY.items():
            code=self.settings[-111][0]['shop_currency_units']['master'][name]
            value=Fraction({'Медная монета':'0.01','Серебряная монета':'0.1','Золотая монета':'1'}[unit])*quantity
            self.sql('UPDATE dnd_currencies SET base_value=? WHERE item_id=(SELECT id FROM dnd_items WHERE code=?)',
                     (exact_text(value),code))
        before=self.sql('SELECT * FROM dnd_items')
        runtime_before=self.csv.read_bytes()
        with self.assertRaisesRegex(CatalogError,'10000'):
            self.shop.create(self.owner,'m')
        self.assertEqual(self.sql('SELECT * FROM dnd_items'),before)
        self.assertEqual(self.csv.read_bytes(), runtime_before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'),[])
        # b keeps its universal 100:1 contract unchanged with this same registry.
        self.shop.create(self.owner,'b')
        code=next(k for k,v in self.codes.items() if v=='Кошель с монетами')
        self.assertEqual(snapshot(self.csv).by_key[(-111,0,code)].price,Fraction('9.975'))

    def test_old_m_history_credit_inventory_and_receipts_preserved_quarantined(self):
        self.add('Золото',100)
        session,token=self.buy_offer()
        receipt=self.shop.execute(session,token,self.actor)
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('budget')):
            self.shop.execute(*self.sale_offer(),self.actor)
        pending=self.buy_offer()
        self.old_settings()
        tables=('inventory','dnd_items','dnd_currencies','dnd_shop_skus','dnd_shop_trades',
                'dnd_shop_money','dnd_shop_credit','dnd_shop_credit_moves','dnd_shop_operations','dnd_shop_owners')
        before={t:self.sql('SELECT * FROM '+t) for t in tables}
        for _ in range(3):
            self.migrate_shop()
            schema.init_db()
        for table,rows in before.items():
            self.assertEqual(self.sql('SELECT * FROM '+table),rows,table)
        self.assertEqual(self.sql('SELECT enabled,settings_version,factor FROM dnd_shop_settings'),[(0,1,'1')])
        self.assertEqual(self.sql('SELECT DISTINCT closed FROM dnd_shop_sessions'),[(1,)])
        for call in (lambda:self.shop.create(self.owner,'m'),
                     lambda:self.shop.execute(*pending,self.actor),
                     lambda:self.shop.execute(session,token,self.actor),
                     lambda:self.new_shop().open(*self.actor)):
            with self.assertRaises(CatalogError):
                call()
        self.assertEqual(self.balance(),3)
        self.assert_clean()

    def test_old_empty_m_explicit_recreate_and_stale_quotes_even_same_copper_price(self):
        # A previous implicit factor 1 is correct only when base really is source
        # copper. Still close all old sessions, never recycle pending tokens.
        self.sql('UPDATE dnd_currencies SET is_base=0')
        for item_id,value in ((self.copper,'1'),(self.silver,'100'),(self.gold,'10000')):
            self.sql('UPDATE dnd_currencies SET base_value=?,is_base=? WHERE item_id=?',
                     (value,int(item_id==self.copper),item_id))
        pending=self.buy_offer()
        self.old_settings()
        self.migrate_shop()
        self.assertEqual(self.shop.create(self.owner,'m')['base_name'],'Медь')
        self.assertEqual(self.sql('SELECT settings_version,factor FROM dnd_shop_settings'),[(2,'1')])
        with self.assertRaises(ValueError):
            self.new_shop().execute(*pending,self.actor)
        self.assert_clean()

    def test_fault_mid_settings_rebuild_is_full_schema_and_data_rollback(self):
        self.old_settings()
        with closing(sqlite3.connect(self.db)) as conn:
            before=conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            saved=conn.execute('SELECT * FROM dnd_shop_settings').fetchall()
            conn.set_authorizer(lambda action,name,*args: sqlite3.SQLITE_DENY
                                if action==sqlite3.SQLITE_INSERT and name=='dnd_shop_settings'
                                else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                with conn:
                    migrate(conn)
            conn.set_authorizer(None)
            self.assertEqual(conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall(),before)
            self.assertEqual(conn.execute('SELECT * FROM dnd_shop_settings').fetchall(),saved)
        self.migrate_shop()
        self.assert_clean()

    def test_existing_b_settings_sessions_and_price_survive_master_schema_upgrade(self):
        self.shop.create(self.owner,'m')
        self.thread_two()
        self.shop.create(self.other,'b')
        actor=(101,'alice',-111,2)
        session=self.shop.open(*actor)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            saved=dict(conn.execute('SELECT * FROM dnd_shop_settings WHERE thread_id=2').fetchone())
        previous=[p for p in snapshot(self.csv).products if p.thread_id==2]
        self.old_settings()
        columns=tuple(saved)
        saved['settings_version']=1
        self.sql('INSERT INTO dnd_shop_settings('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',
                 tuple(saved.values()))
        self.migrate_shop()
        self.assertEqual(self.sql('SELECT enabled,settings_version FROM dnd_shop_settings WHERE thread_id=2'),[(1,1)])
        self.assertEqual(self.sql('SELECT closed FROM dnd_shop_sessions WHERE token=?',(session,)),[(0,)])
        self.new_shop().view(session,actor,'home')
        self.assertEqual([p for p in snapshot(self.csv).products if p.thread_id==2],previous)
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql('UPDATE dnd_shop_settings SET enabled=1 WHERE thread_id=0')
        self.assert_clean()

    def test_unversioned_old_writer_cannot_claim_new_master_formula(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=preflight(self.shop,conn,(-111,0),'m',builder.read_master(self.master))
        with self.assertRaises(sqlite3.IntegrityError):
            # The old INSERT omits settings_version and claims raw base prices.
            self.sql('''INSERT INTO dnd_shop_settings(chat_id,thread_id,enabled,pricing_mode,factor,
                base_item_id,currency_signature,currency_units) VALUES(-111,0,1,'m','1',?,?,?)''',
                (plan['base_item_id'],plan['currency_signature'],plan['currency_units']))
        self.shop.create(self.owner,'m')
        self.assertEqual(self.sql('SELECT enabled,factor,settings_version FROM dnd_shop_settings'),
                         [(1,'1/10000',2)])
        self.assert_clean()

    def test_previous_b_publication_recovers_with_identical_formula(self):
        self.sql("UPDATE dnd_currencies SET base_value='0.01' WHERE item_id=?",(self.copper,))
        self.sql("UPDATE dnd_currencies SET base_value='0.1' WHERE item_id=?",(self.silver,))
        with patch('app.dnd_shop.control.atomic_bytes',side_effect=OSError('power loss')):
            with self.assertRaises(OSError):
                self.shop.create(self.owner,'b')
        plan=json.loads(self.sql('SELECT plan FROM dnd_shop_publications')[0][0])
        plan.pop('pricing_revision')
        self.sql('UPDATE dnd_shop_publications SET plan=?',(json.dumps(plan),))
        self.new_shop().create(self.owner,'b')
        self.assertEqual(snapshot(self.csv).by_key[(-111,0,'sword')].price,Fraction('0.9975'))
        self.assert_clean()

    def test_old_pending_publication_no_settings_cannot_replay_factor_one(self):
        with patch('app.dnd_shop.control.atomic_bytes',side_effect=OSError('power loss')):
            with self.assertRaises(OSError):
                self.shop.create(self.owner,'m')
        plan=json.loads(self.sql('SELECT plan FROM dnd_shop_publications')[0][0])
        plan.pop('pricing_revision')
        self.sql('UPDATE dnd_shop_publications SET plan=?',(json.dumps(plan),))
        with self.assertRaisesRegex(CatalogError,'восстановление'):
            self.shop.create(self.owner,'m')
        with self.assertRaises(CatalogError):
            self.open()
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'),[])

    def test_readonly_diagnosis_measures_loss_and_value_preserving_quantities(self):
        self.all_units()
        self.shop.create(self.owner,'m')
        self.shop.stop(self.owner)
        for name,(unit,quantity) in MASTER_MONEY.items():
            code=self.settings[-111][0]['shop_currency_units']['master'][name]
            value=Fraction({'Медная монета':'0.01','Серебряная монета':'0.1','Золотая монета':'1'}[unit])*quantity
            self.sql('UPDATE dnd_currencies SET base_value=? WHERE item_id=(SELECT id FROM dnd_items WHERE code=?)',
                     (exact_text(value),code))
        self.add('Серебро',5595)
        self.add('Медь',3)
        before=self.db.read_bytes()
        with closing(sqlite3.connect(self.db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            result=analyze(conn,(-111,0))
        self.assertFalse(result['compatible'])
        wallet=result['wallets'][0]
        self.assertEqual(Fraction(wallet['delta']),Fraction('-503.5797'))
        for coin in result['coins']:
            multiplier=10 if coin['item_id']==self.silver else 100
            self.assertEqual(Fraction(coin['preserving_quantity']),coin['quantity']*multiplier)
            self.assertTrue(coin['preserving_quantity_safe'])
        self.assertEqual(self.db.read_bytes(),before)


    def test_source_last_stock_and_last_item_parallel_confirmation(self):
        self.allow('Кошель с монетами',1)
        self.shop.create(self.owner,'m')
        self.add('Золото',4)
        bob=(102,'bob',-111,0)
        self.add('Золото',4,user='@bob')
        offers=[(*self.buy_offer(actor=actor),actor) for actor in (self.actor,bob)]
        def execute(args):
            try:
                return self.new_shop().execute(*args)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(execute,offers))
        self.assertEqual(sum(r is not None for r in results),1)
        winner=self.actor if results[0] else bob
        self.actor=winner
        first=self.sale_offer()
        second=self.sale_offer()
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('budget')):
            with ThreadPoolExecutor(max_workers=2) as pool:
                sales=list(pool.map(execute,[(*first,winner),(*second,winner)]))
        self.assertEqual(sum(r is not None for r in sales),1)
        self.assertEqual(self.balance(),Fraction('0.105'))
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_trades'),[(2,)])
        self.assert_clean()

    def test_source_sql_failure_rolls_back_credit_item_and_saved_result(self):
        self.shop.create(self.owner,'m')
        self.add('Кошель с монетами',1)
        session,token=self.sale_offer()
        before=self.sql('SELECT * FROM inventory')
        original=self.shop.adjust
        def fault(conn,*args,**kwargs):
            original(conn,*args,**kwargs)
            raise sqlite3.OperationalError('injected ledger failure')
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('budget')):
            with patch.object(self.shop,'adjust',side_effect=fault):
                with self.assertRaises(sqlite3.OperationalError):
                    self.shop.execute(session,token,self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'),[])
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_credit_moves'),[])
        self.assertEqual(self.sql('SELECT result FROM dnd_shop_operations WHERE token=?',(token,)),[(None,)])
        receipt=self.shop.execute(session,token,self.actor)
        self.assertEqual(Fraction(receipt['value']),Fraction('0.105'))
        self.assert_clean()


if __name__=='__main__':
    unittest.main()
