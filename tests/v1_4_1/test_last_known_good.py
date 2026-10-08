"""LKG publishing/rollback uses exact protected proofs, never arbitrary OLD HEAD."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from tests.v1_4_1 import test_preflight as fixture_tests
from scripts.release_checks import CHECK_NAMES
import release_lkg as lkg
import release_state as store
import systemd_state as units
from deploy_helpers import DeployError
pref=fixture_tests.pref


class LastKnownGoodTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_tests.DeploymentStateTests(methodName='test_success_durably_records_history_and_compatible_rollback')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.project=self.fixture.root/'project';self.project.mkdir()
        self.fixture.db.rename(self.project/'shpakdnd.db');self.fixture.db=self.project/'shpakdnd.db'
        self.fixture.common['db']=self.fixture.db
        self.project.joinpath('.gitignore').write_text('*.db*\n')
        self.project.joinpath('bot.py').write_text('# no polling fixture\n')
        for args in [('init','-q'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),
                     ('config','core.autocrlf','false'),('config','commit.gpgsign','false'),
                     ('remote','add','origin','https://github.com/Aruzento/shpakdnd_bot.git'),('add','.'),('commit','-qm','Known good')]:self.git(*args)
        self.old=self.git('rev-parse','HEAD')
        (self.project/'code.txt').write_text('new code');self.git('add','.');self.git('commit','-qm','Target')
        self.target=self.git('rev-parse','HEAD')
        self.fixture.common.update(old=self.old,target=self.target)
        self.path=self.fixture.root/'state'/'last-known-good.json'
        self.addCleanup(patch.stopall)
        patch.object(store,'TRUSTED_UID',getattr(os,'geteuid',lambda:0)()).start()
        self.process=dict(pid='123',restarts='0',invocation='e'*32,started_monotonic='999999999999999999')
        self.installed=dict(project=str(self.project),config_hash='c'*64,manifest_hash='f'*64,
                           manifest_path=str(self.fixture.root/'units.json'),units=list(units.UNIT_NAMES),
                           python='fixture-python',bot_user='fixture-bot',tooling_hash=pref.tooling_hash(self.fixture.tools))
        self.unit_verify=patch.object(units,'verify_binding',return_value=self.installed).start()
        self.health=patch.object(units,'health',return_value=self.process).start()
        record=self.record(self.old);store.save_record(self.path,record);self.before=self.path.read_bytes()
        self.fixture.report.update(old_sha=self.old,target_sha=self.target,release_checks=dict.fromkeys(CHECK_NAMES,'OK'),
            installed_systemd=self.installed,lkg_binding=lkg.binding(self.path,self.project),
            lkg_compatibility={'sha':self.old,'source':True,'target':True})
        # Smoke requires a configured world; fixture has one user table initially.
        with fixture_tests.connect(self.fixture.db) as conn:
            for table in ('mini_worlds','mini_wallet_transactions','mini_event_sessions'):
                conn.execute('CREATE TABLE '+table+'(id INTEGER PRIMARY KEY)');conn.execute('INSERT INTO '+table+' VALUES(1)')
            conn.commit()
        self.fixture.backup.unlink();fixture_tests.backup_database(self.fixture.db,self.fixture.backup)
        self.fixture.report['source_schema']=fixture_tests.data.schema_digest(self.fixture.db)
        target_copy=self.fixture.root/'target-full.db';fixture_tests.backup_database(self.fixture.db,target_copy)
        self.fixture.add_target(target_copy)
        self.fixture.report['target_schema']=fixture_tests.data.schema_digest(target_copy)
        pref.save_evidence(self.fixture.evidence,self.fixture.report)
        self.common=dict(project=self.project,db=self.fixture.db,old=self.old,target=self.target,tools=self.fixture.tools)

    def git(self,*args):
        result=subprocess.run(['git','-C',str(self.project),*args],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout.strip()

    def record(self,sha):
        return dict(version=1,status='CONFIRMED',sha=sha,confirmed_at=store.utc_now(),deployment_id='previous-release',
            repository='github.com/Aruzento/shpakdnd_bot',tooling_hash=pref.tooling_hash(self.fixture.tools),
            systemd_hash='c'*64,release_checks=dict.fromkeys(CHECK_NAMES,'OK'),health={'status':'PASS','operator_confirmed':True},
            sqlite={'integrity':'OK','foreign_keys':'OK'},previous=None)

    def ready(self):
        self.fixture.init();self.fixture.migrate()
        state=self.fixture.raw_state();state.update(startup_attempted=True,startup_sha=self.target,
            launch_requested_monotonic=1,launch_requested_at=store.utc_now())
        pref.save_deployment(self.fixture.evidence,state)
        lkg.postdeploy_health(self.fixture.evidence,since=store.utc_now(),wait=0,**self.common)

    def promote(self,operator=None):
        return lkg.promote(self.fixture.evidence,lkg_path=self.path,operator_sha=operator or self.target,**self.common)

    def test_confirmed_lkg_is_bound_to_repository_and_existing_commit(self):
        self.assertEqual(lkg.read_lkg(self.path,self.project)['sha'],self.old)
        self.assertNotEqual(self.old,self.target)

    def test_missing_lkg_never_falls_back_to_old_head(self):
        self.path.unlink()
        with self.assertRaisesRegex(DeployError,'absent'):lkg.read_lkg(self.path,self.project)
        self.assertIsNone(lkg.binding(self.path,self.project)['sha'])

    def test_unconfirmed_or_missing_operator_proof_rejects_record(self):
        for changed in ({'status':'UNCONFIRMED'},{'health':{'status':'PASS'}},{'release_checks':{'fake':'OK'}}):
            store.save_record(self.path,dict(self.record(self.old),**changed))
            with self.assertRaises(DeployError):lkg.read_lkg(self.path,self.project)

    def test_corrupt_json_or_checksum_blocks_rollback(self):
        self.path.write_text('invalid JSON')
        with self.assertRaisesRegex(DeployError,'Invalid state'):lkg.read_lkg(self.path,self.project)
        self.path.write_bytes(self.before+b' ')  # Valid JSON whitespace alone is harmless.
        payload=json.loads(self.path.read_text());payload['payload']['sha']=self.target
        self.path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(DeployError,'checksum'):lkg.read_lkg(self.path,self.project)

    def test_lost_commit_or_wrong_repository_blocks_lkg(self):
        store.save_record(self.path,self.record('d'*40))
        with self.assertRaises(DeployError):lkg.read_lkg(self.path,self.project)
        store.save_record(self.path,self.record(self.old));self.git('remote','set-url','origin','https://example.invalid/other.git')
        with self.assertRaisesRegex(DeployError,'origin'):lkg.read_lkg(self.path,self.project)

    def test_checkout_only_cannot_publish_lkg(self):
        self.fixture.init()
        with self.assertRaises((DeployError,FileNotFoundError)):self.promote()
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_successful_migration_without_startup_cannot_publish_lkg(self):
        self.fixture.init();self.fixture.migrate()
        with self.assertRaisesRegex(DeployError,'startup history'):
            lkg.postdeploy_health(self.fixture.evidence,since=store.utc_now(),wait=0,**self.common)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_startup_without_smoke_cannot_publish_lkg(self):
        self.fixture.init();self.fixture.migrate()
        state=self.fixture.raw_state();state.update(startup_attempted=True,startup_sha=self.target,launch_requested_monotonic=1)
        pref.save_deployment(self.fixture.evidence,state)
        with self.assertRaises((DeployError,FileNotFoundError)):self.promote()
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_full_health_smoke_and_operator_sha_publish_with_history(self):
        self.ready();record=self.promote()
        self.assertEqual(record['sha'],self.target);self.assertTrue(record['health']['operator_confirmed'])
        self.assertEqual(record['previous']['sha'],self.old)
        self.assertEqual(lkg.read_lkg(self.path,self.project),record)

    def test_wrong_operator_sha_never_publishes(self):
        self.ready()
        with self.assertRaisesRegex(DeployError,'Operator confirmation'):self.promote(self.old)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_repeated_confirmation_is_idempotent(self):
        self.ready();first=self.promote();raw=self.path.read_bytes();second=self.promote()
        self.assertEqual(first,second);self.assertEqual(self.path.read_bytes(),raw)
        self.assertEqual(len(list((self.path.parent/'history').glob('*.json'))),1)

    def test_health_error_or_smoke_failure_preserves_previous_lkg(self):
        self.fixture.init();self.fixture.migrate()
        state=self.fixture.raw_state();state.update(startup_attempted=True,startup_sha=self.target,launch_requested_monotonic=1)
        pref.save_deployment(self.fixture.evidence,state)
        self.health.side_effect=DeployError('restart error')
        with self.assertRaisesRegex(DeployError,'restart error'):
            lkg.postdeploy_health(self.fixture.evidence,since=store.utc_now(),wait=0,**self.common)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_process_predating_checkout_cannot_prove_startup(self):
        self.fixture.init();self.fixture.migrate()
        state=self.fixture.raw_state();state.update(startup_attempted=True,startup_sha=self.target,launch_requested_monotonic=999)
        pref.save_deployment(self.fixture.evidence,state)
        self.health.return_value=dict(self.process,started_monotonic='1')
        with self.assertRaisesRegex(DeployError,'predates'):
            lkg.postdeploy_health(self.fixture.evidence,since=store.utc_now(),wait=0,**self.common)

    def test_systemd_drift_after_health_blocks_lkg_promotion(self):
        self.ready();self.unit_verify.side_effect=DeployError('unit drift')
        with self.assertRaisesRegex(DeployError,'unit drift'):self.promote()
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_sigkill_before_atomic_replace_never_publishes_new_lkg(self):
        self.ready()
        with patch.object(store.os,'replace',side_effect=OSError('power loss before replace')):
            with self.assertRaises(OSError):self.promote()
        self.assertEqual(self.path.read_bytes(),self.before)
        self.assertFalse(list(self.path.parent.glob('*.new')))

    def test_corrupt_history_blocks_future_rollback(self):
        self.ready();record=self.promote()
        (self.path.parent/'history'/record['previous']['record']).write_text('corrupt')
        with self.assertRaises(DeployError):lkg.read_lkg(self.path,self.project)

    def test_lkg_differs_from_old_only_lkg_compatibility_is_accepted(self):
        # OLD is the current TARGET commit in this synthetic recovery scenario;
        # LKG still points at the distinct previously confirmed commit.
        self.fixture.report['old_sha']=self.target;self.fixture.common['old']=self.target
        self.fixture.report['rollback_compatible']=False
        pref.save_evidence(self.fixture.evidence,self.fixture.report)
        self.fixture.init()
        self.assertEqual(lkg.rollback_target(self.fixture.evidence,self.project),self.old)
        self.fixture.verify(rollback=True)

    def test_compatibility_for_another_sha_is_not_lkg_proof(self):
        self.fixture.report['lkg_compatibility']['sha']=self.target
        pref.save_evidence(self.fixture.evidence,self.fixture.report);self.fixture.init()
        with self.assertRaisesRegex(DeployError,'mismatched'):lkg.rollback_target(self.fixture.evidence,self.project)

    def test_source_policy_accepts_only_verified_source_lkg(self):
        self.fixture.init();self.fixture.verify(rollback=True)
        state=self.fixture.raw_state()
        self.fixture.report['lkg_compatibility']['source']='unknown';pref.save_evidence(self.fixture.evidence,self.fixture.report)
        state['evidence_sha256']=store.file_hash(self.fixture.evidence);pref.save_deployment(self.fixture.evidence,state)
        with self.assertRaisesRegex(DeployError,'compatibility'):self.fixture.verify(rollback=True)

    def test_target_policy_accepts_only_verified_target_lkg(self):
        self.fixture.init();self.fixture.migrate();self.fixture.verify(rollback=True)
        state=self.fixture.raw_state()
        self.fixture.report['lkg_compatibility']['target']=False;pref.save_evidence(self.fixture.evidence,self.fixture.report)
        state['evidence_sha256']=store.file_hash(self.fixture.evidence);pref.save_deployment(self.fixture.evidence,state)
        with self.assertRaisesRegex(DeployError,'compatibility'):self.fixture.verify(rollback=True)

    def test_unknown_migration_state_blocks_even_compatible_lkg(self):
        state=self.fixture.init();state.update(phase='MIGRATION_STARTED',history=['SOURCE','MIGRATION_STARTED'])
        pref.save_deployment(self.fixture.evidence,state)
        with self.assertRaises(DeployError):lkg.rollback_target(self.fixture.evidence,self.project)
        pref.abort_deployment(self.fixture.evidence)
        with self.assertRaisesRegex(DeployError,'UNKNOWN'):self.fixture.verify(rollback=True)

    def test_new_lkg_record_since_preflight_invalidates_binding(self):
        changed=self.record(self.target);store.save_record(self.path,changed)
        with self.assertRaisesRegex(DeployError,'since preflight'):lkg.verify_binding(self.fixture.report['lkg_binding'],self.project)

    def test_real_process_kill_before_publication_preserves_confirmed_lkg(self):
        import time
        self.ready();ready=self.fixture.root/'publication-ready'
        values=self.fixture.root/'publication.json'
        values.write_text(json.dumps(dict(evidence=str(self.fixture.evidence),lkg_path=str(self.path),operator_sha=self.target,
             common={key:str(value) for key,value in self.common.items()},installed=self.installed,process=self.process,ready=str(ready))))
        child=self.fixture.root/'publish.py'
        child.write_text("""import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import release_state as s,systemd_state as units,release_lkg as lkg
