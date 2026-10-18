"""Lifecycle, preflight, crash recovery, concurrency and downgrade integration."""
import csv
import io
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from app.db import schema
from app.dnd_shop import builder
from app.dnd_shop.audit import audit
from app.dnd_shop.catalog import CatalogError, atomic_bytes, publish, snapshot
from app.dnd_shop.money import CalculationLimit, MoneyError
from app.dnd_shop.pricing import COPPER_FACTOR, GOLD_FACTOR
from app.dnd_shop.rollback import rollback_check
from app.dnd_shop.service import Shop
from tests.v1_4_4 import test_shop as legacy

ROOT = Path(__file__).resolve().parents[2]


class ControlTests(unittest.TestCase):
    sql = legacy.ShopTests.sql
    currency = legacy.ShopTests.currency
    write = legacy.ShopTests.write
    write_rows = legacy.ShopTests.write_rows
    add = legacy.ShopTests.add
    open = legacy.ShopTests.open
    amount = legacy.ShopTests.amount
    buy_offer = legacy.ShopTests.buy_offer
    sale_offer = legacy.ShopTests.sale_offer

    def setUp(self):
        legacy.ShopTests.setUp(self)
        self.settings = {-111: {0: {'admin': '@owner'}, 2: {'admin': '@other'}, 3: {'mini': True}}}
        p = patch('app.db.dnd_items.TOPIC_SETTINGS', self.settings)
        p.start()
        self.addCleanup(p.stop)
        self.master = Path(self.tmp.name) / 'loot.csv'
        self.mapping = Path(self.tmp.name) / 'index.csv'
        self.offers = Path(self.tmp.name) / 'offers.csv'
        rows = [('Медная монета', 1), ('Серебряная монета', 100), ('Золотая монета', 10000),
                ('Меч', 3500), ('Кинжал', 100), ('Максимум', 350000000), ('Кошель с монетами', 35000)]
        stream = io.StringIO(newline='')
        writer = csv.writer(stream, delimiter=';', lineterminator='\n')
        writer.writerow(builder.MASTER_COLUMNS)
        writer.writerows((name, 'эффект', 'описание', price, 'обычный', 'предмет') for name, price in rows)
        self.master.write_text(stream.getvalue(), encoding='utf-8')
        self.codes = builder.index(builder.read_master(self.master), previous={'sword': 'Меч'})
        self.mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS,
                                                  [dict(code=k, name=v) for k, v in self.codes.items()]))
        self.configure(0, self.copper, self.silver, self.gold)
        self.shop = self.new_shop()
        self.owner = (901, 'owner', -111, 0)
        self.other = (902, 'other', -111, 2)

    def new_shop(self):
        return Shop(self.db, self.csv, self.settings, loot_path=self.master,
                    map_path=self.mapping, offers_path=self.offers)

    def configure(self, thread, copper, silver, gold):
        def code(item):
            return self.sql('SELECT code FROM dnd_items WHERE id=?', (item,))[0][0]
        self.settings[-111][thread]['shop_currency_units'] = dict(
            master={'Медная монета': code(copper), 'Серебряная монета': code(silver), 'Золотая монета': code(gold)},
            universal_copper=code(copper), universal_gold=code(gold))

    def thread_two(self):
        copper = self.currency('Медь', '1', base=True, thread=2)
        silver = self.currency('Серебро', '10', thread=2)
        gold = self.currency('Золото', '100', thread=2)
        self.configure(2, copper, silver, gold)
        self.sql("INSERT INTO characters VALUES(-111,2,'@alice','Другая Алиса')")

    def allow(self, name='Меч', stock=3):
        code = next(k for k, v in self.codes.items() if v == name)
        self.offers.write_bytes(builder.csv_bytes(builder.OFFER_COLUMNS, [
            dict(chat_id=-111, thread_id=0, code=code, buy_enabled='true', stock=stock)]))

    def balance(self):
        return Fraction(self.sql('SELECT balance FROM dnd_shop_credit')[0][0])

    def assert_clean(self):
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            audit(conn)

    def test_exact_formula_examples_max_and_two_registered_base_units(self):
        for raw in (1, 100, 3500, 35000, 350000000):
            self.assertEqual(raw * COPPER_FACTOR, raw * Fraction('0.0285'))
            self.assertEqual(raw * GOLD_FACTOR, raw * Fraction('0.000285'))
        self.assertEqual(3500 * COPPER_FACTOR, Fraction('99.75'))
        self.assertEqual(3500 * GOLD_FACTOR, Fraction('0.9975'))
        self.shop.create(self.owner, 'b')
        self.assertEqual(snapshot(self.csv).by_key[(-111, 0, 'sword')].price, Fraction('0.9975'))
        for product in snapshot(self.csv).products:
            self.assertEqual(product.price, builder.read_master(self.master).by_name[product.name.casefold()].price * GOLD_FACTOR)
        self.thread_two()
        self.shop.create(self.other, 'b')
        for product in snapshot(self.csv).products:
            factor = COPPER_FACTOR if product.thread_id == 2 else GOLD_FACTOR
            self.assertEqual(product.price, builder.read_master(self.master).by_name[product.name.casefold()].price * factor)
        self.assertEqual(snapshot(self.csv).by_key[(-111, 2, 'sword')].price, Fraction('99.75'))

    def test_create_master_full_buyback_empty_buying_no_gifts_or_coin_items(self):
        before = self.sql('SELECT * FROM inventory')
        result = self.shop.create(self.owner, 'm')
        self.assertEqual((result['registered'], result['buying'], result['base_name']), (4, 0, 'Золото'))
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(len(snapshot(self.csv).products), 7)
        self.assertEqual(self.sql("SELECT count(*) FROM dnd_items WHERE category='item' AND name IN ('Медная монета','Серебряная монета','Золотая монета')"), [(0,)])
        self.assertEqual(self.shop.view(self.open(), self.actor, 'buy')['buttons'], [])
        self.add('Кошель с монетами', 1)
        self.assertEqual(self.shop.view(self.open(), self.actor, 'sell')['buttons'][0][0], 'Кошель с монетами')
        self.assert_clean()

    def test_currency_preflight_fails_before_any_import(self):
        original = self.settings[-111][0]['shop_currency_units']
        for fault in ('no_base', 'no_config', 'scale', 'missing_coin', 'ordinary_coin'):
            with self.subTest(fault=fault):
                self.settings[-111][0]['shop_currency_units'] = json.loads(json.dumps(original))
                self.sql('UPDATE dnd_items SET active=1 WHERE id=?', (self.gold,))
                self.sql("UPDATE dnd_currencies SET base_value='0.01' WHERE item_id=?", (self.copper,))
                if fault == 'no_base':
                    self.sql('UPDATE dnd_items SET active=0 WHERE id=?', (self.gold,))
                elif fault == 'no_config':
                    del self.settings[-111][0]['shop_currency_units']
                elif fault == 'scale':
                    self.sql("UPDATE dnd_currencies SET base_value='0.02' WHERE item_id=?", (self.copper,))
                elif fault == 'missing_coin':
                    del self.settings[-111][0]['shop_currency_units']['master']['Медная монета']
                else:
                    self.add('Медная монета', 1)
                before = self.sql('SELECT * FROM dnd_items')
                with self.assertRaises(ValueError):
                    self.shop.create(self.owner, 'b')
                self.assertEqual(self.sql('SELECT * FROM dnd_items'), before)
                self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'), [])
                self.assertEqual(self.sql('SELECT * FROM dnd_shop_publications'), [])

    def test_stop_restart_reactivate_preserves_credit_sold_receipts_and_invalidates_sessions(self):
        self.allow('Кинжал')
        self.shop.create(self.owner, 'm')
        self.add('Золото', 200)
        session, token = self.buy_offer()
        self.shop.execute(session, token, self.actor)
        sale_session, sale_token = self.sale_offer()
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('test budget')):
            receipt = self.shop.execute(sale_session, sale_token, self.actor)
        self.assertEqual(self.balance(), 3)
        sold = self.sql('SELECT * FROM dnd_shop_skus')
        trades = self.sql('SELECT * FROM dnd_shop_trades')
        self.assertFalse(self.shop.stop(self.owner)['already'])
        self.assertTrue(self.new_shop().stop(self.owner)['already'])
        for call in (lambda: self.new_shop().open(*self.actor),
                     lambda: self.shop.view(session, self.actor, 'home'),
                     lambda: self.shop.execute(session, token, self.actor),
                     lambda: self.shop.redeem(session, 'missing', self.actor)):
            with self.assertRaisesRegex(CatalogError, 'закрыт'):
                call()
        self.new_shop().create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_skus'), sold)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'), trades)
        self.assertEqual(self.balance(), 3)
        with self.assertRaises(ValueError):
            self.shop.execute(sale_session, sale_token, self.actor)
        self.assertEqual(receipt['credit_added'], '3')
        self.assert_clean()

    def test_other_topic_mode_offers_stock_preserved_and_stop_is_local(self):
        self.allow()
        self.shop.create(self.owner, 'm')
        before = [p for p in snapshot(self.csv).products if p.thread_id == 0]
        self.thread_two()
        self.offers.unlink()
        self.shop.create(self.other, 'b')
        self.assertEqual([p for p in snapshot(self.csv).products if p.thread_id == 0], before)
        self.shop.stop(self.owner)
        self.assertTrue(self.shop.create(self.other, 'b')['already'])
        self.assertEqual(self.sql('SELECT thread_id,enabled,pricing_mode FROM dnd_shop_settings ORDER BY thread_id'),
                         [(0, 0, 'm'), (2, 1, 'b')])
        self.new_shop().open(101, 'alice', -111, 2)
        with self.assertRaisesRegex(CatalogError, 'закрыт'):
            self.open()
        self.assert_clean()

    def test_mode_switch_refused_without_history_or_after_legacy_trade(self):
        self.shop.create(self.owner, 'm')
        before = self.csv.read_bytes()
        with self.assertRaisesRegex(CatalogError, 'Режим цен'):
            self.shop.create(self.owner, 'b')
        self.assertEqual(self.csv.read_bytes(), before)

    def test_legacy_history_cannot_be_implicitly_repriced(self):
        self.add('Меч', 1)
        self.shop.execute(*self.sale_offer(), self.actor)
        with self.assertRaisesRegex(CatalogError, 'Исторический'):
            self.shop.create(self.owner, 'b')
        self.shop.create(self.owner, 'm')
        self.assert_clean()

    def test_only_current_topic_admin_and_valid_mode(self):
        for who in ((101, 'alice', -111, 0), (901, 'owner', -111, 2),
                    (901, 'owner', -111, 3), (901, 'owner', -222, 9)):
            for call in (lambda: self.shop.create(who, 'm'), lambda: self.shop.stop(who)):
                with self.assertRaises(ValueError):
                    call()
        with self.assertRaises(ValueError):
            self.shop.create(self.owner, 'invalid')
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'), [])

    def test_crash_before_replace_recovers_without_partial_registry(self):
        before = self.sql('SELECT * FROM dnd_items')
        with patch('app.dnd_shop.control.atomic_bytes', side_effect=OSError('injected power loss')):
            with self.assertRaises(OSError):
                self.shop.create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT * FROM dnd_items'), before)
        with self.assertRaisesRegex(CatalogError, 'не завершено'):
            self.open()
        self.new_shop().create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_publications'), [])
        self.assert_clean()

    def test_sql_failure_after_replace_and_repeated_recovery(self):
        original = self.shop.sync
        calls = 0
        def fail(conn, products, **kwargs):
            nonlocal calls
            calls += 1
            original(conn, products, **kwargs)
            if calls == 2:
                raise sqlite3.OperationalError('injected finalization failure')
        before = self.sql('SELECT * FROM dnd_items')
        with patch.object(self.shop, 'sync', side_effect=fail):
            with self.assertRaises(sqlite3.Error):
                self.shop.create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT * FROM dnd_items'), before)
        self.new_shop().create(self.owner, 'm')
        count = self.sql('SELECT count(*) FROM dnd_items')
        self.new_shop().create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_items'), count)
        self.assert_clean()

    def test_stop_during_publication_is_not_lost(self):
        def stop_and_replace(path, data):
            self.shop.stop(self.owner)
            atomic_bytes(path, data)
        with patch('app.dnd_shop.control.atomic_bytes', side_effect=stop_and_replace):
            with self.assertRaisesRegex(CatalogError, 'остановлен'):
                self.shop.create(self.owner, 'm')
        with self.assertRaisesRegex(CatalogError, 'закрыт'):
            self.open()
        self.new_shop().create(self.owner, 'm')
        self.assert_clean()

    def test_stop_and_buy_serialize(self):
        self.allow('Кинжал', 1)
        self.shop.create(self.owner, 'm')
        self.add('Золото', 100)
        session, token = self.buy_offer()
        entered, release = threading.Event(), threading.Event()
        original = self.shop.adjust
        def wait(conn, *args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(conn, *args, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(self.shop, 'adjust', side_effect=wait):
                trade = pool.submit(self.shop.execute, session, token, self.actor)
                self.assertTrue(entered.wait(3))
                stop = pool.submit(self.shop.stop, self.owner)
                release.set()
                trade.result()
                stop.result()
        self.assertEqual(self.sql('SELECT count(*) FROM dnd_shop_trades'), [(1,)])
        with self.assertRaisesRegex(CatalogError, 'закрыт'):
            self.shop.execute(session, token, self.actor)
        self.shop.create(self.owner, 'm')
        self.assertEqual(self.shop.view(self.open(), self.actor, 'buy')['buttons'], [])
        self.assert_clean()

    def test_verified_limit_exact_credit_but_corrupt_inventory_does_not_create_debt(self):
        self.shop.create(self.owner, 'b')
        self.add('Меч', 2)
        session, token = self.sale_offer()
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            result = self.shop.execute(session, token, self.actor)
            self.assertEqual(self.shop.execute(session, token, self.actor), result)
        self.assertEqual(self.balance(), Fraction('0.029925'))
        self.assertEqual(self.amount('Меч'), 1)
        session, token = self.sale_offer()
        self.sql("UPDATE inventory SET item_id=? WHERE name='Меч'", (self.gold,))
        before = self.sql('SELECT * FROM inventory')
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            with self.assertRaises(ValueError):
                self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(self.balance(), Fraction('0.029925'))

    def test_currency_change_blocks_operation_without_credit(self):
        self.shop.create(self.owner, 'b')
        self.add('Меч', 1)
        session, token = self.sale_offer()
        before = self.sql('SELECT * FROM inventory')
        self.sql("UPDATE dnd_currencies SET base_value='0.02' WHERE item_id=?", (self.copper,))
        with self.assertRaises(ValueError):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_credit'), [])

    def test_full_sell_filter_and_impossible_redemption_limit_leave_balance_unchanged(self):
        self.shop.create(self.owner, 'b')
        self.add('Меч', 1)
        self.add('Неизвестный', 1)
        session = self.open()
        self.assertEqual([x[0] for x in self.shop.view(session, self.actor, 'sell')['buttons']], ['Меч'])
        self.shop.execute(*self.sale_offer(session), self.actor)
        self.assertEqual(self.balance(), Fraction('0.029925'))
        token = self.shop.view(session, self.actor, 'credit')['token']
        before = self.sql('SELECT * FROM inventory')
        for shop in (self.shop, self.new_shop()):
            with self.assertRaises(MoneyError):
                shop.redeem(session, token, self.actor)
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            with self.assertRaises(CalculationLimit):
                self.shop.redeem(session, token, self.actor)
        self.assertEqual(self.balance(), Fraction('0.029925'))
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assert_clean()

    def test_coin_credit_buy_master_and_exact_audit(self):
        self.allow()
        self.shop.create(self.owner, 'm')
        self.add('Кинжал', 1)
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            self.shop.execute(*self.sale_offer(), self.actor)
        self.add('Золото', 3500)
        result = self.shop.execute(*self.buy_offer(), self.actor)
        self.assertEqual(Fraction(result['credit_spent']), 3)
        self.assertTrue(result['debit'])
        self.assert_clean()

    def test_coin_credit_buy_base_and_exact_audit(self):
        self.currency('Микро', '0.000001')
        self.allow()
        self.shop.create(self.owner, 'b')
        self.add('Кинжал', 1)
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            self.shop.execute(*self.sale_offer(), self.actor)
        self.add('Золото', 1)
        result = self.shop.execute(*self.buy_offer(), self.actor)
        self.assertEqual(Fraction(result['credit_spent']), Fraction('0.000855'))
        self.assertTrue(result['debit'])
        self.assert_clean()

    def test_rollback_old_shop_blocked_even_when_stopped(self):
        self.shop.create(self.owner, 'm')
        self.shop.stop(self.owner)
        with closing(sqlite3.connect(self.db)) as conn:
            for target in (None, '96ff183c08101c9a3bed079c3eeefac95c22cc2b',
                           '6165e5137d34fb0efa7c39bb7fd61fcbad3524a4'):
                with self.assertRaises(CatalogError):
                    rollback_check(conn, target, ROOT)
            self.assertTrue(rollback_check(conn, 'd9856b2038a36490f93a4f5613ba7887698ac7e8', ROOT)['preserve_database'])

    def test_rollback_no_shop_preserves_nonzero_credit(self):
        self.shop.create(self.owner, 'b')
        self.add('Меч', 1)
        self.shop.execute(*self.sale_offer(), self.actor)
        self.shop.stop(self.owner)
        with closing(sqlite3.connect(self.db)) as conn:
            result = rollback_check(conn, 'd9856b2038a36490f93a4f5613ba7887698ac7e8', ROOT)
        self.assertEqual(result['credit_owners'], 1)
        self.assertEqual(self.balance(), Fraction('0.029925'))
        self.assert_clean()

    def test_publish_never_enables_and_repeated_migration_preserves_all_rows(self):
        self.shop.create(self.owner, 'm')
        self.shop.stop(self.owner)
        with closing(sqlite3.connect(self.db)) as conn:
            before = {name: conn.execute('SELECT * FROM ' + name).fetchall()
                      for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        stage = self.csv.with_suffix('.next.csv')
        stage.write_bytes(self.csv.read_bytes())
        publish(stage, self.csv, self.shop.validate_staged)
        schema.init_db()
        schema.init_db()
        with closing(sqlite3.connect(self.db)) as conn:
            for name, rows in before.items():
                self.assertEqual(conn.execute('SELECT * FROM ' + name).fetchall(), rows, name)
        with self.assertRaisesRegex(CatalogError, 'закрыт'):
            self.open()

    def test_credit_redemption_restart_and_double_confirmation_in_base_mode(self):
        self.currency('Микро', '0.000001')
        self.shop.create(self.owner, 'b')
        self.add('Меч', 1)
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            self.shop.execute(*self.sale_offer(), self.actor)
        session = self.open()
        token = self.shop.view(session, self.actor, 'credit')['token']
        result = self.new_shop().redeem(session, token, self.actor)
        self.assertEqual(self.new_shop().redeem(session, token, self.actor), result)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.amount('Микро'), 9925)
        self.assertEqual(self.amount('Медь'), 2)
        self.assert_clean()

    def test_pending_publication_other_admin_and_publisher_cannot_bypass_recovery(self):
        with patch('app.dnd_shop.control.atomic_bytes', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.shop.create(self.owner, 'm')
        self.thread_two()
        with self.assertRaisesRegex(CatalogError, 'незавершённой'):
            self.shop.create(self.other, 'b')
        stage = self.csv.with_suffix('.stage')
        with closing(sqlite3.connect(self.db)) as conn:
            data = conn.execute('SELECT candidate FROM dnd_shop_publications').fetchone()[0]
        stage.write_bytes(data)
        with self.assertRaisesRegex(CatalogError, 'незавершённую'):
            publish(stage, self.csv, self.shop.validate_staged)
        self.new_shop().create(self.owner, 'm')
        self.assert_clean()

    def test_corrupted_publication_hash_and_changed_currency_refuse_recovery(self):
        with patch('app.dnd_shop.control.atomic_bytes', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.shop.create(self.owner, 'm')
        self.sql('UPDATE dnd_shop_publications SET digest=?', ('0' * 64,))
        with self.assertRaisesRegex(CatalogError, 'Повреждён'):
            self.new_shop().create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_settings'), [])
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_skus'), [])

    def test_migration_fault_on_last_table_rolls_back_all_new_settings_ddl(self):
        with closing(sqlite3.connect(self.db)) as conn:
            # Disposable fixture simulates exactly the pre-control schema.
            conn.execute('DROP TABLE dnd_shop_publications')
            conn.execute('DROP TABLE dnd_shop_settings')
            before = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            conn.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
                                if action == sqlite3.SQLITE_CREATE_TABLE and name == 'dnd_shop_publications'
                                else sqlite3.SQLITE_OK)
            from app.db.dnd_shop import migrate
            with self.assertRaises(sqlite3.DatabaseError):
                with conn:
                    migrate(conn)
            conn.set_authorizer(None)
            self.assertEqual(conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall(), before)
        schema.init_db()
        self.assert_clean()

    def test_explicit_loose_coin_bundles_require_registered_quantity_consistency(self):
        with self.master.open('a', encoding='utf-8', newline='') as stream:
            csv.writer(stream, delimiter=';', lineterminator='\n').writerow(
                ['10 медных монет','нет','Денежная позиция',10,'обычный','Деньги и ценности'])
        self.codes = builder.index(builder.read_master(self.master), previous=self.codes)
        self.mapping.write_bytes(builder.csv_bytes(builder.MAP_COLUMNS, [dict(code=k,name=v) for k,v in self.codes.items()]))
        before = self.sql('SELECT * FROM dnd_items')
        with self.assertRaisesRegex(CatalogError, 'все денежные единицы'):
            self.shop.create(self.owner, 'm')
        self.assertEqual(self.sql('SELECT * FROM dnd_items'), before)
        bundle = self.currency('Медная связка', '0.2')
        code = self.sql('SELECT code FROM dnd_items WHERE id=?', (bundle,))[0][0]
        self.settings[-111][0]['shop_currency_units']['master']['10 медных монет'] = code
        with self.assertRaisesRegex(CatalogError, 'количеству'):
            self.shop.create(self.owner, 'b')
        self.sql("UPDATE dnd_currencies SET base_value='0.1' WHERE item_id=?", (bundle,))
        self.shop.create(self.owner, 'm')
        self.assertEqual(self.sql("SELECT id FROM dnd_items WHERE name='10 медных монет'"), [])
        self.assert_clean()

    def test_cli_candidate_cannot_drop_another_topic_and_registry_count_keeps_inactive_items(self):
        self.shop.create(self.owner, 'm')
        self.thread_two()
        self.shop.create(self.other, 'b')
        old = self.csv.read_bytes()
        products = snapshot(self.csv).products
        stage = self.csv.with_suffix('.scope-omitted.csv')
        stage.write_bytes(builder.encode_products([p for p in products if p.thread_id == 0]))
        with self.assertRaisesRegex(CatalogError, 'другую тему'):
            publish(stage, self.csv, self.shop.validate_staged)
        self.assertEqual(self.csv.read_bytes(), old)
        self.sql("UPDATE dnd_items SET active=0 WHERE name='Кинжал' AND thread_id=0")
        result = self.shop.create(self.owner, 'm')
        self.assertEqual(result['registered'], 4)  # Registered, even if disabled for trade.
        self.assert_clean()
