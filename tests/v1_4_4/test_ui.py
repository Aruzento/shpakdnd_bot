import csv
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from aiogram.types import CallbackQuery, Message, User
from app.db import inventory, schema
from app.db.dnd_items import create_currency
from app.dnd_shop.catalog import COLUMNS
from app.dnd_shop.service import Shop
from app.handlers import shop as ui


class ScreenTests(unittest.TestCase):
    def test_main_exactly_three_vertical_actions(self):
        text, keyboard = ui.render('s', {'screen': 'home'})
        self.assertEqual(text, 'Торговец: «Чего желаешь, путник?»')
        self.assertEqual([[b.text for b in row] for row in keyboard.inline_keyboard],
                         [['🛒 Купить'], ['💰 Продать'], ['🚪 Выйти']])

    def test_grid_pagination_back_exit_and_callback_size(self):
        for screen in ('buy', 'sell'):
            _, keyboard = ui.render('x' * 16, {'screen': screen, 'page': 1, 'pages': 3,
                                              'buttons': [(str(i), 'y' * 16) for i in range(4)]})
            self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 2, 2, 1])
            self.assertEqual(keyboard.inline_keyboard[-1][0].text, 'Назад')
            for row in keyboard.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode()), 64)
        self.assertEqual(ui.render('s', {'screen': 'exit'}), ('Магазин закрыт.', None))


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'test.db'
        self.csv = Path(self.tmp.name) / 'shop.csv'
        self.settings = {-111: {0: {'characters': {'@alice': 'Алиса'}}, 2: {'mini': True}}}
        for module in (schema, inventory):
            p = patch.object(module, 'DB_PATH', self.db)
            p.start()
            self.addCleanup(p.stop)
        p = patch('app.db.dnd_items.TOPIC_SETTINGS', self.settings)
        p.start()
        self.addCleanup(p.stop)
        schema.init_db()
        create_currency(-111, 0, 'Золото', 1, '1', is_base=True, db_path=self.db)
        create_currency(-111, 0, 'Медь', 1, '0.01', db_path=self.db)
        inventory.add_inventory_item(-111, 0, '@alice', 'Золото', 10)
        self.write()
        self.shop = Shop(self.db, self.csv, self.settings)
        p = patch.object(ui, 'Shop', return_value=self.shop)
        p.start()
        self.addCleanup(p.stop)
        self.user = User(id=101, is_bot=False, first_name='Алиса', username='alice')
        self.actor = (101, 'alice', -111, 0)

    def write(self, price='1'):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for i in range(6):
            writer.writerow(dict(chat_id=-111, thread_id=0, code=f'item_{i}', name=f'Предмет {i}',
                                 description='Описание', price=price, stock=5, active='true'))
        self.csv.write_text(stream.getvalue(), encoding='utf-8')

    def message(self, thread=0, user=None):
        return Message(message_id=50, date=datetime.now(timezone.utc), chat={'id': -111, 'type': 'group'},
                       from_user=user or self.user, message_thread_id=thread or None, text='/shop')

    def query(self, data, user=None):
        return CallbackQuery(id='q', from_user=user or self.user, chat_instance='test',
                             message=self.message(), data=data)

    async def test_command_normal_mini_no_character_and_username(self):
        for user, thread, allowed in [(self.user, 0, True), (self.user, 2, False),
                (User(id=102, is_bot=False, first_name='Боб', username='bob'), 0, False),
                (User(id=103, is_bot=False, first_name='Нет username'), 0, False)]:
            with self.subTest(allowed=allowed), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await ui.shop_handler(self.message(thread, user))
                self.assertEqual('reply_markup' in answer.call_args.kwargs, allowed)

    async def test_separate_catalogs_pages_and_previous_screen(self):
        session = self.shop.open(*self.actor)
        with patch.object(Message, 'edit_text', new_callable=AsyncMock) as edit, patch.object(CallbackQuery, 'answer', new_callable=AsyncMock):
            await ui.shop_callback(self.query(ui.callback(session, 'b')))
            keyboard = edit.call_args.kwargs['reply_markup']
            self.assertEqual([len(row) for row in keyboard.inline_keyboard], [2, 2, 2, 1])
            await ui.shop_callback(self.query(keyboard.inline_keyboard[2][1].callback_data))
            self.assertIn('2/2', edit.call_args.args[0])
            await ui.shop_callback(self.query(ui.callback(session, 's')))
            self.assertIn('Ваши предметы', edit.call_args.args[0])
            self.assertIn('пусто', edit.call_args.args[0])
            await ui.shop_callback(self.query(ui.callback(session, 'h')))
            self.assertEqual(len(edit.call_args.kwargs['reply_markup'].inline_keyboard), 3)
            await ui.shop_callback(self.query(ui.callback(session, 'e')))
            self.assertIsNone(edit.call_args.kwargs['reply_markup'])

    async def test_foreign_and_stale_callbacks_and_invalid_data(self):
        session = self.shop.open(*self.actor)
        user = User(id=102, is_bot=False, first_name='Боб', username='alice')
        for data, who in [(ui.callback(session, 'b'), user), ('ds:invalid', self.user),
                          (ui.callback('missing', 'b'), self.user), (ui.callback(session, 'b', 'NaN'), self.user)]:
            with patch.object(Message, 'edit_text', new_callable=AsyncMock) as edit, patch.object(CallbackQuery, 'answer', new_callable=AsyncMock) as answer:
                await ui.shop_callback(self.query(data, who))
                edit.assert_not_called()
                self.assertTrue(answer.call_args.kwargs.get('show_alert'))

    async def test_price_refresh_and_telegram_error_after_commit_is_safe(self):
        session = self.shop.open(*self.actor)
        listing = self.shop.view(session, self.actor, 'buy')
        card = self.shop.view(session, self.actor, 'buy_card', listing['buttons'][0][1])
        self.write('2')
        with patch.object(Message, 'edit_text', new_callable=AsyncMock) as edit, patch.object(CallbackQuery, 'answer', new_callable=AsyncMock):
            await ui.shop_callback(self.query(ui.callback(session, 'x', card['token'])))
            self.assertIn('Цена изменилась', edit.call_args.args[0])
            button = edit.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
        with patch.object(Message, 'edit_text', new_callable=AsyncMock, side_effect=RuntimeError('Telegram unavailable')), patch.object(CallbackQuery, 'answer', new_callable=AsyncMock):
            with self.assertRaises(RuntimeError):
                await ui.shop_callback(self.query(button.callback_data))
        with patch.object(Message, 'edit_text', new_callable=AsyncMock) as edit, patch.object(CallbackQuery, 'answer', new_callable=AsyncMock):
            await ui.shop_callback(self.query(button.callback_data))
            self.assertIn('Хороший выбор', edit.call_args.args[0])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM dnd_shop_trades').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT quantity FROM inventory WHERE name='Золото'").fetchone()[0], 8)
