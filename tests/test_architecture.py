import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path
os.environ.setdefault('BOT_TOKEN','test-token')
ROOT=Path(__file__).resolve().parents[1]

class ArchitectureBoundaryTests(unittest.TestCase):
    def test_feature_handlers_never_import_private_handler_helpers(self):
        for path in (ROOT/'app').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if isinstance(node,ast.ImportFrom) and (node.module or '').endswith('.handlers'):
                    self.assertFalse(any(a.name.startswith('_') for a in node.names),str(path))

    def test_shared_combat_import_does_not_load_boss_or_database_or_telegram(self):
        code='''import sys
from app.mini.combat.matchups import faction_multiplier_percent
from app.mini.combat.hero_abilities.engine import resolve_attack
assert faction_multiplier_percent("commoners","beasts")==200
assert resolve_attack("none",base_damage=10,hit_number=1,boss_hp_before=100,boss_max_hp=100)["damage"]==10
assert not any(n.startswith("app.mini.boss") for n in sys.modules)
assert "app.config" not in sys.modules
assert "aiogram" not in sys.modules
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_only_wallet_writes_coin_balance_and_ledger(self):
        import re
        for path in (ROOT/'app/mini').rglob('*.py'):
            if path.name=='wallet.py': continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if isinstance(node,ast.Constant) and isinstance(node.value,str):
                    sql=' '.join(node.value.upper().split())
                    self.assertIsNone(re.search(r'INSERT(?: OR IGNORE)? INTO MINI_WALLET_TRANSACTIONS',sql),str(path))
                    self.assertIsNone(re.search(r'UPDATE MINI_PLAYERS SET[^;]*\bCOINS\s*=',sql),str(path))
