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

    def test_combat_use_cases_do_not_write_entities_or_journals_with_raw_sql(self):
        tree=ast.parse((ROOT/'app/mini/boss/combat.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                self.assertFalse(node.value.strip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')), node.value)

    def test_deploy_watcher_covers_all_app_code_and_catalog_directories(self):
        unit=(ROOT/'deploy/shpakdnd-bot-watch.path').read_text(encoding='utf-8')
        watched={line.split('=',1)[1].removeprefix('/opt/shpakdnd-bot/')
                 for line in unit.splitlines() if line.startswith('PathModified=')}
        for path in (ROOT/'app').rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.json'}:
                self.assertIn(path.parent.relative_to(ROOT).as_posix(),watched,str(path))
        self.assertNotIn('shpakdnd.db',watched)
        self.assertNotIn('.env',watched)
