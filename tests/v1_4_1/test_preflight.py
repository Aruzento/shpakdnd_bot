"""Real Git, SQLite, subprocess migrations and fault injection; no real services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
import release_preflight as pref
import preflight_data as data
from deploy_helpers import DeployError, backup_database, check_database
from scripts.release_checks import NETWORK_GUARD, CHECK_NAMES


def connect(path):
    return closing(sqlite3.connect(path))


class SnapshotPreservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.db = self.root / "live.db"
        with connect(self.db) as conn:
            conn.execute("CREATE TABLE mini_players(id INTEGER PRIMARY KEY,coins INTEGER,shards INTEGER)")
            conn.execute("INSERT INTO mini_players VALUES(1,123,45)")
            conn.commit()

    def test_wal_snapshot_contains_committed_transaction_without_live_writes(self):
        with connect(self.db) as source:
            source.execute("PRAGMA journal_mode=WAL")
            source.execute("UPDATE mini_players SET coins=789"); source.commit()
            before = self.db.read_bytes(), Path(str(self.db)+"-wal").read_bytes()
            copy = self.root / "snapshot.db"
            backup_database(self.db, copy)
            with connect(copy) as conn:
                self.assertEqual(conn.execute("SELECT coins FROM mini_players").fetchone()[0], 789)
                conn.execute("UPDATE mini_players SET coins=0"); conn.commit()
            self.assertEqual((self.db.read_bytes(), Path(str(self.db)+"-wal").read_bytes()), before)
            self.assertEqual(source.execute("SELECT coins FROM mini_players").fetchone()[0], 789)

    def test_corrupt_source_fails_and_removes_incomplete_snapshot(self):
        self.db.write_bytes(b"corrupt sqlite")
        copy = self.root / "snapshot.db"
        with self.assertRaises(sqlite3.DatabaseError): backup_database(self.db, copy)
        self.assertFalse(copy.exists())

    def test_source_and_destination_aliases_are_rejected(self):
        with self.assertRaisesRegex(DeployError, "aliases"):
            backup_database(self.db, self.db)
        hardlink = self.root / "alias.db"
        os.link(self.db, hardlink)
        with self.assertRaisesRegex(DeployError, "aliases"):
            backup_database(self.db, hardlink)

    def test_independent_db_rejects_symlink_and_hardlink_alias(self):
        project = self.root / "checkout"; project.mkdir()
        alias = project / "shpakdnd.db"; os.link(self.db, alias)
        with self.assertRaisesRegex(DeployError, "aliases"):
            data.isolated_database(project, alias, self.db)
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(DeployError, "independent checkout"):
                data.isolated_database(project, alias, self.db)

    def test_missing_or_empty_db_cannot_substitute_for_snapshot(self):
        project = self.root / "checkout"; project.mkdir()
        with self.assertRaisesRegex(DeployError, "real database snapshot"):
            data.isolated_database(project, project / "shpakdnd.db", self.db)

    def test_additive_schema_preserves_original_resources(self):
        before = data.inventory_database(self.db)
        with connect(self.db) as conn:
            conn.execute("ALTER TABLE mini_players ADD COLUMN extra TEXT DEFAULT ''")
            conn.execute("CREATE TABLE new_service(id INTEGER)"); conn.commit()
        result = data.compare_copy(before, self.db)
        self.assertEqual(result["new_columns"]["mini_players"], ["extra"])
        self.assertEqual(result["new_tables"], ["new_service"])

    def test_every_feature_state_and_wallet_change_is_rejected(self):
        for table in ("mini_wallet_transactions", "mini_event_sessions", "mini_boss_participants",
                      "mini_tower_progress", "mini_equipment_owned", "mini_duels", "mini_villages",
                      "mini_mythic_fragments", "mini_shadow_rolls"):
            with self.subTest(table=table), connect(self.db) as conn:
                conn.execute(f'CREATE TABLE "{table}"(id INTEGER PRIMARY KEY,payload TEXT)')
                conn.execute(f'INSERT INTO "{table}" VALUES(1,?)', ('persistent-state',)); conn.commit()
                before = data.inventory_database(self.db)
                conn.execute(f'UPDATE "{table}" SET payload=?', ('lost-state',)); conn.commit()
                with self.assertRaisesRegex(DeployError, "user data changed/lost"):
                    data.compare_copy(before, self.db)
                conn.execute(f'DROP TABLE "{table}"'); conn.commit()

    def test_table_column_and_user_row_loss_fail_closed(self):
        before = data.inventory_database(self.db)
        with connect(self.db) as conn:
            conn.execute("DELETE FROM mini_players"); conn.commit()
        with self.assertRaisesRegex(DeployError, "user data changed/lost"):
            data.compare_copy(before, self.db)
        with connect(self.db) as conn:
            conn.execute("DROP TABLE mini_players"); conn.commit()
        with self.assertRaisesRegex(DeployError, "table lost"):
            data.compare_copy(before, self.db)

    def test_only_documented_service_metadata_is_allowed(self):
        with connect(self.db) as conn:
            conn.execute("CREATE TABLE mini_worlds(id INTEGER PRIMARY KEY,chat_id INTEGER,name TEXT,enabled INTEGER)")
            conn.execute("INSERT INTO mini_worlds VALUES(1,-1001,'Old',1)"); conn.commit()
        before = data.inventory_database(self.db)
        with connect(self.db) as conn:
            conn.execute("UPDATE mini_worlds SET name='Configured title'"); conn.commit()
        data.compare_copy(before, self.db)
        strict = data.inventory_database(self.db, strict=True)
        with connect(self.db) as conn:
            conn.execute("UPDATE mini_worlds SET name='Changed twice'"); conn.commit()
        with self.assertRaisesRegex(DeployError, "user data changed/lost"):
            data.compare_copy(strict, self.db, strict=True)


TARGET_CHECK = '''import os, sqlite3, socket
from app.config import DB_PATH
def main():
    assert os.environ['BOT_TOKEN']=='ci-test-token'
    assert 'REAL_PREFLIGHT_SECRET' not in os.environ
    assert not (DB_PATH.parent / '.env').exists()
    with socket.socket() as sock:
        try: sock.connect(('api.telegram.org',443))
        except RuntimeError: pass
        else: raise AssertionError('External network not blocked')
    with sqlite3.connect(DB_PATH) as conn:
        flags = dict(conn.execute('SELECT name,value FROM migration_fixture'))
        existing = conn.execute("SELECT 1 FROM sqlite_master WHERE name='target_marker'").fetchone()
        if 'extra' not in {r[1] for r in conn.execute('PRAGMA table_info(mini_players)')}:
            conn.execute("ALTER TABLE mini_players ADD COLUMN extra TEXT DEFAULT ''")
        conn.execute('CREATE TABLE IF NOT EXISTS target_marker(id INTEGER PRIMARY KEY)')
        conn.execute('INSERT OR IGNORE INTO target_marker VALUES(1)')
        if flags.get('bad_migration'): conn.execute('BROKEN SQL')
        if flags.get('drop_user'): conn.execute('DELETE FROM mini_players')
        if flags.get('repeat_bad') and existing: conn.execute('UPDATE mini_players SET coins=coins+1')
        if flags.get('incompatible'): conn.execute('CREATE TABLE IF NOT EXISTS incompatible_marker(id INTEGER)')
    print('TARGET startup; independent snapshot')
'''
OLD_CHECK = '''import sqlite3
from app.config import DB_PATH
def main():
    with sqlite3.connect(DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='incompatible_marker'").fetchone():
            raise RuntimeError('old startup cannot use target schema')
        assert conn.execute('SELECT coins FROM mini_players').fetchone()[0]==123
    print('OLD startup; rollback copy only')
'''


class PreflightFixtureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "live"; self.project.mkdir()
        self.db = self.project / "shpakdnd.db"
        self.tools = ROOT / "deploy"
        self.evidence = self.root / "evidence" / "result.json"
        self.workspace = self.root / "work"
        self.git("init", "-q"); self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false"); self.git("config", "core.autocrlf", "false")
        self.write(".gitignore", ".env\nshpakdnd.db*\n__pycache__/\n*.pyc\n")
        self.write(".env", "BOT_TOKEN=live-secret-not-copied\nREAL_PREFLIGHT_SECRET=never-copy-this\n")
        self.write("app/__init__.py", "")
        self.write("app/config.py", "from pathlib import Path\nBASE_DIR=Path(__file__).resolve().parents[1]\nDB_PATH=BASE_DIR/'shpakdnd.db'\nLEGACY_DB_PATH=BASE_DIR/'timers.db'\n")
        self.write("bot.py", "# no polling in fixtures\n")
        self.write("check_bot.py", OLD_CHECK); self.commit()
        self.old = self.git("rev-parse", "HEAD")
        self.write("check_bot.py", TARGET_CHECK); self.commit()
        self.stage_a = self.git("rev-parse", "HEAD")
        for path in pref.REQUIRED:
            if not (self.project / path).exists(): self.write(path, "# fixture\n")
        self.write("requirements.txt", "")
        names = repr(list(CHECK_NAMES))
        runner = "NETWORK_GUARD = " + repr(NETWORK_GUARD) + '''
import json, subprocess, sys, tempfile, shutil, unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    checkout=Path(directory)
    shutil.copytree(root/'tests',checkout/'tests')
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-q'],cwd=checkout)
    count=unittest.defaultTestLoader.discover(str(root/'tests')).countTestCases()
    report={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'checks':dict.fromkeys(CHECK_NAMES,'OK'), 'tests':dict(count=count,failures=0,errors=0,skips=0,expected_failures=0,unexpected_successes=0),
            'head_discovered_tests':count,'baseline_discovered_tests':count}
    Path(sys.argv[sys.argv.index('--report')+1]).write_text(json.dumps(report))
    raise SystemExit(result.returncode)
'''
        runner = runner.replace("dict.fromkeys(CHECK_NAMES", "dict.fromkeys("+names)
        self.write("scripts/release_checks.py", runner)
        self.write("tests/test_fixture.py", "import unittest\nclass Fixture(unittest.TestCase):\n def test_first(self): self.assertTrue(True)\n def test_second(self): self.assertEqual(2,2)\n")
        self.commit(); self.target = self.git("rev-parse", "HEAD")
        self.git("branch", "target-fixture", self.target)
        self.git("checkout", "--detach", self.old)
        with connect(self.db) as conn:
            for name in ("mini_players", "mini_worlds", "mini_wallet_transactions", "mini_event_sessions"):
                conn.execute(f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY,coins INTEGER)')
                conn.execute(f'INSERT INTO "{name}" VALUES(1,123)')
            conn.execute("CREATE TABLE migration_fixture(name TEXT PRIMARY KEY,value INTEGER)"); conn.commit()
        self.addCleanup(patch.stopall)
        patch.object(pref, "BASELINE_SHA", self.old).start()
        patch.object(pref, "STAGE_A_SHA", self.stage_a).start()
        self.dependencies = patch.object(pref.Preflight, "dependencies", return_value=sys.executable).start()

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.project), *args], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, path, content):
        target = self.project / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    def commit(self):
        self.git("add", "."); self.git("commit", "-qm", "Fixture state")

    def operation(self, **kwargs):
        return pref.Preflight(project=self.project, db=self.db, old=self.old, target=self.target,
                              evidence=self.evidence, workspace=self.workspace, tools=self.tools, **kwargs)

    def flag(self, name):
        with connect(self.db) as conn:
            conn.execute("INSERT INTO migration_fixture VALUES(?,1)", (name,)); conn.commit()

    def assert_failed(self, operation=None, message=None):
        before = self.db.read_bytes()
        with self.assertRaises((DeployError, OSError, RuntimeError, sqlite3.Error)) as caught:
            (operation or self.operation()).perform()
        if message: self.assertIn(message, str(caught.exception))
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        if self.evidence.exists():
            self.assertEqual(json.loads(self.evidence.read_text())["status"], "FAIL")
        self.assertFalse(list(self.workspace.glob("shpakdnd-preflight-*")))

    def test_preflight_passes_on_exact_target_and_preserves_live_head_database_env(self):
        before = self.db.read_bytes(), (self.project / ".env").read_bytes()
        with patch.dict(os.environ, {"BOT_TOKEN": "live-secret-not-copied", "REAL_PREFLIGHT_SECRET": "never-copy-this"}):
            report = self.operation().perform()
        self.assertEqual(report["status"], "PASS"); self.assertIs(report["rollback_compatible"], True)
        self.assertEqual(report["tests"]["count"], 2)
        self.assertEqual((self.db.read_bytes(), (self.project/".env").read_bytes()), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.old)
        log = Path(report["log_path"]).read_text()
        self.assertIn("TARGET startup", log); self.assertIn("OLD startup", log)
        self.assertNotIn("live-secret-not-copied", log); self.assertNotIn("never-copy-this", log)
        self.assertEqual(pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)["status"], "PASS")
        self.assertFalse(list(self.workspace.iterdir()))

    def test_snapshot_failure_blocks_preflight_and_leaves_live_unchanged(self):
        with patch.object(pref, "backup_database", side_effect=DeployError("snapshot failed")):
            self.assert_failed(message="snapshot failed")

    def test_check_bot_failure_blocks_before_any_live_write(self):
        self.flag("bad_migration"); self.assert_failed(message="command failed")

    def test_user_data_loss_is_rejected(self):
        self.flag("drop_user"); self.assert_failed(message="user data changed/lost")

    def test_repeat_migration_mutation_is_rejected(self):
        self.flag("repeat_bad"); self.assert_failed(message="user data changed/lost")

    def test_rollback_incompatibility_is_proven_on_a_separate_copy(self):
        self.flag("incompatible")
        report = self.operation().perform()
        self.assertEqual(report["status"], "PASS"); self.assertIs(report["rollback_compatible"], False)
        with self.assertRaisesRegex(DeployError, "Rollback is blocked"):
            pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools, rollback_db=self.db)
        with connect(self.db) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='incompatible_marker'").fetchone())

    def test_unittest_or_validator_failure_blocks_release(self):
        for cause in ("unittest failed", "validator failed"):
            with self.subTest(cause=cause), patch.object(pref.Preflight, "release_checks", side_effect=DeployError(cause)):
                self.assert_failed(message=cause)

    def test_dependency_failure_and_no_changes_to_installed_environment(self):
        with patch.object(pref.Preflight, "dependencies", side_effect=DeployError("dependencies incompatible")):
            self.assert_failed(message="dependencies incompatible")

    def test_dirty_source_blocks_before_snapshot(self):
        self.write("local-edit.py", "# dirty\n")
        self.assert_failed(message="dirty")

    def test_missing_or_changed_sha_blocks_before_snapshot(self):
        operation = self.operation(); operation.target = "0"*40
        self.assert_failed(operation)
        operation = self.operation(); operation.old = self.target
        self.assert_failed(operation, "OLD SHA changed")

    def test_workdir_inside_production_is_rejected(self):
        operation = self.operation(); operation.workspace = self.project / "unsafe-work"
        with self.assertRaisesRegex(DeployError, "outside production"):
            operation.perform()
        self.assertFalse(operation.workspace.exists())

    def test_actual_config_cannot_write_to_live_db(self):
        original = pref.Preflight.checkout
        def bad_checkout(operation, destination, sha):
            result = original(operation, destination, sha)
            if destination.name == "target":
                (destination/"app/config.py").write_text("from pathlib import Path\nBASE_DIR=Path(__file__).resolve().parents[1]\nDB_PATH=Path("+repr(str(self.db))+ ")\nLEGACY_DB_PATH=BASE_DIR/'timers.db'\n")
            return result
        with patch.object(pref.Preflight, "checkout", bad_checkout):
            self.assert_failed(message="command failed")

    def test_interruption_removes_resources_and_never_publishes_pass(self):
        with patch.object(pref.Preflight, "release_checks", side_effect=pref.Interrupted("SIGTERM")):
            self.assert_failed(message="SIGTERM")

    def test_repeated_run_uses_a_new_copy_and_reruns_checks(self):
        first = self.operation().perform()
        second = self.operation().perform()
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(self.dependencies.call_count, 2)
        self.assertFalse(list(self.workspace.iterdir()))

    def test_stale_tampered_mismatched_or_incomplete_evidence_is_rejected(self):
        self.operation().perform()
        valid = self.evidence.read_bytes()
        with self.assertRaisesRegex(DeployError, "stale"):
            pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools, now=10**11)
        with self.assertRaisesRegex(DeployError, "SHA/tooling"):
            pref.validate_evidence(self.evidence, old=self.target, target=self.target, tools=self.tools)
        self.evidence.write_bytes(valid + b" ")
        with self.assertRaisesRegex(DeployError, "checksum"):
            pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)
        self.evidence.write_bytes(valid)
        report = json.loads(valid); report["tests"] = None
        pref.save_evidence(self.evidence, report)
        with self.assertRaises(DeployError):
            pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)

    def test_tampered_log_and_tooling_are_rejected(self):
        report = self.operation().perform()
        log = Path(report["log_path"])
        log.write_text(log.read_text()+"tampered\n")
        with self.assertRaisesRegex(DeployError, "log integrity"):
            pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)
        with patch.object(pref, "tooling_hash", return_value="different-tooling"):
            with self.assertRaisesRegex(DeployError, "SHA/tooling"):
                pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)

    def test_unsafe_evidence_permissions_are_rejected(self):
        self.operation().perform()
        self.evidence.chmod(0o666)
        if os.name == "posix":
            with self.assertRaisesRegex(DeployError, "ownership/permissions"):
                pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)
        else:
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(DeployError, "regular file"):
                    pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools)

    def test_normal_live_transactions_do_not_invalidate_pass_evidence(self):
        self.operation().perform()
        with connect(self.db) as conn:
            conn.execute("UPDATE mini_players SET coins=coins+5"); conn.commit()
        report = pref.validate_evidence(self.evidence, old=self.old, target=self.target, tools=self.tools, live_db=self.db)
        self.assertEqual(report["status"], "PASS")

    def test_sigterm_handler_cleans_real_controller_and_never_leaves_pass(self):
        import signal, time
        marker=self.root/'ready'
        script = """import signal, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import release_preflight as pref
