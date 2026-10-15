"""Run observer on real disposable Git trees; any mutating command is a fault."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from tests.v1_3_3.test_deploy_shell import BASH,ROOT,shell_path


class ObserverSafetyTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.project=self.root/'project';self.project.mkdir();self.bin=self.root/'bin';self.bin.mkdir()
        for name in ('systemctl','chown','sudo','python','python3','deploy-shpakdnd'):
            file=self.bin/name;file.write_text('#!/bin/bash\necho MUTATION >> "$OBSERVER_FORBIDDEN_TRACE"\nexit 99\n',newline='\n');file.chmod(0o755)
        for name,content in {'.gitignore':'.env\n*.db*\n.venv/\n__pycache__/\n','bot.py':'# original bot\n',
                             'app/config.py':'# module\n','app/mini/catalog.json':'{"fixture":1}\n'}.items():
            path=self.project/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        for args in [('init','-q'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),
                     ('config','commit.gpgsign','false'),('config','core.autocrlf','false'),('add','.'),('commit','-qm','Observer fixture')]:
            subprocess.run(['git','-C',str(self.project),*args],check=True,capture_output=True)
        (self.project/'shpakdnd.db').write_bytes(b'protected SQLite fixture')
        (self.project/'.env').write_text('BOT_TOKEN=secret-do-not-load\n')
        self.sha=subprocess.check_output(['git','-C',str(self.project),'rev-parse','HEAD'],text=True).strip()
        self.index=(self.project/'.git/index').read_bytes()
        self.trace=self.root/'forbidden.log'
        self.env=dict(os.environ,SHPAKDND_PROJECT=shell_path(self.project),OBSERVER_FORBIDDEN_TRACE=shell_path(self.trace))

    def observe(self):
        command='export PATH='+shlex.quote(shell_path(self.bin))+':$PATH; exec bash '+shlex.quote(shell_path(ROOT/'deploy/update-shpakdnd-bot.sh'))
        result=subprocess.run([BASH,'-c',command],env=self.env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('HEAD='+self.sha,result.stdout);self.assertNotIn('secret-do-not-load',result.stdout)
        self.assertFalse(self.trace.exists())
        self.assertEqual((self.project/'.git/index').read_bytes(),self.index)
        self.assertEqual((self.project/'shpakdnd.db').read_bytes(),b'protected SQLite fixture')
        self.assertEqual((self.project/'.env').read_text(),'BOT_TOKEN=secret-do-not-load\n')
        return result

    def test_bot_file_change_only_reports_dirty_without_restart(self):
        (self.project/'bot.py').write_text('manual WinSCP copy with invalid syntax!')
        self.assertIn('dirty=yes',self.observe().stdout)

    def test_app_file_change_does_not_import_or_compile_or_restart(self):
        (self.project/'app/config.py').write_text('raise RuntimeError("must never import")')
        self.assertIn('dirty=yes',self.observe().stdout)
        self.assertFalse(list(self.project.rglob('*.pyc')))

    def test_json_catalog_change_never_runs_migrations(self):
        (self.project/'app/mini/catalog.json').write_text('invalid JSON upload')
        self.assertIn('dirty=yes',self.observe().stdout)

    def test_clean_project_only_emits_diagnostic(self):
        self.assertIn('dirty=no',self.observe().stdout)

    def test_repeated_events_never_modify_or_retrigger_own_files(self):
        initial={str(p.relative_to(self.project)):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        for _ in range(3):self.observe()
        final={str(p.relative_to(self.project)):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        self.assertEqual(initial,final)

    def test_event_during_preflight_cannot_interfere_with_lock_or_services(self):
        (self.root/'deployment.lock').write_text('held by preflight')
        self.observe();self.assertEqual((self.root/'deployment.lock').read_text(),'held by preflight')

    def test_event_after_checkout_does_not_start_second_deployment(self):
        (self.project/'bot.py').write_text('# approved new checkout\n')
        for args in [('add','.'),('commit','-qm','Target checkout')]:
            subprocess.run(['git','-C',str(self.project),*args],check=True,capture_output=True)
        self.sha=subprocess.check_output(['git','-C',str(self.project),'rev-parse','HEAD'],text=True).strip()
        self.index=(self.project/'.git/index').read_bytes()
        self.assertIn('dirty=no',self.observe().stdout)

    def test_fsmonitor_config_cannot_execute_external_mutator(self):
        marker=self.root/'fsmonitor-was-called'
        hook=self.root/'fsmonitor.sh';hook.write_text('#!/bin/bash\ntouch '+shlex.quote(shell_path(marker))+'\n',newline='\n');hook.chmod(0o755)
        subprocess.run(['git','-C',str(self.project),'config','core.fsmonitor',shell_path(hook)],check=True)
        self.observe();self.assertFalse(marker.exists())

    def test_ignored_runtime_files_do_not_become_watch_rules(self):
        text=(ROOT/'deploy/shpakdnd-bot-watch.path').read_text(encoding='utf-8')
        for line in text.splitlines():
            if line.startswith('PathModified='):
                for forbidden in ('.env','.db','.venv','__pycache__','preflight','backups','evidence'):
                    self.assertNotIn(forbidden,line)

    def test_unit_executes_trusted_helper_outside_watched_checkout(self):
        text=(ROOT/'deploy/shpakdnd-bot-update.service').read_text(encoding='utf-8')
        self.assertIn('ExecStart=/usr/local/lib/shpakdnd-observer/versions/@SOURCE_SHA@/',text)
        self.assertIn('User=shpakbot',text);self.assertIn('ProtectSystem=strict',text)
        self.assertIn('NoNewPrivileges=yes',text);self.assertNotIn('ExecStart=/opt/shpakdnd-bot/',text)
