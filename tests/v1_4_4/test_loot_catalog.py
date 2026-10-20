"""Actual master verification plus explicitly synthetic edge-case fixtures."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from app.db import schema
from app.dnd_shop import builder
from app.dnd_shop.audit import audit
from app.dnd_shop.catalog import (COLUMNS, EXTENDED, CatalogError, atomic_bytes,
                                  parse_catalog, publish, snapshot)
from app.dnd_shop.money import CalculationLimit, MoneyError, payment, payment_with_credit
from app.dnd_shop.service import PriceChanged, Shop, ShopError
from app.handlers import shop as ui
from tests.v1_4_4 import test_shop as legacy

REAL_MASTER = Path(__file__).resolve().parents[2] / 'data' / 'loot.csv'


class MasterTests(unittest.TestCase):
    def test_real_file_exact_bytes_and_all_original_fields(self):
        data = REAL_MASTER.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), builder.REFERENCE_SHA256)
        self.assertEqual(len(data), 5188747)
        master = builder.read_master(REAL_MASTER)
        self.assertEqual(len(master.rows), 4450)
        self.assertEqual(len({r.category for r in master.rows}), 16)
        self.assertEqual(len({r.rarity for r in master.rows}), 8)
        self.assertEqual(max(len(r.effect) for r in master.rows), 13176)
        self.assertEqual(max(len(r.description) for r in master.rows), 13159)
        self.assertEqual(master.by_name['короткий меч'].price, 35000)
        for name, value in [('медная монета', 1), ('серебряная монета', 100), ('золотая монета', 10000)]:
            self.assertEqual(master.by_name[name].price, value)

    def test_stable_reference_map_survives_reorder_and_removal(self):
        master = builder.read_master(REAL_MASTER)
        mapping = builder.read_map(REAL_MASTER.with_name('loot_code_map.csv'))
        self.assertEqual(len(mapping), 4450)
        reverse = builder.Master(master.rows[::-1], master.by_name, master.digest)
        self.assertEqual(builder.index(reverse, previous=mapping), mapping)
        reduced = builder.Master(master.rows[:1], {master.rows[0].name.casefold(): master.rows[0]}, 'x')
        self.assertEqual(builder.index(reduced, previous=mapping), mapping)

    def test_master_rejects_invalid_utf8_duplicate_empty_fields_and_price(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'master.csv'
            valid = dict(zip(builder.MASTER_COLUMNS, ('Меч', 'эффект', 'описание', '1', 'обычный', 'оружие')))
            for data in [bytes([255]), b'wrong\n', builder.csv_bytes(builder.MASTER_COLUMNS, []),
                         builder.csv_bytes(builder.MASTER_COLUMNS, [valid, valid]).replace(b',', b';'),
                         builder.csv_bytes(builder.MASTER_COLUMNS, [dict(valid, Стоимость='0')]).replace(b',', b';'),
                         builder.csv_bytes(builder.MASTER_COLUMNS, [dict(valid, Эффект='')]).replace(b',', b';')]:
                path.write_bytes(data)
                with self.assertRaises(CatalogError):
                    builder.read_master(path)

    def test_large_change_and_combined_exact_invariants(self):
        for value in (350000000, 10500000, 700001):
            debit, payout = payment(Fraction(value), {1: Fraction(1), 2: Fraction(3)}, {}, payout=True)
            self.assertEqual(sum({1: 1, 2: 3}[i] * n for i, n in payout.items()), value)
        debit, change, credit = payment_with_credit(Fraction(3, 2), {1: Fraction(1)}, {1: 1}, Fraction(1, 2))
        self.assertEqual((debit, change, credit), ({1: 1}, {}, Fraction(1, 2)))
        with patch('app.dnd_shop.money.MAX_WORK', 0):
            with self.assertRaises(CalculationLimit):
                payment(Fraction(7), {1: Fraction(3), 2: Fraction(5)}, {}, payout=True)

    def test_combined_insufficient_funds_is_not_a_calculation_limit(self):
        with patch('app.dnd_shop.money.MAX_WORK', 0):
            with self.assertRaisesRegex(MoneyError, 'Недостаточно средств') as caught:
                payment_with_credit(Fraction(100), {1: Fraction(1, 100)}, {}, Fraction(1, 2))
            self.assertNotIsInstance(caught.exception, CalculationLimit)
        with self.assertRaises(MoneyError):
            payment_with_credit(Fraction(-1), {}, {}, Fraction(1))


class LootTests(unittest.TestCase):
    sql = legacy.ShopTests.sql
    currency = legacy.ShopTests.currency
    write = legacy.ShopTests.write
    write_rows = legacy.ShopTests.write_rows
    add = legacy.ShopTests.add
    open = legacy.ShopTests.open
    buy_offer = legacy.ShopTests.buy_offer
    sale_offer = legacy.ShopTests.sale_offer
    amount = legacy.ShopTests.amount

    def setUp(self):
        legacy.ShopTests.setUp(self)
        self.settings = {-111: {0: {'admin': '@owner'}, 2: {'admin': '@owner'}, 3: {'mini': True}}}
        self.master_path = Path(self.tmp.name) / 'loot.csv'
        self.offers = Path(self.tmp.name) / 'offers.csv'
        self.stage = Path(self.tmp.name) / 'stage.csv'
        self.master_rows = [dict(zip(builder.MASTER_COLUMNS, (name, '<b>эффект; "текст"\n😀', 'описание',
                    str(price), 'обычный', 'оружие'))) for name, price in
                    [('Меч', 1), ('Кинжал', 10), ('Посох', 20), ('Золото', 100)]]
        self.mapping = {'sword': 'Меч', 'dagger': 'Кинжал', 'staff': 'Посох', 'gold': 'Золото'}
        self.save_master()
        self.sql('UPDATE dnd_items SET active=0 WHERE id IN (?,?)', (self.silver, self.copper))
        self.shop = Shop(self.db, self.csv, self.settings, loot_path=self.master_path)
        self.compile()

    def save_master(self):
        import csv, io
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=builder.MASTER_COLUMNS, delimiter=';', lineterminator='\n')
        writer.writeheader()
        writer.writerows(self.master_rows)
        self.master_path.write_text(stream.getvalue(), encoding='utf-8')
        self.master = builder.read_master(self.master_path)

    def compile(self, offers=(), *, scopes=((-111, 0),), publish_now=True):
        self.offers.write_bytes(builder.csv_bytes(builder.OFFER_COLUMNS, [
            dict(chat_id=chat, thread_id=thread, code=code, buy_enabled=str(enabled).lower(), stock=stock)
            for chat, thread, code, enabled, stock in offers]))
        previous = parse_catalog(self.csv.read_bytes()) if self.csv.exists() else ()
        data, report = builder.build(self.master, self.mapping, self.offers, scopes, self.settings, previous=previous)
        self.stage.write_bytes(data)
        if publish_now:
            publish(self.stage, self.csv, self.shop.validate_staged)
        return report

    def balance(self):
        return self.shop.view(self.open(), self.actor)['credit_balance']

    def sell(self, name, session=None):
        session = session or self.open()
        inv_id = str(self.sql('SELECT id FROM inventory WHERE username=? AND name=?', ('@alice', name))[0][0])
        card = self.shop.view(session, self.actor, 'sell_card', inv_id)
        return self.shop.execute(session, card['token'], self.actor)

    def check_audit(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return audit(conn)

    def test_all_reference_positions_default_disabled_and_currency_excluded(self):
        report = self.compile(scopes=((-111, 0), (-111, 2)))
        self.assertEqual((report['imported'], report['buy_disabled'], report['offers']), (8, 8, 0))
        session = self.open()
        self.assertEqual(self.shop.view(session, self.actor, 'buy')['buttons'], [])
        self.add('Меч', 2)
        self.add('Золото', 1)
        listing = self.shop.view(session, self.actor, 'sell')['buttons']
        self.assertEqual([x[0] for x in listing], ['Меч'])
        result = self.sell('Меч', session)
        self.assertEqual(result['value'], '3/100')
        self.assertEqual(self.amount('Меч'), 1)
        self.assertEqual(self.sql('SELECT SUM(sold) FROM dnd_shop_skus'), [(0,)])

    def test_exact_credit_for_all_cheap_source_prices(self):
        for name, value in [('Меч', '3/100'), ('Кинжал', '3/10'), ('Посох', '3/5')]:
            self.add(name, 1)
            result = self.sell(name)
            self.assertEqual(result['credit_added'], value)
            self.assertEqual(result['credit'], [])
        self.assertEqual(self.balance(), Fraction(93, 100))
        self.assertEqual(self.check_audit()['credit_moves'], 3)

    def test_direct_exact_payout_does_not_touch_credit(self):
        self.sql('UPDATE dnd_items SET active=1 WHERE id=?', (self.copper,))
        self.add('Меч', 1)
        result = self.sell('Меч')
        self.assertEqual(result['credit_added'], '0')
        self.assertEqual(self.amount('Медь'), 3)
        self.assertEqual(self.balance(), 0)
        self.check_audit()

    def test_credit_spending_and_conservation(self):
        self.add('Посох', 2)
        for _ in range(2):
            self.sell('Посох')
        self.compile([(-111, 0, 'sword', True, 1)])
        session, token = self.buy_offer()
        result = self.shop.execute(session, token, self.actor)
        self.assertEqual((result['credit_spent'], result['credit_balance'], result['debit']), ('1', '1/5', []))
        self.assertEqual(self.amount('Меч'), 1)
        self.assertEqual(self.shop.execute(session, token, self.actor), result)
        self.check_audit()

    def test_split_credit_and_coin_purchase(self):
        # 15 sales x 0.6 = 9 credit; price 10 needs one real gold coin.
        self.add('Посох', 15)
        for _ in range(15):
            self.sell('Посох')
        self.add('Золото', 1)
        self.compile([(-111, 0, 'dagger', True, 1)])
        session, token = self.buy_offer()
        result = self.shop.execute(session, token, self.actor)
        self.assertEqual(result['credit_spent'], '9')
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.amount('Золото'), 0)
        self.check_audit()

    def test_redemption_unrepresentable_preserves_credit_then_succeeds_once(self):
        self.add('Меч', 1)
        self.sell('Меч')
        session = self.open()
        token = self.shop.view(session, self.actor, 'credit')['token']
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(MoneyError):
            self.shop.redeem(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(self.balance(), Fraction(3, 100))
        self.sql('UPDATE dnd_items SET active=1 WHERE id=?', (self.copper,))
        result = self.shop.redeem(session, token, self.actor)
        self.assertEqual(self.shop.redeem(session, token, self.actor), result)
        self.assertEqual(self.amount('Медь'), 3)
        self.assertEqual(self.balance(), 0)
        self.check_audit()

    def test_redemption_stale_foreign_and_restart(self):
        self.add('Меч', 2)
        self.sell('Меч')
        session = self.open()
        token = self.shop.view(session, self.actor, 'credit')['token']
        self.sell('Меч')
        self.shop = Shop(self.db, self.csv, self.settings, loot_path=self.master_path)
        for actor in ((102, 'alice', -111, 0), (101, 'alice', -111, 2)):
            with self.assertRaises(ShopError):
                self.shop.redeem(session, token, actor)
        with self.assertRaisesRegex(ShopError, 'изменился'):
            self.shop.redeem(session, token, self.actor)
        self.assertEqual(self.balance(), Fraction(3, 50))
        self.check_audit()

    def test_concurrent_credit_sale_last_item_and_same_callback(self):
        self.add('Меч', 1)
        session, token = self.sale_offer()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.shop.execute(session, token, self.actor), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.balance(), Fraction(3, 100))
        self.assertEqual(self.check_audit()['trades'], 1)

    def test_fault_credit_journal_rolls_back_whole_sale(self):
        self.add('Меч', 1)
        session, token = self.sale_offer()
        self.sql("""CREATE TRIGGER fail_credit BEFORE INSERT ON dnd_shop_credit_moves
                    BEGIN SELECT RAISE(ABORT,'credit injection'); END""")
        before = self.sql('SELECT * FROM inventory')
        with self.assertRaises(sqlite3.IntegrityError):
            self.shop.execute(session, token, self.actor)
        self.assertEqual(self.sql('SELECT * FROM inventory'), before)
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_credit'), [])
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_trades'), [])
        self.assertEqual(self.sql('SELECT result FROM dnd_shop_operations WHERE token=?', (token,)), [(None,)])

    def test_fault_redemption_rolls_back_coins_and_credit(self):
        self.add('Меч', 1)
        self.sell('Меч')
        self.sql('UPDATE dnd_items SET active=1 WHERE id=?', (self.copper,))
        session = self.open()
        token = self.shop.view(session, self.actor, 'credit')['token']
        self.sql("""CREATE TRIGGER fail_redemption BEFORE INSERT ON dnd_shop_credit_moves
                    WHEN NEW.direction='redeem' BEGIN SELECT RAISE(ABORT,'injection'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.shop.redeem(session, token, self.actor)
        self.assertEqual(self.amount('Медь'), 0)
        self.assertEqual(self.balance(), Fraction(3, 100))
        self.assertEqual(self.sql('SELECT * FROM dnd_shop_credit_coins'), [])

    def test_tampered_credit_and_inventory_identity_fail_closed(self):
        self.add('Меч', 1)
        self.sell('Меч')
        self.sql("UPDATE dnd_shop_credit SET balance='1'")
        with self.assertRaises(ShopError):
            self.balance()
        with self.assertRaises(ValueError):
            self.check_audit()
        self.sql("UPDATE dnd_shop_credit SET balance='3/100'")
        self.add('Меч', 1)
        self.sql("UPDATE inventory SET item_id=? WHERE name='Меч'", (self.gold,))
        with self.assertRaises(ShopError):
            self.sale_offer()

    def test_verified_payout_budget_exhaustion_credits_entire_exact_amount(self):
        self.add('Меч', 1)
        session, token = self.sale_offer()
        with patch('app.dnd_shop.service.payment', side_effect=CalculationLimit('budget')):
            result = self.shop.execute(session, token, self.actor)
        self.assertEqual(self.amount('Меч'), 0)
        self.assertEqual(self.balance(), Fraction(3, 100))
        self.assertEqual(result['credit_added'], '3/100')
        self.assertEqual(self.shop.execute(session, token, self.actor), result)

    def test_changed_assortment_stock_or_price_invalidates_open_card(self):
        self.add('Золото', 100)
        for change in ('disable', 'stock', 'price'):
            self.compile([(-111, 0, 'sword', True, 5)])
            session, token = self.buy_offer()
            if change == 'price':
                self.master_rows[0]['Стоимость'] = '2'
                self.save_master()
            self.compile([(-111, 0, 'sword', change != 'disable', 6 if change == 'stock' else 5)])
            before = self.sql('SELECT * FROM inventory')
            with self.assertRaises(PriceChanged):
                self.shop.execute(session, token, self.actor)
            self.assertEqual(self.sql('SELECT * FROM inventory'), before)

    def test_invalid_publication_keeps_previous_bytes_and_no_database_writes(self):
        self.open()
        before = self.csv.read_bytes()
        old_count = self.sql('SELECT COUNT(*) FROM dnd_shop_skus')
        products = parse_catalog(before)
        products.pop()
        self.stage.write_bytes(builder.csv_bytes(COLUMNS + EXTENDED, [
            {**vars(p), 'chat_id': p.chat_id, 'price': str(p.price),
             'active': str(p.active).lower(), 'buy_enabled': str(p.buy_enabled).lower()} for p in products]))
        with self.assertRaises(CatalogError):
            publish(self.stage, self.csv, self.shop.validate_staged)
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM dnd_shop_skus'), old_count)

    def test_read_failure_and_bad_snapshot_do_not_serve_cache(self):
        self.open()
        self.csv.write_bytes(bytes([255]))
        with self.assertRaises(CatalogError):
            self.shop.view(self.open(), self.actor, 'sell')

    def test_index_preserves_historical_sku_and_rejects_collisions(self):
        self.open()
        with closing(sqlite3.connect(self.db)) as conn:
            mapping = builder.index(self.master, conn=conn, scopes=[(-111, 0)])
            self.assertEqual(mapping['sword'], 'Меч')
            with self.assertRaises(CatalogError):
                builder.index(self.master, previous={'other': 'Меч'}, conn=conn, scopes=[(-111, 0)])

    def test_currency_cannot_be_enabled_and_bad_scopes_offers_rejected(self):
        for offers in [[(-111, 0, 'gold', True, 1)], [(-111, 3, 'sword', True, 1)],
                       [(-999, 0, 'sword', True, 1)], [(-111, 0, 'missing', True, 1)],
                       [(-111, 0, 'sword', True, 1)] * 2]:
            with self.assertRaises(CatalogError):
                self.compile(offers)

    def test_legacy_active_false_is_preserved_and_cannot_be_bypassed(self):
        # A previously published legacy inactive product is not reactivated.
        self.csv.unlink()
        self.write(active='false')
        self.open()
        self.compile([(-111, 0, 'sword', True, 3)])
        session = self.open()
        self.assertEqual(self.shop.view(session, self.actor, 'buy')['buttons'], [])
        data = self.csv.read_bytes().replace(b',false,true,', b',true,true,')
        self.stage.write_bytes(data)
        with self.assertRaises(CatalogError):
            publish(self.stage, self.csv, self.shop.validate_staged)

    def test_display_long_fields_and_credit_preserves_three_main_buttons(self):
        product = parse_catalog(self.csv.read_bytes())[0]
        from dataclasses import replace
        product = replace(product, description='😀<b>' * 5000, effect='😀&' * 5000)
        text, keyboard = ui.render('s', dict(screen='buy_card', product=product, token='t', available=0))
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4000)
        self.assertGreater(len(product.description), 10000)
        text, keyboard = ui.render('s', dict(screen='home', credit_balance=Fraction(3, 100)))
        self.assertEqual(len(keyboard.inline_keyboard), 3)
        self.assertIn('0,03', text)  # localized UI; stored Fraction remains unchanged
        text, keyboard = ui.render('s', dict(screen='credit', credit_balance=Fraction(3, 100), token='t'))
        self.assertIn(':r:', keyboard.inline_keyboard[0][0].callback_data)

    def test_warm_version_has_no_catalog_scan_or_registration(self):
        self.open()
        with patch('app.dnd_shop.service.register_in_transaction', side_effect=AssertionError('unexpected sync')):
            for _ in range(4):
                self.shop.view(self.open(), self.actor, 'buy')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM dnd_shop_skus'), [(4,)])

    def test_removed_master_sku_retains_sold_and_inactive_tombstone(self):
        self.csv.unlink()
        self.write(active='false')
        self.open()
        self.compile()
        self.open()
        original = self.master_rows[0]
        self.master_rows = self.master_rows[1:]
        self.save_master()
        self.compile()
        self.open()
        self.master_rows.insert(0, original)
        self.save_master()
        # Builder cannot infer deleted legacy inactive flags from current CSV;
        # persistent tombstone blocks automatic reactivation.
        with self.assertRaises(CatalogError):
            self.compile()

    def test_credit_isolated_between_topics_and_separate_sale_tokens(self):
        self.compile(scopes=((-111, 0), (-111, 2)))
        self.add('Меч', 1)
        session, token = self.sale_offer()
        _, other = self.sale_offer(session)
        self.shop.execute(session, token, self.actor)
        with self.assertRaises(ShopError):
            self.shop.execute(session, other, self.actor)
        self.sql("INSERT INTO characters VALUES(-111,2,'@alice','Other character')")
        actor = (101, 'alice', -111, 2)
        second = self.shop.open(*actor)
        self.assertEqual(self.shop.view(second, actor)['credit_balance'], 0)
        self.assertEqual(self.balance(), Fraction(3, 100))
        self.check_audit()

    def test_concurrent_redemption_returns_one_receipt(self):
        self.add('Меч', 1)
        self.sell('Меч')
        self.sql('UPDATE dnd_items SET active=1 WHERE id=?', (self.copper,))
        session = self.open()
        token = self.shop.view(session, self.actor, 'credit')['token']
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.shop.redeem(session, token, self.actor), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.amount('Медь'), 3)
        self.assertEqual(self.balance(), 0)
        self.check_audit()

    def test_small_csv_read_does_not_allocate_entire_catalog_limit(self):
        from app.dnd_shop.catalog import bounded_bytes
        original_open = Path.open
        requests = []
        class Tracking:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, amount):
                requests.append(amount)
                return self.stream.read(amount)
        def tracked(path, *args, **kwargs):
            return Tracking(original_open(path, *args, **kwargs))
        size = self.csv.stat().st_size
        with patch.object(Path, 'open', tracked):
            bounded_bytes(self.csv)
            snapshot(self.csv)
        self.assertGreaterEqual(len(requests), 2)
        self.assertTrue(all(n <= size + 1 for n in requests))
