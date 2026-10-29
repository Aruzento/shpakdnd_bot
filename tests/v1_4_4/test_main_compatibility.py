"""Compatibility checks after merging the V1.4.3.1 deployment hotfix."""
import csv
import io
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, User
from app import context
from app.db import character_registry, characters as character_db, dnd_items, inventory, schema
from app.dnd_shop.catalog import COLUMNS
from app.dnd_shop.service import Shop
from app.handlers import ROUTERS, admin, characters, create
from app.handlers import inventory as inventory_ui, shop as shop_ui
from app.mini.players import create_mini_player
from app.mini.schema import init_mini_db
from app.mini.shop import get_offers_by_category, purchase_offer, sync_shop_catalog
from app.mini.ui import shop as mini_shop_ui
from app.mini.wallet import add_coins
from app.mini.worlds import sync_configured_mini_worlds
from tests.topic_fixtures import isolated_topics

MAIN = 'd9856b2038a36490f93a4f5613ba7887698ac7e8'
ROOT = Path(__file__).resolve().parents[2]


class MainCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / 'isolated.db'
        self.csv = Path(tmp.name) / 'shop.csv'
        self.settings = {-111: {0: {'admin': '@testadmin', 'characters': {}},
                                2: {'admin': '@testadmin', 'mini': True, 'characters': {}}}}
        self.enterContext(isolated_topics(self.settings))
        self.enterContext(patch.object(dnd_items, 'TOPIC_SETTINGS', self.settings))
        self.enterContext(patch.object(context, 'GLOBAL_ADMIN_USERNAME', '@testadmin'))
        for module in (schema, inventory, character_registry, character_db, context):
            self.enterContext(patch.object(module, 'DB_PATH', self.db))
        # Preserve real currency command logic, replacing only the default DB dependency.
        real_create_currency = dnd_items.create_currency
        self.enterContext(patch.object(dnd_items, 'create_currency', side_effect=
            lambda *args, **kwargs: real_create_currency(*args, **dict(kwargs, db_path=self.db))))
        # Legacy public APIs use sqlite Connection context managers, which commit
        # but do not close. The test owns every connection it opens so Windows
        # can remove the disposable DB deterministically; production API is unchanged.
        self.connections = []
        real_connect = sqlite3.connect
        def tracked_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            self.connections.append(conn)
            return conn
        self.enterContext(patch.object(sqlite3, 'connect', side_effect=tracked_connect))
        self.addCleanup(lambda: [conn.close() for conn in self.connections])
        schema.init_db()
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(dict(chat_id=-111, thread_id=0, code='sword', name='Меч',
                             description='Обычный меч', price=100, stock=5, active='true'))
        self.csv.write_text(stream.getvalue(), encoding='utf-8')
        self.shop = Shop(self.db, self.csv, self.settings)
        self.user = User(id=101, username='alice', first_name='Alice', is_bot=False)
        self.admin = User(id=102, username='testadmin', first_name='Admin', is_bot=False)

    def message(self, text, *, administrator=False, thread=0):
        return Message(message_id=1, date=datetime.now(timezone.utc),
                       chat={'id': -111, 'type': 'group'}, message_thread_id=thread or None,
                       from_user=self.admin if administrator else self.user, text=text)

    async def command(self, handler, text, *, administrator=False):
        with patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            await handler(self.message(text, administrator=administrator))
        return '\n'.join(call.args[0] for call in answer.call_args_list)

    def snapshot_mini(self):
        with closing(sqlite3.connect(self.db)) as conn:
            names = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mini_%'")]
            return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in names}

    async def test_real_dnd_commands_and_trades_preserve_populated_mini_economy(self):
        init_mini_db(self.db)
        world_id = sync_configured_mini_worlds(self.db)[0]['id']
        player = create_mini_player(world_id, 101, 'alice', 'Mini Alice', self.db)
        sync_shop_catalog(world_id, self.db)
        add_coins(player['id'], 500, 'Disposable fixture', operation_key='seed', db_path=self.db)
        offer = get_offers_by_category(world_id, 'dnd', self.db)[0]
        purchase_offer(player['id'], world_id, offer['id'], self.db)
        before = self.snapshot_mini()
        self.assertTrue(before['mini_wallet_transactions'])
        self.assertTrue(before['mini_inventory'])

        reply = await self.command(create.create_handler, '/create @alice "Алиса"', administrator=True)
        self.assertIn('Персонаж создан', reply)
        for text in ('/admcoin -111:0 "Золото" 1', '/admscoin -111:0 "Медь" 1 = 0.01'):
            reply = await self.command(admin.currency_handler, text, administrator=True)
            self.assertIn('создана', reply)
        for text in ('/admadd -111:0 @alice Золото x101', '/admadd -111:0 @alice Меч x2'):
            reply = await self.command(admin.admadd_handler, text, administrator=True)
            self.assertIn('получил', reply)
        await self.command(admin.admdel_handler, '/admdel -111:0 @alice Меч x1', administrator=True)
        self.assertIn('Алиса', await self.command(characters.char_handler, '/char @alice'))
        self.assertIn('Меч', await self.command(inventory_ui.inventory_handler, '/inv @alice'))

        actor = (101, 'alice', -111, 0)
        session = self.shop.open(*actor)
        listing = self.shop.view(session, actor, 'buy')
        card = self.shop.view(session, actor, 'buy_card', listing['buttons'][0][1])
        self.shop.execute(session, card['token'], actor)
        self.shop.execute(session, card['token'], actor)  # saved receipt; no second purchase
        listing = self.shop.view(session, actor, 'sell')
        self.assertEqual([name for name, _ in listing['buttons']], ['Меч'])
        card = self.shop.view(session, actor, 'sell_card', listing['buttons'][0][1])
        self.assertEqual(card['payout'], 3)
        self.shop.execute(session, card['token'], actor)
        self.shop.execute(session, card['token'], actor)
        self.assertEqual(dict((name, qty) for name, qty, _ in inventory.get_inventory(-111, 0, '@alice')),
                         {'Золото': 4, 'Меч': 1})
        self.assertIn('Меч', await self.command(inventory_ui.inventory_handler, '/inv @alice'))
        self.assertEqual(self.snapshot_mini(), before)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    async def test_registered_shop_filters_do_not_intercept_mini_or_other_commands(self):
        self.assertEqual(ROUTERS.count(shop_ui.router), 1)
        bot = Bot('123456:offline-filter-test')
        self.addAsyncCleanup(bot.session.close)
        handler = shop_ui.router.message.handlers[0]
        self.assertTrue((await handler.check(self.message('/shop'), bot=bot))[0])
        for text in ('/mini', '/inv @alice', '/admcoin -111:0 Золото 1'):
            self.assertFalse((await handler.check(self.message(text), bot=bot))[0])
        for data in ('mini:shop:1:101', 'mini:launch:1', 'mini:inventory:1:101'):
            query = CallbackQuery(id='q', from_user=self.user, chat_instance='test',
                                  message=self.message('/shop'), data=data)
            for registered in shop_ui.router.callback_query.handlers:
                self.assertFalse((await registered.check(query))[0])
        query = CallbackQuery(id='q', from_user=self.user, chat_instance='test',
                              message=self.message('/shop'), data='ds:session:b:0:0')
        for registered in mini_shop_ui.router.callback_query.handlers:
            self.assertFalse((await registered.check(query))[0])

    async def test_watcher_preserves_all_main_paths_and_excludes_external_catalog(self):
        baseline = subprocess.check_output(['git', 'show', MAIN + ':deploy/shpakdnd-bot-watch.path'],
                                           cwd=ROOT).decode('utf-8')
        current = (ROOT / 'deploy/shpakdnd-bot-watch.path').read_text(encoding='utf-8')
        def watched(text):
            return {line for line in text.splitlines() if line.startswith('PathModified=')}
        self.assertEqual(watched(current) - watched(baseline),
                         {'PathModified=/opt/shpakdnd-bot/app/dnd_shop',
                          'PathModified=/opt/shpakdnd-bot/app/stories',
                          'PathModified=/opt/shpakdnd-bot/app/mini/mythic/raid'})
        self.assertLessEqual(watched(baseline), watched(current))
        self.assertFalse(any('shop.csv' in line or '/var/lib/shpakdnd' in line for line in watched(current)))
