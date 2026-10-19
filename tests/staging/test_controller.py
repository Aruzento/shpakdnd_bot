"""Real SQLite/archive/filesystem plus fake external systemd/Git/container boundary."""
import io
import os
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from deploy.staging import controller as c
from deploy.staging.ssh_entry import parse
from deploy.staging.report import collect
from deploy.staging.runtime_policy import expected
from deploy.staging.public_report import project

SHA = 'a' * 40
OLD = 'b' * 40


def archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def database(path, scope=(-1003883723333, 2)):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('CREATE TABLE mini_worlds(chat_id,thread_id,enabled);'
                           'CREATE TABLE parent(id PRIMARY KEY);CREATE TABLE child(id REFERENCES parent(id));')
        conn.execute('INSERT INTO mini_worlds VALUES(?,?,1)', scope)
        conn.commit()


class PrimitiveTests(unittest.TestCase):
    def test_privileged_sqlite_reads_drop_all_supplementary_groups_and_restore(self):
        import types
        fake_pwd=types.SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=1001,pw_gid=1001))
        trace=[]
        with patch.object(c.os,'name','posix'), patch.object(c.os,'geteuid',return_value=0,create=True), \
             patch.object(c.os,'getegid',return_value=0,create=True), patch.object(c.os,'getgroups',return_value=[0],create=True), \
             patch.object(c.os,'setgroups',side_effect=lambda x: trace.append(('groups',x)),create=True), \
             patch.object(c.os,'setegid',side_effect=lambda x: trace.append(('gid',x)),create=True), \
             patch.object(c.os,'seteuid',side_effect=lambda x: trace.append(('uid',x)),create=True), \
             patch.dict(sys.modules,{'pwd':fake_pwd}):
            with self.assertRaises(RuntimeError):
                with c.as_test_user():
                    trace.append('read');raise RuntimeError('SQLite read refused')
        self.assertEqual(trace,[('groups',[]),('gid',1001),('uid',1001),'read',('uid',0),('gid',0),('groups',[0])])

    def test_control_paths_reject_symlinks_nonroot_and_writable_modes(self):
        for mode,uid in ((0o40775,0),(0o40755,1000),(0o120777,0)):
            with self.subTest(mode=mode,uid=uid), patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=mode,st_uid=uid)):
                with self.assertRaises(c.Refused): c.trusted(Path('/trusted/control'))
        with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=0o40755,st_uid=0)):
            c.trusted(Path('/trusted/control'))

    def test_forced_ssh_entry_never_passes_client_environment_or_shell(self):
        from deploy.staging import ssh_entry
        with patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':f'deploy-v2 12 {SHA} 123-1','PYTHONPATH':'evil','LD_PRELOAD':'evil'}):
            with patch.object(ssh_entry.subprocess,'call',return_value=0) as called:
                self.assertEqual(ssh_entry.main(),0)
                args=called.call_args.args[0]
                self.assertEqual(args[:4],['/usr/bin/sudo','-n','/usr/bin/python3','-I'])
                self.assertNotIn('PYTHONPATH',called.call_args.kwargs['env'])
                self.assertNotIn('LD_PRELOAD',called.call_args.kwargs['env'])
                self.assertFalse(called.call_args.kwargs.get('shell',False))

    def test_allowlisted_commands_only(self):
        self.assertEqual(parse(f'deploy-v2 12 {SHA} 123-1'), ('deploy-v2', '12', SHA, '123-1'))
        self.assertEqual(parse('report-v2 123-2'), ('report-v2', '123-2'))
        for command in ('bash', 'sudo id', 'report-v2 ../etc/passwd', 'report-v2 123-1;id',
                        'report-v2 123-1\n', f'deploy-v2 12 {SHA} 123-1 extra',
                        f'deploy-v2 0 {SHA} 123-1', f'deploy-v2 12 {SHA[:7]} 123-1',
                        f'deploy-v2 12 {SHA} $(id)', 'scp -t /tmp/x', 'sftp', ''):
            with self.subTest(command=command), self.assertRaises(ValueError): parse(command)

    def test_safe_archive_rejects_traversal_server_state_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c.safe_extract(archive({'app/a.py': b'pass\n'}), root)
            self.assertEqual((root / 'app/a.py').read_bytes(), b'pass\n')
            for name in ('../escape', '/tmp/escape', '.env', '.git/config', 'shpakdnd.db',
                         'app/timers.db', '.revision', '.venv/bin/python'):
                with self.subTest(name=name), self.assertRaises(c.Refused):
                    c.safe_extract(archive({name: b'x'}), root)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as tar:
                entry = tarfile.TarInfo('app/link'); entry.type = tarfile.SYMTYPE; entry.linkname = '/etc'
                tar.addfile(entry)
            with self.assertRaises(c.Refused): c.safe_extract(buffer.getvalue(), root)

    def test_online_backup_including_wal_is_exclusive_and_never_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); src = root / 'source.db'; dest = root / 'backup.db'
            database(src)
            with closing(sqlite3.connect(src)) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('INSERT INTO parent VALUES(123)'); writer.commit()
                result = c.backup(src, dest)
                self.assertEqual(result['integrity'], 'ok')
                writer.execute('INSERT INTO parent VALUES(456)'); writer.commit()
            with closing(sqlite3.connect(dest)) as conn:
                self.assertEqual(conn.execute('SELECT * FROM parent').fetchall(), [(123,)])
            with self.assertRaises(FileExistsError): c.backup(src, dest)
            with closing(sqlite3.connect(src)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM parent').fetchone()[0], 2)

    def test_corrupt_missing_fk_and_production_scope_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); db = root / 'test.db'
            with self.assertRaises(FileNotFoundError): c.db_check(db)
            db.write_text('not sqlite')
            with self.assertRaises(sqlite3.DatabaseError): c.db_check(db)
            db.unlink(); database(db, (1, 2))
            with self.assertRaises(c.Refused): c.db_check(db, scope=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('INSERT INTO child VALUES(999)'); conn.commit()
            with self.assertRaises(c.Refused): c.db_check(db)

    def test_topics_are_literal_and_exclusively_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            topics = Path(tmp) / 'topics.py'
            topics.write_text('TOPIC_SETTINGS={-1003883723333:{2:{"mini":True}},-5320347021:{0:{}}}')
            c.topics_scope(topics)
            for bad in ('import os\nTOPIC_SETTINGS={}', 'TOPIC_SETTINGS=__import__("os").environ',
                        'TOPIC_SETTINGS={123:{0:{"mini":True}}}'):
                topics.write_text(bad)
                with self.assertRaises((c.Refused, ValueError)): c.topics_scope(topics)

    def test_fallback_report_and_remote_path_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'server-reports.json').write_text(json.dumps({'../escape': 'bad', 'preflight.log': 'evidence'}))
            result = collect(root)
            self.assertEqual(result['acceptance_ready'], 'FAIL')
            self.assertFalse((root.parent / 'escape').exists())
            self.assertFalse((root / 'preflight.log').exists())

    def test_report_for_another_sha_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'PR':'12','SHA':SHA}):
            root=Path(tmp)
            (root/'report.json').write_text(json.dumps({'pr':12,'sha':'e'*40,'acceptance_ready':'PASS'}))
            (root/'report.md').write_text('stale PASS')
            self.assertEqual(collect(root)['acceptance_ready'],'FAIL')
            self.assertNotIn('stale PASS',(root/'report.md').read_text())

    def test_truncated_ssh_response_still_generates_fail_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'server-reports.json').write_text('{truncated')
            self.assertEqual(collect(root)['acceptance_ready'], 'FAIL')
            self.assertTrue((root / 'report.md').exists())

    def test_container_has_no_network_secrets_docker_socket_or_write_mounts(self):
        result = subprocess.CompletedProcess([], 1, stdout='failed', stderr='')
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.subprocess, 'Popen') as runner:
            runner.return_value.poll.return_value = 1
            runner.return_value.wait.return_value = 1
            with patch.object(c, 'run'), self.assertRaises(c.Refused): c.Controller(None).preflight(Path(tmp), SHA, OLD, 'image@sha256:' + 'a'*64)
            args = runner.call_args.args[0]
            self.assertIn('--network=none', args)
            self.assertIn('--cap-drop=ALL', args)
            self.assertIn('--read-only', args)
            self.assertIn('--user=65534:65534', args)
            self.assertNotIn('/var/run/docker.sock', ' '.join(map(str, args)))
            self.assertTrue(all(str(args[i+1]).endswith(',readonly') for i,x in enumerate(args) if x == '--mount'))

    def test_startup_timeout_kills_client_and_transient_unit_is_in_stop_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.subprocess,'Popen') as started, \
             patch.object(c.time,'monotonic',side_effect=[0,201]):
            started.return_value.poll.return_value=None
            with self.assertRaises(c.Refused):
                c.run(['systemd-run','fixed-startup'],timeout=200,log_path=Path(tmp)/'raw.log')
            started.return_value.kill.assert_called_once()
            self.assertIn(c.STARTUP,c.UNITS)

    def test_container_timeout_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c.subprocess, 'Popen', side_effect=TimeoutError()), patch.object(c, 'run') as runner:
            with self.assertRaises(TimeoutError): c.Controller(None).preflight(Path(tmp), SHA, OLD, 'image')
            self.assertEqual(runner.call_args.args[0], ['docker', 'rm', '-f', 'shpakdnd-preflight'])

    def test_lock_refuses_competing_process(self):
        code = '''import sys
from deploy.staging.controller import lock,Refused
try:
    with lock(sys.argv[1]):
        raise SystemExit(9)
except Refused:
    print('BLOCKED')
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'lock'
            with c.lock(path):
                result = subprocess.run([sys.executable,'-c',code,str(path)],
                                        cwd=Path(__file__).resolve().parents[2],
                                        capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'BLOCKED')

    def test_competing_deployments_use_nonblocking_exclusive_os_lock(self):
        # Real flock on target Linux; equivalent Windows OS lock tests local serialization.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lock'
            with c.lock(path):
                with self.assertRaises(c.Refused):
                    with c.lock(path): self.fail('Concurrent lock acquired')
            with c.lock(path): pass


class ControllerFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.base, self.config, self.state = root/'base', root/'config', root/'state'
        for p in (self.base/'releases', self.config/'editorial', self.state/'data', self.state/'reports'):
            p.mkdir(parents=True)
        (self.state/'reports').chmod(0o700)
        self.old = self.base/'releases'/'old'; self.old.mkdir(); (self.old/'.revision').write_text(OLD)
        if os.name == 'nt':
            # Windows cannot create symlinks without OS privileges. Exercise real directory
            # junctions and file hardlinks locally; Linux CI uses actual POSIX symlinks.
            real_run = subprocess.run
            real_replace = Path.replace
            real_symlink = Path.symlink_to
            def local_link(path, target, target_is_directory=False):
                target = Path(target)
                if target.is_dir():
                    real_run([os.environ['COMSPEC'], '/d', '/c', 'mklink', '/J', str(path), str(target)],
                             check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                else:
                    os.link(target, path)
            def local_replace(path, target):
                target = Path(target)
                if path.is_junction() and target.is_junction():
                    if target.parent != self.base:
                        raise AssertionError('Refuse removal outside temporary test base')
                    os.rmdir(target)
                return real_replace(path, target)
            for method, replacement in [('symlink_to', local_link), ('replace', local_replace)]:
                patcher = patch.object(Path, method, replacement)
                patcher.start(); self.addCleanup(patcher.stop)
        (self.base/'current').symlink_to(self.old, target_is_directory=True)
        self.folder = self.state/'reports'/'123-1'; self.folder.mkdir(mode=0o700)
        database(self.state/'data/shpakdnd.db')
        (self.state/'data/timers.db').touch()
        (self.config/'topics.py').write_text('TOPIC_SETTINGS={-1003883723333:{2:{"mini":True}},-5320347021:{0:{}}}')
        for name, text in (('.env', 'BOT_TOKEN=987654:never-report-this-token'),
                           ('settings.json', json.dumps({'preflight_image':'image@sha256:'+'c'*64, 'test_bot_id':987654})),
                           ('requirements.txt', 'approved'), ('pip-freeze.txt', 'approved==1'), ('runtime.env', 'MINI_EDITORIAL_DIR='+(self.config/'editorial').as_posix()+'\nMINI_CUSTOM_EMOJI_ENABLED=false\nPYTHONDONTWRITEBYTECODE=1')):
            (self.config/name).write_text(text)
        self.trace = []; self.active = False
        self.health_bad = False
        for name,value in [('BASE', self.base), ('CONFIG', self.config), ('STATE', self.state), ('BUNDLE', self.base/'bundle')]:
            patcher = patch.object(c, name, value); patcher.start(); self.addCleanup(patcher.stop)
        for target, kwargs in [('trusted', {'side_effect': lambda p, **kw: Path(p)}),
                               ('run', {'side_effect': self.fake_run}),
                               ('unit_state', {'side_effect': self.unit}),
                               ('stop_stack', {'side_effect': self.stop}),
                               ('check_space', {'return_value': {'status':'PASS','required_mib':100,'free_mib':1000}})]:
            patcher = patch.object(c, target, **kwargs); patcher.start(); self.addCleanup(patcher.stop)
        for target, value in [('socket.gethostname', 'VM-338333'), ('time.sleep', None)]:
            patcher = patch('deploy.staging.controller.' + target, return_value=value); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=archive({'requirements.txt':b'approved', 'app/topics.py':b'production'}), stderr=b''))
        patcher.start(); self.addCleanup(patcher.stop)
        self.controller = c.Controller(None)
        self.controller.evidence = lambda *args: {'ci_run': 42,'ci_attempt':1}
        self.controller.preflight = lambda *args: self.trace.append('preflight') or {'status':'PASS','skip_policy':'PASS'}

    def unit(self, unit):
        result=expected(self.base,self.config,self.state)
        exe=str(self.base/'venv/bin/python')
        tail=' ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
        result.update(ActiveState='active' if self.active and unit==c.BOT else 'inactive',
                      ExecStart='{ path='+exe+' ; argv[]='+exe+' bot.py ; ignore_errors=no'+tail,
                      ExecStartEx='{ path='+exe+' ; argv[]='+exe+' bot.py ; flags='+tail,
                      MainPID='123', NRestarts='1' if self.health_bad else '0')
        return result

    def fake_run(self, args, **kwargs):
        self.trace.append(list(map(str,args)))
        if args[:2] == ['systemctl','start']: self.active=True
        stdout = SHA if 'rev-parse' in args else 'masked' if 'is-enabled' in args else 'approved==1' if 'freeze' in args else 'https://github.com/Aruzento/shpakdnd_bot.git' if 'get-url' in args else str(self.base/'docker') if '--format' in args else '1001' if args[0]=='id' else ''
        if '--get-regexp' in args:
            return subprocess.CompletedProcess(args,1,stdout='',stderr='')
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr='')

    def stop(self):
        self.trace.append('stop'); self.active=False

    def deploy(self):
        return self.controller.deploy('12', SHA, '123-1', self.folder)


class FlowTests(ControllerFixture, unittest.TestCase):
    def test_success_preflight_backup_before_stop_safe_test_configuration_restored(self):
        result = self.deploy()
        self.assertEqual(result['acceptance_ready'], 'PASS', result)
        self.assertLess(self.trace.index('preflight'), self.trace.index('stop'))
        self.assertTrue((self.folder/'before.db').exists())
        self.assertTrue((self.folder/'stopped.db').exists())
        new = (self.base/'current').resolve()
        self.assertEqual((new/'.revision').read_text().strip(), SHA)
        self.assertEqual((new/'app/topics.py').read_text(), (self.config/'topics.py').read_text())
        self.assertTrue((new/'shpakdnd.db').samefile(self.state/'data/shpakdnd.db'))
        self.assertTrue((new/'.env').samefile(self.config/'.env'))
        self.assertTrue((self.folder/'report.md').exists())
        self.assertNotIn('never-report-this-token', (self.folder/'report.json').read_text())

    def test_local_immutable_image_id_requires_no_registry(self):
        (self.config/'settings.json').write_text(json.dumps({'preflight_image':'sha256:'+'c'*64, 'test_bot_id':987654}))
        self.assertEqual(self.deploy()['acceptance_ready'],'PASS')

    def test_mutable_image_tag_never_stops_test(self):
        (self.config/'settings.json').write_text(json.dumps({'preflight_image':'image:latest', 'test_bot_id':987654}))
        self.assertEqual(self.deploy()['acceptance_ready'],'FAIL')
        self.assertNotIn('stop',self.trace)

    def test_credential_bearing_git_remote_never_reaches_preflight(self):
        original=self.fake_run
        def wrong_remote(args,**kwargs):
            if 'get-url' in args:
                return subprocess.CompletedProcess(args,0,stdout='https://token@example.invalid/repo',stderr='')
            return original(args,**kwargs)
        with patch.object(c,'run',side_effect=wrong_remote):
            self.assertEqual(self.deploy()['acceptance_ready'],'FAIL')
        self.assertNotIn('preflight',self.trace)
        self.assertNotIn('stop',self.trace)

    def test_dependency_drift_never_stops_test(self):
        (self.config/'requirements.txt').write_text('changed')
        result = self.deploy()
        self.assertEqual(result['acceptance_ready'],'FAIL')
        self.assertNotIn('stop',self.trace)

    def test_service_hardening_failure_never_stops_test(self):
        original = self.unit
        def weak(unit):
            state=original(unit);state['NoNewPrivileges']='no';return state
        with patch.object(c,'unit_state',side_effect=weak):
            self.assertEqual(self.deploy()['acceptance_ready'],'FAIL')
        self.assertNotIn('stop',self.trace)

    def test_startup_migration_failure_stops_without_restoring_database(self):
        original = self.fake_run
        def fail(args,**kwargs):
            if args[0]=='systemd-run':
                return subprocess.CompletedProcess(args,1,stdout='failure',stderr='')
            return original(args,**kwargs)
        with patch.object(c,'run',side_effect=fail):
            result=self.deploy()
        self.assertEqual(result['acceptance_ready'],'FAIL')
        self.assertFalse(self.active)
        self.assertTrue((self.folder/'stopped.db').exists())
        self.assertEqual((self.base/'current').resolve(),self.base/'releases/123-1')

    def test_red_ci_never_stops_test(self):
        def red(*args): raise c.Refused('CI failed')
        self.controller.evidence = red
        result = self.deploy()
        self.assertEqual(result['acceptance_ready'], 'FAIL')
        self.assertNotIn('stop', self.trace)
        self.assertEqual((self.base/'current').resolve(), self.old)

    def test_preflight_failure_never_stops_test(self):
        def fail(*args): raise c.Refused('preflight failed')
        self.controller.preflight = fail
        self.assertEqual(self.deploy()['acceptance_ready'], 'FAIL')
        self.assertNotIn('stop', self.trace)

    def test_second_ci_check_force_push_never_stops_test(self):
        with patch.object(self.controller, 'evidence', side_effect=[{'ci_run':42}, c.Refused('HEAD changed')]):
            self.assertEqual(self.deploy()['acceptance_ready'], 'FAIL')
        self.assertNotIn('stop', self.trace)

    def test_backup_failure_never_stops_test(self):
        with patch.object(c, 'backup', side_effect=sqlite3.DatabaseError()):
            self.assertEqual(self.deploy()['acceptance_ready'], 'FAIL')
        self.assertNotIn('stop', self.trace)

    def test_wrong_hostname_no_external_commands(self):
        with patch.object(c.socket, 'gethostname', return_value='VM-436996'):
            self.assertEqual(self.deploy()['acceptance_ready'], 'FAIL')
        self.assertEqual(self.trace, [])

    def test_unhealthy_start_leaves_stopped_preserves_current_database_no_rollback(self):
        self.health_bad = True
        result = self.deploy()
        self.assertEqual(result['acceptance_ready'], 'FAIL')
        self.assertEqual(self.trace[-1], 'stop')
        self.assertEqual((self.base/'current').resolve(), self.base/'releases/123-1')
        self.assertTrue((self.state/'data/shpakdnd.db').exists())
        self.assertIn('No code rollback', result['recovery'])
        self.assertIn('systemd_failure',result)
        self.assertTrue((self.folder/'journal.log').exists())

    def test_repeat_request_does_not_redeploy_and_target_change_rejected(self):
        result = self.deploy()
        self.assertEqual(result['acceptance_ready'], 'PASS')
        with patch.object(c.os, 'geteuid', return_value=0, create=True), patch.object(c, 'lock'), patch('builtins.print'):
            self.assertEqual(c.main(['deploy-v2','12',SHA,'123-1']), 0)
            with self.assertRaises(c.Refused): c.main(['deploy-v2','12','e'*40,'123-1'])

    def test_report_operation_never_exposes_backups_or_saved_env(self):
        self.deploy()
        with patch.object(c.os, 'geteuid', return_value=0, create=True), patch('builtins.print') as output:
            c.main(['report-v2','123-1'])
        data = json.loads(output.call_args.args[0])
        self.assertEqual(data['schema_version'],2)
        self.assertNotIn('startup.log',data)
        self.assertNotIn('journal.log',data)
        self.assertNotIn('never-report-this-token', json.dumps(data))
