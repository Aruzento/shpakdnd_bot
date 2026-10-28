"""Frozen release compatibility: actual Git, readonly SQLite, no live resources."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deploy import release_guard as guard
from tests.v1_3.support import MiniCase

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = "046f7dc1baee9e08207af72650fb4636b7da2d52"
PREVIOUS = "cef77cc87f6a2286284f5cf8fdb851c775b04dd1"
LEGACY = "d077dd2a2c29ad420b138bf07af23c77f4674d0b"
CAP = "v15-feedback-ui-stories"


class FeedbackCompatibility(MiniCase):
    def setUp(self):
        super().setUp()
        from app.mini.boss.schema import init_boss_db
        init_boss_db(self.db)
        self.sql("UPDATE mini_event_pvp_control SET paused=1")
        story = json.dumps({"schema_version": 2, "visit_policy": "once"})
        self.sid = self.sql("""INSERT INTO story_sessions(chat_id,code,status,snapshot_json,
            node_id,step,creator_id,created_at) VALUES(-99,'fixture','finished',?,'e',1,900123,1)
            RETURNING id""", (story,))[0]["id"]
        self.sql("INSERT INTO story_publications VALUES(?,1,'sent',12)", (self.sid,))

    def inspect(self, cap=CAP):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro", uri=True)) as conn:
            result = guard.inspect_connection(conn, cap)
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE mini_players SET coins=coins+1")
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        return result

    def seed(self, table):
        if table == "mini_private_windows":
            from app.mini.ui import windows
            generation, _ = windows.reserve(1, self.world["id"], 900123, self.db,
                                           actions=[f"mini:home:{self.world['id']}:900123"])
            windows.activate(1, self.world["id"], 900123, generation, -99, 0, 5, self.db)
        elif table == "story_publication_parts":
            self.sql("INSERT INTO story_publication_parts VALUES(?,1,0,12)", (self.sid,))
        elif table == "story_reminders":
            self.sql("INSERT INTO story_reminders VALUES(?,1,'reminder',100,'sent','[900123]',12)", (self.sid,))
        elif table == "story_achievements":
            self.sql("INSERT INTO story_achievements VALUES(900123,'fixture','e',?,100)", (self.sid,))

    def clear_feedback(self):
        for t in ("story_publication_parts", "story_reminders", "story_achievements", "mini_private_windows"):
            self.sql("DELETE FROM "+t)

    def test_empty_and_each_single_and_combined_feedback_journals(self):
        for key in ("share:backfill:one", "share:backfill:two"):
            self.sql("INSERT INTO mini_ui_publications(player_id,operation_key) VALUES(?,?)", (self.pid,key))
        self.assertTrue(self.inspect()["rollback_ready"])
        self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])
        for table in guard.FEEDBACK_SCHEMA:
            self.clear_feedback()
            self.seed(table)
            self.assertTrue(self.inspect()["rollback_ready"], table)
            for cap in ("legacy-pre-v15", "v15-once-raid-pets-economy"):
                self.assertIn("BLOCKED_FEEDBACK_STATE_UNSUPPORTED", self.inspect(cap)["blockers"])
        self.clear_feedback()
        for table in guard.FEEDBACK_SCHEMA:
            self.seed(table)
        first = self.inspect()
        self.assertTrue(first["rollback_ready"])
        self.assertEqual(first, self.inspect())  # restart/reopen, identical data and result
        self.assertTrue(all(first["counts"][t] == 1 for t in guard.FEEDBACK_SCHEMA))

    def test_unknown_capability_is_rejected_even_without_feedback(self):
        for value in ("future", "", None):
            with self.subTest(capability=value), self.assertRaisesRegex(guard.GuardError, "UNKNOWN_CAPABILITY"):
                self.inspect(value)

    def test_missing_feedback_table_and_unknown_columns_fail_closed(self):
        self.sql("DROP TABLE mini_private_windows")
        with self.assertRaisesRegex(guard.GuardError, "PARTIAL_FEEDBACK"):
            self.inspect()

    def test_unknown_feedback_column_including_generated_is_rejected(self):
        self.sql("ALTER TABLE mini_private_windows ADD COLUMN future TEXT GENERATED ALWAYS AS ('x') VIRTUAL")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_FEEDBACK_COLUMNS"):
            self.inspect()

    def test_same_columns_without_primary_key_are_not_a_known_contract(self):
        self.sql("ALTER TABLE mini_private_windows RENAME TO old_window")
        self.sql("CREATE TABLE mini_private_windows AS SELECT * FROM old_window WHERE 0")
        self.sql("DROP TABLE old_window")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_TABLE_CONTRACT"):
            self.inspect()

    def test_missing_baseline_and_unknown_table_or_column_are_rejected(self):
        self.sql("CREATE TABLE unknown_history(id INTEGER)")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_DATABASE_SCHEMA"):
            self.inspect()
        self.sql("DROP TABLE unknown_history")
        self.sql("ALTER TABLE mini_players ADD COLUMN future INTEGER")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_BASE_COLUMNS"):
            self.inspect()
        self.sql("ALTER TABLE mini_players DROP COLUMN future")
        self.sql("DROP TABLE mini_duel_locks")
        with self.assertRaisesRegex(guard.GuardError, "PARTIAL_BASE_SCHEMA"):
            self.inspect()

    def test_unknown_view_and_mutating_trigger_are_rejected(self):
        self.sql("CREATE VIEW unknown_view AS SELECT id FROM mini_players")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_DATABASE_OBJECT"):
            self.inspect()
        self.sql("DROP VIEW unknown_view")
        self.sql("""CREATE TRIGGER unknown_trigger AFTER UPDATE ON mini_players
            BEGIN UPDATE mini_players SET coins=coins+1 WHERE id=NEW.id; END""")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_DATABASE_OBJECT"):
            self.inspect()

    def test_unknown_feedback_payload_is_not_certified(self):
        self.seed("mini_private_windows")
        for raw in ("{}", "[1]", "{"):
            self.sql("UPDATE mini_private_windows SET callbacks_json=?", (raw,))
            self.assertIn("BLOCKED_UNKNOWN_FEEDBACK_STATE:mini_private_windows", self.inspect()["blockers"])
        self.clear_feedback()
        self.seed("story_reminders")
        self.sql("UPDATE story_reminders SET recipients_json='[true]'")
        self.assertIn("BLOCKED_UNKNOWN_FEEDBACK_STATE:story_reminders", self.inspect()["blockers"])

    def test_partial_story_delivery_and_uncertain_reminders_block_all_capabilities(self):
        for table in ("story_publications", "story_reminders", "mini_public_notifications"):
            if table == "story_reminders":
                self.seed(table)
            for status in ("sending", "uncertain"):
                self.sql("UPDATE "+table+" SET status=?", (status,))
                for cap in set(guard.CAPABILITIES.values()):
                    self.assertIn("BLOCKED_MANUAL_DELIVERY_REVIEW:"+table, self.inspect(cap)["blockers"])
            self.sql("UPDATE "+table+" SET status='sent'")

    def test_pending_parent_with_confirmed_parts_blocks_without_sending_again(self):
        self.seed("story_publication_parts")
        self.sql("UPDATE story_publications SET status='pending'")
        self.assertIn("BLOCKED_UNKNOWN_FEEDBACK_STATE:story_publication_parts", self.inspect()["blockers"])

    def test_terminal_real_raid_and_story_receipts_still_block_legacy(self):
        from app.mini.mythic.raid import service
        raid = service.start(self.pid, "baldur_adamantine_colossus", "real-start", self.db, now=100)
        service.cancel(raid["id"], self.world["id"], self.owner, "real-cancel", self.db,
                       confirmed=True, expected_revision=raid["revision"], now=101)
        from app.mini.mythic.wallet import transaction, change
        with transaction(self.db) as conn:
            change(conn, self.pid, 1, "fixture", "plus")
            change(conn, self.pid, -1, "fixture", "minus")
        self.assertTrue(self.inspect()["rollback_ready"])
        self.assertIn("BLOCKED_V15_PERSISTENT_STATE_UNSUPPORTED", self.inspect("legacy-pre-v15")["blockers"])
        self.assertGreater(self.inspect()["counts"]["mini_mythic_raid_audit"], 0)
        self.assertEqual(self.inspect()["counts"]["mini_mythic_ledger"], 2)
        self.assertEqual(self.inspect()["counts"]["universal_balances"], 0)
        self.assertIn("BLOCKED_V15_PERSISTENT_STATE_UNSUPPORTED", self.inspect("legacy-pre-v15")["blockers"])

    def test_unknown_story_raid_pet_and_publication_statuses(self):
        self.sql("INSERT INTO mini_mythic_raids(world_id,template_code,snapshot_json,original_max_hp,max_hp,current_hp,created_by,created_at) VALUES(?,'fixture','{}',100000,100000,100000,900123,1)", (self.world["id"],))
        self.sql("INSERT INTO mini_mythic_pets(player_id,element,location,settled_at) VALUES(?,'moon','reserve',1)", (self.pid,))
        cases = (("story_sessions","status"), ("mini_mythic_raids","status"),
                 ("mini_mythic_pets","location"), ("story_publications","status"),
                 ("mini_public_notifications","status"))
        for table, column in cases:
            old = self.sql("SELECT "+column+" AS value FROM "+table)[0]["value"]
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute("PRAGMA ignore_check_constraints=ON")
                conn.execute("UPDATE "+table+" SET "+column+"='future'")
            self.assertIn("BLOCKED_UNKNOWN_STATUS:"+table, self.inspect()["blockers"])
            self.sql("UPDATE "+table+" SET "+column+"=?", (old,))

    def test_active_raid_and_pet_progress_survive_readonly_checks(self):
        from app.mini.mythic.raid import service
        service.start(self.pid, "baldur_adamantine_colossus", "active", self.db, now=100)
        self.sql("INSERT INTO mini_mythic_pets(player_id,element,location,evolution_pending,settled_at) VALUES(?,'moon','reserve',1,1)", (self.pid,))
        first = self.inspect()
        self.assertTrue(first["rollback_ready"])
        self.assertEqual(first["counts"]["evolution_pending"], 1)
        self.assertEqual(first, self.inspect())


class TargetIdentity(unittest.TestCase):
    def test_exact_three_capabilities_and_live_head(self):
        for sha, cap in ((CANDIDATE,CAP),(PREVIOUS,"v15-once-raid-pets-economy"),(LEGACY,"legacy-pre-v15")):
            self.assertEqual(guard.target_capability(ROOT,sha),cap)
        head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
        self.assertEqual(guard.target_capability(ROOT,head),CAP)
        with patch.dict(os.environ, GIT_DIR="/nonexistent/foreign", GIT_WORK_TREE="/foreign"):
            self.assertEqual(guard.target_capability(ROOT,CANDIDATE),CAP)

    def test_commit_mismatch_unknown_tree_and_non_tree_are_blocked(self):
        for actual, tree, kind in (("f"*40,"cb678fe9f0978bb115a12e27c56a7160d0c94e4c","tree"),
                                   (CANDIDATE,"f"*40,"tree"),
                                   (CANDIDATE,"cb678fe9f0978bb115a12e27c56a7160d0c94e4c","blob")):
            with patch.object(guard,"_git",side_effect=["",actual,tree,kind]), self.assertRaisesRegex(guard.GuardError,"UNREVIEWED"):
                guard.target_capability(ROOT,CANDIDATE)

    def test_real_git_replacement_cannot_impersonate_approved_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp)/"repo"
            subprocess.run(["git","clone","--shared","--bare",str(ROOT),str(repo)],check=True,capture_output=True)
            def git(*args,input=None):
                return subprocess.check_output(["git","-C",str(repo),*args],input=None if input is None else input.encode(),stderr=subprocess.DEVNULL).decode().strip()
            git("config","user.email","fixture@example.invalid");git("config","user.name","Fixture")
            blob=git("hash-object","-w","--stdin",input="unknown runtime")
            app=git("mktree",input=f"100644 blob {blob}\tfile.py\n")
            root=git("mktree",input=f"040000 tree {app}\tapp\n")
            unknown=git("commit-tree",root,"-m","unreviewed")
            with self.assertRaisesRegex(guard.GuardError,"UNREVIEWED"):
                guard.target_capability(repo,unknown)
            git("replace",unknown,CANDIDATE)
            self.assertEqual(git("rev-parse",unknown+":app"),"cb678fe9f0978bb115a12e27c56a7160d0c94e4c")
            with self.assertRaisesRegex(guard.GuardError,"GIT_REPLACEMENTS"):
                guard.target_capability(repo,unknown)

    def test_real_guard_cli_current_binding_readonly_and_wal(self):
        case=FeedbackCompatibility();case.setUp()
        self.addCleanup(case.tearDown)
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)/"project"
            subprocess.run(["git","clone","--shared","--no-checkout",str(ROOT),str(project)],check=True,capture_output=True)
            db=project/"shpakdnd.db";shutil.copyfile(case.db,db)
            head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip()
            def run(target=CANDIDATE,current=head):
                return subprocess.run([sys.executable,str(ROOT/"deploy/release_guard.py"),"--project",str(project),
                    "--db",str(db),"--target",target,"--current",current],capture_output=True,text=True,encoding="utf-8")
            with closing(sqlite3.connect(db)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("UPDATE mini_players SET coins=coins+1");writer.commit()
                files=[db,Path(str(db)+"-wal")]
                before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
                first=run();second=run()
                self.assertEqual(first.returncode,0,first.stdout+first.stderr)
                self.assertEqual(first.stdout,second.stdout)
                self.assertEqual({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},before)
                bad=run(current=LEGACY)
                self.assertEqual(bad.returncode,2,bad.stdout)
                self.assertIn("CURRENT_HEAD_MISMATCH",bad.stdout)
                old=run(target=LEGACY)
                self.assertEqual(old.returncode,2,old.stdout)
                self.assertIn("V15_PERSISTENT_STATE_UNSUPPORTED",old.stdout)
                self.assertEqual(subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip(),head)

    def test_real_frozen_guard_blocks_shell_rollback_without_start_or_restore(self):
        from tests.v1_4_3_1 import test_deploy_confirm as pty
        f=pty.Fixture();f.setUp();self.addCleanup(f.doCleanups)
        database=f.h.project/"shpakdnd.db"
        # Python on Windows resolves git.exe rather than Bash's extensionless
        # fake command. Give both paths the same real immutable commit identity.
        clone=f.h.root/"git-metadata"
        subprocess.run(["git","clone","--shared","--no-checkout",str(ROOT),str(clone)],check=True,capture_output=True)
        shutil.move(str(clone/".git"),str(f.h.project/".git"))
        subprocess.run(["git","-C",str(f.h.project),"update-ref","HEAD",CANDIDATE],check=True)
        (f.h.state/"head").write_text(LEGACY+"\n")
        (f.h.state/"target").write_text(CANDIDATE+"\n")
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("""INSERT INTO story_sessions(chat_id,code,status,snapshot_json,node_id,step,creator_id,created_at)
                VALUES(-99,'fixture','finished','{"schema_version":2,"visit_policy":"once"}','e',1,1,1)""")
        before=database.read_bytes()
        env_before=(f.h.project/".env").read_bytes()
        result,trace=f.h.run_deploy("y\ny\n2\n",FAKE_CHECK_FAIL=1,FAKE_V15_GUARD_REAL=1)
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("BLOCKED_V15_PERSISTENT_STATE_UNSUPPORTED",result.stdout)
        self.assertNotIn("systemctl start",trace)
        self.assertEqual(trace.count(" switch "),1)
        self.assertEqual((f.h.state/"head").read_text().strip(),CANDIDATE)
        self.assertEqual(database.read_bytes(),before)
        self.assertEqual((f.h.project/".env").read_bytes(),env_before)

    def test_actual_legacy_and_previous_v15_bootstraps_match_frozen_inventory(self):
        boot="""import sys,sqlite3
