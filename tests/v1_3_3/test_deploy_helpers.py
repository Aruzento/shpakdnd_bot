"""Deployment IO against temporary SQLite and fake HTTP, never production."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from contextlib import redirect_stdout,redirect_stderr,closing,contextmanager

spec=importlib.util.spec_from_file_location('deploy_helpers',Path(__file__).resolve().parents[2]/'deploy/deploy_helpers.py')
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)


@contextmanager
def database(path):
    with closing(sqlite3.connect(path)) as conn,conn:
        yield conn


class DeployHelperTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.db=self.root/'test.db'
        with database(self.db) as conn:
            conn.execute('CREATE TABLE mini_worlds(id INTEGER PRIMARY KEY,chat_id INTEGER,thread_id INTEGER,enabled INTEGER)')
            conn.executemany('INSERT INTO mini_worlds VALUES(?,?,?,?)',[(1,-100111,22,1),(2,-100222,0,1),(3,-100333,33,0)])
        self.token='123456:fake_secret_token_abcdefghijklmnopqrstuvwxyz'

    def test_enabled_worlds_and_every_topic_receive_notice(self):
        sender=Mock()
        helpers.send_notices(self.db,self.token,'Перерыв',sender=sender)
        self.assertEqual(sender.call_count,2)
        self.assertEqual([c.args[1] for c in sender.call_args_list],[dict(id=1,chat_id=-100111,thread_id=22),dict(id=2,chat_id=-100222,thread_id=0)])

    def test_http_chat_thread_and_non_forum_payload(self):
        for thread in (22,0):
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            response.read.return_value=b'{"ok":true}'
            opener=Mock(return_value=response)
            helpers.telegram_send(self.token,dict(chat_id=-100111,thread_id=thread),'Перерыв',opener=opener)
            req=opener.call_args.args[0];payload=json.loads(req.data)
            self.assertEqual(payload['chat_id'],-100111)
            self.assertEqual(payload['text'],'Перерыв')
            self.assertEqual(payload.get('message_thread_id'),thread or None)
            self.assertEqual(req.method,'POST')

    def test_telegram_error_still_attempts_other_worlds_without_leaking_token(self):
        sender=Mock(side_effect=[RuntimeError(self.token),None])
        with self.assertRaises(helpers.DeployError) as caught:
            helpers.send_notices(self.db,self.token,'Перерыв',sender=sender)
        self.assertEqual(sender.call_count,2)
        self.assertNotIn(self.token,str(caught.exception))
        self.assertIn('1',str(caught.exception))

    def test_transport_and_api_errors_hide_secret_response(self):
        with self.assertRaises(helpers.DeployError) as caught:
            helpers.telegram_send(self.token,dict(chat_id=1,thread_id=2),'Text',opener=Mock(side_effect=RuntimeError(self.token)))
        self.assertNotIn(self.token,str(caught.exception))
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(dict(ok=False,description=self.token)).encode()
        with self.assertRaises(helpers.DeployError) as caught:
            helpers.telegram_send(self.token,dict(chat_id=1,thread_id=2),'Text',opener=Mock(return_value=response))
        self.assertNotIn(self.token,str(caught.exception))

    def test_notice_cli_returns_nonzero_and_never_prints_token(self):
        config=SimpleNamespace(DB_PATH=self.db,TOKEN=self.token)
        out=io.StringIO()
        with patch.object(helpers,'runtime_config',return_value=config),patch.object(helpers,'telegram_send',side_effect=RuntimeError(self.token)),redirect_stdout(out),redirect_stderr(out):
            code=helpers.notice_main(['--project',str(self.root),'--text','Перерыв'])
        self.assertEqual(code,1);self.assertNotIn(self.token,out.getvalue())
        with patch.object(helpers,'runtime_config',return_value=config),patch.object(helpers,'telegram_send'),redirect_stdout(out):
            self.assertEqual(helpers.notice_main(['--project',str(self.root),'--text','Перерыв']),0)

    def test_no_enabled_worlds_explicit_error_does_not_create_db(self):
        with database(self.db) as conn:conn.execute('UPDATE mini_worlds SET enabled=0')
        with self.assertRaisesRegex(helpers.DeployError,'No enabled Mini worlds'):helpers.enabled_worlds(self.db)
        missing=self.root/'missing.db'
        with self.assertRaises(sqlite3.OperationalError):helpers.enabled_worlds(missing)
        self.assertFalse(missing.exists())

    def test_invalid_topic_and_missing_token_rejected(self):
        with database(self.db) as conn:conn.execute('UPDATE mini_worlds SET thread_id=-1 WHERE id=1')
        with self.assertRaisesRegex(helpers.DeployError,'configuration'):helpers.enabled_worlds(self.db)
        with self.assertRaisesRegex(helpers.DeployError,'BOT_TOKEN'):helpers.send_notices(self.db,'','Text')
        with self.assertRaisesRegex(helpers.DeployError,'empty'):helpers.send_notices(self.db,self.token,' ')

    def test_backup_uses_online_api_preserves_source_and_cannot_overwrite(self):
        before=self.db.read_bytes();backup=self.root/'backup.db'
        helpers.backup_database(self.db,backup);helpers.check_database(backup)
        with database(backup) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM mini_worlds').fetchone()[0],3)
        self.assertEqual(self.db.read_bytes(),before)
        with self.assertRaises(FileExistsError):helpers.backup_database(self.db,backup)
        self.assertEqual(self.db.read_bytes(),before)

    def test_backup_includes_committed_wal_data(self):
        with database(self.db) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('INSERT INTO mini_worlds VALUES(4,-100444,44,1)');conn.commit()
            backup=self.root/'wal-backup.db';helpers.backup_database(self.db,backup)
            with database(backup) as copy:self.assertEqual(copy.execute('SELECT COUNT(*) FROM mini_worlds').fetchone()[0],4)

    def test_foreign_key_violation_stops_database_check(self):
        with database(self.db) as conn:
            conn.execute('CREATE TABLE child(world_id INTEGER REFERENCES mini_worlds(id))')
            conn.execute('INSERT INTO child VALUES(10000)')
        with self.assertRaisesRegex(helpers.DeployError,'foreign_key'):helpers.check_database(self.db)

    def test_sqlite_cli_checks_runtime_path_without_migration(self):
        out=io.StringIO();before=self.db.read_bytes()
        with patch.object(helpers,'runtime_config',return_value=SimpleNamespace(DB_PATH=self.db)),redirect_stdout(out):
            self.assertEqual(helpers.sqlite_main(['config','--project',str(self.root),'--db',str(self.db)]),0)
            self.assertEqual(helpers.sqlite_main(['check','--db',str(self.db)]),0)
        self.assertEqual(self.db.read_bytes(),before)
        with patch.object(helpers,'runtime_config',return_value=SimpleNamespace(DB_PATH=self.root/'wrong.db')),redirect_stderr(out):
            self.assertEqual(helpers.sqlite_main(['config','--project',str(self.root),'--db',str(self.db)]),1)
        self.assertIn('does not match',out.getvalue())

    def test_redactor_hides_env_secrets_and_token_like_strings(self):
        env=self.root/'.env';env.write_text('BOT_TOKEN='+self.token+'\nOTHER_SECRET=abcdefghi\nBOT_TIMEZONE=Europe/Moscow\n',encoding='utf-8')
        output=[]
        helpers.redact_output(env,[self.token+' abcdefghi Europe/Moscow 123456:unknown_secret_abcdefghijklmnopqrstuvwxyz\n'],output.append)
        self.assertNotIn(self.token,''.join(output));self.assertNotIn('abcdefghi',''.join(output))
        self.assertNotIn('unknown_secret',''.join(output));self.assertIn('Europe/Moscow',''.join(output))

    def test_runtime_config_reuses_actual_env_loading_in_independent_process(self):
        # Copy the production config unchanged into an isolated project.
        import subprocess,sys
        (self.root/'app').mkdir();(self.root/'app/__init__.py').write_text('')
        config=Path(__file__).resolve().parents[2]/'app/config.py'
        (self.root/'app/config.py').write_bytes(config.read_bytes())
        (self.root/'.env').write_text('BOT_TOKEN='+self.token+'\nBOT_TIMEZONE=Europe/Moscow\n',encoding='utf-8')
        env=dict(os.environ);env.pop('BOT_TOKEN',None)
        script="import importlib.util; s=importlib.util.spec_from_file_location('helper',__import__('sys').argv[1]); h=importlib.util.module_from_spec(s);s.loader.exec_module(h);c=h.runtime_config(__import__('sys').argv[2]); assert c.TOKEN==__import__('sys').argv[3]; print(c.DB_PATH)"
        result=subprocess.run([sys.executable,'-c',script,str(spec.origin),str(self.root),self.token],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('shpakdnd.db',result.stdout);self.assertNotIn(self.token,result.stdout+result.stderr)

    def test_online_backup_deadline_removes_only_partial_destination(self):
        destination=self.root/'partial.db';before=self.db.read_bytes()
        with self.assertRaises(helpers.DeployError):
            helpers.backup_database(self.db,destination,max_seconds=0)
        self.assertFalse(destination.exists());self.assertEqual(self.db.read_bytes(),before)

    def test_interrupted_online_backup_removes_reserved_destination(self):
        destination=self.root/'partial.db';before=self.db.read_bytes()
        source=Mock();source.backup.side_effect=KeyboardInterrupt('SIGTERM fixture')
        with patch.object(helpers,'readonly_db',return_value=source),self.assertRaises(KeyboardInterrupt):
            helpers.backup_database(self.db,destination)
        self.assertFalse(destination.exists());self.assertEqual(self.db.read_bytes(),before)
