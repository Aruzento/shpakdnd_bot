"""Review regressions: SQLite exfiltration, runtime policy, disk, skips and retry."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.staging import controller as c
from deploy.staging.runtime_policy import expected, validate_unit, validate_config, validate_identity
from deploy.staging.public_report import project
from deploy.staging.report import collect
from deploy.staging.storage_policy import check_space, inventory, GIB
from deploy.staging.unittest_policy import PolicyRunner, allowed_skips
from tests.staging import test_controller as base_tests
SHA, OLD = base_tests.SHA, base_tests.OLD

SECRET = 'CONFIDENTIAL_SQLITE_PLAYER_RECORD_encoded_but_not_a_token'


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'base';self.config=Path(self.temp.name)/'config';self.state=Path(self.temp.name)/'state'
        self.config.mkdir();(self.config/'topics.py').write_text('TOPIC_SETTINGS={}')
        (self.config/'runtime.env').write_text('MINI_EDITORIAL_DIR='+(self.config/'editorial').as_posix()+'\nMINI_CUSTOM_EMOJI_ENABLED=false\nPYTHONDONTWRITEBYTECODE=1')
        (self.config/'.env').write_text('BOT_TOKEN=987654:abcdefghijklmnopqrstuvw\nBOT_TIMEZONE=Europe/Moscow')
        self.actual=expected(self.base,self.config,self.state)
        exe=str(self.base/'venv/bin/python');tail=' ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
        self.actual.update(ExecStart='{ path='+exe+' ; argv[]='+exe+' bot.py ; ignore_errors=no'+tail,
                           ExecStartEx='{ path='+exe+' ; argv[]='+exe+' bot.py ; flags='+tail)

    def test_exact_runtime_and_canonical_config_accepted(self):
        validate_unit(self.actual,self.base,self.config,self.state)
        validate_config(self.config,{'test_bot_id':987654},'LANG=C.UTF-8\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')

    def test_every_unexpected_runtime_property_is_rejected(self):
        for key in self.actual:
            with self.subTest(key=key):
                altered=dict(self.actual);altered[key]=SECRET
                with self.assertRaises(c.Refused):validate_unit(altered,self.base,self.config,self.state)

    def test_execstart_substring_extra_args_second_command_and_privilege_prefix_refused(self):
        exe=str(self.base/'venv/bin/python')
        bad=[self.actual['ExecStart'].replace(' bot.py ;',' bot.py --config=/production ;'),
             self.actual['ExecStart']+' '+self.actual['ExecStart'],
             '/bin/sh -c "'+exe+' bot.py"',
             self.actual['ExecStart'].replace('path='+exe,'path=/usr/bin/env')]
        for value in bad:
            with self.subTest(value=value):
                altered=dict(self.actual,ExecStart=value)
                with self.assertRaises(c.Refused):validate_unit(altered,self.base,self.config,self.state)
        altered=dict(self.actual,ExecStartEx=self.actual['ExecStartEx'].replace('flags= ;','flags=fully-privileged ;'))
        with self.assertRaises(c.Refused):validate_unit(altered,self.base,self.config,self.state)

    def test_structured_arrays_need_typed_empty_proof_not_unprintable_or_absence(self):
        from deploy.staging.runtime_policy import STRUCTURED_EMPTY,structured_empty_values,unit_object_path
        output='\n'.join(signature+' 0' for signature in STRUCTURED_EMPTY.values())
        self.assertTrue(all(value=='' for value in structured_empty_values(output).values()))
        malicious=output.replace('a(sasbttttuii) 0','a(sasbttttuii) 1 '+SECRET,1)
        values=structured_empty_values(malicious)
        self.assertEqual(values['ExecCondition'],'NONEMPTY_OR_UNSUPPORTED')
        self.assertNotIn(SECRET,json.dumps(values))
        with self.assertRaises(c.Refused):structured_empty_values('[unprintable]')
        self.assertEqual(unit_object_path('shpakdnd-bot.service'),'/org/freedesktop/systemd1/unit/shpakdnd_2dbot_2eservice')
        altered=dict(self.actual);altered.update(values)
        with self.assertRaises(c.Refused):validate_unit(altered,self.base,self.config,self.state)

    def test_additional_optional_or_production_environment_file_refused(self):
        for value in ('/production/.env (ignore_errors=no)',self.actual['EnvironmentFiles']+' /production/.env (ignore_errors=no)',
                      self.actual['EnvironmentFiles'].replace('ignore_errors=no','ignore_errors=yes')):
            with self.subTest(value=value),self.assertRaises(c.Refused):
                validate_unit(dict(self.actual,EnvironmentFiles=value),self.base,self.config,self.state)

    def test_manager_and_dotenv_configuration_cannot_override_test_scope(self):
        for environment in ('BOT_TOKEN=123456:production', 'PYTHONPATH=/production', 'MINI_EDITORIAL_DIR=/production','LD_PRELOAD=/evil.so'):
            with self.subTest(environment=environment),self.assertRaises(c.Refused):
                validate_config(self.config,{'test_bot_id':987654},environment)
        for extra in ('\nMINI_EDITORIAL_DIR=/production','\nBOT_TOKEN=987654:duplicate','\nDB_PATH=/production.db'):
            (self.config/'.env').write_text('BOT_TOKEN=987654:abcdefghijklmnopqrstuvw'+extra)
            with self.assertRaises(c.Refused):validate_config(self.config,{'test_bot_id':987654},'')

    def test_identity_alias_to_root_or_additional_groups_refused(self):
        validate_identity('1001\n','1001\n','1001\n')
        for values in (('0','1001','1001'),('1001','0','0'),('1001','1001','1001 27'),('not-uid','1001','1001')):
            with self.subTest(values=values),self.assertRaises(c.Refused):validate_identity(*values)


class PrivacyTests(unittest.TestCase):
    def test_actual_sqlite_diagnostic_output_is_private_even_if_it_forges_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);db=folder/'copy.db'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE player(data)');conn.execute('INSERT INTO player VALUES(?)',(SECRET,));conn.commit()
                private=conn.execute('SELECT data FROM player').fetchone()[0]
            payload=json.dumps({'preflight':'PASS','checks':[{'command':private,'exit_code':0}],'unittest_results':[private]})
            def output(*args,**kwargs):
                kwargs['stdout'].write(payload.encode());kwargs['stdout'].flush()
                return SimpleNamespace(poll=lambda:0,wait=lambda timeout:0)
            with patch.object(c.subprocess,'Popen',side_effect=output):
                result=c.Controller(None).preflight(folder,SHA,OLD,'sha256:'+'a'*64)
            self.assertIn(SECRET,(folder/'preflight.log').read_text())
            self.assertNotIn(SECRET,json.dumps(result))
            safe=project({'schema_version':2,'sha':SHA,'pr':12,'preflight':result,'errors':[private],
                          'systemd_failure':{c.BOT:{'Environment':private,'ExecStart':private,'ActiveState':private}},
                          'manual_telegram_scenarios':[private]})
            self.assertNotIn(SECRET,json.dumps(safe))

    def test_legacy_embedded_logs_and_markdown_never_become_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);incoming=root/'transport.json'
            incoming.write_text(json.dumps({'report.json':json.dumps({'acceptance_ready':'PASS','data':SECRET}),
                                            'report.md':SECRET,'preflight.log':SECRET,'startup.log':SECRET,'journal.log':SECRET}))
            artifacts=root/'artifacts';result=collect(artifacts,incoming)
            self.assertEqual(result['acceptance_ready'],'FAIL')
            self.assertEqual({p.name for p in artifacts.iterdir()},{'report.json','report.md'})
            for path in artifacts.iterdir():self.assertNotIn(SECRET,path.read_text())

    def test_public_projection_is_bounded_and_idempotent(self):
        raw={'schema_version':2,'pr':12,'sha':SHA,'request_id':'123-1','phase':'ready_for_manual_acceptance',
             'acceptance_ready':'PASS','ci':{'ci_run':42,'ci_attempt':1,'jobs':[SECRET]},
             'preflight':{'status':'PASS','skip_policy':'PASS','checks':[SECRET]},
             'runtime_verified':True,'configuration_verified':True,'capacity':{'status':'PASS','free_mib':1000,'required_mib':500},
             'backup':{'integrity':'ok','foreign_keys':0,'test_scope':True,'sha256':'a'*64,'path':SECRET},
             'installed_sha':SHA, 'sqlite_final':{'integrity':'ok','foreign_keys':0,'test_scope':True},
             'quiescent_backup':{'integrity':'ok','foreign_keys':0,'test_scope':True,'sha256':'b'*64},
             'systemd_after':{c.BOT:{'ActiveState':'active','MainPID':'123','NRestarts':'0','Environment':SECRET}}}
        first=project(raw);self.assertEqual(first['acceptance_ready'],'PASS')
        self.assertEqual(project(first),first)
        self.assertLess(len(json.dumps(first)),12000);self.assertNotIn(SECRET,json.dumps(first))

    def test_legacy_remote_protocol_is_refused_before_it_can_return_logs(self):
        from deploy.staging.ssh_entry import parse
        for command in ('report 123-1','deploy 12 '+SHA+' 123-1'):
            with self.subTest(command=command),self.assertRaises(ValueError):parse(command)
        text=(Path(__file__).resolve().parents[2]/'.github/workflows/staging-deploy.yml').read_text()
        self.assertIn('deploy-v2 $PR',text);self.assertIn('report-v2 $GITHUB',text)

    def test_workflow_uploads_only_safe_explicit_files(self):
        text=(Path(__file__).resolve().parents[2]/'.github/workflows/staging-deploy.yml').read_text()
        upload=text.split('      - name: Upload staging evidence',1)[1]
        for name in ('preflight.log','startup.log','journal.log','controller-response.json','server-reports.json'):
            self.assertNotIn(name,upload)
        self.assertNotIn('path: staging-artifacts/',upload)
        self.assertIn('staging-artifacts/report.json',upload)


class SkipPolicyTests(unittest.TestCase):
    def run_case(self,case):
        return PolicyRunner(stream=io.StringIO(),verbosity=0).run(unittest.TestSuite([case]))

    def test_new_or_runtime_skip_and_skip_reason_do_not_pass(self):
        class Unexpected(unittest.TestCase):
            def runTest(self):self.skipTest('convenient skip')
        result=self.run_case(Unexpected());self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.skipped),1)

    def test_decorated_skip_cannot_pass_linux_policy(self):
        @unittest.skip('new condition')
        class Unexpected(unittest.TestCase):
            def runTest(self):pass
        with patch('deploy.staging.unittest_policy.sys.platform','linux'):
            self.assertFalse(self.run_case(Unexpected()).wasSuccessful())

    def test_empty_suite_and_new_expected_failure_are_not_success(self):
        self.assertFalse(PolicyRunner(stream=io.StringIO(),verbosity=0).run(unittest.TestSuite()).wasSuccessful())
        class HiddenFailure(unittest.TestCase):
            @unittest.expectedFailure
            def runTest(self):self.fail('hidden')
        self.assertFalse(self.run_case(HiddenFailure()).wasSuccessful())

    def test_windows_allowlist_requires_exact_existing_id_and_reason(self):
        baseline=allowed_skips('win32');self.assertEqual(len(baseline),19)
        identifier,reason=next(iter(baseline.items()))
        class Existing(unittest.TestCase):
            def id(self):return identifier
            def runTest(self):self.skipTest(reason)
        with patch('deploy.staging.unittest_policy.sys.platform','win32'):
            self.assertTrue(self.run_case(Existing()).wasSuccessful())
        self.assertEqual(allowed_skips('linux'),{})


class StoragePolicyTests(unittest.TestCase):
    def test_shared_filesystem_budgets_are_aggregated_without_double_counting_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'test.db';db.write_bytes(b'x'*100)
            with patch('deploy.staging.storage_policy.shutil.disk_usage',return_value=SimpleNamespace(free=8*GIB)):
                result=check_space(root,root,root,db)
            self.assertEqual(result['free_mib'],8192)
            self.assertGreater(result['required_mib'],6*1024)
            with patch('deploy.staging.storage_policy.shutil.disk_usage',return_value=SimpleNamespace(free=6*GIB)):
                with self.assertRaises(c.Refused):check_space(root,root,root,db)
            self.assertEqual(db.read_bytes(),b'x'*100)

    def test_wal_and_multiple_snapshots_are_budgeted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'test.db';db.touch();wal=Path(str(db)+'-wal');wal.touch()
            real=Path.lstat
            def sizes(path,*args,**kwargs):
                original=real(path,*args,**kwargs)
                return SimpleNamespace(st_mode=original.st_mode,st_size=GIB) if path in (db,wal) else original
            with patch.object(Path,'lstat',sizes),patch('deploy.staging.storage_policy.shutil.disk_usage',return_value=SimpleNamespace(free=10*GIB)):
                with self.assertRaises(c.Refused):check_space(root,root,root,db)

    def test_retention_inventory_never_deletes_current_pinned_or_failed_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'base';state=root/'state'
            (base/'releases/current').mkdir(parents=True);(state/'reports/123-1').mkdir(parents=True)
            # Inventory's current lookup is a real directory for portable Windows tests.
            (base/'current').mkdir();(state/'reports/123-1/before.db').write_bytes(b'backup')
            (state/'reports/123-1/KEEP').touch()
            result=inventory(base,state)
            self.assertFalse(result['automatic_deletion'])
            self.assertTrue(all(row['protected'] for row in result['entries']))
            self.assertEqual((state/'reports/123-1/before.db').read_bytes(),b'backup')


class ReviewFlowTests(base_tests.ControllerFixture, unittest.TestCase):
    def test_sqlite_output_from_startup_and_journal_remains_only_on_test(self):
        original=self.fake_run
        def leaking(args,**kwargs):
            result=original(args,**kwargs)
            if args[0] in ('systemd-run','journalctl'):result.stdout=SECRET
            return result
        with patch.object(c,'run',side_effect=leaking):report=self.deploy()
        self.assertEqual(report['acceptance_ready'],'PASS')
        self.assertIn(SECRET,(self.folder/'startup.log').read_text())
        self.assertIn(SECRET,(self.folder/'journal.log').read_text())
        with patch.object(c.os,'geteuid',return_value=0,create=True),patch('builtins.print') as output:
            c.main(['report-v2','123-1'])
        safe=output.call_args.args[0];self.assertNotIn(SECRET,safe)
        artifacts=self.folder/'download';transport=self.folder/'safe.json';transport.write_text(safe)
        collect(artifacts,transport)
        for path in artifacts.iterdir():self.assertNotIn(SECRET,path.read_text())

    def test_environment_file_drift_before_stop_refuses_without_restart(self):
        original=self.unit;calls=0
        def changed(unit):
            nonlocal calls
            result=original(unit)
            if unit==c.BOT:
                calls+=1
                if calls>1:result['EnvironmentFiles']='/production/.env (ignore_errors=no)'
            return result
        with patch.object(c,'unit_state',side_effect=changed):result=self.deploy()
        self.assertEqual(result['acceptance_ready'],'FAIL');self.assertNotIn('stop',self.trace)

    def test_no_space_before_fetch_or_before_stop_preserves_running_test(self):
        for results in ([c.Refused('no space')],[{'status':'PASS'},c.Refused('no space')]):
            with self.subTest(stage=len(results)),patch.object(c,'check_space',side_effect=results):
                result=self.deploy()
            self.assertEqual(result['acceptance_ready'],'FAIL');self.assertNotIn('stop',self.trace)
            # A new isolated fixture is needed for a second request with the same directory.
            if len(results)==1:self.assertNotIn('preflight',self.trace)

    def test_legacy_successful_request_is_not_redeployed_or_reapproved(self):
        report=self.deploy();self.assertEqual(report['acceptance_ready'],'PASS')
        report['schema_version']=1
        (self.folder/'report.json').write_text(json.dumps(report))
        trace=list(self.trace)
        with patch.object(c.os,'geteuid',return_value=0,create=True),patch.object(c,'lock'),patch('builtins.print') as output:
            self.assertEqual(c.main(['deploy-v2','12',SHA,'123-1']),1)
        self.assertEqual(self.trace,trace)
        self.assertEqual(json.loads(output.call_args.args[0])['acceptance_ready'],'FAIL')

    def test_repeated_deploy_cannot_export_injected_private_fields(self):
        report=self.deploy();self.assertEqual(report['acceptance_ready'],'PASS')
        report['preflight']['checks']=[SECRET];report['errors']=[SECRET]
        (self.folder/'report.json').write_text(json.dumps(report))
        with patch.object(c.os,'geteuid',return_value=0,create=True),patch.object(c,'lock'),patch('builtins.print') as output:
            self.assertEqual(c.main(['deploy-v2','12',SHA,'123-1']),0)
        self.assertNotIn(SECRET,output.call_args.args[0])
