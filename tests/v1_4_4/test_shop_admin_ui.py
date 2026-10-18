"""Exercise real topic-admin guard, command filters and worker dispatch."""
import threading
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.types import Message, User
from app import context
from app.handlers import shop as ui
from tests.v1_4_4 import test_ui as legacy


class AdminHandlerTests(unittest.IsolatedAsyncioTestCase):
    setUp = legacy.HandlerTests.setUp
    write = legacy.HandlerTests.write
    message = legacy.HandlerTests.message

    async def asyncSetUp(self):
        self.settings[-111][0]['admin'] = '@alice'
        p = patch.object(context, 'TOPIC_SETTINGS', self.settings)
        p.start()
        self.addCleanup(p.stop)

    def command(self, text, *, thread=0, user=None):
        return self.message(thread, user).model_copy(update={'text': text})

    async def test_create_flags_help_no_ids_or_both_modes_and_real_permission(self):
        result = dict(already=False, pricing_mode='b', base_name='Золото', registered=4447, buying=0)
        worker = []
        def create(who, mode):
            worker.append(threading.get_ident())
            self.assertEqual(who, self.actor)
            return dict(result, pricing_mode=mode)
        main_thread = threading.get_ident()
        with patch.object(self.shop, 'create', side_effect=create) as operation, patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            for text in ('/shopcreate', '/shopcreate -b -m', '/shopcreate -b -111:0', '/shopcreate -x'):
                await ui.shop_admin_handler(self.command(text))
            operation.assert_not_called()
            for mode in ('b', 'm'):
                await ui.shop_admin_handler(self.command('/shopcreate -' + mode))
            self.assertEqual(operation.call_count, 2)
            self.assertTrue(all(t != main_thread for t in worker))
            self.assertIn('Золото', answer.call_args.args[0])
            self.assertIn('4447', answer.call_args.args[0])

    async def test_normal_user_global_admin_and_mini_cannot_create_or_stop(self):
        unauthorized = User(id=900, is_bot=False, first_name='Global admin', username='arukozento')
        with patch.object(self.shop, 'create') as create, patch.object(self.shop, 'stop') as stop, patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            for command in ('/shopcreate -m', '/stopshop'):
                await ui.shop_admin_handler(self.command(command, user=unauthorized))
                await ui.shop_admin_handler(self.command(command, thread=2))
                await ui.shop_admin_handler(self.command(command, thread=999))
            create.assert_not_called()
            stop.assert_not_called()
            self.assertGreaterEqual(answer.await_count, 6)

    async def test_stop_idempotent_shop_closed_and_all_callbacks_blocked(self):
        session = self.shop.open(*self.actor)
        listing = self.shop.view(session, self.actor, 'buy')
        card = self.shop.view(session, self.actor, 'buy_card', listing['buttons'][0][1])
        with patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            await ui.shop_admin_handler(self.command('/stopshop'))
            self.assertEqual(answer.call_args.args[0], 'Магазин остановлен.')
            await ui.shop_admin_handler(self.command('/stopshop'))
            self.assertEqual(answer.call_args.args[0], 'Магазин уже остановлен.')
            await ui.shop_handler(self.message())
            self.assertIn('временно закрыт', answer.call_args.args[0])
            self.assertNotIn('reply_markup', answer.call_args.kwargs)
        # Authoritative service also blocks replay, navigation and exit.
        for action in ('home', 'buy', 'sell', 'credit', 'exit'):
            with self.assertRaises(ValueError):
                self.shop.view(session, self.actor, action)
        with self.assertRaises(ValueError):
            self.shop.execute(session, card['token'], self.actor)

    async def test_new_filters_are_exact_and_do_not_capture_mini(self):
        bot = Bot('123456:offline-shop-admin-filter')
        self.addAsyncCleanup(bot.session.close)
        registered = ui.router.message.handlers[-1]
        for text in ('/shopcreate -b', '/shopcreate -m', '/stopshop'):
            self.assertTrue((await registered.check(self.command(text), bot=bot))[0])
        for text in ('/mini', '/shop', '/inv', '/admcoin', '/stopshopx'):
            self.assertFalse((await registered.check(self.command(text), bot=bot))[0])
