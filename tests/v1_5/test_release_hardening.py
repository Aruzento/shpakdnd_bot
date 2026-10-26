"""Release hardening: intentional failures, read-only matrix and exact-SHA isolation."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy import release_content as content, release_guard as guard, release_preflight as preflight
from app.stories.catalog import discover
from tests.v1_3_3 import test_deploy_shell as shell
from tests.v1_4_3_1 import test_deploy_confirm as pty
from tests.v1_3.support import MiniCase

ROOT = Path(__file__).resolve().parents[2]
SHA = "cef77cc87f6a2286284f5cf8fdb851c775b04dd1"
LEGACY = "d077dd2a2c29ad420b138bf07af23c77f4674d0b"
PACKAGE = ROOT / "content/stories/unpublished/tayna_propavshey_ovtsy"
AUTHOR = PACKAGE / "tayna_propavshey_ovtsy.json"


class TargetContent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "external"
        self.directory.mkdir()

    def write(self):
        (self.directory / content.REQUIRED_FILE).write_bytes(AUTHOR.read_bytes())

    def reject(self, reason=None, code=1):
        with self.assertRaises(content.ContentError) as caught:
            content.approved_story(self.directory, ROOT, discover)
        self.assertEqual(caught.exception.exit_code, code)
        if reason:
            self.assertIn(reason, str(caught.exception))

    def test_missing_empty_and_required_filename_are_blocked_two(self):
        self.reject(code=2)
        with self.assertRaises(content.ContentError) as caught:
            content.approved_story(self.directory / "missing", ROOT, discover)
        self.assertEqual(caught.exception.exit_code, 2)
        (self.directory / "renamed.json").write_bytes(AUTHOR.read_bytes())
        self.reject("REQUIRED_STORY", 2)

    def test_authored_full_hash_graph_and_additional_v1_story_without_writes(self):
        self.write()
        (self.directory / "additional.json").write_bytes((ROOT / "docs/v1.5/STORY_AUTHOR_TEMPLATE.json").read_bytes())
        before = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in self.directory.iterdir()}
        receipt = content.approved_story(self.directory, ROOT, discover)
        self.assertEqual(len(receipt["codes"]), 2)
        self.assertEqual(before, {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in self.directory.iterdir()})

    def test_malformed_duplicate_hash_schema_and_unexpected_json_fail_one(self):
        self.write()
        original = AUTHOR.read_bytes()
        for data in (b"{", original + b" ", original.replace(b'"schema_version": 2', b'"schema_version": 3')):
            with self.subTest(data=data[:30]):
                (self.directory / content.REQUIRED_FILE).write_bytes(data)
                self.reject()
        self.write()
        (self.directory / "same.json").write_bytes(original)
        self.reject("INVALID_STORY_CATALOG")
        (self.directory / "same.json").write_text('{"manifest":true}')
        self.reject("INVALID_STORY_CATALOG")

    def test_relative_git_file_boundary_and_symlink_are_rejected(self):
        for value in ("relative", ROOT / "content", ROOT):
            with self.subTest(value=value), self.assertRaises(content.ContentError):
                content.external_directory(value, ROOT)
        self.write()
        # Portable negative boundary test; Linux also exercises real symlinks.
        with patch.object(Path, "is_symlink", return_value=True):
            self.reject("SYMLINK")
        if os.name == "posix":
            link = self.directory / "link.json"
            link.symlink_to(AUTHOR)
            self.reject("BOUNDARY")
            link.unlink()
            outside = Path(self.tmp.name) / "linked-directory"
            outside.symlink_to(self.directory, target_is_directory=True)
            with self.assertRaises(content.ContentError):
                content.external_directory(outside, ROOT)

    def test_unreadable_and_overlarge_files_rejected_without_read(self):
        self.write()
        (self.directory / "large.json").write_bytes(b" " * 2000001)
        self.reject("SIZE_OR_PERMISSIONS")
        (self.directory / "large.json").unlink()
        if os.name == "posix":
            file = self.directory / content.REQUIRED_FILE
            file.chmod(0)
            try:
                self.reject("SIZE_OR_PERMISSIONS")
            finally:
                file.chmod(0o600)

    def test_real_cli_exit_two_and_one_never_imports_network_or_opens_live_db(self):
        from tools import validate_release_content as cli
        self.assertEqual(cli.check(self.directory)[1], 2)
        self.write()
        (self.directory / "broken.json").write_text("{")
        with patch("socket.socket.connect", side_effect=AssertionError("No network")), patch("sqlite3.connect", side_effect=AssertionError("No DB")):
            self.assertEqual(cli.check(self.directory)[1], 1)
        (self.directory / "broken.json").unlink()
        # External process contracts tested without repeating costly image decode
        # for each negative fixture. Real strict PASS0 is a separate final/CI gate.
        completed = subprocess.CompletedProcess([], 0, stdout="{}")
        with patch.object(cli.subprocess, "run", return_value=completed) as run:
            report, code = cli.check(self.directory)
        self.assertEqual(code, 0)
        self.assertEqual(report["STRICT_CONTENT_TARGET_PASS"], "PASS")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.kwargs["env"]["STORIES_DIR"], str(self.directory))

    def test_race_during_discovery_fails_closed(self):
        self.write()
        def changed(path):
            found = discover(path)
            (path / "added.json").write_text("{}")
            return found
        with self.assertRaisesRegex(content.ContentError, "CHANGED_DURING_CHECK"):
            content.approved_story(self.directory, ROOT, changed)


class RollbackMatrix(MiniCase):
    def setUp(self):
        super().setUp()
        from app.mini.boss.schema import init_boss_db
        init_boss_db(self.db)
        self.sql("UPDATE mini_event_pvp_control SET paused=1")

    def inspect(self, capability="legacy-pre-v15"):
        before = self.db.read_bytes() if isinstance(self.db, Path) else Path(self.db).read_bytes()
        with closing(sqlite3.connect(Path(self.db).resolve().as_uri()+"?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            report = guard.inspect_connection(conn, capability)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden(id)")
        self.assertEqual(Path(self.db).read_bytes(), before)
        return report

    def story(self, version=2, status="active"):
        story = {"schema_version": version}
        if version == 2:
            story["visit_policy"] = "once"
        return self.sql("INSERT INTO story_sessions(chat_id,code,status,snapshot_json,node_id,step,creator_id,created_at) VALUES(-99,'tayna_propavshey_ovtsy',?,?,'scene_1',1,1,1) RETURNING id", (status, json.dumps(story)))[0]["id"]

    def test_clean_capable_and_legacy_ro_but_unknown_targets_are_never_safe(self):
        self.assertTrue(self.inspect()["rollback_ready"])
        self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])
        self.assertEqual(guard.target_capability(ROOT, SHA), "v15-once-raid-pets-economy")
        self.assertEqual(guard.target_capability(ROOT, LEGACY), "legacy-pre-v15")
        for target in ("bad", "f"*40):
            with self.assertRaises(guard.GuardError):
                guard.target_capability(ROOT, target)
        with patch.object(guard.subprocess, "check_output", side_effect=[SHA+"\n", "f"*40+"\n"]), self.assertRaisesRegex(guard.GuardError, "UNREVIEWED"):
            guard.target_capability(ROOT, SHA)

    def test_active_and_completed_raid_need_capable_code_even_after_drain(self):
        for status in ("active", "rewarded", "cancelled"):
            self.sql("DELETE FROM mini_mythic_raids")
            self.sql("INSERT INTO mini_mythic_raids(world_id,template_code,snapshot_json,status,original_max_hp,max_hp,current_hp,created_by,created_at) VALUES(?,'fixture','{}',?,100000,100000,1,1,1)", (self.world["id"], status))
            result = self.inspect()
            self.assertFalse(result["rollback_ready"])
            self.assertEqual(result["counts"]["mini_mythic_raids:"+status], 1)
            self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])

    def test_v1_v2_registration_votes_and_pending_sending_uncertain(self):
        for version, status in ((1,"active"),(2,"active"),(2,"registration")):
            self.sql("DELETE FROM story_sessions")
            sid = self.story(version, status)
            self.assertFalse(self.inspect()["rollback_ready"])
            self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])
        for status in ("pending", "sending", "uncertain"):
            self.sql("DELETE FROM story_publications")
            self.sql("INSERT INTO story_publications(session_id,step,status) VALUES(?,1,?)", (sid,status))
            self.assertFalse(self.inspect()["rollback_ready"])
            result = self.inspect("v15-once-raid-pets-economy")
            self.assertEqual(result["rollback_ready"], status=="pending")
        self.sql("DELETE FROM story_publications")
        self.sql("UPDATE story_sessions SET snapshot_json='{}' WHERE id=?",(sid,))
        self.assertEqual(self.inspect("v15-once-raid-pets-economy")["counts"]["story_v2:registration"],1)
        self.sql("UPDATE story_sessions SET snapshot_json='{}',code='unknown' WHERE id=?",(sid,))
        self.assertIn("BLOCKED_UNKNOWN_STORY_SNAPSHOT", self.inspect()["blockers"])

    def test_active_reserve_inventory_and_pending_evolution_block_downgrade(self):
        for location in ("active", "reserve", "inventory"):
            self.sql("DELETE FROM mini_mythic_pets")
            self.sql("INSERT INTO mini_mythic_pets(player_id,element,location,active_seconds,evolution_pending,settled_at) VALUES(?,'moon',?,123,1,1)", (self.pid,location))
            result = self.inspect()
            self.assertFalse(result["rollback_ready"])
            self.assertEqual(result["counts"]["evolution_pending"], 1)
            self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])

    def test_ledger_operations_balances_star_and_owned_mythic_block_downgrade(self):
        mutations = [
            ("INSERT INTO mini_mythic_ledger(player_id,operation_key,amount,balance_after,source,payload) VALUES(?,'receipt',1,1,'fixture','{}')", (self.pid,)),
            ("INSERT INTO mini_mythic_operations VALUES(?,'receipt','[]','{}')", (self.pid,)),
            ("UPDATE mini_players SET mythic_shards=1 WHERE id=?", (self.pid,)),
            ("INSERT INTO mini_star_rewards VALUES('tower',1,?,5,1,300,1,'{}')", (self.pid,)),
        ]
        for query, args in mutations:
            self.sql(query, args)
            self.assertFalse(self.inspect()["rollback_ready"])
        self.hero(next(h["code"] for h in json.loads((ROOT/'app/mini/content/heroes/mythic.json').read_text(encoding='utf-8'))["heroes"]))
        self.assertGreater(self.inspect()["counts"]["mythic_ownership"], 0)
        self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])

    def test_pvp_escrow_unknown_schema_status_and_corrupt_db_block(self):
        self.sql("UPDATE mini_event_pvp_control SET paused=0")
        self.assertIn("BLOCKED_PVP_UNRESOLVED", self.inspect()["blockers"])
        self.sql("UPDATE mini_event_pvp_control SET paused=1")
        from app.mini.events import pvp
        self.sql("UPDATE mini_players SET coins=100")
        self.sql("UPDATE mini_event_pvp_control SET paused=0")
        event=pvp.create(self.pid,self.world["id"],"hardening-escrow",self.db,now=1000)
        self.sql("UPDATE mini_event_pvp_control SET paused=1")
        escrow=self.inspect("v15-once-raid-pets-economy")
        self.assertFalse(escrow["rollback_ready"])
        self.assertEqual(escrow["counts"]["pvp:active_stake_coins"],5)
        self.sql("CREATE TABLE mini_mythic_unknown(id)")
        with self.assertRaisesRegex(guard.GuardError, "UNKNOWN_V15_SCHEMA"):
            self.inspect()
        self.sql("DROP TABLE mini_mythic_unknown")
        self.sql("DROP TABLE story_votes")
        with self.assertRaisesRegex(guard.GuardError, "PARTIAL"):
            self.inspect()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"shpakdnd.db"
            path.write_bytes(b"corrupt")
            with closing(sqlite3.connect(path)) as conn, self.assertRaises(sqlite3.DatabaseError):
                guard.inspect_connection(conn, "legacy-pre-v15")
            path.unlink()
            with closing(sqlite3.connect(path)) as conn, self.assertRaisesRegex(guard.GuardError,"EMPTY"):
                guard.inspect_connection(conn, "legacy-pre-v15")

    def test_unknown_status_and_boss_tower_snapshots_are_not_silently_ignored(self):
        from app.mini.boss.service import create_boss_event
        boss = create_boss_event(self.world["id"], "simple_village_guy", 900123, self.db)
        self.assertEqual(self.inspect()["counts"]["mini_bosses:unfinished"], 1)
        self.sql("UPDATE mini_bosses SET rules_version=15 WHERE id=?",(boss["id"],))
        self.assertFalse(self.inspect()["rollback_ready"])
        self.assertTrue(self.inspect("v15-once-raid-pets-economy")["rollback_ready"])
        self.sql("UPDATE mini_bosses SET team_state_json='[]' WHERE id=?",(boss["id"],))
        self.assertIn("BLOCKED_INVALID_COMBAT_SNAPSHOT:mini_bosses",self.inspect()["blockers"])
        self.sql("UPDATE mini_bosses SET status='unrecognized' WHERE id=?",(boss["id"],))
        self.assertIn("BLOCKED_UNKNOWN_COMBAT_STATUS:mini_bosses",self.inspect()["blockers"])
        h=self.hero()
        self.sql("INSERT INTO mini_tower_attempts(player_id,floor,hero_id,hero_json,enemy_json,equipment_bonus,current_hp,runtime_json) VALUES(?,1,?,'{}','{}',0,100,?)",(self.pid,h["id"],json.dumps({"rules_version":15,"team":{"reward_policy":{"version":2}}})))
        self.assertEqual(self.inspect()["counts"]["mini_tower_attempts:unfinished"],1)

    def test_unknown_policy_extra_columns_and_legacy_balances_are_explicit(self):
        self.sql("INSERT INTO mini_mythic_fragments VALUES(?,'legacy_fixture',7)",(self.pid,))
        self.assertEqual(self.inspect()["counts"]["legacy_fragment_balances"],1)
        self.assertTrue(self.inspect()["rollback_ready"])
        self.sql("INSERT INTO mini_mythic_raids(world_id,template_code,snapshot_json,state_json,original_max_hp,max_hp,current_hp,created_by,created_at) VALUES(?,'fixture','{}',?,100000,100000,1,1,1)",(self.world["id"],json.dumps({"mechanic":{"version":999}})))
        self.assertIn("BLOCKED_UNKNOWN_RAID_POLICY",self.inspect("v15-once-raid-pets-economy")["blockers"])
        self.sql("ALTER TABLE mini_mythic_operations ADD COLUMN unknown_migration TEXT")
        with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_V15_COLUMNS"):
            self.inspect()

    def test_guard_cli_ro_project_boundary_and_readonly_bytes(self):
        project = Path(self.tmp.name)/"explicit-project"
        project.mkdir()
        database=project/"shpakdnd.db"
        shutil.copyfile(self.db,database)
        before=database.read_bytes()
        with patch.object(guard,"target_capability",return_value="v15-once-raid-pets-economy"):
            self.assertTrue(guard.inspect(project,database,SHA,SHA)["read_only"])
            with self.assertRaises(guard.GuardError):
                guard.inspect(project,self.db,SHA,SHA)
        self.assertEqual(database.read_bytes(),before)


class ExactSHAIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.project=self.base/"live";self.project.mkdir()
        self.stories=self.base/"stories";self.stories.mkdir()
        (self.stories/content.REQUIRED_FILE).write_bytes(AUTHOR.read_bytes())
        self.workspace=self.base/"shpakdnd-preflight.fixture";self.workspace.mkdir()
        def git(*args):
            return subprocess.check_output(["git","-C",str(self.project),*args],stderr=subprocess.DEVNULL,text=True).strip()
        self.git=git
        git("init");git("config","user.email","fixture@example.invalid");git("config","user.name","Fixture")
        shutil.copytree(ROOT/"deploy",self.project/"deploy",ignore=shutil.ignore_patterns("__pycache__"))
        (self.project/"app").mkdir();(self.project/"app/file.py").write_text("# fixture\n")
        (self.project/".gitignore").write_bytes((ROOT/".gitignore").read_bytes())
        (self.project/".env").write_text("BOT_TOKEN=123456:fixture-only\nSTORIES_DIR="+str(self.stories)+"\n",encoding="utf-8")
        (self.project/".gitattributes").write_bytes((ROOT/".gitattributes").read_bytes())
        git("add","app","deploy",".gitattributes",".gitignore");git("commit","-m","fixture")
        self.sha=git("rev-parse","HEAD");git("update-ref","refs/remotes/origin/main",self.sha)
        self.args=SimpleNamespace(project=str(self.project),workspace=str(self.workspace),sha=self.sha,old_sha=self.sha,stories_directory=str(self.stories),installed=False)

    def prepare(self, exit_code=0, mutate=None):
        real_run=subprocess.run
        commands=[]
        def run(command,**kwargs):
            commands.append((command,kwargs))
            if command[0]=="git":return real_run(command,**kwargs)
            self.assertNotEqual(Path(kwargs["cwd"]),self.project)
            self.assertEqual(preflight.git(kwargs["cwd"],"rev-parse","HEAD"),self.sha)
            self.assertNotIn("REAL_TOKEN",str(kwargs["env"]))
            if mutate:mutate()
            return subprocess.CompletedProcess(command,exit_code)
        with patch.object(preflight.shutil,"disk_usage",return_value=SimpleNamespace(free=8*1024**3)),patch.object(preflight.subprocess,"run",side_effect=run):
            preflight.prepare(self.args)
        return commands

    def test_detached_exact_sha_receipt_live_unchanged_and_repeat_refused(self):
        commands=self.prepare()
        self.assertEqual(self.git("rev-parse","HEAD"),self.sha)
        self.assertEqual(self.git("status","--porcelain"),"")
        self.assertTrue(any("--detach" in command for command, _ in commands))
        preflight.verify(self.args)
        with self.assertRaisesRegex(ValueError,"WORKSPACE_NOT_EMPTY"):
            self.prepare()

    def test_failed_gate_never_authorizes_receipt_and_bad_sha_dirty_disk_block(self):
        with self.assertRaisesRegex(ValueError,"OFFLINE_PREFLIGHT_GATE"):
            self.prepare(exit_code=1)
        with self.assertRaisesRegex(ValueError,"RECEIPT_SHA_OR_RESULT"):
            preflight.verify(self.args)
        self.args.sha="bad"
        with self.assertRaisesRegex(ValueError,"INVALID_SHA"):
            preflight.prepare(self.args)
        self.args.sha=self.sha
        (self.project/"dirty").write_text("user data")
        with self.assertRaisesRegex(ValueError,"DIRTY"):
            preflight.prepare(self.args)
        (self.project/"dirty").unlink()
        with patch.object(preflight.shutil,"disk_usage",return_value=SimpleNamespace(free=1)),self.assertRaisesRegex(ValueError,"DISK_SPACE"):
            preflight.prepare(self.args)

    def test_receipt_target_head_and_story_races_block_before_cutover(self):
        self.prepare()
        receipt=self.workspace/"receipt.json"
        good=receipt.read_bytes()
        data=json.loads(good);data["target_sha"]="f"*40;receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"RECEIPT_SHA"):
            preflight.verify(self.args)
        receipt.write_bytes(good)
        (self.stories/"added.json").write_text("{}")
        with self.assertRaisesRegex(ValueError,"STORIES_CHANGED"):
            preflight.verify(self.args)
        (self.stories/"added.json").unlink()
        self.git("update-ref","refs/remotes/origin/main","HEAD~0")
        (self.project/"app/file.py").write_text("# new\n");self.git("add","app");self.git("commit","-m","new")
        with self.assertRaisesRegex(ValueError,"HEAD_OR_DIRTY"):
            preflight.verify(self.args)
        self.git("update-ref","refs/remotes/origin/main","HEAD")
        self.args.old_sha=self.git("rev-parse","HEAD")
        data=json.loads(good);data["old_sha"]=self.args.old_sha;receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"TARGET_MOVED"):
            preflight.verify(self.args)

    def test_story_change_during_long_checks_is_not_certified(self):
        with self.assertRaisesRegex(ValueError,"STORIES_CHANGED_DURING_PREFLIGHT"):
            self.prepare(mutate=lambda:(self.stories/"new.json").write_text("{}"))
        self.assertFalse((self.workspace/"receipt.json").exists())

    def test_frozen_bundle_mismatch_and_workspace_boundary_are_blocked(self):
        (self.project/"deploy/release_schema.json").write_text("{}")
        self.git("add","deploy");self.git("commit","-m","unreviewed bundle")
        self.sha=self.git("rev-parse","HEAD");self.git("update-ref","refs/remotes/origin/main",self.sha)
        self.args.sha=self.sha;self.args.old_sha=self.sha
        with self.assertRaisesRegex(ValueError,"FROZEN_BUNDLE_TARGET_MISMATCH"):
            self.prepare()
        with self.assertRaisesRegex(ValueError,"WORKSPACE_BOUNDARY"):
            preflight.validate_workspace(self.project,self.project)

    def test_runtime_env_and_systemd_target_are_bound_not_overridden(self):
        original=(self.project/".env").read_bytes()
        config=preflight.configuration(self.project,str(self.stories))
        self.assertEqual(config["directory"],str(self.stories.resolve()))
        other=self.base/"other-stories";other.mkdir()
        with self.assertRaisesRegex(ValueError,"CONFIGURATION_MISMATCH"):
            preflight.configuration(self.project,str(other))
        values={"Environment":shlex.quote("STORIES_DIR="+str(self.stories)),"EnvironmentFiles":str(self.project/".env")+" (ignore_errors=no)","WorkingDirectory":str(self.project),"ExecStart":"{ path=/usr/bin/python ; argv[]=/usr/bin/python bot.py ; ignore_errors=no ; start_time=[Mon] ; stop_time=[n/a] ; pid=7 ; code=(null) ; status=0/0 }","User":"fixture"}
        def value(command,**kwargs):
            if command[1]=="cat": return "# fake.service\n[Service]\nExecStart=python bot.py\n"
            return values[command[3]]+"\n"
        with patch.object(preflight.subprocess,"check_output",side_effect=value):
            running=preflight.configuration(self.project,str(self.stories),"fake.service")["service_signature"]
            self.assertIsNotNone(running)
            before=values["ExecStart"]
            values["ExecStart"]=before.replace("pid=7","pid=0").replace("stop_time=[n/a]","stop_time=[Mon]").replace("code=(null)","code=exited")
            self.assertEqual(preflight.configuration(self.project,str(self.stories),"fake.service")["service_signature"],running)
            values["ExecStart"]=before.replace("bot.py","different.py")
            self.assertNotEqual(preflight.configuration(self.project,str(self.stories),"fake.service")["service_signature"],running)
            values["ExecStart"]=before
            values["Environment"]=shlex.quote("STORIES_DIR="+str(other))
            with self.assertRaisesRegex(ValueError,"SERVICE_STORIES_OVERRIDE"):
                preflight.configuration(self.project,str(self.stories),"fake.service")
            values["Environment"]=""
            values["EnvironmentFiles"]=str(self.base/"foreign.env")+" (ignore_errors=no)"
            with self.assertRaisesRegex(ValueError,"ENVIRONMENT_FILE"):
                preflight.configuration(self.project,str(self.stories),"fake.service")
        self.prepare()
        (self.project/".env").write_bytes(original+b"# config changed during operator review\n")
        with self.assertRaisesRegex(ValueError,"CONFIGURATION_CHANGED"):
            preflight.verify(self.args)
        (self.project/".env").write_bytes(original)
        preflight.verify(self.args)

    def test_workspace_environment_denies_tokens_live_sqlite_and_network(self):
        with patch.dict(os.environ,BOT_TOKEN="REAL_TOKEN",OTHER_SECRET="REAL_SECRET"):
            env=preflight.safe_environment(self.project,self.stories)
        self.assertNotIn("REAL_TOKEN",str(env));self.assertNotIn("OTHER_SECRET",env)
        (self.project/"shpakdnd.db").write_bytes(b"sentinel DB")
        (self.project/".env").write_text("SECRET=sentinel")
        # Real child and actual audit hook: deny Python/SQLite access plus sockets.
        script=self.base/"negative.py"
        script.write_text("""import os,socket,sqlite3,asyncio
