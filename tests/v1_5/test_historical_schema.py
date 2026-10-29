"""Historical contracts from owner-supplied DDL, never production SQLite."""
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
import zipfile

from deploy import release_guard as guard

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "historical_v14_nine_tables.json"
LEGACY = "d077dd2a2c29ad420b138bf07af23c77f4674d0b"
PREVIOUS = "cef77cc87f6a2286284f5cf8fdb851c775b04dd1"
CAP = "v15-feedback-ui-stories"


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def historical_copy(source, target, *, historical=True):
    """Rebuild a synthetic bootstrap with exact supplied DDL, copying by name."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as old, \
         closing(sqlite3.connect(target)) as new, new:
        old.execute("PRAGMA query_only=ON")
        tables = old.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for name, sql in tables:
            new.execute(fixture["tables"].get(name, sql) if historical else sql)
        for name, _ in tables:
            columns = ",".join(quoted(r[1]) for r in old.execute("PRAGMA table_info("+quoted(name)+")"))
            rows = old.execute("SELECT "+columns+" FROM "+quoted(name)).fetchall()
            if rows:
                new.executemany("INSERT INTO "+quoted(name)+"("+columns+") VALUES("+",".join("?" for _ in rows[0])+")", rows)
        for name, sql in old.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
            new.execute(fixture["indexes"].get(name, sql) if historical else sql)
        for sql, in old.execute("SELECT sql FROM sqlite_master WHERE type IN ('view','trigger')"):
            new.execute(sql)


class HistoricalSQLite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.workspace.cleanup)
        cls.root = Path(cls.workspace.name)
        cls.source = cls.root / "legacy-source"
        cls.source.mkdir()
        archive = cls.root / "legacy.zip"
        subprocess.run(["git", "archive", "--format=zip", "--output="+str(archive), LEGACY, "app"], cwd=ROOT, check=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(cls.source)
        cls.clean = cls.root / "clean.db"
        boot = """import sys,sqlite3
from pathlib import Path
import app.config as c
c.DB_PATH=Path(sys.argv[1]);c.LEGACY_DB_PATH=c.DB_PATH.with_name('unused.db')
from app.db.schema import init_db
from app.mini.schema import init_mini_db
from app.mini.boss.schema import init_boss_db
init_db();init_mini_db(c.DB_PATH);init_boss_db(c.DB_PATH)
with sqlite3.connect(c.DB_PATH) as db:
 db.execute('UPDATE mini_event_pvp_control SET paused=1');db.commit()
