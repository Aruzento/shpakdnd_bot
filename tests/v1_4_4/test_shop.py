import csv
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from app.db import inventory, schema
from app.db.dnd_items import create_currency, register_in_transaction
from app.dnd_shop.catalog import COLUMNS, CatalogError, catalog_lock, publish, read_catalog
from app.dnd_shop.money import MoneyError
from app.dnd_shop.service import PriceChanged, Shop, ShopError

SETTINGS = {-111: {0: {}, 2: {}, 3: {'mini': True}}, -222: {0: {}}}


class ShopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'shop.db'
        self.csv = Path(self.tmp.name) / 'shop.csv'
        for module in (schema, inventory):
            p = patch.object(module, 'DB_PATH', self.db)
            p.start()
            self.addCleanup(p.stop)
        p = patch('app.db.dnd_items.TOPIC_SETTINGS', SETTINGS)
        p.start()
        self.addCleanup(p.stop)
        schema.init_db()
        self.shop = Shop(self.db, self.csv, SETTINGS)
        self.write()
        for user in ('@alice', '@bob'):
            self.sql('INSERT INTO characters VALUES(-111,0,?,?)', (user, user + ' character'))
        self.gold = self.currency('Золото', '1', base=True)
        self.silver = self.currency('Серебро', '0.1')
        self.copper = self.currency('Медь', '0.01')
        self.actor = (101, 'alice', -111, 0)

    def sql(self, text, args=()):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            return conn.execute(text, args).fetchall()

    def currency(self, name, value, *, base=False, quantity=1, thread=0):
        return create_currency(-111, thread, name, quantity, value, is_base=base, db_path=self.db)['item_id']

    def write(self, **changes):
        product = dict(chat_id=-111, thread_id=0, code='sword', name='Меч', description='Обычный меч',
                       price='100', stock=5, active='true')
        product.update(changes)
        self.write_rows([product])

    def write_rows(self, products):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(products)
        self.csv.write_text(stream.getvalue(), encoding='utf-8')

    def add(self, name, quantity, user='@alice', thread=0):
        inventory.add_inventory_item(-111, thread, user, name, quantity)

    def open(self, actor=None):
        actor = self.actor if actor is None else actor
        return self.shop.open(*actor)

    def buy_offer(self, session=None, actor=None):
        actor = self.actor if actor is None else actor
        session = self.open(actor) if session is None else session
        listing = self.shop.view(session, actor, 'buy')
        selected = listing['buttons'][0][1]
        return session, self.shop.view(session, actor, 'buy_card', selected)['token']

    def sale_offer(self, session=None):
        session = self.open() if session is None else session
        listing = self.shop.view(session, self.actor, 'sell')
        item_id = listing['buttons'][0][1]
        return session, self.shop.view(session, self.actor, 'sell_card', item_id)['token']

    def amount(self, name, user='@alice'):
        return sum(row[0] for row in self.sql('SELECT quantity FROM inventory WHERE name=? AND username=? AND thread_id=0', (name, user)))

    def test_buy_audited_exact_and_restart_duplicate(self):
        self.add('Золото', 102)
        session, token = self.buy_offer()
        result = self.shop.execute(session, token, self.actor)
        self.assertEqual(self.amount('Меч'), 1)
        self.assertEqual(self.amount('Золото'), 2)
        self.assertEqual(self.sql('SELECT sold FROM dnd_shop_skus'), [(1,)])
        self.shop = Shop(self.db, self.csv, SETTINGS)
        schema.init_db()
        self.assertEqual(self.shop.execute(session, token, self.actor)['trade_id'], result['trade_id'])
        self.assertEqual(self.amount('Золото'), 2)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM dnd_shop_trades'), [(1,)])
        self.assertEqual(self.sql('SELECT quantity,denomination,direction FROM dnd_shop_money'), [(100, '1', 'debit')])

    def test_secondary_mixed_change_and_invariant(self):
        for holdings, price in [([('Серебро', 15)], '1.5'),
                                ([('Золото', 1), ('Серебро', 5)], '1.5'),
                                ([('Золото', 2)], '1.25')]:
            with self.subTest(holdings=holdings):
                self.sql('DELETE FROM inventory')
                self.write(price=price)
                for name, quantity in holdings:
                    self.add(name, quantity)
                session, token = self.buy_offer()
                self.shop.execute(session, token, self.actor)
                trade = self.sql('SELECT id,value FROM dnd_shop_trades ORDER BY id DESC')[0]
                moves = self.sql('SELECT direction,quantity,denomination FROM dnd_shop_money WHERE trade_id=?', (trade[0],))
                total = sum((1 if d == 'debit' else -1) * n * Fraction(v) for d, n, v in moves)
                self.assertEqual(total, Fraction(price))
                if price == '1.25':
                    self.assertEqual(self.amount('Серебро'), 7)
                    self.assertEqual(self.amount('Медь'), 5)

    def test_insufficient_and_unrepresentable_payment_do_not_mutate(self):
        session, token = self.buy_offer()
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaisesRegex(MoneyError, 'Недостаточно'):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.write(price='0.005')
        self.add('Золото', 1)
        session, token = self.buy_offer()
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(MoneyError):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)

    def test_sale_three_percent_all_examples_and_no_restock(self):
        for price, payout in [('100', '3'), ('50', '1.5'), ('10', '0.3'), ('1', '0.03')]:
            with self.subTest(price=price):
                self.sql('DELETE FROM inventory')
                self.write(price=price, stock=0)
                self.add('Меч', 2)
                session, token = self.sale_offer()
                result = self.shop.execute(session, token, self.actor)
                self.assertEqual(Fraction(result['value']), Fraction(payout))
                self.assertEqual(self.amount('Меч'), 1)
                self.assertEqual(self.sql('SELECT sold FROM dnd_shop_skus'), [(0,)])
                trade = result['trade_id']
                actual = sum(n * Fraction(v) for n, v in self.sql('SELECT quantity,denomination FROM dnd_shop_money WHERE trade_id=?', (trade,)))
                self.assertEqual(actual, Fraction(payout))

    def test_unknown_inactive_currency_and_ambiguous_legacy_sale(self):
        self.add('Неизвестный', 1)
        session = self.open()
        listing = self.shop.view(session, self.actor, 'sell')
        self.assertEqual(listing['buttons'], [])
        with self.assertRaisesRegex(ShopError, 'Не, это я не куплю'):
            self.shop.view(session, self.actor, 'sell_card', str(self.sql("SELECT id FROM inventory WHERE name='Неизвестный'")[0][0]))
        self.add('Золото', 1)
        self.assertEqual(len(self.shop.view(session, self.actor, 'sell')['buttons']), 0)
        coin_id = self.sql("SELECT id FROM inventory WHERE name='Золото'")[0][0]
        with self.assertRaises(ShopError):
            self.shop.view(session, self.actor, 'sell_card', str(coin_id))
        self.add('Меч', 1)
        row = self.sql("SELECT id FROM inventory WHERE name='Меч'")[0][0]
        self.write(active='false')
        with self.assertRaises(ShopError):
            self.shop.view(session, self.actor, 'sell_card', str(row))
        self.write()
        self.sql("UPDATE inventory SET item_id=NULL WHERE name='Меч'")
        self.sql("INSERT INTO inventory(chat_id,thread_id,username,name,quantity) VALUES(-111,0,'@alice','меч',1)")
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(ShopError):
            self.shop.view(session, self.actor, 'sell_card', str(row))
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)

    def test_unambiguous_legacy_sale_does_not_rewrite_link(self):
        self.add('Меч', 2)
        self.sql("UPDATE inventory SET item_id=NULL WHERE name='Меч'")
        session, token = self.sale_offer()
        self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql("SELECT quantity,item_id FROM inventory WHERE name='Меч'"), [(1, None)])

    def test_unrepresentable_payout_becomes_exact_credit(self):
        self.add('Меч', 1)
        self.sql('UPDATE dnd_items SET active=0 WHERE id IN (?,?)', (self.silver, self.copper))
        self.write(price='1')
        session, token = self.sale_offer()
        result = self.shop.execute(session, token, self.actor)
        self.assertEqual(result['credit_added'], '3/100')
        self.assertEqual(result['credit'], [])
        self.assertEqual(self.amount('Меч'), 0)
        self.assertEqual(self.shop.execute(session, token, self.actor), result)
        self.assertEqual(self.sql('SELECT balance FROM dnd_shop_credit'), [('3/100',)])

    def test_changed_price_buy_and_sell_requires_new_offer(self):
        self.add('Золото', 100)
        self.add('Меч', 1)
        for method in (self.buy_offer, self.sale_offer):
            self.write(price='100')
            session, token = method()
            self.write(price='50')
            before = self.sql('SELECT * FROM inventory')
            with self.assertRaises(PriceChanged):
                self.shop.execute(session, token, self.actor)
            self.assertEqual(self.sql('SELECT * FROM inventory'), before)
            refreshed = self.shop.view(session, self.actor, 'refresh', token)
            self.assertEqual(refreshed['product'].price, Fraction(50))
            self.shop.execute(session, refreshed['token'], self.actor)

    def test_removed_inactive_and_missing_file_never_use_cache(self):
        self.add('Золото', 200)
        session, token = self.buy_offer()
        for state in ('deleted', 'inactive', 'missing', 'corrupt'):
            self.write()
            if state == 'deleted':
                self.write_rows([])
            elif state == 'inactive':
                self.write(active='false')
            elif state == 'missing':
                self.csv.unlink()
            else:
                self.csv.write_bytes(b'\xff')
            before = self.sql('SELECT * FROM inventory')
            with self.assertRaises((ShopError, CatalogError)):
                self.shop.execute(session, token, self.actor)
            self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(self.sql('SELECT sold FROM dnd_shop_skus'), [(0,)])

    def test_stock_changes_delete_readd_and_no_sku_reassignment(self):
        self.add('Золото', 1000)
        session, token = self.buy_offer()
        self.shop.execute(session, token, self.actor)
        self.write(stock=1)
        self.assertEqual(self.shop.view(session, self.actor, 'buy')['buttons'], [])
        self.write_rows([])
        self.assertEqual(self.shop.view(session, self.actor, 'buy')['buttons'], [])
        self.write(stock=3, name='Новое имя меча')
        listing = self.shop.view(session, self.actor, 'buy')
        card = self.shop.view(session, self.actor, 'buy_card', listing['buttons'][0][1])
        self.assertEqual(card['available'], 2)
        self.shop.execute(session, card['token'], self.actor)
        self.assertEqual(self.amount('Меч'), 2)
        self.assertEqual(self.sql('SELECT name FROM dnd_items WHERE category=\'item\''), [('Меч',)])
        self.add('Верёвка', 1)
        self.write(name='Верёвка')
        with self.assertRaises(CatalogError):
            self.shop.view(session, self.actor, 'buy')

    def test_inventory_and_currency_changed_after_offer(self):
        self.add('Золото', 2)
        self.write(price='1.5')
        session, token = self.buy_offer()
        self.sql('UPDATE dnd_currencies SET base_value=\'0.2\' WHERE item_id=?', (self.silver,))
        self.shop.execute(session, token, self.actor)
        self.assertEqual(self.amount('Серебро'), 2)
        self.assertEqual(self.amount('Медь'), 10)
        self.add('Меч', 1)
        session, token = self.sale_offer()
        self.sql("DELETE FROM inventory WHERE name='Меч'")
        with self.assertRaises(ShopError):
            self.shop.execute(session, token, self.actor)

    def test_missing_base_currency_blocks_trade(self):
        self.add('Золото', 100)
        session, token = self.buy_offer()
        self.sql('UPDATE dnd_items SET active=0 WHERE id=?', (self.gold,))
        with self.assertRaisesRegex(ShopError, 'базовой валюты'):
            self.shop.execute(session, token, self.actor)

    def test_concurrent_last_stock(self):
        self.write(stock=1)
        for user in ('@alice', '@bob'):
            self.add('Золото', 100, user)
        actors = [self.actor, (102, 'bob', -111, 0)]
        offers = [self.buy_offer(actor=actor) for actor in actors]
        def run(index):
            try:
                return self.shop.execute(*offers[index], actors[index])
            except ShopError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.sql('SELECT SUM(quantity) FROM inventory WHERE name=\'Меч\''), [(1,)])
        self.assertEqual(self.sql('SELECT sold FROM dnd_shop_skus'), [(1,)])

    def test_duplicate_callbacks_and_concurrent_last_sale(self):
        self.add('Золото', 100)
        session, token = self.buy_offer()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.shop.execute(session, token, self.actor), range(2)))
        self.assertEqual([r['trade_id'] for r in results], [1, 1])
        offers = [self.sale_offer(session) for _ in range(2)]
        def run(offer):
            try:
                return self.shop.execute(*offer, self.actor)
            except ShopError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, offers))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.amount('Меч'), 0)
        self.assertEqual(self.amount('Золото'), 3)

    def test_sale_after_restart_and_same_callback(self):
        self.add('Меч', 1)
        session, token = self.sale_offer()
        self.shop = Shop(self.db, self.csv, SETTINGS)
        result = self.shop.execute(session, token, self.actor)
        self.assertEqual(self.shop.execute(session, token, self.actor)['trade_id'], result['trade_id'])
        self.assertEqual(self.amount('Золото'), 3)

    def test_failure_currency_and_history_full_rollback(self):
        for direction in ('buy', 'sell'):
            self.sql('DELETE FROM inventory')
            self.add('Золото', 100)
            self.add('Меч', 1)
            session, token = self.buy_offer() if direction == 'buy' else self.sale_offer()
            before = self.sql('SELECT * FROM inventory')
            self.sql("CREATE TRIGGER trade_fault BEFORE INSERT ON dnd_shop_money BEGIN SELECT RAISE(ABORT,'injected'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                self.shop.execute(session, token, self.actor)
            self.sql('DROP TRIGGER trade_fault')
            self.assertEqual(self.sql('SELECT * FROM inventory'), before)
            self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'), [])
            self.assertEqual(self.sql('SELECT sold FROM dnd_shop_skus'), [(0,)])
            self.assertEqual(self.sql('SELECT result FROM dnd_shop_operations WHERE token=?', (token,)), [(None,)])
        # Payout failure after item/currency mutation also must roll everything back.
        self.sql("CREATE TRIGGER coin_fault BEFORE UPDATE ON inventory WHEN NEW.name='Золото' BEGIN SELECT RAISE(ABORT,'coin fault'); END")
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(sqlite3.IntegrityError):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)

    def test_foreign_user_scope_username_character_expired_closed(self):
        session, token = self.buy_offer()
        for actor in [(102, 'bob', -111, 0), (101, None, -111, 0), (101, 'alice', -111, 2), (101, 'bob', -111, 0)]:
            with self.subTest(actor=actor), self.assertRaises(ShopError):
                self.shop.execute(session, token, actor)
        self.sql("UPDATE characters SET name='Новый' WHERE username='@alice'")
        with self.assertRaises(ShopError):
            self.shop.execute(session, token, self.actor)
        session = self.open()
        self.shop.view(session, self.actor, 'exit')
        with self.assertRaises(ShopError):
            self.shop.view(session, self.actor)
        session = self.open()
        self.sql('UPDATE dnd_shop_sessions SET expires=0 WHERE token=?', (session,))
        with self.assertRaises(ShopError):
            self.shop.view(session, self.actor)

    def test_no_character_mini_unconfigured_identity_transfer(self):
        for actor in [(101, None, -111, 0), (101, 'nobody', -111, 0),
                      (101, 'alice', -111, 3), (101, 'alice', -999, 0)]:
            with self.subTest(actor=actor), self.assertRaises(ShopError):
                self.open(actor)
        self.open()
        with self.assertRaisesRegex(ShopError, 'Связь Telegram'):
            self.open((102, 'alice', -111, 0))
        self.sql("INSERT INTO characters VALUES(-111,0,'@alias','Alias')")
        with self.assertRaisesRegex(ShopError, 'Связь Telegram'):
            self.open((101, 'alias', -111, 0))

    def test_hot_reload_all_navigation_points_and_scope_isolation(self):
        session = self.open()
        self.write(description='Новое описание', price='50', stock=9)
        listing = self.shop.view(session, self.actor, 'buy', '1')
        card = self.shop.view(session, self.actor, 'buy_card', listing['buttons'][0][1])
        self.assertEqual((card['product'].description, card['product'].price, card['available']),
                         ('Новое описание', Fraction(50), 9))
        self.add('Золото', 100, thread=2)
        with self.assertRaises(MoneyError):
            self.shop.execute(session, card['token'], self.actor)
        self.assertEqual(self.sql('SELECT quantity FROM inventory WHERE thread_id=2'), [(100,)])
        self.write_rows(read_catalog_dicts(self.csv) + [dict(chat_id=-222, thread_id=0, code='sword', name='Другой меч',
                       description='', price='1', stock=5, active='true')])
        self.assertEqual(len(self.shop.view(session, self.actor, 'buy')['buttons']), 1)

    def test_registry_currency_name_conflict_and_catalog_atomic_validation(self):
        self.write(name='Золото')
        with self.assertRaises(CatalogError):
            self.open()
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_skus'), [])
        self.write()
        products = read_catalog_dicts(self.csv)
        self.write_rows(products + [dict(products[0], code='other', name='Серебро')])
        with self.assertRaises(CatalogError):
            self.open()
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_skus'), [])

    def test_overflow_and_corrupt_historical_quantities_block(self):
        self.add('Меч', 2**63 - 1)
        self.add('Золото', 100)
        session, token = self.buy_offer()
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(ShopError):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        for qty in ('2.5', 'oops', '-1'):
            self.sql("UPDATE inventory SET quantity=? WHERE name='Меч'", (qty,))
            with self.assertRaises(ShopError):
                self.shop.view(session, self.actor, 'sell')

    def test_atomic_publish_preserves_data_and_waits_for_trade_lock(self):
        session = self.open()
        candidate = self.csv.with_name('candidate.csv')
        self.write(price='50')
        candidate.write_bytes(self.csv.read_bytes())
        self.write()
        started = threading.Event()
        def writer():
            started.set()
            publish(candidate, self.csv, self.shop.validate_staged)
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            with catalog_lock(self.csv):
                future = pool.submit(writer)
                self.assertTrue(started.wait(2))
                time.sleep(0.1)
                self.assertFalse(future.done())
                self.assertEqual(read_catalog(self.csv)[0].price, Fraction(100))
            future.result(timeout=5)
        finally:
            pool.shutdown()
        self.assertEqual(read_catalog(self.csv)[0].price, Fraction(50))
        self.assertEqual(self.sql('SELECT sold FROM dnd_shop_skus'), [(0,)])

    def test_migration_idempotence_legacy_real_types_and_old_api(self):
        self.sql("INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description) VALUES(-111,2,'@old','Наследие','не число','История')")
        before = self.sql('SELECT * FROM inventory')
        for _ in range(3):
            schema.init_db()
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(self.sql('PRAGMA integrity_check'), [('ok',)])
        self.assertEqual(self.sql('PRAGMA foreign_key_check'), [])
        self.add('Верёвка', 2)
        self.assertEqual(inventory.delete_inventory_item(-111, 0, '@alice', 'Верёвка', 1)[2], 1)
        self.assertEqual(inventory.get_inventory(-111, 0, '@alice')[0][0], 'Верёвка')


    def test_repeating_currency_real_trade(self):
        self.currency('Треть', '1', quantity=3)
        self.add('Треть', 3)
        self.write(price='1')
        session, token = self.buy_offer()
        result = self.shop.execute(session, token, self.actor)
        self.assertEqual(result['debit'], [['Треть', 3]])
        self.assertEqual(self.sql('SELECT denomination FROM dnd_shop_money'), [('1/3',)])

    def test_locked_database_is_explicit_and_retry_applies_once(self):
        self.add('Золото', 100)
        session, token = self.buy_offer()
        original_connect = sqlite3.connect
        def fast_connect(*args, **kwargs):
            kwargs['timeout'] = 0.01
            return original_connect(*args, **kwargs)
        with closing(original_connect(self.db)) as locked:
            locked.execute('BEGIN IMMEDIATE')
            with patch('app.dnd_shop.service.sqlite3.connect', side_effect=fast_connect):
                with self.assertRaisesRegex(ShopError, 'Магазин занят'):
                    self.shop.execute(session, token, self.actor)
            locked.rollback()
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'), [])
        self.shop.execute(session, token, self.actor)
        self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM dnd_shop_trades'), [(1,)])

    def test_publication_cannot_change_source_mid_purchase(self):
        self.add('Золото', 100)
        session, token = self.buy_offer()
        candidate = self.csv.with_name('candidate.csv')
        self.write(price='50')
        candidate.write_bytes(self.csv.read_bytes())
        self.write()
        entered = threading.Event()
        release = threading.Event()
        original = self.shop.adjust
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise RuntimeError('test synchronization timeout')
            return original(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(self.shop, 'adjust', side_effect=blocked):
                trade = pool.submit(self.shop.execute, session, token, self.actor)
                self.assertTrue(entered.wait(3))
                publication = pool.submit(publish, candidate, self.csv, self.shop.validate_staged)
                try:
                    time.sleep(0.1)
                    self.assertFalse(publication.done())
                finally:
                    release.set()
                result = trade.result(timeout=5)
                publication.result(timeout=5)
        self.assertEqual(Fraction(result['value']), Fraction(100))
        self.assertEqual(read_catalog(self.csv)[0].price, Fraction(50))

    def test_sell_currency_id_is_rejected_even_after_offer_tampering(self):
        self.add('Меч', 1)
        self.add('Золото', 1)
        session, token = self.sale_offer()
        coin_row = self.sql("SELECT id FROM inventory WHERE name='Золото'")[0][0]
        self.sql('UPDATE dnd_shop_operations SET inventory_id=? WHERE token=?', (coin_row, token))
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(ShopError):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)

    def test_ambiguous_character_and_alias_inventory_fail_closed(self):
        self.sql("INSERT INTO characters VALUES(-111,0,'ALICE','Other')")
        with self.assertRaises(ShopError):
            self.open()
        self.sql("DELETE FROM characters WHERE username='ALICE'")
        session = self.open()
        self.sql("INSERT INTO inventory(chat_id,thread_id,username,name,quantity) VALUES(-111,0,'Alice','Меч',1)")
        with self.assertRaises(ShopError):
            self.shop.view(session, self.actor, 'sell')

    def test_catalog_add_item_and_missing_file_exit(self):
        session = self.open()
        products = read_catalog_dicts(self.csv)
        self.write_rows(products + [dict(products[0], code='rope', name='Верёвка')])
        self.assertEqual(len(self.shop.view(session, self.actor, 'buy')['buttons']), 2)
        self.csv.unlink()
        self.assertEqual(self.shop.view(session, self.actor, 'home')['screen'], 'home')
        self.assertEqual(self.shop.view(session, self.actor, 'exit')['screen'], 'exit')


def read_catalog_dicts(path):
    with path.open(encoding='utf-8', newline='') as source:
        return list(csv.DictReader(source))
