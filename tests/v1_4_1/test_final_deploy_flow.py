"""Final gate and explicit unconfirmed release statuses in real Bash/fake services."""
import unittest
from tests.v1_3_3 import test_deploy_shell as shell


class FinalDeploymentFlowTests(unittest.TestCase):
    def setUp(self):
        self.f=shell.DeployShellTests('test_success_orders_fetch_notice_stop_backup_checkout_checks_start')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_final_readiness_failure_precedes_notice_and_stop(self):
        result,trace=self.f.run_deploy('y\n',FAKE_FINAL_GATE_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assertNotIn('systemctl stop',trace);self.assertNotIn('telegram-deploy-notice.py',trace)
        self.assertEqual((self.f.state/'head').read_text().strip(),shell.OLD)
    def test_preflight_then_final_gate_before_maintenance(self):
        result,trace=self.f.run_deploy('y\ny\ny\n')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertLess(trace.index('-m unittest'),trace.index('release_report.py gate'))
        self.assertLess(trace.index('release_report.py gate'),trace.index('systemctl stop'))
    def test_no_semantic_keeps_previous_lkg_and_reports_unconfirmed(self):
        result,trace=self.f.run_deploy('y\ny\ny\n',FAKE_SEMANTIC_ABSENT=1)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual((self.f.state/'lkg').read_text().strip(),shell.OLD)
        self.assertIn('release NOT CONFIRMED',result.stdout);self.assertIn('release_lkg.py status',trace)
    def test_eof_never_promotes_new_lkg(self):
        result,trace=self.f.run_deploy('y\ny\ny\n')
        self.assertEqual(result.returncode,0);self.assertEqual((self.f.state/'lkg').read_text().strip(),shell.OLD)
        self.assertIn('current LKG',result.stdout)
    def test_failed_target_preserves_lkg_and_no_success(self):
        result,trace=self.f.run_deploy('y\ny\n2\n',FAKE_RUNTIME_FAIL=1)
        self.assertNotEqual(result.returncode,0);self.assertEqual((self.f.state/'lkg').read_text().strip(),shell.OLD)
        self.assertNotIn('LKG CONFIRMED',result.stdout);self.f.assert_stopped()

if __name__=='__main__':unittest.main()
