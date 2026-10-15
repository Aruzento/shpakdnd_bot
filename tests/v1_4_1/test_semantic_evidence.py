"""Manual real-Telegram evidence is distinct from mock/technical smoke."""
import copy
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.v1_4_1 import test_last_known_good as lkg_tests
from tests.v1_4_1.test_preflight import pref
import release_lkg as lkg
import semantic_evidence as semantic
import release_state as store
from deploy_helpers import DeployError


class SemanticLkgTests(unittest.TestCase):
    def setUp(self):
        self.f=lkg_tests.LastKnownGoodTests('test_full_health_smoke_and_operator_sha_publish_with_history')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.ready();self.path=self.f.fixture.evidence.with_suffix('.semantic.json')
    def read(self):return store.read_record(self.path)
    def change(self,**changes):
        value=self.read();value.update(changes);value.pop('confirmation_hash',None);value['confirmation_hash']=store.digest(value);store.save_record(self.path,value)
    def blocked(self):
        with self.assertRaises((DeployError,FileNotFoundError)):self.f.promote()
        self.assertEqual(self.f.path.read_bytes(),self.f.before)
    def test_technical_health_without_semantic_never_promotes(self):self.path.unlink();self.blocked()
    def test_semantic_without_technical_never_promotes(self):self.f.fixture.evidence.with_suffix('.health.json').unlink();self.blocked()
    def test_mock_is_not_real_telegram(self):self.change(source='mock_bot_api');self.blocked()
    def test_api_mock_false_is_not_real(self):
        self.change(api={'status':'PASS','method':'getMe','real':False});self.blocked()
    def test_other_sha_rejected(self):self.change(sha=self.f.old);self.blocked()
    def test_other_deployment_rejected(self):self.change(deployment_id='another');self.blocked()
    def test_old_smoke_rejected(self):self.change(finished_at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat());self.blocked()
    def test_interrupted_smoke_not_confirmed(self):self.change(status='UNKNOWN');self.blocked()
    def test_failed_api_never_promotes(self):self.change(api={'status':'FAIL','method':'getMe','real':True});self.blocked()
    def test_loss_of_connectivity_blocks_confirmation(self):
        with patch.object(semantic,'telegram_identity',side_effect=DeployError('API unavailable')):self.blocked()
    def test_changed_bot_identity_blocks_confirmation(self):
        with patch.object(semantic,'telegram_identity',return_value=dict(self.f.api,bot_identity_hash='e'*64)):self.blocked()
    def test_incomplete_operator_checks_block(self):self.change(cases={});self.blocked()
    def test_missing_staging_blocks(self):Path(self.read()['staging_path']).unlink();self.blocked()
    def test_changed_staging_hash_blocks(self):
        value=self.read();path=Path(value['staging_path']);path.write_bytes(path.read_bytes()+b' ');self.blocked()
    def test_changed_process_blocks(self):self.change(process={'pid':'unverified'});self.blocked()
    def test_changed_tooling_blocks(self):self.change(tooling_hash='f'*64);self.blocked()
    def test_operator_refusal_keeps_previous_lkg(self):
        with self.assertRaises(DeployError):self.f.promote(self.f.old)
        self.assertEqual(self.f.path.read_bytes(),self.f.before)
    def test_success_retains_previous_history_and_semantic_link(self):
        value=self.f.promote();self.assertEqual(value['version'],2);self.assertEqual(value['semantic']['sha'],self.f.target)
        self.assertEqual(value['previous']['sha'],self.f.old)
    def test_repeated_promotion_cannot_create_second_record(self):
        first=self.f.promote();raw=self.f.path.read_bytes();self.assertEqual(self.f.promote(),first);self.assertEqual(raw,self.f.path.read_bytes())
    def test_completed_promotion_report_reads_actual_new_lkg(self):
        from scripts import release_report as reporting,github_release
        from tests.v1_4_1.test_final_release import ci
        self.f.promote()
        with patch.object(reporting,'BASELINE',self.f.old),patch.object(github_release,'api',return_value={'object':{'sha':self.f.target}}):
            value=reporting.generate(self.f.project,self.f.target,preflight=self.f.fixture.evidence,provider=lambda sha:ci(sha))
        self.assertEqual(value['deployment']['lkg_sha'],self.f.target)
        self.assertTrue(value['deployment']['promotion']);self.assertEqual(value['checks']['lkg_promotion']['status'],'PASS')
        self.assertFalse(value['readiness']['deployment_start_ready'])
        self.assertEqual(value['deployment']['production_status'],'RELEASE_CONFIRMED')

    def test_completed_report_flag_cannot_attest_wrong_preflight(self):
        current=self.f.promote();changed=dict(current,completed_preflight_hash='e'*64)
        with self.assertRaises(DeployError):pref.validate_evidence(self.f.fixture.evidence,old=self.f.old,target=self.f.target,tools=self.f.fixture.tools,completed_lkg=changed)

    def test_corrupted_confirmation_hash_rejected(self):
        value=self.read();value['confirmation_hash']='a'*64;store.save_record(self.path,value);self.blocked()
    def test_gameplay_scope_is_not_full_staging(self):
        path=Path(self.read()['staging_path']);value=store.read_record(path)
        for name in semantic.INFRA_CASES:value['cases'][name]={'status':'NOT_RUN'}
        value.pop('confirmation_hash');value['confirmation_hash']=store.digest(value);store.save_record(path,value)
        self.assertEqual(semantic.validate_staging(path,self.f.target,full=False)['status'],'PASS')
        with self.assertRaises(DeployError):semantic.validate_staging(path,self.f.target)


class AttestationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.project=self.root/'game';self.project.mkdir();self.sha='a'*40
        self.addCleanup(patch.stopall);patch.object(store,'TRUSTED_UID',getattr(os,'geteuid',lambda:0)()).start()
        patch.object(store,'command',side_effect=lambda args,**kw:self.sha if 'rev-parse' in args else '').start()
        import systemd_state as units
        patch.object(units,'verify_installed',return_value={'project':str(self.project)}).start()
        patch.object(units,'staging_identity',return_value={'environment_kind':'isolated_staging'}).start()
        self.api=patch.object(semantic,'telegram_identity',return_value={'status':'PASS','method':'getMe','real':True,'bot_identity_hash':'b'*64}).start()
        self.artifact=self.root/'artifact.txt';self.artifact.write_text('Anonymized observation fixture');self.artifact.chmod(0o600)
        self.checklist=self.root/'checklist.json'
        self.raw={'sha':self.sha,'operator_sha':self.sha,'source':'operator_observed_real_telegram','environment_kind':'isolated_staging','environment_id':'fixture-vm',
            'cases':{name:dict(status='PASS',artifact=str(self.artifact),initial='fixture',action='observed',expected='expected',actual='observed',date=store.utc_now(),executor='fixture') for name in semantic.STAGING_CASES}}
        self.checklist.write_text(json.dumps(self.raw));self.checklist.chmod(0o600);self.output=self.root/'private'/'staging.json'
    def attest(self):return semantic.attest(self.project,self.sha,self.checklist,self.output,kind='staging',environment='isolated_staging',operator_sha=self.sha)
    def test_complete_observations_and_api_bound_to_hashes(self):
        value=self.attest();self.assertEqual(value['scope'],'FULL_STAGING');self.assertEqual(semantic.validate_staging(self.output,self.sha),value)
        self.assertNotIn('executor',json.dumps(value));self.assertNotIn('actual',json.dumps(value));self.assertNotIn(str(self.artifact),json.dumps(value))
    def test_failed_api_keeps_unknown_record(self):
        self.api.side_effect=DeployError('Telegram unavailable')
        with self.assertRaises(DeployError):self.attest()
        self.assertEqual(store.read_record(self.output)['status'],'UNKNOWN')
    def test_sigterm_keeps_unknown_no_false_pass(self):
        self.api.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.attest()
        self.assertFalse(store.read_record(self.output)['operator_confirmed'])
    def test_arbitrary_tests_ok_not_an_observation(self):
        self.raw['cases']={'Tests':'OK'};self.checklist.write_text(json.dumps(self.raw))
        with self.assertRaises(DeployError):self.attest()
    def test_eof_or_wrong_operator_sha_cannot_attest(self):
        for answer in ('','b'*40):
            with self.assertRaises(DeployError):semantic.attest(self.project,self.sha,self.checklist,self.output,kind='staging',environment='isolated_staging',operator_sha=answer)
    def test_bad_source_mock_cannot_attest(self):
        self.raw['source']='mock_bot_api';self.checklist.write_text(json.dumps(self.raw))
        with self.assertRaises(DeployError):self.attest()

if __name__=='__main__':unittest.main()