pref.BASELINE_SHA=sys.argv[4]; pref.STAGE_A_SHA=sys.argv[6]
def interrupted(signum,frame):raise pref.Interrupted('SIGTERM fixture')
signal.signal(signal.SIGTERM,interrupted)
class Controller(pref.Preflight):
    def dependencies(self,checkout):return sys.executable
    def release_checks(self,checkout,python):
        if sys.platform=='win32':
            Path(sys.argv[9]).write_text('ready')
            signal.raise_signal(signal.SIGTERM)
        self.run_command([sys.executable,'-c',"import pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(120)",sys.argv[9]],env=self.env)
op=Controller(project=sys.argv[2],db=sys.argv[3],old=sys.argv[4],target=sys.argv[5],evidence=sys.argv[7],workspace=sys.argv[8],tools=sys.argv[1])
try:op.perform()
except pref.Interrupted:raise SystemExit(143)
"""
        args=[str(self.tools),str(self.project),str(self.db),self.old,self.target,self.stage_a,
              str(self.evidence),str(self.workspace),str(marker)]
        process=subprocess.Popen([sys.executable,'-c',script,*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+30
            while not marker.exists():
                if process.poll() is not None:
                    out,error=process.communicate();self.fail((out+error).decode('utf-8'))
                if time.monotonic()>deadline:self.fail('Controller never reached release checks')
                time.sleep(0.02)
            if os.name=='posix':process.send_signal(signal.SIGTERM)
            out,error=process.communicate(timeout=15)
            self.assertEqual(process.returncode,143,(out+error).decode('utf-8'))
            self.assertEqual(json.loads(self.evidence.read_text())['status'],'FAIL')
            self.assertFalse(list(self.workspace.glob('shpakdnd-preflight-*')))
            self.assertEqual(self.git('rev-parse','HEAD'),self.old)
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def test_cleanup_also_runs_when_configuration_setup_fails(self):
        with patch('dotenv.dotenv_values',side_effect=RuntimeError('bad configuration')):
            self.assert_failed(message='bad configuration')

    def test_unknown_rollback_result_is_blocked(self):
        report=self.operation().perform()
        report['rollback_compatible']='unknown';pref.save_evidence(self.evidence,report)
        with self.assertRaisesRegex(DeployError,'compatibility is false or unknown'):
            pref.validate_evidence(self.evidence,old=self.old,target=self.target,tools=self.tools,rollback_db=self.db)


if __name__ == "__main__":
    unittest.main()
