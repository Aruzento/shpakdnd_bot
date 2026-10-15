"""Legacy LKG is strictly pinned; arbitrary HEAD cannot bypass C release gate."""
import unittest
import sys
from tests.v1_4_1 import test_preflight as fixtures
from deploy_helpers import DeployError
import legacy_lkg
from scripts.release_checks import NETWORK_GUARD


class LegacyInitializationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PreflightFixtureTests(methodName='test_preflight_passes_on_exact_target_and_preserves_live_head_database_env')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_arbitrary_old_target_is_not_a_legacy_baseline(self):
        op=legacy_lkg.LegacyVerification(project=self.fixture.project,db=self.fixture.db,old=self.fixture.old,target=self.fixture.target,
                 evidence=self.fixture.evidence,workspace=self.fixture.workspace,tools=self.fixture.tools,python=sys.executable)
        before=self.fixture.db.read_bytes()
        with self.assertRaisesRegex(DeployError,'pinned reviewed V1.4'):op.source_check()
        self.assertEqual(self.fixture.db.read_bytes(),before)

    def test_legacy_adapter_does_not_need_runner_at_old_sha(self):
        self.assertFalse((self.fixture.project/'scripts/release_checks.py').exists())
        op=legacy_lkg.LegacyVerification(project=self.fixture.project,db=self.fixture.db,old=self.fixture.old,target=self.fixture.target,
                 evidence=self.fixture.evidence,workspace=self.fixture.workspace,tools=self.fixture.tools,python=sys.executable)
        self.assertEqual(op.network_guard(self.fixture.project),NETWORK_GUARD)