from pathlib import Path
import app.config as config
config.DB_PATH=Path(sys.argv[1]);config.LEGACY_DB_PATH=config.DB_PATH.with_name('unused.db')
from app.db.schema import init_db
from app.mini.schema import init_mini_db
from app.mini.boss.schema import init_boss_db
init_db();init_mini_db(config.DB_PATH);init_boss_db(config.DB_PATH)
with sqlite3.connect(config.DB_PATH) as c:
 c.execute('UPDATE mini_event_pvp_control SET paused=1');c.commit()
"""
        with tempfile.TemporaryDirectory() as temp:
            for sha in (LEGACY,PREVIOUS):
                folder=Path(temp)/sha;folder.mkdir()
                archive=folder/"source.zip"
                subprocess.run(["git","archive","--format=zip","--output="+str(archive),sha,"app"],cwd=ROOT,check=True)
                with zipfile.ZipFile(archive) as source:source.extractall(folder)
                database=folder/"fixture.db"
                env=dict(os.environ,BOT_TOKEN="123456:offline-compatibility-fixture")
                env.pop("PYTHONPATH",None)
                result=subprocess.run([sys.executable,"-c",boot,str(database)],cwd=folder,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                before=database.read_bytes()
                with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as conn:
                    self.assertTrue(guard.inspect_connection(conn,CAP)["rollback_ready"])
                self.assertEqual(database.read_bytes(),before)
                with closing(sqlite3.connect(database)) as conn, conn:
                    conn.execute("DROP TABLE character_profiles")
                with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as conn:
                    with self.assertRaisesRegex(guard.GuardError,"PARTIAL_BASE_SCHEMA"):
                        guard.inspect_connection(conn,CAP)
