"""Staging gate cannot authorize production; only reviewed external topic hook."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.v1_4_1.test_preflight import ROOT
import release_state as store
import staging_topics
import systemd_state as units
from deploy_helpers import DeployError


class StagingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.game=self.root/'game';self.game.mkdir();self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.config=self.runtime/'topics.json';self.config.write_text(json.dumps({'chat_id':-100999111,'thread_id':17}));self.config.chmod(0o600)
        (self.runtime/'staging_topics.py').write_bytes((ROOT/'deploy/staging_topics.py').read_bytes())
        self.hook=self.runtime/'sitecustomize.py';self.hook.write_text('from staging_topics import install; install()')
        self.binding={'project':str(self.game),'units':list(units.UNIT_NAMES)}
        self.environment='SHPAKDND_STAGING_ONLY=1 SHPAKDND_STAGING_TOPICS="'+self.config.as_posix()+'" PYTHONPATH="'+self.runtime.as_posix()+'"'
        self.addCleanup(patch.stopall)
        uid=getattr(os,'geteuid',lambda:0)();patch.object(store,'TRUSTED_UID',uid).start();patch.object(staging_topics,'TRUSTED_UID',uid).start()
        self.show=patch.object(units,'show',side_effect=lambda unit,field:self.environment if field=='Environment' else '').start()
    def test_explicit_staging_runtime_has_bound_hashes(self):
        value=units.staging_identity(self.binding);self.assertEqual(value['environment_kind'],'isolated_staging')
        self.assertEqual(value['topics_hash'],store.digest({'chat_id':-100999111,'thread_id':17}))
    def test_unmarked_bot_cannot_be_staging(self):
        self.environment='';self.assertIsNone(units.staging_identity(self.binding))
    def test_default_production_path_forbidden(self):
        self.binding['project']=str(Path('/opt/shpakdnd-bot').resolve())
        with self.assertRaises(DeployError):units.staging_identity(self.binding)
    def test_unreviewed_hook_is_rejected(self):
        self.hook.write_text('print("unverified startup")')
        with self.assertRaises(DeployError):units.staging_identity(self.binding)
    def test_changed_helper_is_rejected(self):
        (self.runtime/'staging_topics.py').write_text('# different helper')
        with self.assertRaises(DeployError):units.staging_identity(self.binding)
    def test_topic_config_must_be_valid_group_topic(self):
        self.config.write_text('{"chat_id": 123, "thread_id": 0}')
        with self.assertRaises(RuntimeError):units.staging_identity(self.binding)
    def test_helper_import_without_explicit_flag_is_rejected(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(RuntimeError,'explicit'):staging_topics.install(self.config)
    def test_missing_config_is_not_verified(self):
        self.config.unlink()
        with self.assertRaises((DeployError,FileNotFoundError)):units.staging_identity(self.binding)

if __name__=='__main__':unittest.main()
