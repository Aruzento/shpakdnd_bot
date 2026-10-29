"""Run the actual rollback path; only system commands and Git are disposable fakes."""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tests.v1_3_3 import test_deploy_shell as harness
from tests.v1_4_3 import test_pvp as fixtures
from app.mini.events import pvp

ROOT = Path(__file__).resolve().parents[2]


class RollbackShellTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(harness.BASH and Path(harness.BASH).exists(), "Bash required")
        self.h = harness.DeployShellTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.f = fixtures.PvpCase()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.h.env.update(FAKE_GUARD_REAL='1',
                          FAKE_REAL_PYTHON=harness.shell_path(sys.executable, preserve_symlink=True))
        self.db = self.h.project/'shpakdnd.db'

    def install_db(self):
        shutil.copy2(self.f.db, self.db)

    def paused(self):
        pvp.maintenance(True, self.f.db, now=1000)
        self.install_db()

    def run_rollback(self, **flags):
        return self.h.run_deploy('y\ny\n2\n', FAKE_CHECK_FAIL=1, **flags)

    def assert_blocked_before_checkout(self, trace):
        self.assertNotIn(' switch --no-overwrite-ignore -C main '+harness.OLD, trace)
        if 'pvp_ops.py' in trace:
            self.assertNotIn('systemctl start', trace[trace.index('pvp_ops.py'):])
        self.assertEqual((self.h.state/'head').read_text().strip(), harness.TARGET)
        self.h.assert_stopped()

    def inject(self, body):
        path=self.h.root/'inject.py'
        path.write_text('import sqlite3,sys\nfrom pathlib import Path\n'+body, encoding='utf-8')
        return harness.shell_path(path)

    def test_active_escrow_blocks_real_shell_without_restoring_database(self):
        pvp.create(self.f.pid, self.f.wid, 'rollback-active', self.f.db, now=1000)
        self.install_db()
        before=self.db.read_bytes()
        result,trace=self.run_rollback()
        self.assertNotEqual(result.returncode,0, result.stdout+result.stderr)
        self.assert_blocked_before_checkout(trace)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(trace.count(' backup '),1)
        self.assertIn('Rollback заблокирован', result.stdout+result.stderr)

    def test_empty_new_schema_requires_explicit_pause(self):
        self.install_db()
        result,trace=self.run_rollback()
        self.assertNotEqual(result.returncode,0)
        self.assert_blocked_before_checkout(trace)
        self.assertIn('pause_new_required', result.stdout+result.stderr)

    def test_safe_paused_database_checked_before_checkout_and_before_start(self):
        self.paused()
        result,trace=self.run_rollback()
        self.assertEqual(result.returncode,1, result.stdout+result.stderr)
        self.assertEqual((self.h.state/'head').read_text().strip(), harness.OLD)
        lines=trace.splitlines()
        guards=[i for i,line in enumerate(lines) if 'pvp_ops.py' in line]
        self.assertEqual(len(guards),3, trace)
        checkout=next(i for i,line in enumerate(lines) if ' switch --no-overwrite-ignore -C main '+harness.OLD in line)
        startup=next(i for i,line in enumerate(lines) if 'systemctl start shpakdnd-bot.service' in line)
        self.assertLess(guards[0],guards[1]); self.assertLess(guards[1],checkout)
        self.assertLess(checkout,guards[2]); self.assertLess(guards[2],startup)

    def test_changed_database_rechecked_immediately_before_checkout(self):
        self.paused()
        script=self.inject("c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE mini_event_pvp_control SET paused=0'); c.commit(); c.close()\n")
        result,trace=self.run_rollback(FAKE_GUARD_INJECT=script,FAKE_GUARD_INJECT_AT=2)
        self.assertNotEqual(result.returncode,0)
        self.assert_blocked_before_checkout(trace)
        self.assertEqual((self.h.state/'guards').read_text().strip(),'2')

    def test_old_code_checks_cannot_invalidate_pre_start_gate(self):
        self.paused()
        script=self.inject("c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE mini_event_pvp_control SET paused=0'); c.commit(); c.close()\n")
        result,trace=self.run_rollback(FAKE_GUARD_INJECT=script,FAKE_GUARD_INJECT_AT=3)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.h.state/'head').read_text().strip(),harness.OLD)
        self.assertNotIn('systemctl start',trace)
        self.h.assert_stopped()

    def test_corrupt_database_fails_closed_without_restore(self):
        self.db.write_bytes(b'corrupt sqlite fixture')
        before=self.db.read_bytes()
        result,trace=self.run_rollback()
        self.assertNotEqual(result.returncode,0)
        self.assert_blocked_before_checkout(trace)
        self.assertEqual(before,self.db.read_bytes())

    def test_missing_database_is_not_created(self):
        self.paused()
        script=self.inject("p=Path(sys.argv[1]); p.rename(p.with_suffix('.saved'))\n")
        result,trace=self.run_rollback(FAKE_GUARD_INJECT=script,FAKE_GUARD_INJECT_AT=1)
        self.assertNotEqual(result.returncode,0)
        self.assert_blocked_before_checkout(trace)
        self.assertFalse(self.db.exists())

    def test_missing_frozen_helper_blocks_checkout(self):
        self.paused()
        result,trace=self.run_rollback(FAKE_REMOVE_FROZEN_GUARD=1)
        self.assertNotEqual(result.returncode,0)
        self.assert_blocked_before_checkout(trace)
        self.assertIn('helper_missing',result.stdout+result.stderr)

    def test_frozen_helper_survives_checkout_removing_new_source(self):
        self.paused()
        deploy=self.h.project/'deploy'; deploy.mkdir()
        for name in ('deploy-shpakdnd.sh','deploy_helpers.py','pvp_ops.py','sqlite-deploy.py','telegram-deploy-notice.py','release_preflight.py','release_content.py','release_guard.py','release_schema.json'):
            shutil.copy2(ROOT/'deploy'/name,deploy/name)
        self.h.script=deploy/'deploy-shpakdnd.sh'
        (self.h.state/'old').write_text(harness.OLD)
        result,trace=self.run_rollback(FAKE_REMOVE_SOURCE_GUARD=1)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertFalse((deploy/'pvp_ops.py').exists())
        self.assertTrue((self.h.state/'removed-source-guard.py').exists())
        self.assertEqual((self.h.state/'guards').read_text().strip(),'3')
        self.assertIn('systemctl start shpakdnd-bot.service',trace)

    def test_failed_start_uses_same_guard(self):
        self.install_db()
        result,trace=self.h.run_deploy('y\ny\n2\n',FAKE_START_FAIL=1)
        self.assertNotEqual(result.returncode,0)
        self.assert_blocked_before_checkout(trace)

    def test_installer_bundles_independent_helper(self):
        self.assertIn('pvp_ops.py',(ROOT/'deploy/install-deploy-shpakdnd.sh').read_text())
        self.paused()
        helper=self.h.root/'standalone.py'; shutil.copy2(ROOT/'deploy/pvp_ops.py',helper)
        env=dict(os.environ);env.pop('BOT_TOKEN',None);env.pop('PYTHONPATH',None)
        result=subprocess.run([sys.executable,str(helper),'--db',str(self.db),'rollback-check'],
                              cwd=self.h.root,env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
