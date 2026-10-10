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

    def test_failed_evidence_keeps_fail_not_pass_or_not_run(self):
        path=self.root/'state'/'review.json';store.save_record(path,{'sha':SHA,'kind':'INDEPENDENT_REVIEW','status':'FAIL'})
        self.assertEqual(self.generate(review=path)['checks']['independent_review']['status'],'FAIL')

    def test_staging_scenarios_publish_hashes_without_private_observations(self):
        path=self.root/'state'/'staging.json'
        store.save_record(path,{'sha':SHA,'kind':'REAL_TELEGRAM_STAGING','status':'PASS','finished_at':store.utc_now(),
            'operator_confirmed':True,'cases':{'menu':{'status':'PASS','artifact_hash':'a'*64,'actual':'SENSITIVE_SENTINEL'}}})
        with patch.object(report.semantic,'validate_staging',return_value={}):value=self.generate(staging=path)
        self.assertEqual(value['staging']['scenarios']['menu']['status'],'PASS')
        self.assertNotIn('SENSITIVE_SENTINEL',json.dumps(value))

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



class LegacyBootstrapGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.project=Path(self.temp.name)/'game';self.project.mkdir()
        self.sha=report.BASELINE;self.tools_sha='d'*40;self.path=Path(self.temp.name)/'legacy.json'
        self.raw={'kind':'PINNED_LEGACY_BASELINE','old_sha':self.sha,'target_sha':self.sha,'tooling_hash':'e'*64,
            'tests':{'count':979},'head_discovered_tests':979,'baseline_discovered_tests':979,'release_checks':dict.fromkeys(CHECK_NAMES,'OK'),
            'installed_systemd':{'source_sha':self.tools_sha,'tooling_hash':'e'*64},'lkg_binding':{'sha':None}}
        self.data=ci(self.tools_sha);self.path.write_text(json.dumps(self.raw))
        self.addCleanup(patch.stopall)
        patch.object(pref,'validate_evidence',side_effect=lambda *a,**kw:self.raw).start()
        patch.object(store,'command',side_effect=lambda args,**kw:self.sha if 'rev-parse' in args else '').start()
        import systemd_state as units,release_lkg as lkg
        self.binding=patch.object(units,'verify_binding').start()
        self.lkg=patch.object(lkg,'verify_binding',return_value=None).start()
        self.staging=patch.object(report.semantic,'validate_staging',return_value={'status':'PASS'}).start()
        self.manual=patch.object(report,'protected',return_value=(report.item('PASS',{'artifact_hash':'a'*64}),
            {'operator_confirmed':True,'source':'explicit_operator_attestation','artifact_hash':'a'*64})).start()
    def gate(self):return report.legacy_bootstrap(self.project,self.sha,self.path,'staging','review','approval',provider=lambda sha:self.data)
    def test_pinned_legacy_adapter_and_exact_tooling_ci_allow_initialization_only(self):
        value=self.gate();self.assertEqual(value['sha'],self.sha);self.assertFalse(value['new_release_ready'])
        self.staging.assert_called_once_with('staging',self.sha,full=False);self.binding.assert_called_once_with(self.raw['installed_systemd'],active=True)
    def test_nonbaseline_sha_cannot_use_initialization_exception(self):
        self.sha='b'*40
        with self.assertRaises(DeployError):self.gate()
    def test_normal_preflight_cannot_impersonate_legacy(self):
        self.raw['kind']='NORMAL';self.path.write_text(json.dumps(self.raw))
        with self.assertRaises(DeployError):self.gate()
    def test_existing_lkg_cannot_be_replaced_by_bootstrap(self):
        self.lkg.return_value={'sha':'b'*40}
        with self.assertRaises(DeployError):self.gate()
    def test_incomplete_legacy_tests_block_initialization(self):
        self.raw['tests']['count']=978
        with self.assertRaises(DeployError):self.gate()
    def test_missing_installed_tooling_ci_blocks_initialization(self):
        self.data['jobs']['systemd-staging']['status']='NOT_RUN'
        with self.assertRaises(DeployError):self.gate()
    def test_wrong_tooling_ci_sha_blocks_initialization(self):
        self.data['jobs']['Linux release checks']['checkout_sha']='f'*40
        with self.assertRaises(DeployError):self.gate()
    def test_missing_required_ci_blocks_initialization(self):
        self.data['protection']['status']='FAIL'
        with self.assertRaises(DeployError):self.gate()
    def test_no_operator_review_or_decision_blocks_initialization(self):
        self.manual.return_value=(report.item('NOT_RUN'),None)
        with self.assertRaises(DeployError):self.gate()
    def test_unverified_real_gameplay_blocks_initialization(self):
        self.staging.side_effect=DeployError('Real Telegram NOT_RUN')
        with self.assertRaises(DeployError):self.gate()
    def test_started_operation_cannot_replay_initialization(self):
        pref.deployment_path(self.path).touch()
        with self.assertRaises(DeployError):self.gate()



