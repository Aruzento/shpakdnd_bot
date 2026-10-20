"""Atomic ordinary D&D trades. Lock order: catalog lock, then SQLite.

Public inventory APIs deliberately are not used here: every mutation and audit
row shares one connection and one BEGIN IMMEDIATE.
"""
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from app.config import (BASE_DIR, DB_PATH, DND_SHOP_CSV_PATH, DND_LOOT_CSV_PATH,
                        DND_SHOP_CODE_MAP_PATH, DND_SHOP_OFFERS_PATH)
from app.context import normalize_username
from app.db.dnd_items import register_in_transaction
from app.dnd_shop.catalog import CatalogError, MAX_INTEGER, catalog_lock, snapshot
from app.dnd_shop.money import CalculationLimit, MoneyError, payment, payment_with_credit
from app.topics import TOPIC_SETTINGS


class ShopError(ValueError):
    pass


class PriceChanged(ShopError):
    pass


class Shop:
    def __init__(self, db_path=DB_PATH, csv_path=DND_SHOP_CSV_PATH, settings=None, *, loot_path=DND_LOOT_CSV_PATH,
                 map_path=DND_SHOP_CODE_MAP_PATH, offers_path=DND_SHOP_OFFERS_PATH):
        self.db_path = Path(db_path)
        self.loot_path = Path(loot_path)
        self.map_path = Path(map_path)
        self.offers_path = Path(offers_path)
        self.csv_path = Path(csv_path).resolve()
        if self.csv_path.is_relative_to(BASE_DIR.resolve()):
            raise CatalogError('CSV должен находиться вне Git checkout и области watcher.')
        self.settings = TOPIC_SETTINGS if settings is None else settings

    @contextmanager
    def transaction(self, *, catalog=True, scope=None):
        # Keep the read lock until COMMIT/ROLLBACK, including all money work.
        with catalog_lock(self.csv_path) if catalog else nullcontext():
            products = None
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN IMMEDIATE')
                if scope is not None:
                    from app.dnd_shop.control import enabled
                    enabled(self, conn, scope)
                if catalog:
                    products = snapshot(self.csv_path)
                    self.sync(conn, products)
                yield conn, products
                conn.commit()
            except sqlite3.OperationalError as error:
                conn.rollback()
                if 'locked' in str(error).lower() or 'busy' in str(error).lower():
                    raise ShopError('Магазин занят. Повторите действие; выполненная сделка не повторится.') from error
                raise
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def sync(self, conn, catalog, *, force=False):
        path = str(self.csv_path)
        seen = conn.execute('SELECT digest,master_sha256 FROM dnd_shop_catalog_versions WHERE source_path=?', (path,)).fetchone()
        if not force and seen and seen[0] == catalog.digest:
            return
        products = catalog.products
        extended = any(p.master_sha256 for p in products)
        if seen and seen['master_sha256'] and not extended:
            raise CatalogError('Нельзя подменить полный master snapshot старым/пустым CSV.')
        if extended:
            from app.dnd_shop.builder import read_master, verify_snapshot, require_scopes
            factors = {(r['chat_id'], r['thread_id']): Fraction(r['factor'])
                       for r in conn.execute('SELECT * FROM dnd_shop_settings')}
            verify_snapshot(products, read_master(self.loot_path), factors)
            published_scopes = {(p.chat_id, p.thread_id) for p in products}
            require_scopes(self.settings, published_scopes)
            previous_scopes = {tuple(row) for row in conn.execute(
                'SELECT DISTINCT chat_id,thread_id FROM dnd_shop_catalog_entries WHERE source_path=? AND present=1', (path,))}
            if not previous_scopes <= published_scopes:
                raise CatalogError('Нельзя удалить другую тему из общего каталога. Соберите все существующие scope; удаление требует отдельной миграции.')
        items = {(r['chat_id'], r['thread_id'], r['name_key']): r
                 for r in conn.execute('SELECT * FROM dnd_items')}
        by_id = {r['id']: r for r in items.values()}
        mappings = {(r['chat_id'], r['thread_id'], r['code']): r
                    for r in conn.execute('SELECT * FROM dnd_shop_skus')}
        mapped_items = {(r['chat_id'], r['thread_id'], r['item_id']): r['code'] for r in mappings.values()}
        currencies = {r[0] for r in conn.execute('SELECT item_id FROM dnd_currencies')}
        prior_entries = {(r['chat_id'], r['thread_id'], r['code']): r
                         for r in conn.execute('SELECT * FROM dnd_shop_catalog_entries WHERE source_path=?', (path,))}
        currency_aliases = {(r['chat_id'], r['thread_id']): json.loads(r['currency_units'])
                            for r in conn.execute('SELECT * FROM dnd_shop_settings')}
        entries = []
        for product in products:
            scope = product.chat_id, product.thread_id
            self.scope(*scope)
            currency_id = currency_aliases.get(scope, {}).get(product.name.casefold())
            named = by_id.get(currency_id) if currency_id else items.get((*scope, product.name.casefold()))
            existing = mappings.get(product.key)
            if existing:
                item_id = existing['item_id']
                if named and named['id'] != item_id:
                    raise CatalogError('SKU нельзя переназначить другому предмету без явной миграции.')
                item = by_id.get(item_id)
                if not item or extended and item['name_key'] != product.name.casefold() and item_id != currency_id:
                    raise CatalogError('Каноническое имя SKU изменено; требуется явная миграция.')
            else:
                item_id = named['id'] if named else register_in_transaction(
                    conn, *scope, product.name, product.description)
                if (*scope, item_id) in mapped_items:
                    raise CatalogError('Одному предмету не могут соответствовать несколько SKU.')
                conn.execute('INSERT INTO dnd_shop_skus(chat_id,thread_id,code,item_id,original_name_key) VALUES(?,?,?,?,?)',
                             (*product.key, item_id, product.name.casefold()))
                mapped_items[(*scope, item_id)] = product.code
                item = named or conn.execute('SELECT * FROM dnd_items WHERE id=?', (item_id,)).fetchone()
                by_id[item_id] = item
            is_currency = item['category'] == 'currency' or item_id in currencies
            if is_currency and (not extended or product.buy_enabled):
                raise CatalogError('Товар/предложение конфликтует с зарегистрированной валютой.')
            prior = prior_entries.get(product.key)
            if extended and prior and not prior['active'] and product.active:
                raise CatalogError('Нельзя автоматически включить legacy active=false. Нужна согласованная миграция.')
            entries.append((path, *product.key, item_id, str(product.price), product.stock,
                            int(product.active), int(product.buy_enabled), int(is_currency)))
        conn.execute('UPDATE dnd_shop_catalog_entries SET present=0 WHERE source_path=?', (path,))
        conn.executemany('''INSERT INTO dnd_shop_catalog_entries
            (source_path,chat_id,thread_id,code,item_id,price,stock,active,buy_enabled,is_currency)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_path,chat_id,thread_id,code) DO UPDATE SET
                item_id=excluded.item_id,price=excluded.price,stock=excluded.stock,
                active=excluded.active,buy_enabled=excluded.buy_enabled,is_currency=excluded.is_currency,present=1''', entries)
        conn.execute('''INSERT INTO dnd_shop_catalog_versions(source_path,digest,imported,master_sha256) VALUES(?,?,?,?)
            ON CONFLICT(source_path) DO UPDATE SET digest=excluded.digest,imported=excluded.imported,
            updated_at=CURRENT_TIMESTAMP,master_sha256=excluded.master_sha256''',
            (path, catalog.digest, len(products), products[0].master_sha256 if extended else ''))

    def scope(self, chat, thread):
        topic = self.settings.get(chat, {}).get(thread)
        if topic is None or topic.get('mini'):
            raise ShopError('Магазин доступен только в настроенной теме обычного D&D.')
        return topic

    def character(self, conn, chat, thread, username):
        topic = self.scope(chat, thread)
        rows = [row for row in conn.execute('SELECT rowid,username,name FROM characters WHERE chat_id=? AND thread_id=?', (chat, thread))
                if normalize_username(row['username']) == username]
        if len(rows) > 1:
            raise ShopError('Неоднозначный владелец персонажа.')
        if rows:
            if rows[0]['username'] != username or not rows[0]['name'].strip():
                raise ShopError('Неоднозначный владелец персонажа.')
            return f"db:{rows[0]['rowid']}:{rows[0]['name']}"
        names = [name for user, name in topic.get('characters', {}).items() if normalize_username(user) == username]
        if len(names) != 1 or not str(names[0]).strip():
            raise ShopError('Для вашего username не найден однозначный персонаж в этой теме.')
        return 'config:' + str(names[0])

    def open(self, user_id, username, chat, thread):
        self.scope(chat, thread)
        if type(user_id) is not int or user_id <= 0 or not username or not username.strip('@ '):
            raise ShopError('Для магазина необходим Telegram username.')
        username = normalize_username(username)
        with self.transaction(scope=(chat, thread)) as (conn, _):
            character = self.character(conn, chat, thread, username)
            owners = conn.execute('SELECT * FROM dnd_shop_owners WHERE chat_id=? AND thread_id=? AND (user_id=? OR username=?)',
                                  (chat, thread, user_id, username)).fetchall()
            if owners and (len(owners) != 1 or owners[0]['user_id'] != user_id or owners[0]['username'] != username):
                raise ShopError('Связь Telegram ID и username изменилась. Нужна проверка владельца администратором.')
            if not owners:
                conn.execute('INSERT INTO dnd_shop_owners VALUES(?,?,?,?)', (chat, thread, username, user_id))
            token = secrets.token_urlsafe(12)
            conn.execute('INSERT INTO dnd_shop_sessions(token,chat_id,thread_id,user_id,username,character_key,expires) VALUES(?,?,?,?,?,?,?)',
                         (token, chat, thread, user_id, username, character, int(time.time()) + 86400))
            return token

    def session(self, conn, token, user_id, username, chat, thread):
        row = conn.execute('SELECT * FROM dnd_shop_sessions WHERE token=?', (token,)).fetchone()
        if not username or not row or (row['user_id'], row['username'], row['chat_id'], row['thread_id']) != (user_id, normalize_username(username), chat, thread):
            raise ShopError('Это чужая кнопка магазина или другая тема.')
        if row['closed'] or row['expires'] < int(time.time()):
            raise ShopError('Магазин закрыт или кнопка устарела. Откройте /shop.')
        owner = conn.execute('SELECT user_id FROM dnd_shop_owners WHERE chat_id=? AND thread_id=? AND username=?', (chat, thread, row['username'])).fetchone()
        if not owner or owner[0] != user_id or self.character(conn, chat, thread, row['username']) != row['character_key']:
            raise ShopError('Владелец или персонаж изменился. Откройте /shop.')
        return row

    def inventory(self, conn, session):
        rows = conn.execute('SELECT * FROM inventory WHERE chat_id=? AND thread_id=?',
                            (session['chat_id'], session['thread_id'])).fetchall()
        result = []
        for row in rows:
            if normalize_username(row['username']) != session['username']:
                continue
            if row['username'] != session['username']:
                raise ShopError('Неоднозначное имя владельца в инвентаре.')
            if type(row['quantity']) is not int or not 0 < row['quantity'] <= MAX_INTEGER:
                raise ShopError('Некорректное количество в инвентаре. Нужна проверка администратором.')
            result.append(row)
        return result

    @staticmethod
    def resolve(conn, row):
        if row['item_id'] is not None:
            item = conn.execute('SELECT * FROM dnd_items WHERE id=? AND chat_id=? AND thread_id=?',
                                (row['item_id'], row['chat_id'], row['thread_id'])).fetchone()
        else:
            item = conn.execute('SELECT * FROM dnd_items WHERE name_key=? AND chat_id=? AND thread_id=?',
                                (row['name'].strip().casefold(), row['chat_id'], row['thread_id'])).fetchone()
        if item and row['item_id'] is not None and row['name'].strip().casefold() != item['name_key']:
            raise ShopError('Название инвентаря конфликтует с item_id.')
        return item

    def rows_for_item(self, conn, rows, item_id):
        return [row for row in rows if (item := self.resolve(conn, row)) and item['id'] == item_id]

    def currencies(self, conn, session):
        invalid = conn.execute("""SELECT 1 FROM dnd_currencies c LEFT JOIN dnd_items i ON i.id=c.item_id
            WHERE c.chat_id=? AND c.thread_id=? AND
            (i.id IS NULL OR i.chat_id!=c.chat_id OR i.thread_id!=c.thread_id OR i.category!='currency') LIMIT 1""",
            (session['chat_id'], session['thread_id'])).fetchone()
        if invalid:
            raise ShopError('Нарушена целостность реестра валют.')
        rows = conn.execute('''SELECT i.*,c.base_value,c.currency_quantity,c.is_base
            FROM dnd_currencies c JOIN dnd_items i ON i.id=c.item_id
            WHERE c.chat_id=? AND c.thread_id=? AND i.active=1 AND i.category='currency'
            ORDER BY i.id''', (session['chat_id'], session['thread_id'])).fetchall()
        if len(rows) > 32:
            raise ShopError('Превышен безопасный предел расчёта: более 32 активных номиналов.')
        if sum(row['is_base'] for row in rows) != 1:
            raise ShopError('Нет активной базовой валюты темы. Обратитесь к администратору.')
        denominations = {}
        for row in rows:
            try:
                value = Fraction(Decimal(row['base_value'])) / row['currency_quantity']
                if value <= 0 or type(row['currency_quantity']) is not int or row['currency_quantity'] <= 0:
                    raise ValueError()
            except (ValueError, ArithmeticError, TypeError) as error:
                raise ShopError('Некорректный номинал валюты.') from error
            denominations[row['id']] = value
        return rows, denominations

    def coin_profile(self, conn, session, rows=None, denominations=None):
        from app.dnd_shop.coins import profile
        if rows is None:
            rows,denominations=self.currencies(conn,session)
        state=conn.execute('SELECT pricing_mode FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?',
                           (session['chat_id'],session['thread_id'])).fetchone()
        return profile(rows,denominations,self.scope(session['chat_id'],session['thread_id']),state[0] if state else None)

    def presentation(self, session_token, actor, result=None):
        # Read-only UI layer. Historical JSON and denomination rows are never rewritten.
        with self.transaction(catalog=False,scope=actor[2:]) as (conn,_):
            session=self.session(conn,session_token,*actor)
            display=dict(profile=self.coin_profile(conn,session),base_name=self.base_name(conn,session))
            if result and result['kind']!='redeem':
                trade=conn.execute('SELECT id FROM dnd_shop_trades WHERE id=? AND chat_id=? AND thread_id=? AND user_id=?',
                                   (result['trade_id'],*actor[2:],actor[0])).fetchone()
                if not trade:
                    raise ShopError('Квитанция не принадлежит владельцу.')
                rows=conn.execute('SELECT * FROM dnd_shop_money WHERE trade_id=?',(trade[0],)).fetchall()
                display['debit_total']=str(sum((r['quantity']*Fraction(r['denomination']) for r in rows if r['direction']=='debit'),Fraction(0)))
                display['credit_total']=str(sum((r['quantity']*Fraction(r['denomination']) for r in rows if r['direction']!='debit'),Fraction(0)))
            return display

    def product(self, conn, products, session, code):
        key = session['chat_id'], session['thread_id'], code
        product = products.by_key.get(key)
        if product is None or not product.active:
            raise ShopError('Торговец: «Не, это я не куплю». Товар удалён или отключён.')
        mapping = conn.execute('SELECT * FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND code=?', key).fetchone()
        item = conn.execute('SELECT * FROM dnd_items WHERE id=?', (mapping['item_id'],)).fetchone()
        if not item['active']:
            raise ShopError('Предмет отключён в реестре.')
        if item['category'] == 'currency' or conn.execute('SELECT 1 FROM dnd_currencies WHERE item_id=?', (item['id'],)).fetchone():
            raise ShopError('Зарегистрированную валюту нельзя продавать как вещь.')
        if product.master_sha256 and item['name_key'] != product.name.casefold():
            raise ShopError('Каноническое имя предмета изменено.')
        return product, mapping

    def offer(self, conn, session, kind, product, mapping, inventory_id=None):
        token = secrets.token_urlsafe(12)
        conn.execute('''INSERT INTO dnd_shop_operations(token,session_token,kind,sku,item_id,inventory_id,price)
                        VALUES(?,?,?,?,?,?,?)''',
                     (token, session['token'], kind, product.code, mapping['item_id'], inventory_id, str(product.price)))
        conn.execute('UPDATE dnd_shop_operations SET quote_digest=? WHERE token=?', (product.quote, token))
        return token

    @staticmethod
    def base_name(conn, session):
        row = conn.execute('''SELECT i.name FROM dnd_items i JOIN dnd_currencies c ON c.item_id=i.id
            WHERE c.chat_id=? AND c.thread_id=? AND c.is_base=1''',
            (session['chat_id'], session['thread_id'])).fetchone()
        return row[0] if row else 'базовой валюты'

    def view(self, session_token, actor, action='home', argument='0'):
        """Fresh source on every navigation; return data for a thin Telegram UI."""
        with self.transaction(catalog=action not in ('home', 'exit'), scope=actor[2:]) as (conn, products):
            session = self.session(conn, session_token, *actor)
            if action == 'exit':
                conn.execute('UPDATE dnd_shop_sessions SET closed=1 WHERE token=?', (session_token,))
                return {'screen': 'exit'}
            if action == 'home':
                return {'screen': 'home', 'credit_balance': self.credit(conn, session)}
            if action in ('buy', 'sell'):
                page = max(0, int(argument))
                entries = []
                if action == 'buy':
                    scope = session['chat_id'], session['thread_id']
                    codes = conn.execute('''SELECT e.code FROM dnd_shop_catalog_entries e
                        JOIN dnd_shop_skus s ON s.chat_id=e.chat_id AND s.thread_id=e.thread_id AND s.code=e.code
                        JOIN dnd_items i ON i.id=e.item_id
                        WHERE e.source_path=? AND e.chat_id=? AND e.thread_id=?
                        AND e.present=1 AND e.buy_enabled=1 AND e.active=1 AND i.active=1 AND e.is_currency=0
                        AND i.category!='currency' AND NOT EXISTS(SELECT 1 FROM dnd_currencies c WHERE c.item_id=i.id)
                        AND s.sold<e.stock ORDER BY e.code''', (str(self.csv_path), *scope)).fetchall()
                    entries = [products.by_key[(*scope, row[0])] for row in codes]
                else:
                    inventory_rows = self.inventory(conn, session)
                    resolved = [(row, self.resolve(conn, row)) for row in inventory_rows]
                    from collections import Counter
                    counts = Counter(item['id'] for _, item in resolved if item)
                    currencies = {r[0] for r in conn.execute('SELECT item_id FROM dnd_currencies')}
                    codes = {r['item_id']: r['code'] for r in conn.execute(
                        'SELECT item_id,code FROM dnd_shop_skus WHERE chat_id=? AND thread_id=?',
                        (session['chat_id'], session['thread_id']))}
                    for row, item in resolved:
                        if not item or not item['active'] or item['category'] == 'currency' or item['id'] in currencies:
                            continue
                        product = products.by_key.get((session['chat_id'], session['thread_id'], codes.get(item['id'])))
                        if product and product.active and counts[item['id']] == 1:
                            entries.append(row)
                pages = max(1, (len(entries) + 3) // 4)
                page = min(page, pages - 1)
                buttons = []
                for entry in entries[page * 4:page * 4 + 4]:
                    if action == 'buy':
                        product = entry
                        _, mapping = self.product(conn, products, session, product.code)
                        token = self.offer(conn, session, 'buy', product, mapping)
                        buttons.append((product.name, token))
                    else:
                        buttons.append((entry['name'], str(entry['id'])))
                return {'screen': action, 'page': page, 'pages': pages, 'buttons': buttons,
                        'prices': [(p.name,p.price) for p in entries[page*4:page*4+4]] if action=='buy' else [],
                        'credit_balance': self.credit(conn, session)}
            if action == 'credit':
                balance = self.credit(conn, session)
                if balance <= 0:
                    raise ShopError('Торговый остаток пуст.')
                token = secrets.token_urlsafe(12)
                conn.execute('INSERT INTO dnd_shop_credit_redemptions(token,session_token,expected) VALUES(?,?,?)',
                             (token, session_token, str(balance)))
                return dict(screen='credit', credit_balance=balance, token=token)
            if action in ('buy_card', 'refresh'):
                old = conn.execute('SELECT * FROM dnd_shop_operations WHERE token=? AND session_token=?',
                                   (argument, session_token)).fetchone()
                if not old:
                    raise ShopError('Предложение устарело.')
                product, mapping = self.product(conn, products, session, old['sku'])
                if old['kind'] == 'sell':
                    return self.sale_card(conn, products, session, old['inventory_id'])
                if not product.buy_enabled or product.stock <= mapping['sold']:
                    raise ShopError('Позиция недоступна для покупки. Откройте ассортимент заново.')
                token = self.offer(conn, session, 'buy', product, mapping)
                return {'screen': 'buy_card', 'product': product, 'token': token,
                        'available': max(0, product.stock - mapping['sold']), 'base_name': self.base_name(conn, session)}
            if action == 'sell_card':
                return self.sale_card(conn, products, session, int(argument))
            raise ShopError('Неизвестная кнопка магазина.')

    def sale_card(self, conn, products, session, inventory_id):
        rows = self.inventory(conn, session)
        row = next((row for row in rows if row['id'] == inventory_id), None)
        if not row:
            raise ShopError('Предмета больше нет в инвентаре.')
        item = self.resolve(conn, row)
        if not item or item['category'] == 'currency' or len(self.rows_for_item(conn, rows, item['id'])) != 1:
            raise ShopError('Торговец: «Не, это я не куплю». Неоднозначный предмет или валюта.')
        mapping = conn.execute('SELECT code FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND item_id=?',
                               (session['chat_id'], session['thread_id'], item['id'])).fetchone()
        if not mapping:
            raise ShopError('Торговец: «Не, это я не куплю».')
        product, mapping = self.product(conn, products, session, mapping['code'])
        token = self.offer(conn, session, 'sell', product, mapping, row['id'])
        return {'screen': 'sell_card', 'product': product, 'token': token, 'quantity': row['quantity'],
                'payout': product.price * Fraction(3, 100), 'name': row['name'], 'base_name': self.base_name(conn, session)}

    def adjust(self, conn, session, item_id, delta, rows=None):
        rows = self.inventory(conn, session) if rows is None else rows
        matches = self.rows_for_item(conn, rows, item_id)
        if len(matches) > 1:
            raise ShopError('Неоднозначные старые записи инвентаря. Нужна проверка администратором.')
        if matches:
            row = matches[0]
            quantity = row['quantity'] + delta
            if not 0 <= quantity <= MAX_INTEGER:
                raise ShopError('Недостаточно предметов или превышен предел количества.')
            if quantity:
                conn.execute('UPDATE inventory SET quantity=? WHERE id=?', (quantity, row['id']))
            else:
                conn.execute('DELETE FROM inventory WHERE id=?', (row['id'],))
        elif delta > 0 and delta <= MAX_INTEGER:
            item = conn.execute('SELECT * FROM dnd_items WHERE id=?', (item_id,)).fetchone()
            # Protect old name-based admin APIs from merging different identities.
            if any(row['name'].casefold() == item['name_key'] for row in rows):
                raise ShopError('Название конфликтует со старой записью инвентаря.')
            conn.execute('INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description,item_id) VALUES(?,?,?,?,?,?,?)',
                         (session['chat_id'], session['thread_id'], session['username'], item['name'], delta, item['description'], item_id))
        else:
            raise ShopError('Недостаточно предметов в инвентаре.')

    @staticmethod
    def rational(text):
        try:
            value = Fraction(text)
            if not isinstance(text, str) or len(text) > 1024 or value < 0 or str(value) != text:
                raise ValueError()
            return value
        except (ValueError, ZeroDivisionError, TypeError) as error:
            raise ShopError('Некорректный торговый остаток; требуется проверка журнала.') from error

    def credit(self, conn, session):
        owner = session['chat_id'], session['thread_id'], session['user_id']
        row = conn.execute('SELECT * FROM dnd_shop_credit WHERE chat_id=? AND thread_id=? AND user_id=?', owner).fetchone()
        if not row:
            return Fraction(0)
        if row['username'] != session['username']:
            raise ShopError('Владелец торгового остатка изменился.')
        balance = self.rational(row['balance'])
        last = conn.execute('''SELECT balance_after FROM dnd_shop_credit_moves
            WHERE chat_id=? AND thread_id=? AND user_id=? ORDER BY id DESC LIMIT 1''', owner).fetchone()
        if (self.rational(last[0]) if last else Fraction(0)) != balance:
            raise ShopError('Торговый остаток не совпадает с журналом.')
        return balance

    def move_credit(self, conn, session, amount, direction, *, trade=None, redemption=None):
        amount = Fraction(amount)
        if amount <= 0:
            raise ShopError('Движение остатка должно быть положительным.')
        before = self.credit(conn, session)
        after = before + amount if direction == 'add' else before - amount
        if after < 0 or len(str(after)) > 1024:
            raise ShopError('Недостаточно торгового остатка или превышен предел учёта.')
        owner = session['chat_id'], session['thread_id'], session['user_id']
        conn.execute('''INSERT INTO dnd_shop_credit(chat_id,thread_id,user_id,username,balance) VALUES(?,?,?,?,?)
            ON CONFLICT(chat_id,thread_id,user_id) DO UPDATE SET balance=excluded.balance''',
            (*owner, session['username'], str(after)))
        conn.execute('''INSERT INTO dnd_shop_credit_moves
            (chat_id,thread_id,user_id,trade_id,redemption_token,direction,amount,balance_after)
            VALUES(?,?,?,?,?,?,?,?)''', (*owner, trade, redemption, direction, str(amount), str(after)))
        return after

    def execute(self, session_token, operation_token, actor):
        with self.transaction(scope=actor[2:]) as (conn, products):
            session = self.session(conn, session_token, *actor)
            operation = conn.execute('SELECT * FROM dnd_shop_operations WHERE token=? AND session_token=?',
                                     (operation_token, session_token)).fetchone()
            if not operation:
                raise ShopError('Предложение устарело.')
            if operation['result'] is not None:
                return json.loads(operation['result'])
            product, mapping = self.product(conn, products, session, operation['sku'])
            if mapping['item_id'] != operation['item_id']:
                raise ShopError('Соответствие предмета изменилось.')
            if product.price != Fraction(operation['price']):
                raise PriceChanged('Цена изменилась. Проверьте актуальное предложение и нажмите ещё раз.')
            if operation['kind'] == 'buy':
                if operation['quote_digest'] != product.quote and (operation['quote_digest'] or product.master_sha256):
                    raise PriceChanged('Ассортимент или остаток изменился. Откройте новое предложение.')
                if not product.buy_enabled:
                    raise ShopError('Покупка этой позиции отключена владельцем.')
            inventory = self.inventory(conn, session)
            currency_rows, denominations = self.currencies(conn, session)
            from app.dnd_shop.coins import output_denominations
            payouts=output_denominations(self.coin_profile(conn,session,currency_rows,denominations),denominations)
            kind = operation['kind']
            debit, coins_out = {}, {}
            credit_added = credit_spent = Fraction(0)
            balance_before = self.credit(conn, session)
            if kind == 'buy':
                if mapping['sold'] >= product.stock:
                    raise ShopError('Товара больше нет в наличии.')
                holdings = {}
                for item_id in denominations:
                    matches = self.rows_for_item(conn, inventory, item_id)
                    if len(matches) > 1:
                        raise ShopError('Неоднозначные записи монет в инвентаре.')
                    holdings[item_id] = matches[0]['quantity'] if matches else 0
            # Keep planning and mutation separate so all failures precede writes.
            if kind == 'buy':
                debit, coins_out, credit_spent = payment_with_credit(product.price,
                    {i:v for i,v in denominations.items() if holdings.get(i,0) or i in payouts}, holdings,
                    balance_before, change_denominations=payouts)
                value = product.price
            else:
                row = next((r for r in inventory if r['id'] == operation['inventory_id']), None)
                item = self.resolve(conn, row) if row else None
                if not item or item['id'] != mapping['item_id'] or item['category'] == 'currency' or conn.execute(
                        'SELECT 1 FROM dnd_currencies WHERE item_id=?', (item['id'],)).fetchone():
                    raise ShopError('Предмета больше нет или его нельзя продать.')
                if len(self.rows_for_item(conn, inventory, item['id'])) != 1:
                    raise ShopError('Неоднозначный предмет в инвентаре.')
                value = product.price * Fraction(3, 100)
                try:
                    _, coins_out = payment(value, payouts, {}, payout=True)
                except MoneyError:
                    coins_out = {}
                    credit_added = value
            coin_net = sum(denominations[i] * n for i, n in debit.items()) - sum(
                denominations[i] * n for i, n in coins_out.items())
            if kind == 'buy':
                if coin_net + credit_spent != value:
                    raise ShopError('Нарушен денежный инвариант покупки.')
            else:
                if -coin_net + credit_added != value:
                    raise ShopError('Нарушен денежный инвариант продажи.')
            if kind == 'sell':
                self.adjust(conn, session, mapping['item_id'], -1)
            for item_id, quantity in debit.items():
                self.adjust(conn, session, item_id, -quantity)
            for item_id, quantity in coins_out.items():
                self.adjust(conn, session, item_id, quantity)
            if kind == 'buy':
                self.adjust(conn, session, mapping['item_id'], 1)
                conn.execute('UPDATE dnd_shop_skus SET sold=sold+1 WHERE chat_id=? AND thread_id=? AND code=?', product.key)
            trade = conn.execute('''INSERT INTO dnd_shop_trades(operation_token,kind,chat_id,thread_id,user_id,username,
                character_key,sku,item_id,price,value,quantity) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)''',
                (operation_token, kind, session['chat_id'], session['thread_id'], session['user_id'], session['username'],
                 session['character_key'], product.code, mapping['item_id'], str(product.price), str(value))).lastrowid
            for direction, amounts in (('debit', debit), ('change' if kind == 'buy' else 'payout', coins_out)):
                for item_id, quantity in amounts.items():
                    conn.execute('INSERT INTO dnd_shop_money VALUES(?,?,?,?,?)',
                                 (trade, item_id, direction, quantity, str(denominations[item_id])))
            balance_after = balance_before
            if credit_added:
                balance_after = self.move_credit(conn, session, credit_added, 'add', trade=trade)
            if credit_spent:
                balance_after = self.move_credit(conn, session, credit_spent, 'spend', trade=trade)
            names = {row['id']: row['name'] for row in currency_rows}
            result = {'kind': kind, 'name': product.name, 'quantity': 1, 'value': str(value),
                      'debit': [[names[i], n] for i, n in debit.items()],
                      'credit': [[names[i], n] for i, n in coins_out.items()], 'trade_id': trade,
                      'credit_added': str(credit_added), 'credit_spent': str(credit_spent),
                      'credit_balance': str(balance_after), 'base_name': self.base_name(conn, session)}
            conn.execute('UPDATE dnd_shop_operations SET result=?,completed_at=CURRENT_TIMESTAMP WHERE token=?',
                         (json.dumps(result, ensure_ascii=False), operation_token))
            return result

    def redeem(self, session_token, token, actor):
        with self.transaction(scope=actor[2:]) as (conn, _):
            session = self.session(conn, session_token, *actor)
            operation = conn.execute('SELECT * FROM dnd_shop_credit_redemptions WHERE token=? AND session_token=?',
                                     (token, session_token)).fetchone()
            if not operation:
                raise ShopError('Предложение обмена устарело.')
            if operation['result'] is not None:
                return json.loads(operation['result'])
            balance = self.credit(conn, session)
            if balance <= 0 or str(balance) != operation['expected']:
                raise ShopError('Остаток изменился; откройте новое предложение обмена.')
            currency_rows, denominations = self.currencies(conn, session)
            from app.dnd_shop.coins import output_denominations
            outputs=output_denominations(self.coin_profile(conn,session,currency_rows,denominations),denominations)
            _, payout = payment(balance, outputs, {}, payout=True)
            for item_id, quantity in payout.items():
                self.adjust(conn, session, item_id, quantity)
                conn.execute('INSERT INTO dnd_shop_credit_coins VALUES(?,?,?,?)',
                             (token, item_id, quantity, str(denominations[item_id])))
            self.move_credit(conn, session, balance, 'redeem', redemption=token)
            names = {row['id']: row['name'] for row in currency_rows}
            result = dict(kind='redeem', value=str(balance), credit_balance='0',
                          credit=[[names[i], n] for i, n in payout.items()])
            conn.execute('UPDATE dnd_shop_credit_redemptions SET result=?,completed_at=CURRENT_TIMESTAMP WHERE token=?',
                         (json.dumps(result, ensure_ascii=False), token))
            return result

    def validate_staged(self, path):
        """Full validation under the caller's catalog lock; no persisted mapping changes."""
        catalog = snapshot(path)
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM dnd_shop_publications WHERE source_path=?', (str(self.csv_path),)).fetchone():
                raise CatalogError('Сначала завершите незавершённую публикацию через /shopcreate.')
            self.sync(conn, catalog, force=True)
        finally:
            conn.rollback()
            conn.close()

    def stop(self, actor):
        from app.dnd_shop.control import stop
        return stop(self, actor)

    def create(self, actor, mode):
        from app.dnd_shop.control import create
        return create(self, actor, mode)

    @staticmethod
    def catalog_from_bytes(data):
        import hashlib
        from types import MappingProxyType
        from app.dnd_shop.catalog import Snapshot, parse_catalog
        products = tuple(parse_catalog(data))
        return Snapshot(products, hashlib.sha256(data).hexdigest(),
                        MappingProxyType({p.key: p for p in products}), len(data))
