import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
os.environ.setdefault('BOT_TOKEN', 'test-token')
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendMessage
from app.mini.ui.context import personal_callback, parse_personal_callback, parse_extended_callback
from app.mini.ui.transport import send_private_text_from_callback, delete_current_ephemeral

class SharedUIContractTests(unittest.IsolatedAsyncioTestCase):
    def callback(self):
        return SimpleNamespace(id='cb', data='mini:home:2:7', from_user=SimpleNamespace(id=7),
            bot=SimpleNamespace(send_message=AsyncMock()),
            message=SimpleNamespace(ephemeral_message_id=9, delete_ephemeral=AsyncMock()))

    async def test_callback_round_trip_and_extra_payload(self):
        cb=self.callback()
        cb.data=personal_callback('home', 2, 7)
        self.assertEqual(parse_personal_callback(cb), ('home', 2, 7))
        cb.data='mini:ev:2:7:turn.10.2.l'
        self.assertEqual(parse_extended_callback(cb, 'ev'), (2, 7, 'turn.10.2.l'))
        self.assertIsNone(parse_extended_callback(cb, 'hero'))
        cb.data='mini:home:bad:7'
        self.assertIsNone(parse_personal_callback(cb))

    async def test_send_finishes_before_delete(self):
        cb=self.callback(); order=[]
        async def send(**kwargs): order.append('send'); return 'sent'
        async def delete(): order.append('delete')
        cb.bot.send_message.side_effect=send; cb.message.delete_ephemeral.side_effect=delete
        self.assertEqual(await send_private_text_from_callback(cb, {'chat_id':1,'thread_id':0}, 'text', None), 'sent')
        self.assertEqual(order, ['send', 'delete'])
        params=cb.bot.send_message.call_args.kwargs['ephemeral_message_parameters']
        self.assertEqual((params.receiver_user_id,params.callback_query_id,params.replace_callback_query_message),(7,'cb',False))

    async def test_uncertain_send_does_not_delete_previous(self):
        cb=self.callback()
        cb.bot.send_message.side_effect=TelegramAPIError(method=SendMessage(chat_id=1,text='x'),message='timeout')
        with self.assertRaises(TelegramAPIError):
            await send_private_text_from_callback(cb, {'chat_id':1,'thread_id':0}, 'text', None)
        cb.message.delete_ephemeral.assert_not_awaited()

    async def test_public_message_is_never_deleted(self):
        cb=self.callback(); cb.message.ephemeral_message_id=None
        self.assertFalse(await delete_current_ephemeral(cb))
        cb.message.delete_ephemeral.assert_not_awaited()

    async def test_delete_failure_does_not_send_duplicate(self):
        cb=self.callback()
        cb.message.delete_ephemeral.side_effect=TelegramAPIError(method=SendMessage(chat_id=1,text='x'),message='timeout')
        await send_private_text_from_callback(cb, {'chat_id':1,'thread_id':0}, 'text', None)
        cb.bot.send_message.assert_awaited_once()