class DeploymentEvidenceAtomicTests(unittest.TestCase):
    """Real disposable Git/SQLite/envelopes, fake process/API only; no production."""
    def setUp(self):
        from tests.v1_4_1 import test_last_known_good as fixtures
        self.f=fixtures.LastKnownGoodTests('test_full_health_smoke_and_operator_sha_publish_with_history')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.ready()
        self.path=self.f.fixture.evidence;self.state_path=pref.deployment_path(self.path)
        self.baseline=patch.object(report,'BASELINE',self.f.old);self.baseline.start();self.addCleanup(self.baseline.stop)
        self.main=patch.object(github,'api',return_value={'object':{'sha':self.f.target}});self.main.start();self.addCleanup(self.main.stop)
    def generate(self):return report.generate(self.f.project,self.f.target,preflight=self.path,provider=lambda sha:ci(sha))
    def no_pass(self,value,names):
        for name in names:self.assertNotEqual(value['checks'][name]['status'],'PASS',name)
        self.assertFalse(value['readiness']['deployment_start_ready']);self.assertFalse(value['readiness']['production_release_confirmed'])
    def automatic_pass(self,value):
        for name in ('ci','guard','tests','validators','automatic_smoke','systemd_ci','legacy_ci'):self.assertEqual(value['checks'][name]['status'],'PASS',name)
    def change_state(self,**changes):
        value=self.f.fixture.raw_state();value.update(changes);pref.save_deployment(self.path,value)
    def change_semantic(self,**changes):
        path=self.path.with_suffix('.semantic.json');value=store.read_record(path);value.update(changes)
        value.pop('confirmation_hash',None);value['confirmation_hash']=store.digest(value);store.save_record(path,value)
    def change_pref(self,**changes):
        value=self.f.fixture.report;value.update(changes);pref.save_evidence(self.path,value)
        self.change_state(evidence_sha256=store.file_hash(self.path))
    def test_valid_deployment_groups_publish_only_confirmed_pass(self):
        value=self.generate()
        for name in ('preflight','installed_systemd','sqlite','migration','rollback','backup_readiness','final_backup','startup_health','real_telegram_postdeploy'):self.assertEqual(value['checks'][name]['status'],'PASS',name)
        self.assertEqual(value['checks']['lkg_promotion']['status'],'NOT_RUN');self.automatic_pass(value)
    def test_corrupt_preflight_json_invalidates_dependents(self):
        self.path.write_text('{broken')
        value=self.generate();self.no_pass(value,report.DEPLOYMENT_CHECKS);self.automatic_pass(value)
    def test_wrong_preflight_checksum_invalidates_dependents(self):
        Path(str(self.path)+'.sha256').write_text('a'*64)
        value=self.generate();self.no_pass(value,report.DEPLOYMENT_CHECKS);self.automatic_pass(value)
    def test_wrong_preflight_sha_is_stale(self):
        raw=json.loads(self.path.read_text());raw['target_sha']='b'*40;pref.save_evidence(self.path,raw)
        value=self.generate();self.assertEqual(value['checks']['preflight']['status'],'STALE');self.no_pass(value,report.DEPLOYMENT_CHECKS)
    def test_missing_preflight_is_not_run(self):
        self.path.unlink();value=self.generate()
        for name in report.DEPLOYMENT_CHECKS:self.assertEqual(value['checks'][name]['status'],'NOT_RUN')
        self.automatic_pass(value)
    def test_unreadable_journal_never_confirms_db_stage(self):
        with patch.object(pref,'read_deployment',side_effect=OSError('PRIVATE_PATH_SENTINEL')):value=self.generate()
        self.no_pass(value,('sqlite','migration','rollback','backup_readiness','deployment_window','final_backup','startup_health','real_telegram_postdeploy','lkg_promotion'))
        self.assertEqual(value['checks']['preflight']['status'],'PASS');self.assertEqual(value['checks']['installed_systemd']['status'],'PASS')
        self.assertNotIn('PRIVATE_PATH_SENTINEL',json.dumps(value));self.automatic_pass(value)
    def test_corrupt_journal_checksum_never_confirms_stage(self):
        Path(str(self.state_path)+'.sha256').write_text('f'*64)
        self.no_pass(self.generate(),('sqlite','migration','rollback','backup_readiness','deployment_window','final_backup','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_unknown_journal_phase_never_confirms_stage(self):
        self.change_state(phase='UNKNOWN')
        self.no_pass(self.generate(),('sqlite','migration','rollback','backup_readiness','deployment_window','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_started_journal_phase_never_confirms_stage(self):
        self.change_state(phase='MIGRATION_STARTED',history=['SOURCE','MIGRATION_STARTED'],runtime_success=False,preserved=False)
        self.no_pass(self.generate(),('sqlite','migration','rollback','backup_readiness','deployment_window','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_target_without_runtime_success_is_unknown(self):
        self.change_state(runtime_success=False)
        self.no_pass(self.generate(),('sqlite','migration','rollback','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_source_claim_with_target_database_fails_sqlite(self):
        self.change_state(phase='SOURCE',history=['SOURCE'],runtime_success=False,preserved=False,startup_attempted=False)
        value=self.generate();self.assertEqual(value['checks']['sqlite']['status'],'FAIL')
        self.no_pass(value,('sqlite','migration','rollback','backup_readiness','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_corrupt_final_backup_does_not_erase_independent_checks(self):
        self.f.fixture.backup.write_bytes(b'corrupt fixture')
        value=self.generate();self.assertEqual(value['checks']['final_backup']['status'],'FAIL')
        self.no_pass(value,('final_backup','migration','rollback','backup_readiness','deployment_window','startup_health','real_telegram_postdeploy','lkg_promotion'))
        for name in ('preflight','installed_systemd','sqlite'):self.assertEqual(value['checks'][name]['status'],'PASS')
        self.automatic_pass(value)
    def test_matching_hash_but_corrupt_backup_is_fail(self):
        self.f.fixture.backup.write_bytes(b'not sqlite');self.change_state(backup_sha256=store.file_hash(self.f.fixture.backup))
        value=self.generate();self.assertEqual(value['checks']['final_backup']['status'],'FAIL')
        self.no_pass(value,('migration','rollback','backup_readiness','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_backup_hash_mismatch_is_fail(self):
        self.change_state(backup_sha256='f'*64)
        value=self.generate();self.assertEqual(value['checks']['final_backup']['status'],'FAIL')
        self.no_pass(value,('migration','rollback','backup_readiness','deployment_window','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_exception_reading_backup_does_not_leave_pending_pass(self):
        original=store.file_hash
        def read(path):
            if Path(path)==self.f.fixture.backup:raise OSError('PRIVATE_BACKUP_SENTINEL')
            return original(path)
        with patch.object(store,'file_hash',side_effect=read):value=self.generate()
        self.assertEqual(value['checks']['final_backup']['status'],'UNKNOWN');self.no_pass(value,('migration','rollback','backup_readiness','startup_health','real_telegram_postdeploy','lkg_promotion'))
        self.automatic_pass(value);self.assertNotIn('PRIVATE_BACKUP_SENTINEL',json.dumps(value))
    def test_live_sqlite_integrity_corruption_is_fail(self):
        self.f.fixture.db.write_bytes(b'not sqlite')
        value=self.generate();self.assertEqual(value['checks']['sqlite']['status'],'FAIL')
        self.no_pass(value,('sqlite','migration','rollback','backup_readiness','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_live_foreign_key_error_is_fail(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.f.fixture.db)) as conn:
            conn.execute('CREATE TABLE invalid_fk(id INTEGER REFERENCES mini_players(id))');conn.execute('INSERT INTO invalid_fk VALUES(999999)');conn.commit()
        value=self.generate();self.assertEqual(value['checks']['sqlite']['status'],'FAIL');self.no_pass(value,('sqlite','migration','rollback','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_systemd_manifest_read_failure_cannot_leave_pass(self):
        with patch.object(self.f.unit_verify,'side_effect',OSError('PRIVATE_MANIFEST_SENTINEL')):value=self.generate()
        self.no_pass(value,report.DEPLOYMENT_CHECKS);self.automatic_pass(value)
    def test_late_systemd_drift_keeps_verified_sqlite_only(self):
        original=self.f.installed
        calls=0
        def verify(*a,**kw):
            nonlocal calls
            calls+=1
            if calls>1:raise DeployError('Loaded systemd configuration drift')
            return original
        with patch.object(self.f.unit_verify,'side_effect',verify):value=self.generate()
        self.assertEqual(value['checks']['installed_systemd']['status'],'FAIL');self.no_pass(value,('installed_systemd','startup_health','real_telegram_postdeploy','lkg_promotion'))
        self.assertEqual(value['checks']['sqlite']['status'],'PASS');self.automatic_pass(value)
    def test_exception_reading_initial_lkg_preserves_ci_inventory(self):
        import release_lkg as lkg
        with patch.object(lkg,'read_lkg',side_effect=OSError('PRIVATE_LKG_SENTINEL')):value=self.generate()
        self.no_pass(value,report.DEPLOYMENT_CHECKS);self.automatic_pass(value)
    def test_late_lkg_binding_error_does_not_undo_database_proofs(self):
        import release_lkg as lkg
        original=lkg.verify_binding;calls=0
        def verify(*a,**kw):
            nonlocal calls
            calls+=1
            if calls>1:raise OSError('PRIVATE_LKG_SENTINEL')
            return original(*a,**kw)
        with patch.object(lkg,'verify_binding',side_effect=verify):value=self.generate()
        self.assertEqual(value['checks']['rollback']['status'],'UNKNOWN')
        for name in ('preflight','sqlite','migration','final_backup'):self.assertEqual(value['checks'][name]['status'],'PASS')
        self.automatic_pass(value)
    def test_missing_lkg_never_uses_old_head(self):
        self.f.path.unlink();value=self.generate();self.no_pass(value,('preflight','rollback','lkg_promotion'));self.automatic_pass(value)
    def test_incompatible_lkg_is_explicit_fail(self):
        self.change_pref(lkg_compatibility={'sha':self.f.old,'source':True,'target':False})
        value=self.generate();self.assertEqual(value['checks']['rollback']['status'],'FAIL');self.no_pass(value,('rollback',))
    def test_same_schema_changed_data_cannot_claim_rollback(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.f.fixture.db)) as conn:conn.execute('UPDATE mini_players SET coins=0');conn.commit()
        value=self.generate();self.no_pass(value,('rollback',));self.assertEqual(value['checks']['sqlite']['status'],'PASS')
    def test_missing_health_is_not_run(self):
        self.path.with_suffix('.health.json').unlink();value=self.generate()
        self.assertEqual(value['checks']['startup_health']['status'],'NOT_RUN');self.no_pass(value,('startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_corrupt_health_does_not_invalidate_verified_backup(self):
        self.path.with_suffix('.health.json').write_text('bad health');value=self.generate()
        self.assertEqual(value['checks']['startup_health']['status'],'UNKNOWN');self.assertEqual(value['checks']['final_backup']['status'],'PASS')
        self.no_pass(value,('startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_exception_checking_health_keeps_database_and_ci_proofs(self):
        self.f.health.side_effect=OSError('PRIVATE_JOURNAL_SENTINEL');value=self.generate()
        self.assertEqual(value['checks']['startup_health']['status'],'UNKNOWN');self.no_pass(value,('startup_health','real_telegram_postdeploy','lkg_promotion'))
        self.assertEqual(value['checks']['sqlite']['status'],'PASS');self.automatic_pass(value)
    def test_health_expired_is_stale(self):
        path=self.path.with_suffix('.health.json');value=store.read_record(path);value['finished_at']='2000-01-01T00:00:00+00:00';store.save_record(path,value)
        result=self.generate();self.assertEqual(result['checks']['startup_health']['status'],'STALE');self.no_pass(result,('real_telegram_postdeploy','lkg_promotion'))
    def test_health_wrong_sha_is_stale(self):
        path=self.path.with_suffix('.health.json');value=store.read_record(path);value['sha']=self.f.old;store.save_record(path,value)
        result=self.generate();self.assertEqual(result['checks']['startup_health']['status'],'STALE')
    def test_health_recorded_fail_is_not_replaced_by_unknown(self):
        path=self.path.with_suffix('.health.json');value=store.read_record(path);value['status']='FAIL';store.save_record(path,value)
        result=self.generate();self.assertEqual(result['checks']['startup_health']['status'],'FAIL');self.no_pass(result,('real_telegram_postdeploy','lkg_promotion'))
    def test_missing_semantic_keeps_automatic_smoke_pass(self):
        self.path.with_suffix('.semantic.json').unlink();value=self.generate()
        self.assertEqual(value['checks']['real_telegram_postdeploy']['status'],'NOT_RUN');self.automatic_pass(value)
    def test_semantic_wrong_sha_is_stale(self):
        self.change_semantic(sha=self.f.old);value=self.generate();self.assertEqual(value['checks']['real_telegram_postdeploy']['status'],'STALE');self.no_pass(value,('lkg_promotion',))
    def test_semantic_wrong_deployment_is_stale(self):
        self.change_semantic(deployment_id='other');value=self.generate();self.assertEqual(value['checks']['real_telegram_postdeploy']['status'],'STALE')
    def test_semantic_mock_cannot_confirm_promotion(self):
        self.change_semantic(source='mock_bot_api');value=self.generate();self.no_pass(value,('real_telegram_postdeploy','lkg_promotion'))
    def test_semantic_read_error_keeps_technical_health(self):
        original=store.read_record
        def read(path):
            if Path(path)==self.path.with_suffix('.semantic.json'):raise OSError('PRIVATE_SEMANTIC_SENTINEL')
            return original(path)
        with patch.object(store,'read_record',side_effect=read):value=self.generate()
        self.assertEqual(value['checks']['real_telegram_postdeploy']['status'],'UNKNOWN');self.assertEqual(value['checks']['startup_health']['status'],'PASS');self.no_pass(value,('lkg_promotion',))
    def test_actual_promotion_requires_all_dependencies_on_regeneration(self):
        self.f.promote();value=self.generate();self.assertEqual(value['checks']['lkg_promotion']['status'],'PASS')
        self.path.with_suffix('.semantic.json').unlink();value=self.generate();self.no_pass(value,('real_telegram_postdeploy','lkg_promotion'))
        self.assertFalse(value['deployment']['promotion']);self.assertNotEqual(value['deployment']['production_status'],'RELEASE_CONFIRMED')
    def test_corrupt_outcome_cannot_restore_promotion(self):
        self.f.promote();self.path.with_suffix('.outcome.json').write_text('broken outcome');value=self.generate()
        self.no_pass(value,('lkg_promotion','deployment_window'));self.assertEqual(value['checks']['startup_health']['status'],'PASS');self.automatic_pass(value)
    def test_exception_during_outcome_validation_never_escapes_as_success(self):
        import release_lkg as lkg
        self.f.promote();lkg.deployment_outcome(self.path,self.f.project,self.f.target,self.f.path)
        original=lkg.read_lkg;calls=0
        def read(*a,**kw):
            nonlocal calls
            calls+=1
            if calls>2:raise OSError('PRIVATE_OUTCOME_SENTINEL')
            return original(*a,**kw)
        with patch.object(lkg,'read_lkg',side_effect=read):value=self.generate()
        self.no_pass(value,('lkg_promotion','deployment_window'));self.automatic_pass(value)
    def test_repaired_backup_does_not_make_missing_semantic_pass(self):
        self.path.with_suffix('.semantic.json').unlink();self.change_state(backup_sha256='f'*64)
        self.no_pass(self.generate(),('final_backup','migration','real_telegram_postdeploy','lkg_promotion'))
        self.change_state(backup_sha256=store.file_hash(self.f.fixture.backup));value=self.generate()
        self.assertEqual(value['checks']['final_backup']['status'],'PASS');self.assertEqual(value['checks']['real_telegram_postdeploy']['status'],'NOT_RUN');self.no_pass(value,('lkg_promotion',))
    def test_backup_source_data_digest_mismatch_is_fail(self):
        self.change_state(source_data='f'*64);value=self.generate()
        self.assertEqual(value['checks']['final_backup']['status'],'FAIL');self.no_pass(value,('migration','rollback','backup_readiness','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_journal_database_binding_mismatch_blocks_db_statuses(self):
        self.change_state(db=str(self.f.fixture.root/'other.db'));value=self.generate()
        self.no_pass(value,('sqlite','migration','rollback','backup_readiness','startup_health','real_telegram_postdeploy','lkg_promotion'))
    def test_missing_automatic_health_proof_blocks_health_promotion(self):
        self.path.with_suffix('.automatic-smoke.json').unlink();value=self.generate()
        self.no_pass(value,('startup_health','real_telegram_postdeploy','lkg_promotion'));self.automatic_pass(value)
    def test_outcome_stat_error_is_structured_not_positive_verdict(self):
        self.f.promote();original=Path.exists;outcome=self.path.with_suffix('.outcome.json')
        def exists(path):
            if path==outcome:raise PermissionError('PRIVATE_PATH_SENTINEL')
            return original(path)
        with patch.object(Path,'exists',exists):value=self.generate()
        self.no_pass(value,('deployment_window','lkg_promotion'));self.automatic_pass(value)
    def test_preflight_stat_error_is_unknown_not_not_run(self):
        original=Path.exists
        def exists(path):
            if path==self.path:raise PermissionError('PRIVATE_PATH_SENTINEL')
            return original(path)
        with patch.object(Path,'exists',exists):value=self.generate()
        self.assertEqual(value['checks']['preflight']['status'],'UNKNOWN');self.no_pass(value,report.DEPLOYMENT_CHECKS)
    def test_repeated_report_matches_same_verified_facts(self):
        first=self.generate();second=self.generate()
        first.pop('created_at_utc');second.pop('created_at_utc');self.assertEqual(first,second)
    def test_diagnostics_never_disclose_private_exception_text(self):
        self.f.health.side_effect=OSError('BOT_TOKEN=PRIVATE_SENTINEL .env /private/player.db')
        value=self.generate();encoded=json.dumps(value)+report.markdown(value)
        self.assertNotIn('PRIVATE_SENTINEL',encoded);self.assertTrue(value['deployment_diagnostics'])

if __name__=='__main__':unittest.main()
