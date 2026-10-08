"""Final readiness is explicit and missing/manual/mock proofs never become PASS."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import release_report as report,github_release as github
from scripts.release_checks import CHECK_NAMES
from tests.v1_4_1.test_preflight import pref
import release_state as store
from deploy_helpers import DeployError

SHA='a'*40


def ci(sha=SHA):
    source=dict(status='PASS',checkout_sha=sha,run_id=123,job_id=456,log_hash='d'*64)
    return dict(sha=sha,source='github_api',jobs={name:copy.deepcopy(source) for name in github.REQUIRED_CHECKS},
        release={'status':'PASS','sha':sha,'checks':dict.fromkeys(CHECK_NAMES,'PASS'),'tests':{'baseline':979,'discovered':1200,'executed':1200,'failures':0,'errors':0,'skips':0},'guard':{}},
        automatic_smoke={'status':'PASS','sha':sha,'real_telegram':False},protection={'status':'PASS','source_hash':'c'*64,'blockers':[]})


class FinalReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.addCleanup(patch.stopall);patch.object(store,'TRUSTED_UID',getattr(os,'geteuid',lambda:0)()).start()
        def git(args,**kwargs):
            if 'status' in args:return ''
            if 'branch' in args:return 'codex/v1.4.1'
            if 'diff' in args:return 'deploy/semantic_evidence.py'
            if 'rev-parse' in args:return SHA
            return ''
        patch.object(store,'command',side_effect=git).start()
        patch.object(github,'api',return_value={'object':{'sha':'f'*40}}).start()

    def generate(self,data=None,**kwargs):return report.generate(self.root,SHA,provider=lambda sha:data if data is not None else ci(sha),**kwargs)

    def test_valid_sources_generate_matching_json_markdown(self):
        value=self.generate();encoded=json.loads(json.dumps(value));markdown=report.markdown(value)
        self.assertEqual(encoded['sha'],SHA);self.assertIn(SHA,markdown)
        self.assertEqual(value['readiness']['version_status'],'Готово к тестовому стенду')
        self.assertFalse(value['readiness']['merge_ready']);self.assertFalse(value['readiness']['production_release_confirmed'])
        for name,check in value['checks'].items():
            if check['status']=='PASS':self.assertTrue(check['source'],name)
            self.assertIn(check['status'],markdown)

    def test_missing_ci_never_passes(self):
        value=self.generate({'jobs':{},'protection':{'status':'UNKNOWN'}})
        self.assertEqual(value['checks']['ci']['status'],'NOT_RUN');self.assertEqual(value['readiness']['version_status'],'В разработке')

    def test_missing_staging_stays_not_run(self):
        self.assertEqual(self.generate()['checks']['staging']['status'],'NOT_RUN')

    def test_new_sha_invalidates_previous_ci(self):
        value=self.generate(ci('b'*40));self.assertNotEqual(value['checks']['ci']['status'],'PASS')
        self.assertFalse(value['readiness']['merge_ready'])

    def test_plain_self_asserted_pass_is_not_an_evidence(self):
        path=self.root/'state'/'review.json';path.parent.mkdir();path.write_text('{"status":"PASS","Tests":"OK"}')
        value=self.generate(review=path);self.assertEqual(value['checks']['independent_review']['status'],'UNKNOWN')

    def test_corrupt_checksum_is_rejected(self):
        path=self.root/'state'/'review.json';store.save_record(path,{'sha':SHA,'kind':'INDEPENDENT_REVIEW','status':'PASS','finished_at':store.utc_now()})
        raw=json.loads(path.read_text());raw['payload']['sha']='e'*40;path.write_text(json.dumps(raw))
        self.assertEqual(self.generate(review=path)['checks']['independent_review']['status'],'UNKNOWN')

    def test_evidence_from_other_sha_is_stale(self):
        path=self.root/'state'/'review.json';store.save_record(path,{'sha':'b'*40,'kind':'INDEPENDENT_REVIEW','status':'PASS'})
        self.assertEqual(self.generate(review=path)['checks']['independent_review']['status'],'STALE')

    def test_unconfirmed_lkg_is_visible(self):
        deployment=self.generate()['deployment'];self.assertIsNone(deployment['lkg_sha']);self.assertEqual(deployment['lkg_status'],'NOT_RUN')
        self.assertFalse(deployment['promotion'])

    def test_secret_raw_ci_fields_are_not_published(self):
        data=ci();data['token']='SENSITIVE_SENTINEL';data['environment']={'BOT_TOKEN':'SENSITIVE_SENTINEL'}
        value=self.generate(data);self.assertNotIn('SENSITIVE_SENTINEL',json.dumps(value));self.assertNotIn('SENSITIVE_SENTINEL',report.markdown(value))

    def test_failing_tests_or_new_skip_blocks_staging_readiness(self):
        for field in ('failures','errors','skips'):
            data=ci();data['release']['tests'][field]=1
            value=self.generate(data);self.assertEqual(value['checks']['tests']['status'],'FAIL');self.assertFalse(value['readiness']['merge_ready'])

    def test_missing_validator_is_not_success(self):
        data=ci();del data['release']['checks']['tower']
        self.assertEqual(self.generate(data)['checks']['validators']['status'],'UNKNOWN')

    def test_arbitrary_ci_pass_without_run_log_hash_is_unknown(self):
        data=ci();del data['jobs']['Linux release checks']['log_hash']
        self.assertEqual(self.generate(data)['checks']['ci']['status'],'UNKNOWN')

    def test_independent_review_requires_explicit_artifact_and_sha(self):
        artifact=self.root/'review.txt';artifact.write_text('Actual independent review fixture')
        with self.assertRaises(DeployError):report.review_attestation(self.root/'game',SHA,artifact,self.root/'state'/'review.json','b'*40,'INDEPENDENT_REVIEW')


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        names=('git','ci','guard','tests','validators','automatic_smoke','systemd_ci','legacy_ci','required_ci','staging','independent_review','merged_main_sha','production_decision','preflight','sqlite','migration','rollback','installed_systemd','backup_readiness','deployment_window','final_backup','startup_health','real_telegram_postdeploy','lkg_promotion')
        self.checks={name:report.item('PASS',{'fixture_command_hash':'a'*64}) for name in names}

    def block(self,name,status='NOT_RUN',gate='deployment'):
        self.checks[name]=report.item(status,reason='Explicit fixture blocker')
        value=report.readiness(self.checks,on_main=True)
        self.assertTrue(any(b['check']==name for b in value['blockers'][gate]));return value

    def test_complete_proofs_and_user_decision_allow_start_not_fictional_success(self):
        self.checks['lkg_promotion']=report.item('NOT_RUN')
        value=report.readiness(self.checks,on_main=True)
        self.assertTrue(value['deployment_start_ready']);self.assertFalse(value['production_release_confirmed'])
        self.assertEqual(value['version_status'],'Можно в прод')

    def test_no_required_ci_blocks_merge(self):self.assertFalse(self.block('required_ci',gate='merge')['merge_ready'])
    def test_no_staging_blocks_merge(self):self.assertFalse(self.block('staging',gate='merge')['merge_ready'])
    def test_no_review_blocks_merge(self):self.assertFalse(self.block('independent_review',gate='merge')['merge_ready'])
    def test_new_commit_requires_main_gate(self):self.assertFalse(self.block('merged_main_sha','STALE')['deployment_start_ready'])
    def test_incomplete_preflight_blocks_deploy(self):self.assertFalse(self.block('preflight','UNKNOWN')['deployment_start_ready'])
    def test_unknown_migration_blocks_deploy(self):self.assertFalse(self.block('migration','UNKNOWN')['deployment_start_ready'])
    def test_missing_backup_proof_blocks_deploy(self):self.assertFalse(self.block('backup_readiness','NOT_RUN')['deployment_start_ready'])
    def test_unsafe_rollback_blocks_deploy(self):self.assertFalse(self.block('rollback','UNKNOWN')['deployment_start_ready'])
    def test_failed_systemd_blocks_confirmation(self):self.assertFalse(self.block('installed_systemd','FAIL')['production_release_confirmed'])
    def test_telegram_failure_blocks_confirmation(self):self.assertFalse(self.block('real_telegram_postdeploy','FAIL',gate='production_confirmation')['production_release_confirmed'])
    def test_no_user_decision_cannot_authorize_production(self):self.assertFalse(self.block('production_decision')['deployment_start_ready'])
    def test_rollback_never_makes_failed_target_success(self):self.assertFalse(self.block('preflight','FAIL')['production_release_confirmed'])


class GitHubProtectionTests(unittest.TestCase):
    def settings(self):return {'required_status_checks':{'strict':True,'contexts':list(github.REQUIRED_CHECKS)},'required_pull_request_reviews':{'required_approving_review_count':1,'dismiss_stale_reviews':True},'enforce_admins':{'enabled':True},'allow_force_pushes':{'enabled':False},'allow_deletions':{'enabled':False}}
    def result(self,settings):
        with patch.object(github,'api',side_effect=[settings,[]]):return github.protection()
    def test_actual_complete_protection_passes_with_source(self):
        value=self.result(self.settings());self.assertEqual(value['status'],'PASS');self.assertTrue(value['source_hash'])
    def test_missing_systemd_and_legacy_checks_fail(self):
        value=self.settings();value['required_status_checks']['contexts']=['Linux release checks']
        self.assertEqual(self.result(value)['status'],'FAIL')
    def test_admin_bypass_strict_and_stale_reviews_must_be_enforced(self):
        for field in ('enforce_admins','required_status_checks','required_pull_request_reviews'):
            settings=self.settings();settings[field]={};self.assertEqual(self.result(settings)['status'],'FAIL')
    def test_unreadable_api_not_verified(self):
        with patch.object(github,'api',side_effect=RuntimeError('API denied')):self.assertEqual(github.protection()['status'],'UNKNOWN')
    def test_no_exact_sha_ci_is_not_run(self):
        with patch.object(github,'api',return_value={'workflow_runs':[]}),patch.object(github,'protection',return_value={}):
            value=github.collect(SHA)
        self.assertTrue(all(job['status']=='NOT_RUN' for job in value['jobs'].values()))

if __name__=='__main__':unittest.main()