from pathlib import Path
root=Path(os.environ['DND_CHECK_DENY_PROJECT'])
for op in (lambda: (root/'.env').read_bytes(),lambda: (root/'shpakdnd.db').read_bytes(),lambda:sqlite3.connect(root/'shpakdnd.db'),lambda:sqlite3.connect((root/'shpakdnd.db').as_uri()+'?mode=ro',uri=True),lambda:socket.create_connection(('127.0.0.1',1))):
 try: op()
 except PermissionError: pass
 else: raise AssertionError('live resource accessible')
# Creating a loop must work on Windows even while external connects fail.
async def probe(): return 7
assert asyncio.run(probe())==7
left,right=socket.socketpair()
try:
 left.sendall(b'ok');assert right.recv(2)==b'ok'
finally:
 left.close();right.close()
try: socket.create_connection(('127.0.0.1',1))
except PermissionError: pass
else: raise AssertionError('ordinary loopback allowed')
print('LIVE_DB_ENV_NETWORK_DENIED_ASYNCIO_SOCKETPAIR_PASS')
""",encoding="utf-8")
        env.update(PYTHONPATH=os.environ.get("PYTHONPATH",""))
        result=subprocess.run([sys.executable,"scripts/run_isolated_checks.py","--label","hardening-sentinel","--",str(script)],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8")
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertIn("LIVE_DB_ENV_NETWORK_DENIED",result.stdout)
        self.assertEqual((self.project/"shpakdnd.db").read_bytes(),b"sentinel DB")


class DeploymentNegativePaths(unittest.TestCase):
    def harness(self):
        h=shell.DeployShellTests();h.setUp();self.addCleanup(h.doCleanups)
        return h

    def test_preflight_intentional_failures_never_stop_change_head_or_backup(self):
        for flag in ("FAKE_PREFLIGHT_FAIL","FAKE_PREFLIGHT_DISK_FAIL","FAKE_PREFLIGHT_SQLITE_FAIL","FAKE_PREFLIGHT_SHA_FAIL","FAKE_PREFLIGHT_CONTENT_FAIL","FAKE_TARGET_MOVED","FAKE_HEAD_MOVED","FAKE_RECEIPT_FAIL"):
            with self.subTest(flag=flag):
                h=self.harness()
                before=(h.project/"shpakdnd.db").read_bytes()
                result,trace=h.run_deploy("y\n",**{flag:1})
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertNotIn("systemctl stop",trace)
                self.assertNotIn("systemctl start",trace)
                self.assertNotIn(" switch ",trace)
                self.assertNotIn(" backup ",trace)
                self.assertEqual((h.state/"shpakdnd-bot.service").read_text().strip(),"active")
                self.assertEqual((h.project/"shpakdnd.db").read_bytes(),before)
                self.assertFalse(list(h.root.glob("shpakdnd-preflight.*")))

    def test_success_before_stop_post_failure_cannot_bypass_v15_guard(self):
        h=self.harness()
        result,trace=h.run_deploy("y\ny\ny\n")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("preflight service=active target="+shell.TARGET,trace)
        self.assertLess(trace.index("-m unittest"),trace.index("systemctl stop"))
        self.assertNotIn("-m unittest",trace[trace.index("systemctl stop"):])
        self.assertEqual(trace.count("systemctl stop shpakdnd-bot.service"),1)
        self.assertFalse(list(h.root.glob("shpakdnd-preflight.*")))
        h=self.harness()
        result,trace=h.run_deploy("y\ny\n2\n",FAKE_CHECK_FAIL=1,FAKE_V15_GUARD_FAIL=1)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("BLOCKED_V15",result.stdout)
        self.assertEqual((h.state/"head").read_text().strip(),shell.TARGET)
        self.assertNotIn("systemctl start",trace)
        self.assertEqual(trace.count(" switch "),1)

    def test_post_receipt_failure_keeps_unverified_version_stopped(self):
        h=self.harness()
        result,trace=h.run_deploy("y\ny\n1\n",FAKE_POST_RECEIPT_FAIL=1)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn("systemctl start",trace)
        h.assert_stopped()

    def test_real_linux_pty_long_preflight_interrupt_and_unsafe_rollback(self):
        if not pty.LINUX:
            h=self.harness()
            result,trace=h.run_deploy("y\ny\n2\n",FAKE_CHECK_FAIL=1,FAKE_V15_GUARD_FAIL=1)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn("systemctl start",trace)
            return
        f=pty.Fixture();f.setUp();self.addCleanup(f.doCleanups)
        f.h.env.update(FAKE_TEST_DELAY="10")
        t=pty.Terminal(f.h);self.addCleanup(t.close)
        t.wait_text("FAKE_LONG_TEST_BEGIN")
        self.assertEqual((f.h.state/"shpakdnd-bot.service").read_text().strip(),"active")
        t.send(chr(3))
        self.assertEqual(t.finish(),130,t.text)
        self.assertNotIn("systemctl stop",f.trace())
        f=pty.Fixture();f.setUp();self.addCleanup(f.doCleanups)
        with closing(sqlite3.connect(f.h.project/"shpakdnd.db")) as conn,conn:
            story=json.loads(AUTHOR.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO story_sessions(chat_id,code,status,snapshot_json,node_id,step,creator_id,created_at) VALUES(-99,?,'active',?,'scene_1',1,1,1)",(story["code"],json.dumps(story)))
        before=(f.h.project/"shpakdnd.db").read_bytes()
        f.h.env.update(FAKE_CHECK_FAIL="1",FAKE_V15_GUARD_REAL="1")
        t=pty.Terminal(f.h);self.addCleanup(t.close)
        f.first_two(t)
        t.wait_text("Выбор [1]:");t.send("2\r\n")
        self.assertEqual(t.finish(),1,t.text)
        self.assertIn("BLOCKED_V15_PERSISTENT_STATE_UNSUPPORTED",t.text)
        self.assertNotIn("systemctl start",f.trace())
        self.assertEqual((f.h.state/"head").read_text().strip(),shell.TARGET)
        self.assertEqual((f.h.project/"shpakdnd.db").read_bytes(),before)

    def test_preflight_only_keeps_active_stack_and_does_not_send_notice(self):
        h=self.harness()
        result,trace=h.run_deploy(preflight_only=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("Preflight-only: PASS",result.stdout)
        self.assertIn("-m unittest",trace)
        for forbidden in ("systemctl stop","systemctl start","telegram-deploy-notice.py"," switch "," backup "):
            self.assertNotIn(forbidden,trace)
        self.assertEqual((h.state/"head").read_text().strip(),shell.OLD)
        self.assertFalse(list(h.root.glob("shpakdnd-preflight.*")))

    def test_game_tree_and_author_payloads_are_unchanged(self):
        tree=subprocess.check_output(["git","rev-parse","HEAD:app"],cwd=ROOT,text=True).strip()
        self.assertEqual(tree,"64823c903daa7e3cea5d12613f364da67f630a54")
        self.assertEqual(hashlib.sha256(AUTHOR.read_bytes()).hexdigest(),content.APPROVED_STORY_SHA256)
        for item in json.loads((ROOT/"docs/v1.5/CONTENT_ASSET_SHA256.json").read_text(encoding="utf-8"))["assets"]:
            self.assertEqual(hashlib.sha256((ROOT/item["path"]).read_bytes()).hexdigest(),item["sha256"])


class MoonNoMechanicChange(MiniCase):
    def test_confirmed_zero_damage_moon_arcana_is_saved_before_damage_without_reroll(self):
        from app.mini.mythic.raid import service as raids
        heroes=json.loads((ROOT/"app/mini/content/heroes/mythic.json").read_text(encoding="utf-8"))["heroes"]
        moon=self.hero(next(h["code"] for h in heroes if h["mythic_element"]=="moon"),stars=1)
        raid=raids.start(self.pid,"baldur_adamantine_colossus","start",self.db,now=100)
        chooser=Mock(side_effect=lambda pool:0 if isinstance(pool[0],int) else pool[0])
        first=raids.attack(self.pid,raid["id"],moon["id"],"moon-zero",self.db,now=100,chooser=chooser)
        self.assertEqual(first["actual_hp_damage"],0)
        self.assertEqual(first["state"]["lunar"]["arcana"],0)
        self.assertEqual(raids.attack(self.pid,raid["id"],moon["id"],"moon-zero",self.db,now=101,chooser=chooser),first)
        before=chooser.call_count
        second=raids.attack(self.pid,raid["id"],moon["id"],"moon-next",self.db,now=86500,chooser=chooser)
        self.assertEqual(second["state"]["lunar"],first["state"]["lunar"])
        self.assertEqual(chooser.call_count,before)


class ProtectedHardeningBundle(unittest.TestCase):
    def test_exact_manifest_immutable_helpers_and_negative_corruption(self):
        from tools import validate_deploy_bundle as bundle
        bundle.validate()
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            for name in bundle.HARDENED | set(bundle.IMMUTABLE) | {"docs/v1.5/RELEASE_HARDENING_BUNDLE_SHA256.json"}:
                path=target/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes((ROOT/name).read_bytes())
            bundle.validate(target)
            (target/"deploy/release_guard.py").write_text("# altered guard")
            with self.assertRaisesRegex(AssertionError,"hash mismatch"):
                bundle.validate(target)
