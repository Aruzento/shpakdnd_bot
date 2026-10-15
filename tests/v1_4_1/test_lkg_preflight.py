"""Actual subprocess probes must use LKG SHA, including a different OLD commit."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.v1_4_1 import test_preflight as fixtures
from scripts.release_checks import CHECK_NAMES
import release_lkg as lkg
import release_state as store


class LkgPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PreflightFixtureTests(methodName='test_preflight_passes_on_exact_target_and_preserves_live_head_database_env')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.git('remote','add','origin','https://github.com/Aruzento/shpakdnd_bot.git')
        patch.object(store,'TRUSTED_UID',getattr(os,'geteuid',lambda:0)()).start()
        self.path=self.fixture.root/'registry'/'last-known-good.json'
        self.sha=self.fixture.old

    def confirm_fixture(self):
        record=dict(version=1,status='CONFIRMED',sha=self.sha,confirmed_at=store.utc_now(),deployment_id='fixture-confirmed',
            repository='github.com/Aruzento/shpakdnd_bot',tooling_hash=fixtures.pref.tooling_hash(self.fixture.tools),
            systemd_hash='f'*64,release_checks=dict.fromkeys(CHECK_NAMES,'OK'),health={'status':'PASS','operator_confirmed':True},
            sqlite={'integrity':'OK','foreign_keys':'OK'},previous=None)
        store.save_record(self.path,record)

    def test_exact_lkg_source_and_target_probes_preserve_live_db_and_head(self):
        self.confirm_fixture();before=self.fixture.db.read_bytes()
        report=self.fixture.operation(lkg_path=self.path).perform()
        self.assertEqual(report['lkg_compatibility'],{'sha':self.sha,'source':True,'target':True})
        self.assertEqual(self.fixture.db.read_bytes(),before)
        self.assertEqual(self.fixture.git('rev-parse','HEAD'),self.fixture.old)
        self.assertFalse(list(self.fixture.workspace.iterdir()))

    def test_lkg_differs_from_old_and_its_compatibility_is_independent(self):
        self.confirm_fixture()
        # New OLD accepts a marker the previous confirmed LKG rejects.
        self.fixture.write('check_bot.py',"import sqlite3\nfrom app.config import DB_PATH\ndef main():\n with sqlite3.connect(DB_PATH) as conn:assert conn.execute('SELECT coins FROM mini_players').fetchone()[0]==123\n")
        self.fixture.commit();self.fixture.old=self.fixture.git('rev-parse','HEAD')
        self.fixture.flag('incompatible')
        report=self.fixture.operation(lkg_path=self.path).perform()
        self.assertNotEqual(report['old_sha'],self.sha)
        self.assertIs(report['rollback_compatible'],True)
        self.assertEqual(report['lkg_compatibility'],{'sha':self.sha,'source':True,'target':False})
        self.assertEqual(report['lkg_binding']['sha'],self.sha)

    def test_absent_lkg_allows_release_checks_but_marks_rollback_unavailable(self):
        report=self.fixture.operation(lkg_path=self.path).perform()
        self.assertEqual(report['status'],'PASS')
        self.assertIsNone(report['lkg_binding']['sha'])
        self.assertEqual(report['lkg_compatibility']['source'],'unknown')
        self.assertEqual(report['lkg_compatibility']['target'],'unknown')

    def test_corrupt_lkg_fails_preflight_and_leaves_production_untouched(self):
        self.confirm_fixture();self.path.write_text('broken JSON')
        self.fixture.assert_failed(self.fixture.operation(lkg_path=self.path),message='Invalid state')

    def test_lost_lkg_commit_fails_before_authorizing_rollback(self):
        self.sha='d'*40;self.confirm_fixture()
        self.fixture.assert_failed(self.fixture.operation(lkg_path=self.path),message='Trusted command failed')
