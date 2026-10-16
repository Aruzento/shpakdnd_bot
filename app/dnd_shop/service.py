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

from app.config import BASE_DIR, DB_PATH, DND_SHOP_CSV_PATH
from app.context import normalize_username
from app.db.dnd_items import register_in_transaction
from app.dnd_shop.catalog import CatalogError, MAX_INTEGER, catalog_lock, read_catalog
from app.dnd_shop.money import MoneyError, payment
from app.topics import TOPIC_SETTINGS


class ShopError(ValueError):
    pass


class PriceChanged(ShopError):
    pass


class Shop:
    def __init__(self, db_path=DB_PATH, csv_path=DND_SHOP_CSV_PATH, settings=None):
        self.db_path = Path(db_path)
        self.csv_path = Path(csv_path).resolve()
        if self.csv_path.is_relative_to(BASE_DIR.resolve()):
            raise CatalogError('CSV должен находиться вне Git checkout и области watcher.')
        self.settings = TOPIC_SETTINGS if settings is None else settings

    @contextmanager
    def transaction(self, *, catalog=True):
        # Keep the read lock until COMMIT/ROLLBACK, including all money work.
        with catalog_lock(self.csv_path) if catalog else nullcontext():
            products = read_catalog(self.csv_path) if catalog else []
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('BEGIN IMMEDIATE')
                if catalog:
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

    def sync(self, conn, products):
        for product in products:
            scope = product.chat_id, product.thread_id
            if self.settings.get(scope[0], {}).get(scope[1], {}).get('mini'):
                raise CatalogError('CSV содержит товар в теме D&D Mini.')
            existing = conn.execute('SELECT * FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND code=?', product.key).fetchone()
            named = conn.execute('SELECT * FROM dnd_items WHERE chat_id=? AND thread_id=? AND name_key=?',
                                 (*scope, product.name.casefold())).fetchone()
            if named and (named['category'] == 'currency' or conn.execute('SELECT 1 FROM dnd_currencies WHERE item_id=?', (named['id'],)).fetchone()):
                raise CatalogError('Название товара конфликтует с зарегистрированной валютой.')
            if existing:
                item_id = existing['item_id']
                if named and named['id'] != item_id:
                    raise CatalogError('SKU нельзя переназначить другому предмету без явной миграции.')
            else:
                item_id = register_in_transaction(conn, *scope, product.name, product.description)
                collision = conn.execute('SELECT code FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND item_id=?', (*scope, item_id)).fetchone()
                if collision:
                    raise CatalogError('Одному предмету не могут соответствовать несколько SKU.')
                conn.execute('INSERT INTO dnd_shop_skus(chat_id,thread_id,code,item_id,original_name_key) VALUES(?,?,?,?,?)',
                             (*product.key, item_id, product.name.casefold()))
            item = conn.execute('SELECT * FROM dnd_items WHERE id=?', (item_id,)).fetchone()
            if not item or item['category'] == 'currency' or conn.execute('SELECT 1 FROM dnd_currencies WHERE item_id=?', (item_id,)).fetchone():
                raise CatalogError('Недопустимое соответствие SKU и реестра предметов.')

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
        with self.transaction() as (conn, _):
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
        return item

    def rows_for_item(self, conn, rows, item_id):
        return [row for row in rows if (item := self.resolve(conn, row)) and item['id'] == item_id]

    def currencies(self, conn, session):
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

    def product(self, conn, products, session, code):
        key = session['chat_id'], session['thread_id'], code
        product = next((p for p in products if p.key == key), None)
        if product is None or not product.active:
            raise ShopError('Торговец: «Не, это я не куплю». Товар удалён или отключён.')
        mapping = conn.execute('SELECT * FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND code=?', key).fetchone()
        item = conn.execute('SELECT * FROM dnd_items WHERE id=?', (mapping['item_id'],)).fetchone()
        if not item['active']:
            raise ShopError('Предмет отключён в реестре.')
        return product, mapping

    def offer(self, conn, session, kind, product, mapping, inventory_id=None):
        token = secrets.token_urlsafe(12)
        conn.execute('''INSERT INTO dnd_shop_operations(token,session_token,kind,sku,item_id,inventory_id,price)
                        VALUES(?,?,?,?,?,?,?)''',
                     (token, session['token'], kind, product.code, mapping['item_id'], inventory_id, str(product.price)))
        return token

    @staticmethod
    def base_name(conn, session):
        row = conn.execute('''SELECT i.name FROM dnd_items i JOIN dnd_currencies c ON c.item_id=i.id
            WHERE c.chat_id=? AND c.thread_id=? AND c.is_base=1''',
            (session['chat_id'], session['thread_id'])).fetchone()
        return row[0] if row else 'базовой валюты'

    def view(self, session_token, actor, action='home', argument='0'):
        """Fresh source on every navigation; return data for a thin Telegram UI."""
        with self.transaction(catalog=action not in ('home', 'exit')) as (conn, products):
            session = self.session(conn, session_token, *actor)
            if action == 'exit':
                conn.execute('UPDATE dnd_shop_sessions SET closed=1 WHERE token=?', (session_token,))
                return {'screen': 'exit'}
            if action == 'home':
                return {'screen': 'home'}
            if action in ('buy', 'sell'):
                page = max(0, int(argument))
                entries = []
                if action == 'buy':
                    for product in products:
                        if product.key[:2] != (session['chat_id'], session['thread_id']) or not product.active:
                            continue
                        try:
                            _, mapping = self.product(conn, products, session, product.code)
                        except ShopError:
                            continue
                        entries.append((product, mapping))
                else:
                    for row in self.inventory(conn, session):
                        item = self.resolve(conn, row)
                        if item and (item['category'] == 'currency' or conn.execute('SELECT 1 FROM dnd_currencies WHERE item_id=?', (item['id'],)).fetchone()):
                            continue
                        entries.append(row)
                pages = max(1, (len(entries) + 3) // 4)
                page = min(page, pages - 1)
                buttons = []
                for entry in entries[page * 4:page * 4 + 4]:
                    if action == 'buy':
                        product, mapping = entry
                        token = self.offer(conn, session, 'buy', product, mapping)
                        buttons.append((product.name, token))
                    else:
                        buttons.append((entry['name'], str(entry['id'])))
                return {'screen': action, 'page': page, 'pages': pages, 'buttons': buttons}
            if action in ('buy_card', 'refresh'):
                old = conn.execute('SELECT * FROM dnd_shop_operations WHERE token=? AND session_token=?',
                                   (argument, session_token)).fetchone()
                if not old:
                    raise ShopError('Предложение устарело.')
                product, mapping = self.product(conn, products, session, old['sku'])
                if old['kind'] == 'sell':
                    return self.sale_card(conn, products, session, old['inventory_id'])
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

    def execute(self, session_token, operation_token, actor):
        with self.transaction() as (conn, products):
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
            inventory = self.inventory(conn, session)
            currency_rows, denominations = self.currencies(conn, session)
            kind = operation['kind']
            if kind == 'buy':
                if mapping['sold'] >= product.stock:
                    raise ShopError('Товара больше нет в наличии.')
                holdings = {}
                for item_id in denominations:
                    matches = self.rows_for_item(conn, inventory, item_id)
                    if len(matches) > 1:
                        raise ShopError('Неоднозначные записи монет в инвентаре.')
                    holdings[item_id] = matches[0]['quantity'] if matches else 0
                debit, credit = payment(product.price, denominations, holdings)
                value = product.price
            else:
                row = next((row for row in inventory if row['id'] == operation['inventory_id']), None)
                item = self.resolve(conn, row) if row else None
                if not item or item['id'] != mapping['item_id'] or item['category'] == 'currency' or conn.execute('SELECT 1 FROM dnd_currencies WHERE item_id=?', (item['id'],)).fetchone():
                    raise ShopError('Предмета больше нет или его нельзя продать.')
                if len(self.rows_for_item(conn, inventory, item['id'])) != 1:
                    raise ShopError('Неоднозначный предмет в инвентаре.')
                value = product.price * Fraction(3, 100)
                debit, credit = payment(value, denominations, {}, payout=True)
            if kind == 'buy':
                assert sum(denominations[i] * n for i, n in debit.items()) - sum(denominations[i] * n for i, n in credit.items()) == value
            else:
                assert sum(denominations[i] * n for i, n in credit.items()) == value
            if kind == 'sell':
                self.adjust(conn, session, mapping['item_id'], -1)
            for item_id, quantity in debit.items():
                self.adjust(conn, session, item_id, -quantity)
            for item_id, quantity in credit.items():
                self.adjust(conn, session, item_id, quantity)
            if kind == 'buy':
                self.adjust(conn, session, mapping['item_id'], 1)
            if kind == 'buy':
                conn.execute('UPDATE dnd_shop_skus SET sold=sold+1 WHERE chat_id=? AND thread_id=? AND code=?', product.key)
            trade = conn.execute('''INSERT INTO dnd_shop_trades(operation_token,kind,chat_id,thread_id,user_id,username,
                character_key,sku,item_id,price,value,quantity) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)''',
                (operation_token, kind, session['chat_id'], session['thread_id'], session['user_id'], session['username'],
                 session['character_key'], product.code, mapping['item_id'], str(product.price), str(value))).lastrowid
            for direction, amounts in (('debit', debit), ('change' if kind == 'buy' else 'payout', credit)):
                for item_id, quantity in amounts.items():
                    conn.execute('INSERT INTO dnd_shop_money VALUES(?,?,?,?,?)',
                                 (trade, item_id, direction, quantity, str(denominations[item_id])))
            names = {row['id']: row['name'] for row in currency_rows}
            result = {'kind': kind, 'name': product.name, 'quantity': 1, 'value': str(value),
                      'debit': [(names[i], n) for i, n in debit.items()],
                      'credit': [(names[i], n) for i, n in credit.items()], 'trade_id': trade, 'base_name': self.base_name(conn, session)}
            conn.execute('UPDATE dnd_shop_operations SET result=?,completed_at=CURRENT_TIMESTAMP WHERE token=?',
                         (json.dumps(result, ensure_ascii=False), operation_token))
            return result

    def validate_staged(self, path):
        """Check registry consistency without reserving SKUs or mutating DB."""
        products = read_catalog(path)
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            self.sync(conn, products)
        finally:
            conn.rollback()
            conn.close()
