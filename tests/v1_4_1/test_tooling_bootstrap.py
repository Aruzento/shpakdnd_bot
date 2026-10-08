"""Atomic tooling bootstrap; real Bash/files, fake root ownership and lock."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from tests.v1_3_3.test_deploy_shell import BASH, ROOT, DISPATCH, shell_path


class ToolingBootstrapTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source';self.source.mkdir()
        for name in ('install-deploy-shpakdnd.sh','deploy-shpakdnd.sh','deploy_helpers.py','sqlite-deploy.py',
                     'telegram-deploy-notice.py','release_preflight.py','preflight_data.py'):
            shutil.copy2(ROOT/'deploy'/name,self.source/name)
        self.bin=self.root/'fake-bin';self.bin.mkdir();self.state=self.root/'state';self.state.mkdir()
        for name in ('id','flock','install'):
            file=self.bin/name;file.write_text(DISPATCH,encoding='utf-8',newline='\n');file.chmod(0o755)
        self.dest=self.root/'installed';self.entry=self.root/'commands'
        self.env=dict(os.environ,FAKE_STATE=shell_path(self.state),SHPAKDND_TOOLING_DEST=shell_path(self.dest),
                      SHPAKDND_TOOLING_BIN=shell_path(self.entry),SHPAKDND_LOCK=shell_path(self.root/'lock'),
                      SHPAKDND_TOOLING_PYTHON=shell_path(sys.executable,preserve_symlink=True))

    def install(self,**flags):
        command='export PATH="'+shell_path(self.bin)+':$PATH"; exec bash "'+shell_path(self.source/'install-deploy-shpakdnd.sh')+'"'
        return subprocess.run([BASH,'-c',command],env=dict(self.env,**flags),capture_output=True,text=True,encoding='utf-8',timeout=30)

    def test_complete_bundle_is_installed_and_reinstallation_is_idempotent(self):
        for _ in range(2):
            result=self.install();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        versions=list((self.dest/'versions').glob('tooling_*'));self.assertEqual(len(versions),1)
        for name in ('release_preflight.py','preflight_data.py','deploy_helpers.py','deploy-shpakdnd.sh'):
            self.assertEqual((versions[0]/name).read_bytes(),(self.source/name).read_bytes())
        self.assertTrue((self.entry/'deploy-shpakdnd').exists())
        if os.name=='posix':
            self.assertEqual((self.entry/'deploy-shpakdnd').resolve(),versions[0]/'deploy-shpakdnd.sh')
        self.assertFalse(list((self.dest/'versions').glob('.install.*')))
        trace=(self.state/'trace').read_text()
        for forbidden in ('systemctl','git switch','telegram','shpakdnd.db'):
            self.assertNotIn(forbidden,trace)

    def test_invalid_new_helper_never_replaces_working_installed_command(self):
        result=self.install();self.assertEqual(result.returncode,0,result.stderr)
        before=(self.entry/'deploy-shpakdnd').read_bytes()
        (self.source/'release_preflight.py').write_text('invalid python syntax!\n')
        failed=self.install();self.assertNotEqual(failed.returncode,0)
        self.assertEqual((self.entry/'deploy-shpakdnd').read_bytes(),before)
        self.assertEqual(len(list((self.dest/'versions').glob('tooling_*'))),1)
        self.assertFalse(list((self.dest/'versions').glob('.install.*')))

    def test_locked_deployment_prevents_bootstrap(self):
        failed=self.install(FAKE_LOCKED='1');self.assertNotEqual(failed.returncode,0)
        self.assertFalse(self.dest.exists());self.assertFalse(self.entry.exists())

    def test_unsafe_versions_path_is_rejected_without_replacing_it(self):
        self.dest.mkdir();file=self.dest/'versions';file.write_text('existing unrelated file')
        failed=self.install();self.assertNotEqual(failed.returncode,0)
        self.assertEqual(file.read_text(),'existing unrelated file');self.assertFalse(self.entry.exists())

    def test_existing_lock_file_is_never_truncated(self):
        lock=self.root/'lock';lock.write_text('existing lock metadata')
        result=self.install();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(lock.read_text(),'existing lock metadata')