v=json.loads(Path(sys.argv[2]).read_text());s.TRUSTED_UID=getattr(os,'geteuid',lambda:0)()
units.verify_binding=lambda *a,**kw:v['installed']
units.health=lambda *a,**kw:v['process']
original=s.os.replace
def pause(src,dst):
 if Path(dst)==Path(v['lkg_path']):
  Path(v['ready']).write_text('verified confirmation; waiting before atomic publication')
  time.sleep(60)
 return original(src,dst)
s.os.replace=pause
lkg.promote(v['evidence'],lkg_path=v['lkg_path'],operator_sha=v['operator_sha'],**v['common'])
""",encoding='utf-8')
        process=subprocess.Popen([sys.executable,str(child),str(self.fixture.tools),str(values)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+20
            while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            if not ready.exists():
                if process.poll() is None:process.kill()
                out,err=process.communicate(timeout=5);self.fail((out+err).decode('utf-8'))
            process.kill();process.communicate(timeout=5)
        finally:
            if process.poll() is None:process.kill();process.communicate(timeout=5)
        self.assertEqual(self.path.read_bytes(),self.before)
        self.assertEqual(lkg.read_lkg(self.path,self.project)['sha'],self.old)

    def test_sigterm_during_health_never_publishes_lkg(self):
        self.fixture.init();self.fixture.migrate()
        state=self.fixture.raw_state();state.update(startup_attempted=True,startup_sha=self.target,launch_requested_monotonic=1)
        pref.save_deployment(self.fixture.evidence,state)
        self.health.side_effect=KeyboardInterrupt('SIGTERM before health confirmation')
        with self.assertRaises(KeyboardInterrupt):lkg.postdeploy_health(self.fixture.evidence,since=store.utc_now(),wait=0,**self.common)
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_stale_health_confirmation_preserves_old_lkg(self):
        self.ready();path=Path(self.fixture.evidence).with_suffix(".health.json");proof=store.read_record(path)
        proof['finished_at']='2020-01-01T00:00:00+00:00';store.save_record(path,proof)
        with self.assertRaisesRegex(DeployError,'stale'):self.promote()
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_target_startup_health_requires_fresh_process_and_clean_sha(self):
        self.ready();proof=lkg.startup_health(self.fixture.evidence,project=self.project,old=self.old,target=self.target,tools=self.fixture.tools,before_watcher=True)
        self.assertEqual(proof['sha'],self.target);self.assertEqual(proof['stage'],'TARGET')
        self.health.assert_called_with(self.installed,since=unittest.mock.ANY,before_watcher=True)
        self.health.return_value=dict(self.process,started_monotonic='0')
        with self.assertRaisesRegex(DeployError,'predates'):lkg.startup_health(self.fixture.evidence,project=self.project,old=self.old,target=self.target,tools=self.fixture.tools,before_watcher=True)

    def test_source_recovery_startup_health_uses_actual_lkg(self):
        self.fixture.init();self.git('checkout','--detach',self.old)
        state=self.fixture.raw_state();state.update(startup_attempted=True,startup_sha=self.old,launch_requested_monotonic=1,launch_requested_at=store.utc_now())
        pref.save_deployment(self.fixture.evidence,state)
        proof=lkg.startup_health(self.fixture.evidence,project=self.project,old=self.old,target=self.target,tools=self.fixture.tools,before_watcher=True)
        self.assertEqual(proof['sha'],self.old);self.assertEqual(proof['stage'],'SOURCE')
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_unknown_state_cannot_be_healthy_startup(self):
        self.ready();pref.abort_deployment(self.fixture.evidence)
        with self.assertRaisesRegex(DeployError,'confirmed SOURCE/TARGET'):lkg.startup_health(self.fixture.evidence,project=self.project,old=self.old,target=self.target,tools=self.fixture.tools,before_watcher=True)
        self.assertEqual(self.path.read_bytes(),self.before)
