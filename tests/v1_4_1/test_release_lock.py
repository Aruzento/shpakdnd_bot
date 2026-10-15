"""The inherited FD cannot substitute another file for the one deployment lock."""
import os,stat,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tests.v1_4_1.test_preflight import pref
import release_state as store
from deploy_helpers import DeployError


class ReleaseLockTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.lock=self.root/'lock';self.lock.write_text('');self.lock.chmod(0o600)
        self.db=self.root/'game.db';self.db.write_text('fixture')
        info=self.lock.stat();uid=getattr(os,'geteuid',lambda:0)()
        self.info=SimpleNamespace(st_uid=uid,st_mode=stat.S_IFREG|0o600,st_dev=info.st_dev,st_ino=info.st_ino)
        self.addCleanup(patch.stopall);patch.object(store,'TRUSTED_UID',uid).start()
        patch.dict(os.environ,SHPAKDND_LOCK=str(self.lock)).start()

    def test_only_configured_lock_inode_is_accepted(self):
        pref.verify_lock_identity(self.info,self.db)
        self.info.st_ino+=1
        with self.assertRaisesRegex(DeployError,'identity'):pref.verify_lock_identity(self.info,self.db)

    def test_db_alias_or_writable_lock_is_rejected(self):
        self.info.st_mode=stat.S_IFREG|0o666
        with self.assertRaises(DeployError):pref.verify_lock_identity(self.info,self.db)
        self.info.st_mode=stat.S_IFREG|0o600
        with self.assertRaises(DeployError):pref.verify_lock_identity(self.info,self.lock)
