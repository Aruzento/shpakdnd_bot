"""Real app imports/routers/catalogs and WAL snapshot, never live Telegram."""
import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from tests.v1_4_1.test_preflight import pref,ROOT
import semantic_smoke as smoke
import release_state as store
from deploy_helpers import DeployError


class ReadonlySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);cls.project=cls.root/'code';cls.project.mkdir()
        # The shared release runner intentionally archives code without .git.
        shutil.copytree(ROOT,cls.project,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git','.env','.venv','__pycache__','*.db','*.db-wal','*.db-shm'))
        for args in [('init','-q'),('config','user.name','SmokeFixture'),('config','user.email','smoke@example.invalid'),('config','core.autocrlf','false'),('config','commit.gpgsign','false'),('add','.'),('commit','-qm','Exact game source fixture')]:
            subprocess.run(['git','-C',str(cls.project),*args],check=True,capture_output=True)
        cls.sha=subprocess.check_output(['git','-C',str(cls.project),'rev-parse','HEAD'],text=True).strip()
        cls.db=cls.root/'source.db';cls.game_db=cls.project/'shpakdnd.db'
        env=pref.safe_environment(cls.root);env['BOT_TOKEN']='123456:fixture-smoke-token'
        result=subprocess.run([sys.executable,'check_bot.py'],cwd=cls.project,env=env,capture_output=True,text=True,encoding='utf-8')
        if result.returncode:raise RuntimeError('Disposable fixture setup failed: '+result.stderr)
        cls.game_db.replace(cls.db)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        subprocess.run(['git','-C',str(self.project),'checkout','--detach',self.sha],check=True,capture_output=True)
        self.output=self.root/'private'/'smoke.json'
        self.uid=patch.object(store,'TRUSTED_UID',getattr(os,'geteuid',lambda:0)());self.uid.start();self.addCleanup(self.uid.stop)
    def run_smoke(self,sha=None,db=None):return smoke.run(self.project,sha or self.sha,db or self.db,self.output)
    def change(self,path,text):
        (self.project/path).write_text(text,encoding='utf-8');subprocess.run(['git','-C',str(self.project),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.project),'commit','-qm','Fault injection only isolated fixture'],check=True,capture_output=True)
        return subprocess.check_output(['git','-C',str(self.project),'rev-parse','HEAD'],text=True).strip()
    def test_actual_routers_games_catalogs_and_readonly_database(self):
        before=self.db.read_bytes();value=self.run_smoke()
        self.assertEqual(value['status'],'PASS');self.assertFalse(value['real_telegram']);self.assertTrue(value['data_preserved'])
        self.assertEqual(set(value['checks']['routers']),smoke.ROUTERS);self.assertEqual(value['checks']['events_games'],['rps','lab'])
        self.assertEqual(self.db.read_bytes(),before);self.assertEqual(smoke.validate(self.output,self.sha),value)
    def test_repeated_smoke_never_changes_wallet_resources(self):
        before=self.db.read_bytes();self.run_smoke();self.run_smoke();self.assertEqual(before,self.db.read_bytes())
    def test_missing_events_router_is_fail(self):
        path='app/handlers/__init__.py';sha=self.change(path,(self.project/path).read_text().replace('    mini_events_router,',''))
        with self.assertRaises(DeployError):self.run_smoke(sha)
        self.assertEqual(store.read_record(self.output)['status'],'FAIL')
    def test_nonexistent_router_import_is_fail(self):
        path='app/handlers/__init__.py';sha=self.change(path,(self.project/path).read_text()+'\nfrom app.handlers.missing_fixture import router\n')
        with self.assertRaises(DeployError):self.run_smoke(sha)
    def test_invalid_real_catalog_is_fail(self):
        candidates=list((self.project/'app/mini/content').rglob('*.json'));self.assertTrue(candidates)
        sha=self.change(candidates[0].relative_to(self.project),'{invalid-json')
        with self.assertRaises(DeployError):self.run_smoke(sha)
    def test_other_checkout_sha_is_rejected(self):
        with self.assertRaises(DeployError):self.run_smoke('e'*40)
    def test_wal_committed_transaction_included_without_source_write(self):
        copy=self.root/'wal.db';copy.write_bytes(self.db.read_bytes())
        with closing(sqlite3.connect(copy)) as conn:
            conn.execute('PRAGMA journal_mode=WAL');conn.execute('CREATE TABLE smoke_wal_fixture(value INTEGER)');conn.execute('INSERT INTO smoke_wal_fixture VALUES(17)');conn.commit()
            before=copy.read_bytes();value=self.run_smoke(db=copy)
            self.assertEqual(before,copy.read_bytes());self.assertTrue(value['data_preserved'])
            self.assertEqual(conn.execute('SELECT value FROM smoke_wal_fixture').fetchall(),[(17,)])

if __name__=='__main__':unittest.main()
