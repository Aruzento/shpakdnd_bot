"""Read-only operator preflight uses real registry IDs/codes and exact nominals."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from unittest.mock import patch

from app.db.dnd_items import create_currency, parse_currency_command
from app.dnd_shop.pricing import MASTER_MONEY
from tools.inspect_dnd_shop_currencies import inspect, VALUES
from tests.v1_4_4 import test_source_money as source


class CurrencyInspectionTests(unittest.TestCase):
    for _name in ('sql', 'currency', 'write', 'write_rows', 'add', 'open',
                  'amount', 'buy_offer', 'sale_offer', 'new_shop', 'configure',
                  'thread_two', 'allow', 'balance', 'assert_clean', 'all_units'):
        locals()[_name] = getattr(source.SourceMoneyTests, _name)
    del _name

    def setUp(self):
        source.SourceMoneyTests.setUp(self)
        for item_id, name in ((self.copper, 'Медная монета'), (self.silver, 'Серебряная монета'),
                              (self.gold, 'Золотая монета')):
            self.sql('UPDATE dnd_items SET name=?,name_key=? WHERE id=?', (name, name.casefold(), item_id))

    def report(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('PRAGMA query_only=ON')
            return inspect(conn, (-111, 0))

    def test_missing_twelve_commands_use_existing_admin_contract_without_grants(self):
        result = self.report()
        self.assertFalse(result['errors'])
        self.assertEqual(len(result['missing']), 12)
        before = self.sql('SELECT * FROM inventory ORDER BY id')
        for command in result['registration_commands']:
            target, name, quantity, value = parse_currency_command(command, secondary=True)
            self.assertEqual(target, '-111:0')
            create_currency(-111, 0, name, quantity, value, db_path=self.db)
        self.assertEqual(self.sql('SELECT * FROM inventory ORDER BY id'), before)
        result = self.report()
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['currencies']), 15)
        for row in result['currencies']:
            self.assertEqual(row['denomination'], row['expected'])
            registered = self.sql('SELECT code FROM dnd_items WHERE id=?', (row['item_id'],))[0][0]
            self.assertEqual(result['candidate_shop_currency_units']['master'][row['name']], registered)

    def test_names_do_not_prove_nominals(self):
        self.all_units()
        self.sql("UPDATE dnd_currencies SET base_value='0.1' WHERE item_id=?", (self.silver,))
        result = self.report()
        self.assertFalse(result['complete'])
        self.assertTrue(any('Wrong denomination: Серебряная' in error for error in result['errors']))
        self.assertEqual(result['registration_commands'], [])

    def test_ordinary_item_collision_blocks_registration_plan(self):
        self.sql("INSERT INTO dnd_items(chat_id,thread_id,code,name,name_key) VALUES(-111,0,'collision','10 медных монет','10 медных монет')")
        result = self.report()
        self.assertTrue(any('Not an active registered currency' in error for error in result['errors']))
        self.assertEqual(result['registration_commands'], [])

    def test_read_only_cli_preserves_source_hash_and_requires_all_fifteen(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        command = [sys.executable, str(source.ROOT / 'tools/inspect_dnd_shop_currencies.py'),
                   '--source', str(self.db), '--chat-id=-111', '--thread-id=0']
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1, result.stderr)
        result = subprocess.run(command + ['--allow-missing'], env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['source_sha256'], before)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
