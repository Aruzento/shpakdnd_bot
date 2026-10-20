"""Physical coins, exact display and additive wallet normalization."""
import itertools
import csv
import io
import subprocess
import tempfile
import sys
from pathlib import Path
import json
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from fractions import Fraction
from unittest.mock import patch
from app.db import inventory
from app.dnd_shop import coin_migration as migration
from app.dnd_shop.audit import audit
from app.dnd_shop.catalog import CatalogError,MAX_INTEGER
from app.dnd_shop.formatting import amount
from app.dnd_shop.money import MoneyError,payment
from app.dnd_shop.pricing import MASTER_MONEY
from app.handlers import shop as ui
from tests.v1_4_4 import test_shop_control as control
from tests.v1_4_4 import test_source_money as source

PROFILE=dict(ids=[1,2,3],values=['1/10000','1/100','1'],universal=False)


class FormattingTests(unittest.TestCase):
    def test_exact_examples(self):
        for value,text in [
            ('1','1 золотая'),('2','2 золотые'),('5','5 золотых'),
            ('1.501','1 золотая, 50 серебряных, 10 медных'),
            ('3.5','3 золотые, 50 серебряных'),('0.5','50 серебряных'),
            ('0.105','10 серебряных, 50 медных'),('0.01','1 серебряная'),
            ('0.0001','1 медная'),('10.0005','10 золотых, 5 медных'),
            ('0.000003','0,03 медной монеты')]:
            self.assertEqual(amount(Fraction(value),PROFILE),text)
        self.assertEqual(amount(Fraction('0.000003'),PROFILE,credit=True),
                         '0,03 медной монеты (торговый остаток)')
        self.assertIn('1/3 медной монеты',amount(Fraction(1,30000),PROFILE,credit=True))
        self.assertEqual(amount(0,PROFILE),'0')

    def test_russian_plural_and_unconfirmed_scale(self):
        for n,word in [(11,'золотых'),(12,'золотых'),(21,'золотая'),(22,'золотые'),(111,'золотых'),(101,'золотая')]:
            self.assertEqual(amount(Fraction(n),PROFILE),f'{n} {word}')
        self.assertEqual(amount(Fraction('3.5'),None,base_name='жетонов'),'3,5 жетонов')
        universal=dict(PROFILE,values=['1/100','1/10','1'],universal=True)
        self.assertEqual(amount(Fraction('9.975'),universal),
                         '9 универсальных золотых, 9 универсальных серебряных, 7 универсальных медных, 0,5 универсальной медной монеты')

    def test_three_coin_payment_against_full_bounded_oracle(self):
        denominations={1:Fraction(1),2:Fraction(3),3:Fraction(9)}
        for counts in itertools.product(range(4),repeat=3):
            for target in range(1,25):
                options=[]
                for debit in itertools.product(*(range(c+1) for c in counts)):
                    total=sum(n*v for n,v in zip(debit,(1,3,9)))
                    if total<target:
                        continue
                    change=total-target
                    candidates=[]
                    for gold in range(change//9+1):
                        for silver in range((change-gold*9)//3+1):
                            copper=change-gold*9-silver*3
                            vector=(copper,silver,gold)
                            candidates.append((sum(vector),vector))
                    _,credit=min(candidates)
                    options.append((change,sum(debit)+sum(credit),debit,credit))
                if not options:
                    with self.assertRaises(MoneyError):
                        payment(Fraction(target),denominations,dict(zip((1,2,3),counts)))
                else:
                    _,_,debit,credit=min(options)
                    self.assertEqual(payment(Fraction(target),denominations,dict(zip((1,2,3),counts))),
                                     ({i:n for i,n in zip((1,2,3),debit) if n},{i:n for i,n in zip((1,2,3),credit) if n}))

    def test_large_three_coin_wallet_constant_work(self):
        coins={i:Fraction(v) for i,v in zip((1,2,3),PROFILE['values'])}
        counter=[0]
        debit,credit=payment(Fraction('1000000000000.105'),coins,{1:MAX_INTEGER,2:MAX_INTEGER,3:MAX_INTEGER},_counter=counter)
        self.assertEqual(sum(coins[i]*q for i,q in debit.items())-sum(coins[i]*q for i,q in credit.items()),Fraction('1000000000000.105'))
        self.assertLessEqual(counter[0],10)


class CoinIntegrationTests(unittest.TestCase):
    for _name in ('setUp','sql','currency','write','write_rows','add','open','amount','buy_offer',
                  'sale_offer','new_shop','configure','thread_two','allow','balance','assert_clean'):
        locals()[_name]=getattr(control.ControlTests,_name)
    all_units=source.SourceMoneyTests.all_units
    del _name

    def setup_shop(self):
        self.all_units()
        self.allow('Кошель с монетами')
        self.shop.create(self.owner,'m')

    def raw(self,name,quantity,*,null=False,user='@alice'):
        item=self.sql('SELECT id FROM dnd_items WHERE name_key=?',(name.casefold(),))[0][0]
        return self.sql('''INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description,item_id)
            VALUES(-111,0,?,?,?,'legacy',?) RETURNING id''',(user,name,quantity,None if null else item))[0][0]

    def test_all_twelve_admin_bundles_grant_unit_coins(self):
        self.setup_shop()
        expected={}
        for name,(unit,count) in MASTER_MONEY.items():
            if count==1:
                continue
            self.add(name,2)
            target={'Медная монета':'Медь','Серебряная монета':'Серебро','Золотая монета':'Золото'}[unit]
            expected[target]=expected.get(target,0)+2*count
            self.assertEqual(self.amount(name),0)
        for unit,count in expected.items():
            self.assertEqual(self.amount(unit),count)
        self.assert_clean()

    def test_buy_sell_change_credit_and_ui_without_receipt_rewrite(self):
        self.setup_shop()
        self.add('Золото',4)
        session,token=self.buy_offer()
        result=self.shop.execute(session,token,self.actor)
        self.assertEqual(self.amount('Золото'),0)
        self.assertEqual(self.amount('Серебро'),50)
        session,sale=self.sale_offer()
        sold=self.shop.execute(session,sale,self.actor)
        self.assertEqual(self.amount('Серебро'),60)
        self.assertEqual(self.amount('Медь'),50)
        self.assertEqual(Fraction(sold['value']),Fraction('0.105'))
        before=self.sql('SELECT result FROM dnd_shop_operations')
        text=ui.result_text(sold,self.shop.presentation(session,self.actor,sold))
        self.assertIn('10 серебряных, 50 медных',text)
        self.assertEqual(self.sql('SELECT result FROM dnd_shop_operations'),before)
        self.assertEqual(self.new_shop().execute(session,sale,self.actor),sold)
        ids={self.gold,self.silver,self.copper}
        self.assertTrue(all(r[0] in ids for r in self.sql('SELECT item_id FROM dnd_shop_money WHERE trade_id=?',(sold['trade_id'],))))
        self.assert_clean()

    def test_legacy_bundle_payment_issues_only_unit_change(self):
        self.setup_shop()
        self.raw('5 золотых монет',1)
        receipt=self.shop.execute(*self.buy_offer(),self.actor)
        self.assertEqual(self.amount('5 золотых монет'),0)
        self.assertEqual(self.amount('Золото'),1)
        self.assertEqual(self.amount('Серебро'),50)
        self.assertTrue(all(name not in ('5 золотых монет','50 серебряных монет') for name,_ in receipt['credit']))
        self.assert_clean()

    def test_calculation_limit_exact_credit_then_three_core_coin_redemption(self):
        self.setup_shop()
        self.add('Медь',1)  # ordinary currency never offered for sale
        self.add('Кинжал',1)
        # raw 100 -> .01 -> payout .0003 (three copper), then inject a
        # calculation limit to retain the complete, exact obligation.
        with patch('app.dnd_shop.service.payment',side_effect=__import__('app.dnd_shop.money',fromlist=['CalculationLimit']).CalculationLimit('budget')):
            self.shop.execute(*self.sale_offer(),self.actor)
        self.assertEqual(self.balance(),Fraction('0.0003'))
        session=self.open()
        token=self.shop.view(session,self.actor,'credit')['token']
        redeemed=self.shop.redeem(session,token,self.actor)
        self.assertEqual(self.new_shop().redeem(session,token,self.actor),redeemed)
        self.assertEqual(self.amount('Медь'),4)
        self.assert_clean()

    def test_real_sub_copper_credit_redeem_refusal_and_exact_purchase(self):
        self.all_units()
        from app.dnd_shop import builder
        master=builder.read_master(self.master)
        stream=io.StringIO(newline='')
        writer=csv.writer(stream,delimiter=';',lineterminator='\n')
        writer.writerow(builder.MASTER_COLUMNS)
        writer.writerows((r.name,r.effect,r.description,r.price,r.rarity,r.category) for r in master.rows)
        writer.writerow(('Пылинка','нет','synthetic item',1,'обычный','предмет'))
        self.master.write_text(stream.getvalue(),encoding='utf-8')
        self.codes=builder.index(builder.read_master(self.master),previous=self.codes)
        self.mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS,[dict(code=k,name=v) for k,v in self.codes.items()]))
        self.allow('Пылинка')
        self.shop.create(self.owner,'m')
        self.add('Пылинка',1)
        row=self.sql("SELECT id FROM inventory WHERE name='Пылинка'")[0][0]
        session=self.open()
        card=self.shop.view(session,self.actor,'sell_card',str(row))
        result=self.shop.execute(session,card['token'],self.actor)
        self.assertEqual(Fraction(result['credit_added']),Fraction('0.000003'))
        text=ui.result_text(result,self.shop.presentation(session,self.actor,result))
        self.assertIn('0,03 медной монеты (торговый остаток)',text)
        token=self.shop.view(session,self.actor,'credit')['token']
        before=self.sql('SELECT * FROM inventory')
        for shop in (self.shop,self.new_shop()):
            with self.assertRaises(MoneyError):
                shop.redeem(session,token,self.actor)
        self.assertEqual(self.balance(),Fraction('0.000003'))
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        # All-credit payment retains its exact non-coin residual.
        self.assert_clean()

    def test_migration_preserves_credit_history_other_topic_and_blocks_old_button(self):
        self.setup_shop()
        self.add('Меч',1)
        with patch('app.dnd_shop.service.payment',side_effect=__import__('app.dnd_shop.money',fromlist=['CalculationLimit']).CalculationLimit('budget')):
            self.shop.execute(*self.sale_offer(),self.actor)
        self.raw('50 серебряных монет',2)
        self.thread_two()
        aliases=self.settings[-111][2]['shop_currency_units']['master']
        for name,(unit,count) in MASTER_MONEY.items():
            if count==1:
                continue
            value={'Медная монета':1,'Серебряная монета':10,'Золотая монета':100}[unit]*count
            item=self.currency(name,str(value),thread=2)
            aliases[name]=self.sql('SELECT code FROM dnd_items WHERE id=?',(item,))[0][0]
        self.shop.create(self.other,'b')
        self.sql("INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description,item_id) SELECT chat_id,thread_id,'@alice',name,7,'other scope',id FROM dnd_items WHERE chat_id=-111 AND thread_id=2 AND name_key='золото'")
        self.add('Золото',4)
        old_session,old_token=self.buy_offer()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
            protected=migration.snapshot(conn,protected=True)
        balance=self.balance()
        other_before=self.sql('SELECT * FROM inventory WHERE thread_id=2')
        migration.apply(self.shop,plan)
        self.assertEqual(self.balance(),balance)
        self.assertEqual(self.sql('SELECT * FROM inventory WHERE thread_id=2'),other_before)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(migration.snapshot(conn,protected=True),protected)
        self.shop.create(self.owner,'m')
        with self.assertRaises(ValueError):
            self.new_shop().execute(old_session,old_token,self.actor)
        self.assert_clean()

    def test_process_interruption_rolls_back_and_same_plan_recovers(self):
        self.setup_shop()
        self.raw('50 серебряных монет',2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
        payload=Path(self.tmp.name)/'synthetic-process-plan.json'
        payload.write_text(json.dumps(dict(plan=plan,settings=self.settings)),encoding='utf-8')
        before=self.sql('SELECT * FROM inventory')
        code="""import json,os,sys
from app.dnd_shop import coin_migration as cm
from app.dnd_shop.service import Shop
d=json.load(open(sys.argv[1],encoding='utf-8'))
settings={int(c):{int(t):v for t,v in topics.items()} for c,topics in d['settings'].items()}
shop=Shop(sys.argv[2],sys.argv[3],settings,loot_path=sys.argv[4])
original=cm.normalize
def crash(conn,scope,wallets):
    original(conn,scope,wallets)
    os._exit(75)
cm.normalize=crash
cm.apply(shop,d['plan'])
"""
        child=subprocess.run([sys.executable,'-c',code,str(payload),str(self.db),str(self.csv),str(self.master)],cwd=control.ROOT,capture_output=True)
        self.assertEqual(child.returncode,75,child.stderr.decode(errors='replace'))
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        result=migration.apply(self.new_shop(),plan)
        self.assertTrue(result['value_preserved'])
        self.assertEqual(self.amount('Серебро'),100)
        self.assert_clean()

    def test_admin_merges_whitespace_legacy_unit_before_and_after_normalization(self):
        self.setup_shop()
        row=self.raw('Серебро',15,null=True)
        self.sql("UPDATE inventory SET name='  Серебро  ' WHERE id=?",(row,))
        self.add('50 серебряных монет',1)
        self.assertEqual(self.sql('SELECT quantity,item_id FROM inventory WHERE id=?',(row,)),[(65,self.silver)])
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
        migration.apply(self.shop,plan)
        self.add('50 серебряных монет',1)
        self.assertEqual(self.sql('SELECT quantity,item_id FROM inventory WHERE id=?',(row,)),[(115,self.silver)])
        self.assertEqual(self.sql('SELECT count(*) FROM inventory'),[(1,)])
        self.assert_clean()

    def test_admin_overflow_and_ambiguous_existing_rows_rollback(self):
        self.setup_shop()
        self.raw('Золото',MAX_INTEGER)
        before=self.sql('SELECT * FROM inventory')
        with self.assertRaises(ValueError):
            self.add('5 золотых монет',1)
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.raw('Медь',1,null=True)
        self.raw('Медь',1)
        with self.assertRaises(ValueError):
            self.add('10 медных монет',1)

    def test_migration_all_bundles_null_duplicates_history_and_idempotence(self):
        self.setup_shop()
        self.raw('Серебро',15,null=True)
        self.raw('Серебро',2)
        for name,(unit,count) in MASTER_MONEY.items():
            if count!=1:
                self.raw(name,2,null=name=='50 серебряных монет')
        self.add('Меч',1)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
            protected=migration.snapshot(conn,protected=True)
        result=migration.apply(self.shop,plan)
        self.assertEqual(migration.apply(self.new_shop(),plan),result)
        self.assertEqual(self.amount('Серебро'),17+2*(5+10+50))
        for name,(unit,count) in MASTER_MONEY.items():
            if count!=1:
                self.assertEqual(self.amount(name),0)
        self.assertEqual(self.amount('Меч'),1)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(migration.snapshot(conn,protected=True),protected)
            self.assertEqual(audit(conn)['errors'],0)
        with self.assertRaises(CatalogError):
            self.open()
        self.shop.create(self.owner,'m')
        self.open()

    def test_lost_ack_after_commit_returns_saved_migration_receipt(self):
        self.setup_shop()
        self.raw('50 серебряных монет',2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
        from contextlib import contextmanager
        original=self.shop.transaction
        @contextmanager
        def lost_ack(**kwargs):
            with original(**kwargs) as context:
                yield context
            raise OSError('lost client acknowledgement after committed transaction')
        with patch.object(self.shop,'transaction',lost_ack),self.assertRaises(OSError):
            migration.apply(self.shop,plan)
        result=migration.apply(self.new_shop(),plan)
        self.assertTrue(result['value_preserved'])
        self.assertEqual(self.amount('Серебро'),100)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_coin_migrations'),[(1,)])
        self.assert_clean()

    def test_normalization_scope_control_tampering_rolls_back(self):
        self.setup_shop()
        self.raw('50 серебряных монет',2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
        original=migration.normalize
        def corrupt(conn,scope,wallets):
            original(conn,scope,wallets)
            conn.execute("UPDATE dnd_shop_settings SET factor='1'")
        before=self.sql('SELECT * FROM inventory')
        with patch.object(migration,'normalize',side_effect=corrupt),self.assertRaises(CatalogError):
            migration.apply(self.shop,plan)
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_coin_migrations'),[(0,)])
        self.assert_clean()

    def test_migration_sql_failure_atomic_and_parallel_retry(self):
        self.setup_shop()
        self.raw('50 серебряных монет',2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
        before=self.sql('SELECT * FROM inventory')
        original=migration.normalize
        def failure(conn,scope,wallets):
            original(conn,scope,wallets)
            raise sqlite3.OperationalError('injected power loss before COMMIT')
        with patch.object(migration,'normalize',side_effect=failure),self.assertRaises(sqlite3.Error):
            migration.apply(self.shop,plan)
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:migration.apply(self.shop,plan),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.amount('Серебро'),100)

    def test_migration_overflow_collision_and_new_operation_fail_closed(self):
        self.setup_shop()
        self.raw('1000 золотых монет',MAX_INTEGER)
        with closing(sqlite3.connect(self.db)) as conn,self.assertRaises(CatalogError):
            migration.prepare(self.shop,conn,(-111,0))
        self.sql('DELETE FROM inventory')
        self.raw('50 серебряных монет',1)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            plan=migration.prepare(self.shop,conn,(-111,0))
        self.add('Меч',1)
        with self.assertRaises(CatalogError):
            migration.apply(self.shop,plan)
        self.sql("UPDATE inventory SET name='Меч' WHERE name='50 серебряных монет'")
        with closing(sqlite3.connect(self.db)) as conn,self.assertRaises(ValueError):
            migration.prepare(self.shop,conn,(-111,0))


class CoinEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools.validate_dnd_coins_release import seed_post_b
        cls.temp=tempfile.TemporaryDirectory(prefix='coins-cli-synthetic-')
        cls.folder=Path(cls.temp.name)
        cls.backup,cls.meta,cls.runtime=seed_post_b(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_default_cli_dry_run_and_host_guard_do_not_change_sources(self):
        from app.dnd_shop import coin_evidence
        hashes=[migration.digest(p.read_bytes()) for p in (self.backup,self.meta,self.runtime)]
        output=self.folder/'default-cli-plan.json'
        child=subprocess.run([sys.executable,str(control.ROOT/'tools/normalize_dnd_coins_test.py'),
            '--backup',str(self.backup),'--metadata',str(self.meta),'--runtime-snapshot',str(self.runtime),
            '--output',str(output)],cwd=control.ROOT,capture_output=True)
        self.assertEqual(child.returncode,0,child.stderr.decode(errors='replace'))
        plan=json.loads(output.read_text(encoding='utf-8'))
        sha=migration.digest(output.read_bytes())
        self.assertEqual(sha,migration.digest(migration.canonical(plan).encode()))
        # No hostname or target path override exists in the production-facing CLI.
        with patch('app.dnd_shop.test_migration.LOOT',control.ROOT/'data/loot.csv'),patch('app.dnd_shop.test_migration.socket.gethostname',return_value='NOT-TEST'),self.assertRaisesRegex(CatalogError,'VM-338333'):
            coin_evidence.apply(self.backup,self.meta,self.runtime,plan,sha)
        self.assertEqual([migration.digest(p.read_bytes()) for p in (self.backup,self.meta,self.runtime)],hashes)

    def test_modified_metadata_or_stale_snapshot_fail_closed(self):
        from app.dnd_shop import coin_evidence
        metadata=json.loads(self.meta.read_text(encoding='utf-8'))
        invalid=self.folder/'invalid-meta.json'
        for change in (dict(sha256='0'*64),dict(captured_utc='2000-01-01T00:00:00Z'),dict(hostname='PROD')):
            invalid.write_text(json.dumps(dict(metadata,**change)),encoding='utf-8')
            with self.assertRaises(CatalogError):
                coin_evidence.prepare(self.backup,invalid,self.runtime,control.ROOT/'data/loot.csv')

    def test_apply_requires_the_exact_plan_hash(self):
        from app.dnd_shop import coin_evidence
        plan,_=coin_evidence.prepare(self.backup,self.meta,self.runtime,control.ROOT/'data/loot.csv')
        with self.assertRaises(CatalogError):
            coin_evidence.apply(self.backup,self.meta,self.runtime,plan,'0'*64)


if __name__=='__main__':
    unittest.main()
