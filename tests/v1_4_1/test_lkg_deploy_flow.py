"""Real deployment Bash, isolated paths and fake Git/systemd/Telegram."""
import unittest
from tests.v1_3_3 import test_deploy_shell as shell
LKG='c'*40


class LkgDeploymentFlowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=shell.DeployShellTests(methodName='test_success_orders_fetch_notice_stop_backup_checkout_checks_start')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def run_flow(self,answers,**flags):return self.fixture.run_deploy(answers,**flags)
    def head(self):return (self.fixture.state/'head').read_text().strip()
    def lkg(self):return (self.fixture.state/'lkg').read_text().strip()

    def test_source_checkout_failure_returns_actual_lkg_with_consent(self):
        result,trace=self.run_flow('y\ny\n2\n',FAKE_CHECKOUT_FAIL=1,FAKE_LKG_DIFFERENT=1)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr);self.assertEqual(self.head(),LKG)
        self.assertIn('--startup-sha '+LKG,trace);self.assertNotIn('MIGRATION_STARTED',trace)
        self.assertEqual(self.lkg(),shell.OLD);self.assertNotIn(' restore ',trace)

    def test_target_recovery_uses_actual_lkg_not_old_compatibility(self):
        result,trace=self.run_flow('y\ny\n2\n',FAKE_TARGET_POSTCHECK_FAIL=1,FAKE_LKG_DIFFERENT=1)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr);self.assertEqual(self.head(),LKG)
        self.assertIn('DB_STAGE TARGET',trace);self.assertIn('--startup-sha '+LKG,trace)
        self.assertEqual(self.lkg(),shell.OLD)
        stopped=trace[trace.index('systemctl stop'):]
        self.assertNotIn('-m unittest',stopped);self.assertNotIn('compileall',stopped)

    def test_missing_corrupt_or_lost_lkg_blocks_return_to_old(self):
        for flag in ('FAKE_LKG_ABSENT','FAKE_LKG_CORRUPT','FAKE_LKG_COMMIT_MISSING'):
            with self.subTest(flag=flag):
                result,trace=self.run_flow('y\ny\n2\n',FAKE_CHECKOUT_FAIL=1,**{flag:1})
                self.assertNotEqual(result.returncode,0);self.fixture.assert_stopped()
                self.assertNotIn('systemctl start',trace)

    def test_source_compatibility_is_not_target_compatibility(self):
        result,trace=self.run_flow('y\ny\n2\n',FAKE_CHECKOUT_FAIL=1,FAKE_LKG_TARGET_INCOMPATIBLE=1)
        self.assertEqual(result.returncode,1);self.assertIn('Предыдущая версия восстановлена',result.stdout)
        self.assertNotIn('MIGRATION_STARTED',trace)

    def test_unknown_or_incompatible_source_never_returns_lkg(self):
        for flag in ('FAKE_ROLLBACK_UNKNOWN','FAKE_LKG_SOURCE_INCOMPATIBLE'):
            result,trace=self.run_flow('y\ny\n2\n',FAKE_CHECKOUT_FAIL=1,**{flag:1})
            self.assertNotEqual(result.returncode,0);self.fixture.assert_stopped();self.assertNotIn('systemctl start',trace)

    def test_incompatible_lkg_target_blocks_even_when_old_is_compatible(self):
        result,trace=self.run_flow('y\ny\n2\n',FAKE_TARGET_POSTCHECK_FAIL=1,FAKE_LKG_DIFFERENT=1,FAKE_LKG_TARGET_INCOMPATIBLE=1)
        self.assertNotEqual(result.returncode,0);self.fixture.assert_stopped();self.assertEqual(self.head(),shell.TARGET)
        self.assertNotIn('systemctl start',trace)

    def test_only_full_operator_sha_promotes_after_health(self):
        result,trace=self.run_flow('y\ny\ny\n'+shell.TARGET+'\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(self.lkg(),shell.TARGET)
        self.assertLess(trace.index('systemctl start shpakdnd-bot.service'),trace.index('release_lkg.py smoke'))
        self.assertLess(trace.index('release_lkg.py smoke'),trace.index('systemctl start shpakdnd-bot-watch.path'))
        self.assertLess(trace.index('release_lkg.py health'),trace.index('release_lkg.py confirm'))

    def test_eof_does_not_promote_new_lkg(self):
        result,trace=self.run_flow('y\ny\ny\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(self.lkg(),shell.OLD)
        self.assertIn('release_lkg.py confirm',trace)

    def test_failed_post_start_smoke_stops_bot_without_watcher_or_lkg(self):
        result,trace=self.run_flow('y\ny\ny\n1\n',FAKE_SMOKE_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.fixture.assert_stopped();self.assertEqual(self.lkg(),shell.OLD)
        self.assertNotIn('systemctl start shpakdnd-bot-watch.path',trace);self.assertNotIn('release_lkg.py confirm',trace)

    def test_unit_drift_before_stop_preserves_working_bot(self):
        result,trace=self.run_flow('y\n',FAKE_UNIT_DRIFT=1)
        self.assertNotEqual(result.returncode,0);self.assertNotIn('systemctl stop',trace);self.assertEqual(self.head(),shell.OLD)
        self.assertEqual((self.fixture.state/'shpakdnd-bot.service').read_text().strip(),'active')

    def test_unit_drift_after_checkout_blocks_startup(self):
        result,trace=self.run_flow('y\ny\ny\n1\n',FAKE_UNIT_DRIFT_AFTER=1)
        self.assertNotEqual(result.returncode,0);self.fixture.assert_stopped();self.assertNotIn('systemctl start',trace)
        self.assertEqual(self.lkg(),shell.OLD)

    def test_partial_migration_cannot_publish_or_return_lkg(self):
        result,trace=self.run_flow('y\ny\n2\n',FAKE_RUNTIME_FAIL=1,FAKE_LKG_DIFFERENT=1)
        self.assertNotEqual(result.returncode,0);self.fixture.assert_stopped();self.assertEqual(self.lkg(),shell.OLD)
        self.assertNotIn('systemctl start',trace);self.assertNotIn('release_lkg.py confirm',trace)
