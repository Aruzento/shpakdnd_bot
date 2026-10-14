"""Preserve the concurrently published e27153e draft's schema and callbacks."""
import asyncio
import json
import time
from functools import partial
from unittest.mock import patch,AsyncMock
from tests.v1_3.support import MiniCase
from tests.v1_4_2.test_completion_navigation import callback
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini.migrations import v1_4_2
from app.mini.gacha import perform_gacha_batch,get_gacha_batch
from app.mini.wallet import add_coins,get_balance
from app.mini import registration
from app.mini.ui import state,summons,transport
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from types import SimpleNamespace


class EarlierDraftCompatibilityTests(MiniCase):
    def batch(self):
        add_coins(self.pid,1000,'Fixture',db_path=self.db)
        with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            return perform_gacha_batch(self.pid,self.db,operation_key='batch:112233445566')

    def test_draft_schema_upgrade_keeps_saved_batch_names_and_share_claims(self):
        batch=self.batch();saved=self.sql('SELECT * FROM mini_gacha_batches')[0]
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DROP TABLE mini_gacha_batch_pulls');conn.execute('DROP TABLE mini_gacha_batches')
            conn.execute('DROP TABLE mini_registration_requests');conn.execute('DROP TABLE mini_ui_publications')
            v1_4_2.apply(conn)
            conn.execute('INSERT INTO mini_gacha_batches(id,player_id,operation_key,cost_coins,result_json,created_at) VALUES(?,?,?,?,?,?)',
                tuple(saved[k] for k in ('id','player_id','operation_key','cost_coins','result_json','created_at')))
            conn.execute('INSERT INTO mini_registration_sessions VALUES(?,?,?,?,1,0)',
                         ('draft-name',self.world['id'],900125,int(time.time())+300))
            conn.execute('INSERT INTO mini_public_shares VALUES(?,?)',(self.pid,'share:1:abcdef12'))
        before=self.sql('SELECT * FROM mini_gacha_pulls')
        for _ in range(3):init_mini_db(self.db)
        self.assertEqual(self.sql('SELECT * FROM mini_gacha_pulls'),before)
        self.assertEqual(self.sql('SELECT result_json FROM mini_gacha_batches')[0]['result_json'],saved['result_json'])
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_batch_pulls')),10)
        self.assertEqual(self.sql('SELECT pull_price FROM mini_gacha_batches')[0]['pull_price'],30)
        self.assertEqual(registration.pending(900125,self.db)['token'],'draft-name')
        self.assertFalse(state.claim_publication(self.pid,'share:1.abcdef12',self.db))
        replay=perform_gacha_batch(self.pid,self.db,operation_key='batch:112233445566')
        self.assertEqual(replay['batch_id'],batch['batch_id']);self.assertEqual(get_balance(self.pid,self.db),700)

    def test_earlier_batchview_callback_reads_same_result_without_payment(self):
        batch=self.batch();cb=callback(self.world,f"page.{batch['batch_id']}.2",action='batchview')
        with patch.object(summons,'load_extended_context',AsyncMock(return_value=(self.world,self.player,cb.data.split(':',4)[4]))),patch.object(summons,'get_gacha_batch',side_effect=partial(get_gacha_batch,db_path=self.db)),patch.object(summons,'send_private_text_from_callback',AsyncMock()) as send:
            asyncio.run(summons.batch_result_callback(cb));init_mini_db(self.db);asyncio.run(summons.batch_result_callback(cb))
        self.assertEqual(send.await_count,2);self.assertEqual(get_balance(self.pid,self.db),700)
        self.assertEqual(len(self.sql('SELECT * FROM mini_gacha_pulls')),10)

    def test_legacy_collection_origin_converts_to_owned_durable_context(self):
        raw,origin=state.split_context('1~r2~0000000');self.assertEqual(raw,'1')
        token=state.validate_navigation(self.pid,origin,self.db)
        self.assertNotIn('~',token)
        init_mini_db(self.db)
        self.assertEqual(state.restore(self.pid,token,self.db),dict(view='results',page=2,filter='0000000'))
        with self.assertRaises(ValueError):state.restore(self.pid+1,token,self.db)
        with self.assertRaises(ValueError):state.validate_navigation(self.pid,'r2~zzzzzzz',self.db)

    def test_private_photo_to_text_badrequest_falls_back_without_public_leak(self):
        cb=callback(self.world);cb.message.chat=SimpleNamespace(id=900123,type='private')
        cb.message.ephemeral_message_id=None
        cb.message.edit_text=AsyncMock(side_effect=TelegramBadRequest(method=EditMessageText(text='Menu'),message='there is no text in the message to edit'))
        asyncio.run(transport.edit_private(cb,'Menu',None))
        self.assertEqual(cb.bot.send_message.await_args.kwargs['chat_id'],900123)
        self.assertNotIn('ephemeral_message_parameters',cb.bot.send_message.await_args.kwargs)
        self.assertNotIn('message_thread_id',cb.bot.send_message.await_args.kwargs)
