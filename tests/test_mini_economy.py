import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('BOT_TOKEN','test-token')
from app.mini.db import connect_mini_db
from app.mini.schema import init_mini_db
from app.mini.boss.schema import init_boss_db
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.players import create_mini_player
from app.mini.heroes import sync_hero_catalog
from app.mini.shop import sync_shop_catalog, purchase_offer
from app.mini.gacha import perform_gacha_pull
from app.mini.daily import claim_daily
from app.mini.items import use_inventory_item
from app.mini.wallet import add_coins, change_balance_in_transaction, get_balance, get_wallet_history

class EconomyTransactionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/'mini.db'; init_mini_db(self.db); init_boss_db(self.db)
        self.world=sync_configured_mini_worlds(self.db)[0]['id']
        self.player=create_mini_player(self.world,111,'@tester','Игрок',self.db)['id']
        sync_shop_catalog(self.world,self.db); sync_hero_catalog(self.db)
        add_coins(self.player,1000,'Start',db_path=self.db)

    def snapshot(self):
        with connect_mini_db(self.db) as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mini_%' ORDER BY name")]
            return {t:conn.execute(f'SELECT * FROM {t} ORDER BY rowid').fetchall() for t in tables}

    def test_wallet_uses_caller_transaction_and_operation_key(self):
        with connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE')
            first=change_balance_in_transaction(conn,self.player,-10,'Pay','test',9,'one')
            repeated=change_balance_in_transaction(conn,self.player,-10,'Pay','test',9,'one')
            self.assertTrue(conn.in_transaction)
            self.assertTrue(first['applied']); self.assertFalse(repeated['applied'])
            self.assertEqual(first['transaction_id'],repeated['transaction_id'])
        self.assertEqual(get_balance(self.player,self.db),990)
        self.assertEqual(get_wallet_history(self.player,db_path=self.db)[0]['operation_key'],'one')

    def test_wallet_and_related_write_roll_back_together(self):
        before=self.snapshot()
        with self.assertRaises(RuntimeError), connect_mini_db(self.db) as conn:
            conn.execute('BEGIN IMMEDIATE')
            change_balance_in_transaction(conn,self.player,10,'Reward',operation_key='rollback')
            conn.execute('UPDATE mini_players SET shards=50 WHERE id=?',(self.player,))
            raise RuntimeError('fail')
        self.assertEqual(self.snapshot(),before)

    def test_daily_wallet_failure_rolls_back_claim(self):
        before=self.snapshot()
        with patch('app.mini.daily.change_balance_in_transaction',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): claim_daily(self.player,'Игрок',db_path=self.db)
        self.assertEqual(self.snapshot(),before)

    def test_gacha_wallet_failure_rolls_back_hero_and_history(self):
        before=self.snapshot()
        with patch('app.mini.gacha.change_balance_in_transaction',side_effect=RuntimeError('fail')), patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
            with self.assertRaises(RuntimeError): perform_gacha_pull(self.player,db_path=self.db)
        self.assertEqual(self.snapshot(),before)

    def test_shop_wallet_failure_rolls_back_delivery_and_purchase(self):
        with connect_mini_db(self.db) as conn:
            offer=conn.execute('SELECT id FROM mini_shop_offers WHERE price>0 AND item_id IS NOT NULL LIMIT 1').fetchone()[0]
        before=self.snapshot()
        with patch('app.mini.shop.change_balance_in_transaction',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): purchase_offer(self.player,self.world,offer,db_path=self.db)
        self.assertEqual(self.snapshot(),before)

    def test_item_wallet_failure_rolls_back_consumption(self):
        with connect_mini_db(self.db) as conn:
            item=conn.execute("SELECT id FROM mini_items WHERE effect_key='boss_coin_pouch'").fetchone()[0]
            conn.execute('INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(?,?,1)',(self.player,item))
        before=self.snapshot()
        with patch('app.mini.items.change_balance_in_transaction',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): use_inventory_item(self.player,item,operation_key='item-1',db_path=self.db)
        self.assertEqual(self.snapshot(),before)

    def test_transaction_api_rejects_unstarted_and_autocommit_connections(self):
        import sqlite3
        before=self.snapshot()
        for isolation in (None, "DEFERRED"):
            conn=sqlite3.connect(self.db,isolation_level=isolation)
            try:
                with self.assertRaises(ValueError):
                    change_balance_in_transaction(conn,self.player,5,"Unowned transaction")
            finally:
                conn.close()
        self.assertEqual(self.snapshot(),before)