"""
        env = dict(os.environ, BOT_TOKEN="123456:offline-historical-contract")
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-c", boot, str(cls.clean)], cwd=cls.source, env=env, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout+result.stderr)
        cls.historical = cls.root / "historical.db"
        historical_copy(cls.clean, cls.historical)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "shpakdnd.db"
        shutil.copyfile(self.historical, self.db)

    def inspect(self, cap=CAP):
        before = self.db.read_bytes()
        with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro", uri=True)) as conn:
            report = guard.inspect_connection(conn, cap)
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
        self.assertEqual(before, self.db.read_bytes())
        return report

    def mutate_schema(self, before, after):
        # Only this disposable fixture. No production path or writable guard.
        with closing(sqlite3.connect(self.db)) as conn, conn:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql LIKE ?", ("%"+before+"%",)).fetchone()
            self.assertIsNotNone(sql, before)
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute("UPDATE sqlite_master SET sql=replace(sql,?,?) WHERE type='table'", (before,after))
            conn.execute("PRAGMA schema_version=999")
        with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro", uri=True)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_supplied_schema_exact_indexes_and_seven_column_orders(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        baseline = json.loads((ROOT/"deploy/release_schema.json").read_text(encoding="utf-8"))["_baseline"]
        differing = []
        with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro", uri=True)) as old, \
             closing(sqlite3.connect(self.clean.as_uri()+"?mode=ro", uri=True)) as clean:
            for name in fixture["tables"]:
                cols = list(old.execute("PRAGMA table_xinfo("+quoted(name)+")"))
                fresh = list(clean.execute("PRAGMA table_xinfo("+quoted(name)+")"))
                self.assertEqual({r[1]:r[2:] for r in cols}, {r[1]:r[2:] for r in fresh})
                self.assertEqual(list(old.execute("PRAGMA foreign_key_list("+quoted(name)+")")),
                                 list(clean.execute("PRAGMA foreign_key_list("+quoted(name)+")")))
                if [r[1] for r in cols] != [r[1] for r in fresh]:
                    differing.append(name)
                self.assertIn(guard._table_signature(old,name),baseline["table_signatures"][name])
                self.assertIn(guard._exact_table_contract(old,name),baseline["exact_table_contracts"][name])
        self.assertEqual(len(differing),7)
        for cap in guard.CAPABILITIES.values():
            self.assertTrue(self.inspect(cap)["rollback_ready"])

    def test_changed_check_with_identical_pragma_contract_is_blocked(self):
        self.mutate_schema("CHECK (max_hp > 0)", "CHECK (max_hp >= 0)")
        with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_TABLE_CONTRACT"):
            self.inspect()

    def test_partial_predicate_and_collation_are_not_normalized(self):
        for index,table,definition in (
            ("idx_mini_events_active_player","mini_event_sessions","(player_id) WHERE status='resolved'"),
            ("idx_mini_item_uses_operation","mini_item_uses","(player_id,operation_key) WHERE operation_key=''"),
            ("idx_mini_wallet_operation_key","mini_wallet_transactions","(player_id,operation_key COLLATE NOCASE) WHERE operation_key<>''"),
        ):
            with self.subTest(index=index):
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    original = conn.execute("SELECT sql FROM sqlite_master WHERE name=?",(index,)).fetchone()[0]
                    conn.execute("DROP INDEX "+quoted(index))
                    conn.execute("CREATE UNIQUE INDEX "+quoted(index)+" ON "+quoted(table)+definition)
                with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_TABLE_CONTRACT"):
                    self.inspect()
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    conn.execute("DROP INDEX "+quoted(index));conn.execute(original)

    def test_same_pragma_columns_do_not_certify_unknown_table_options(self):
        self.mutate_schema("PRIMARY KEY (boss_id, player_id)", "PRIMARY KEY (boss_id DESC, player_id)")
        with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_TABLE_CONTRACT"):
            self.inspect()

    def test_unreviewed_column_permutation_remains_blocked(self):
        self.mutate_schema("boss_skip_hours INTEGER NOT NULL DEFAULT 4,\n                enabled INTEGER NOT NULL DEFAULT 1",
                           "enabled INTEGER NOT NULL DEFAULT 1,\n                boss_skip_hours INTEGER NOT NULL DEFAULT 4")
        with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_TABLE_CONTRACT"):
            self.inspect()

    def seed_history(self):
        baseline = json.loads((ROOT/"deploy/release_schema.json").read_text(encoding="utf-8"))["_baseline"]
        code = baseline["hero_codes"]["legacy"][0]
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO mini_worlds(id,chat_id) VALUES(61001,-99001)")
            c.execute("INSERT INTO mini_heroes(id,code,name,rarity,race,class_name) VALUES(81001,?,'synthetic','common','fixture','fixture')",(code,))
            c.execute("INSERT INTO mini_players(id,world_id,telegram_user_id,character_name,coins,shards,active_hero_id) VALUES(71001,61001,900123,'synthetic',17,3,81001)")
            c.execute("INSERT INTO mini_player_heroes(player_id,hero_id,stars) VALUES(71001,81001,2)")
            c.execute("INSERT INTO mini_wallet_transactions(player_id,amount,balance_after,reason,operation_key) VALUES(71001,17,17,'synthetic','historical-wallet')")
            c.execute("INSERT INTO mini_bosses(id,world_id,name,max_hp,current_hp,status,created_by_user_id) VALUES(91001,61001,'synthetic',100,0,'completed',900123)")
            c.execute("INSERT INTO mini_boss_participants(boss_id,player_id,queue_position,hero_id,hit_count,total_damage,reward_granted) VALUES(91001,71001,1,81001,3,100,1)")
            c.execute("INSERT INTO mini_event_sessions(id,player_id,game_type,stake,status,start_key) VALUES(101001,71001,'rps',1,'resolved','historical-event')")
            c.execute("INSERT INTO mini_event_requests VALUES(71001,'historical-event',101001)")
            item = c.execute("SELECT id FROM mini_items ORDER BY id LIMIT 1").fetchone()
            if item is None:
                c.execute("INSERT INTO mini_items(id,code,name,category) VALUES(201001,'fixture','synthetic','fixture')")
                item=(201001,)
            c.execute("INSERT INTO mini_item_uses(player_id,item_id,operation_key) VALUES(71001,?,'historical-item')",(item[0],))

    def migrate_current(self):
        from app.mini.schema import init_mini_db
        from app.mini.boss.schema import init_boss_db
        init_mini_db(self.db)
        init_boss_db(self.db)

    def snapshot(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        out={}
        with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro",uri=True)) as c:
            for table in fixture["tables"]:
                # The original columns are selected by name after ALTER as well.
                with closing(sqlite3.connect(self.historical.as_uri()+"?mode=ro",uri=True)) as original:
                    cols=[r[1] for r in original.execute("PRAGMA table_info("+quoted(table)+")")]
                query="SELECT "+",".join(quoted(col) for col in cols)+" FROM "+quoted(table)+" ORDER BY "+",".join(quoted(col) for col in cols)
                out[table]=c.execute(query).fetchall()
        return out

    def test_populated_history_migrates_three_times_and_remains_readonly(self):
        self.seed_history()
        original=self.snapshot()
        for replay in range(3):
            self.migrate_current()
            self.assertEqual(self.snapshot(),original,replay)
            with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro",uri=True)) as c:
                self.assertEqual(c.execute("PRAGMA integrity_check").fetchall(),[("ok",)])
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
            with closing(sqlite3.connect(self.db)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("UPDATE mini_players SET last_seen_at='synthetic-wal'")
                writer.execute("UPDATE mini_players SET last_seen_at=NULL")
                writer.commit()
                files=(self.db,Path(str(self.db)+"-wal"))
                before={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
                reports=[self.inspect(cap) for cap in guard.CAPABILITIES.values()]
                self.assertTrue(all(r["rollback_ready"] for r in reports),reports)
                self.assertEqual(reports,[self.inspect(cap) for cap in guard.CAPABILITIES.values()])
                self.assertEqual(before,{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files})
            self.assertEqual(self.snapshot(),original)

    def test_invalid_shards_and_stars_are_blocked_without_repair(self):
        self.seed_history()
        for table,column in (("mini_players","shards"),("mini_player_heroes","shards"),("mini_player_heroes","stars")):
            with closing(sqlite3.connect(self.db)) as c:
                original=c.execute("SELECT "+quoted(column)+" FROM "+quoted(table)).fetchone()[0]
            for value in (-1,1.5,"not-an-integer",sqlite3.Binary(b"1")):
                with self.subTest(table=table,column=column,value_type=type(value).__name__):
                    with closing(sqlite3.connect(self.db)) as c,c:
                        c.execute("UPDATE "+quoted(table)+" SET "+quoted(column)+"=?",(value,))
                    for cap in guard.CAPABILITIES.values():
                        with self.assertRaisesRegex(guard.GuardError,"INVALID_SHARDS_OR_STARS"):
                            self.inspect(cap)
                    with closing(sqlite3.connect(self.db)) as c,c:
                        c.execute("UPDATE "+quoted(table)+" SET "+quoted(column)+"=?",(original,))

    def test_null_historical_values_fail_closed_without_repair(self):
        self.seed_history()
        for table,column in (("mini_players","shards"),("mini_player_heroes","shards"),("mini_player_heroes","stars")):
            with self.subTest(table=table,column=column):
                copy=Path(self.temp.name)/(table+"-"+column+".db")
                shutil.copyfile(self.db,copy)
                with closing(sqlite3.connect(copy)) as c,c:
                    sql=c.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]
                    c.execute("PRAGMA writable_schema=ON")
                    c.execute("UPDATE sqlite_master SET sql=? WHERE name=?",(sql.replace(column+" INTEGER NOT NULL",column+" INTEGER"),table))
                    c.execute("PRAGMA schema_version=999")
                with closing(sqlite3.connect(copy)) as c,c:
                    c.execute("UPDATE "+quoted(table)+" SET "+quoted(column)+"=NULL")
                    c.execute("PRAGMA writable_schema=ON")
                    c.execute("UPDATE sqlite_master SET sql=? WHERE name=?",(sql,table))
                    c.execute("PRAGMA schema_version=1000")
                before=copy.read_bytes()
                for cap in guard.CAPABILITIES.values():
                    with closing(sqlite3.connect(copy.as_uri()+"?mode=ro",uri=True)) as c:
                        with self.assertRaisesRegex(guard.GuardError,"INTEGRITY"):
                            guard.inspect_connection(c,cap)
                self.assertEqual(before,copy.read_bytes())

    def test_actual_runtime_integer_overflow_is_blocked_not_repaired(self):
        self.seed_history()
        from app.mini.boss.repository import add_kill_shards
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute("UPDATE mini_players SET shards=?",(2**63-1,))
        self.assertTrue(self.inspect()["rollback_ready"])
        with closing(sqlite3.connect(self.db)) as c,c:
            add_kill_shards(c,71001,1)
            self.assertEqual(c.execute("SELECT typeof(shards) FROM mini_players").fetchone()[0],"real")
        for cap in guard.CAPABILITIES.values():
            with self.assertRaisesRegex(guard.GuardError,"INVALID_SHARDS_OR_STARS:mini_players.shards"):
                self.inspect(cap)

    def test_unknown_type_default_foreign_key_and_unique_constraints_block(self):
        cases=(("copies INTEGER NOT NULL DEFAULT 1","copies REAL NOT NULL DEFAULT 1"),
               ("copies INTEGER NOT NULL DEFAULT 1","copies INTEGER NOT NULL DEFAULT 2"),
               ("REFERENCES mini_heroes(id)\n                    ON DELETE RESTRICT","REFERENCES mini_heroes(id)\n                    ON DELETE CASCADE"),
               ("UNIQUE (boss_id, queue_position)","UNIQUE (boss_id, joined_at)"))
        for before,after in cases:
            with self.subTest(change=after):
                shutil.copyfile(self.historical,self.db)
                self.mutate_schema(before,after)
                with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_TABLE_CONTRACT"):
                    self.inspect()

    def test_unreviewed_sql_format_is_not_globally_normalized(self):
        self.mutate_schema("CREATE TABLE mini_worlds (","CREATE TABLE mini_worlds  (")
        with self.assertRaisesRegex(guard.GuardError,"UNKNOWN_TABLE_CONTRACT"):
            self.inspect()

    def test_actual_old_sources_explain_both_partial_index_formats(self):
        import ast
        fixture=json.loads(FIXTURE.read_text(encoding="utf-8"))
        for commit,index in (("55aea187a7c6f23dd010a39f0ca2eb8a6b87716d","idx_mini_events_active_player"),
                             ("c61c4e81e55ed424b0cc410a2d2886ac3c65fbcd","idx_mini_item_uses_operation")):
            source=subprocess.check_output(["git","show",commit+":app/mini/schema.py"],cwd=ROOT).decode("utf-8")
            statements=[node.value for node in ast.walk(ast.parse(source))
                        if isinstance(node,ast.Constant) and isinstance(node.value,str)
                        and "CREATE UNIQUE INDEX" in node.value and index in node.value]
            self.assertEqual(len(statements),1)
            with closing(sqlite3.connect(":memory:")) as c:
                for sql in fixture["tables"].values():c.execute(sql)
                c.execute(statements[0])
                actual=c.execute("SELECT sql FROM sqlite_master WHERE name=?",(index,)).fetchone()[0]
            self.assertEqual(actual,fixture["indexes"][index])

    def test_real_cli_original_and_migrated_contracts_and_blocked_ownership(self):
        self.seed_history()
        project=Path(self.temp.name)/"project"
        subprocess.run(["git","clone","--shared","--no-checkout",str(ROOT),str(project)],check=True,capture_output=True)
        targetdb=project/"shpakdnd.db"
        head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip()
        def check(target,allowed):
            before=targetdb.read_bytes()
            result=subprocess.run([sys.executable,str(ROOT/"deploy/release_guard.py"),
                "--project",str(project),"--db",str(targetdb),"--target",target,"--current",head],
                capture_output=True,text=True,encoding="utf-8")
            self.assertEqual(result.returncode,0 if allowed else 2,result.stdout+result.stderr)
            self.assertEqual(before,targetdb.read_bytes())
            return json.loads(result.stdout)
        for migrated in (False,True):
            if migrated:self.migrate_current()
            shutil.copyfile(self.db,targetdb)
            for sha in (LEGACY,PREVIOUS,head):check(sha,True)
        from app.mini.heroes import sync_hero_catalog
        sync_hero_catalog(targetdb)
        with closing(sqlite3.connect(targetdb)) as c,c:
            hero=c.execute("SELECT id FROM mini_heroes WHERE code='ordo_vallis_secretary'").fetchone()[0]
            c.execute("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(71001,?)",(hero,))
        self.assertIn("BLOCKED_TARGET_HERO_OWNERSHIP_UNSUPPORTED",check(LEGACY,False)["blockers"])
        check(PREVIOUS,True);check(head,True)
        with closing(sqlite3.connect(targetdb)) as c,c:
            sid=c.execute("INSERT INTO story_sessions(chat_id,code,status,snapshot_json,node_id,step,creator_id,created_at) VALUES(-99001,'synthetic','finished',?,'end',1,900123,100)",
                          (json.dumps({"schema_version":2,"visit_policy":"once"}),)).lastrowid
            c.execute("INSERT INTO story_achievements VALUES(900123,'synthetic','end',?,100)",(sid,))
        self.assertIn("BLOCKED_FEEDBACK_STATE_UNSUPPORTED",check(PREVIOUS,False)["blockers"])
        check(head,True)

    def test_partial_or_empty_exact_inventory_fails_closed(self):
        from unittest.mock import patch
        schema=json.loads((ROOT/"deploy/release_schema.json").read_text(encoding="utf-8"))
        for malformed in ({}, {"mini_worlds":[]}, None):
            schema["_baseline"]["exact_table_contracts"]=malformed
            with patch.object(Path,"read_text",return_value=json.dumps(schema)):
                with self.assertRaisesRegex(guard.GuardError,"SCHEMA_INVENTORY"):
                    self.inspect()
