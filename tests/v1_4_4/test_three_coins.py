"""Three physical coins, source-only bundles and preserved historical identities."""
import csv
import io
import json
import sqlite3
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fractions import Fraction
from unittest.mock import patch

from app.dnd_shop import builder
from app.dnd_shop.catalog import CatalogError, snapshot, parse_catalog
from app.dnd_shop.currency_units import MASTER_MONEY, UNITS, is_source_money
from app.dnd_shop.money import MoneyError, CalculationLimit
from app.dnd_shop.service import ShopError
from app.dnd_shop.audit import audit
from tests.v1_4_4 import test_shop_control as control
from tests.v1_4_4 import test_source_money as source


class ThreeCoinsTests(unittest.TestCase):
    for _name in ('setUp','sql','currency','write','write_rows','add','open','amount','buy_offer',
                  'sale_offer','new_shop','configure','thread_two','allow','balance','assert_clean'):
        locals()[_name] = getattr(control.ControlTests, _name)
    del _name

    def source_bundles(self):
        # Explicit synthetic fixture; the original data/loot.csv is never edited.
        master = builder.read_master(self.master)
        existing = set(master.by_name)
        source = builder.read_master(control.ROOT/'data/loot.csv')
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, delimiter=';', lineterminator='\n')
        writer.writerow(builder.MASTER_COLUMNS)
        writer.writerows((r.name,r.effect,r.description,r.price,r.rarity,r.category) for r in master.rows)
        for name in MASTER_MONEY:
            if name.casefold() not in existing:
                r = source.by_name[name.casefold()]
                writer.writerow((r.name,r.effect,r.description,r.price,r.rarity,r.category))
        self.master.write_text(stream.getvalue(),encoding='utf-8')
        self.codes = builder.index(builder.read_master(self.master),previous=self.codes)
        self.mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS,[dict(code=k,name=v) for k,v in self.codes.items()]))

    def test_three_codes_suffice_and_all_source_money_is_excluded(self):
        self.source_bundles()
        before = self.sql('SELECT * FROM dnd_currencies')
        self.shop.create(self.owner,'m')
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),before)
        self.assertEqual(len(before),3)
        self.assertEqual(len(snapshot(self.csv).products),4)
        self.assertTrue(all(not p.monetary for p in snapshot(self.csv).products))
        self.assertEqual(self.sql("SELECT count(*) FROM dnd_items"),[(7,)])
        self.assertTrue(all(not is_source_money(name) for name, in self.sql(
            'SELECT i.name FROM dnd_shop_skus s JOIN dnd_items i ON i.id=s.item_id')))
        self.assert_clean()

    def test_actual_4450_source_has_4435_goods_without_twelve_registrations(self):
        self.master = control.ROOT/'data/loot.csv'
        self.codes = builder.read_map(self.master.with_name('loot_code_map.csv'))
        self.mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS,[dict(code=k,name=v) for k,v in self.codes.items()]))
        self.shop = self.new_shop()
        result = self.shop.create(self.owner,'m')
        self.assertEqual(result['registered'],4435)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_currencies'),[(3,)])
        self.assertEqual(len(snapshot(self.csv).products),4435)
        sword = next(p for p in snapshot(self.csv).products if p.name=='Короткий меч')
        self.assertEqual(sword.price,Fraction('3.5'))
        self.assertEqual(sword.price*Fraction(3,100),Fraction('0.105'))
        self.assert_clean()

    def test_cash_purse_is_ordinary_and_sale_issues_only_three_coins(self):
        self.source_bundles()
        self.allow('Кошель с монетами')
        self.shop.create(self.owner,'m')
        self.add('Золото',4)
        bought = self.shop.execute(*self.buy_offer(),self.actor)
        self.assertEqual(bought['value'],'7/2')
        self.assertEqual(self.amount('Серебро'),50)
        sold = self.shop.execute(*self.sale_offer(),self.actor)
        self.assertEqual(Fraction(sold['value']),Fraction('0.105'))
        self.assertEqual((self.amount('Серебро'),self.amount('Медь')),(60,50))
        self.assertEqual(len(self.sql('SELECT * FROM dnd_currencies')),3)
        self.assertTrue({row[0] for row in self.sql('SELECT item_id FROM dnd_shop_money')} <= {self.gold,self.silver,self.copper})
        self.assert_clean()

    def test_offer_cannot_enable_any_source_money_code(self):
        self.source_bundles()
        before = self.sql('SELECT * FROM dnd_items')
        for name in MASTER_MONEY:
            self.allow(name)
            with self.subTest(name=name),self.assertRaises(CatalogError):
                self.shop.create(self.owner,'m')
            self.assertEqual(self.sql('SELECT * FROM dnd_items'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'),[])

    def test_complete_historical_runtime_money_rows_are_inert(self):
        self.source_bundles()
        self.shop.create(self.owner,'m')
        master = builder.read_master(self.master)
        money = []
        for code,name in self.codes.items():
            if is_source_money(name):
                r=master.by_name[name.casefold()]
                model=snapshot(self.csv).products[0]
                money.append(replace(model,code=code,name=r.name,description=r.description,
                    effect=r.effect,category=r.category,rarity=r.rarity,price=Fraction(r.price,10000),
                    stock=10,buy_enabled=False,active=True))
        legacy=builder.encode_products(list(snapshot(self.csv).products)+money)
        self.csv.write_bytes(legacy)
        self.shop.open(*self.actor)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_currencies'),[(3,)])
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_skus'),[(4,)])
        session=self.open()
        for product in money:
            with self.subTest(name=product.name),self.shop.transaction() as (conn,products):
                s=self.shop.session(conn,session,*self.actor)
                with self.assertRaises(ShopError):
                    self.shop.product(conn,products,s,product.code)
        self.assert_clean()

    def test_existing_optional_bundle_registry_sku_sold_and_inventory_survive_rebuild(self):
        source.SourceMoneyTests.all_units(self)
        self.shop.create(self.owner,'m')
        bundle=self.sql("SELECT id FROM dnd_items WHERE name='50 серебряных монет'")[0][0]
        code=next(k for k,v in self.codes.items() if v=='50 серебряных монет')
        self.sql("INSERT INTO dnd_shop_skus VALUES(-111,0,?,?,'50 серебряных монет',8)",(code,bundle))
        self.sql("""INSERT INTO inventory(chat_id,thread_id,username,name,quantity,item_id)
            VALUES(-111,0,'@alice','50 серебряных монет',2,?)""",(bundle,))
        inventory=self.sql('SELECT * FROM inventory')
        currencies=self.sql('SELECT * FROM dnd_currencies')
        self.shop.create(self.owner,'m')
        self.assertEqual(self.sql('SELECT * FROM inventory'),inventory)
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),currencies)
        self.assertEqual(self.sql('SELECT item_id,sold FROM dnd_shop_skus WHERE code=?',(code,)),[(bundle,8)])
        self.assertEqual(self.shop.view(self.open(),self.actor,'sell')['buttons'],[])
        self.assert_clean()

    def test_credit_limit_is_exact_and_mixed_payment_survives_restart_and_repeats(self):
        self.allow('Кинжал')
        self.shop.create(self.owner,'m')
        self.add('Кинжал',1)
        session,token=self.sale_offer()
        with patch('app.dnd_shop.service.payment',side_effect=CalculationLimit('synthetic limit')):
            result=self.shop.execute(session,token,self.actor)
        self.assertEqual(Fraction(result['credit_added']),Fraction('0.0003'))
        self.assertEqual(self.new_shop().execute(session,token,self.actor),result)
        self.add('Медь',97)
        purchase=self.shop.execute(*self.buy_offer(),self.actor)
        self.assertEqual(Fraction(purchase['credit_spent']),Fraction('0.0003'))
        self.assertEqual(self.amount('Медь'),0)
        self.assertEqual(self.balance(),0)
        self.assert_clean()

    def test_last_stock_two_concurrent_callbacks_only_one_purchase(self):
        self.allow('Кошель с монетами',stock=1)
        self.shop.create(self.owner,'m')
        self.add('Золото',10)
        offers=[self.buy_offer(),self.buy_offer()]
        def buy(pair):
            try:
                return self.new_shop().execute(*pair,self.actor)
            except ShopError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(buy,offers))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.amount('Кошель с монетами'),1)
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_trades'),[(1,)])
        self.assert_clean()

    def test_money_offer_is_rejected_by_parser_after_cache_was_warmed(self):
        self.open()
        before=self.sql('SELECT * FROM inventory')
        currencies=self.sql('SELECT * FROM dnd_currencies')
        self.write(name='10 медных монет')
        with self.assertRaises(CatalogError):
            self.open()
        self.assertEqual(self.sql('SELECT * FROM inventory'),before)
        self.assertEqual(self.sql('SELECT * FROM dnd_currencies'),currencies)
        self.assertEqual(self.sql('SELECT sum(sold) FROM dnd_shop_skus'),[(0,)])
