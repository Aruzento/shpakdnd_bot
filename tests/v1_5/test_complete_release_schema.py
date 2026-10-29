"""Reviewed complete SQLite contracts; owner supplied schemas, no user rows."""
from contextlib import closing
import hashlib,json,shutil,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from deploy import release_guard as guard
from tests.v1_3.support import MiniCase

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=Path(__file__).with_name("fixtures")/"reviewed_schema_metadata.json"
FIXTURE_SHA="e95fb119b9cd6b5593b00a2e38ceed43c6559405b71102092a76b1193cc90b0f"
CAP="v15-feedback-ui-stories"
LEGACY="d077dd2a2c29ad420b138bf07af23c77f4674d0b"
PREVIOUS="cef77cc87f6a2286284f5cf8fdb851c775b04dd1"

def q(name):return '"'+name.replace('"','""')+'"'

def create_snapshot(path,snapshot):
    with closing(sqlite3.connect(path)) as c,c:
        for o in snapshot["objects"]:
            if o["type"]=="table" and not o["name"].startswith("sqlite_"):c.execute(o["sql"])
        for kind in ("index","trigger"):
            for o in snapshot["objects"]:
                if o["type"]==kind and o["sql"] is not None:c.execute(o["sql"])
        c.execute("INSERT INTO mini_event_pvp_control(id,paused) VALUES(1,1)")

def first_check(sql):
    import re
    m=re.search(r"\bCHECK\s*\(",sql,re.I)
    if not m:return None
    start=m.start();i=m.end();depth=1;quote=None
    while i<len(sql):
        ch=sql[i]
        if quote:
            if ch==quote:
                if i+1<len(sql) and sql[i+1]==quote:i+=1
                else:quote=None
        elif ch in ("'",'"'):quote=ch
        elif ch=="(":depth+=1
        elif ch==")":
            depth-=1
            if not depth:return start,i+1
        i+=1
    raise AssertionError("unbalanced reviewed CHECK")

class CompleteContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=FIXTURE.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=FIXTURE_SHA:raise AssertionError("reviewed metadata changed")
        cls.metadata=json.loads(raw)
        cls.workspace=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.workspace.cleanup)
        cls.root=Path(cls.workspace.name)
        cls.profiles={}
        for label,snapshot in cls.metadata["snapshots"].items():
            path=cls.root/(label+".db");create_snapshot(path,snapshot);cls.profiles[label]=path

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"shpakdnd.db"
        shutil.copyfile(self.profiles["migrated_v15"],self.db)

    def inspect(self,cap=CAP):
        before=self.db.read_bytes()
        with closing(sqlite3.connect(self.db.as_uri()+"?mode=ro",uri=True)) as c:
            try:result=guard.inspect_connection(c,cap)
            finally:
                self.assertEqual(c.execute("PRAGMA query_only").fetchone()[0],1)
                self.assertEqual(before,self.db.read_bytes())
        self.assertEqual(before,self.db.read_bytes())
        return result

    def sql(self,sql,args=()):
        with closing(sqlite3.connect(self.db)) as c,c:return c.execute(sql,args).fetchall()

    def schema_change(self,table,change):
        with closing(sqlite3.connect(self.db)) as c,c:
            sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
            changed=change(sql);self.assertNotEqual(changed,sql)
            c.execute("PRAGMA writable_schema=ON")
            c.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",(changed,table))
            c.execute("PRAGMA schema_version=951")

    def blocked(self,code="UNKNOWN_TABLE_CONTRACT"):
        with self.assertRaisesRegex(guard.GuardError,code):self.inspect()

    def seed_player(self):
        self.sql("INSERT INTO mini_worlds(id,chat_id) VALUES(9001,-99001)")
        self.sql("INSERT INTO mini_players(id,world_id,telegram_user_id,character_name) VALUES(9002,9001,900123,'synthetic')")
        legacy=json.loads((ROOT/"deploy/release_schema.json").read_text())["_baseline"]["hero_codes"]["legacy"][0]
        self.sql("INSERT INTO mini_heroes(id,code,name,rarity,race,class_name) VALUES(9003,?,'synthetic','common','fixture','fixture')",(legacy,))
        self.sql("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(9002,9003)")

    def test_reviewed_metadata_old_signatures_indexes_and_all_capabilities(self):
        self.assertTrue(self.metadata["db_wal_hashes_unchanged"])
        for label,source in self.profiles.items():
            shutil.copyfile(source,self.db)
            for cap in guard.CAPABILITIES.values():self.assertTrue(self.inspect(cap)["rollback_ready"],label)
            with closing(sqlite3.connect(self.db)) as c:
                for table,entries in self.metadata["snapshots"][label]["table_indexes"].items():
                    if table.startswith("sqlite_"):continue
                    actual={r[1]:r for r in c.execute("PRAGMA index_list("+q(table)+")")}
                    self.assertEqual(set(actual),{e["index_list"]["name"] for e in entries})
                    for e in entries:
                        name=e["index_list"]["name"]
                        info=[dict(zip(("seqno","cid","name","desc","coll","key"),r)) for r in c.execute("PRAGMA index_xinfo("+q(name)+")")]
                        self.assertEqual(info,e["index_xinfo"])

    def test_added_plain_unique_partial_expression_indexes_are_blocked(self):
        for statement in (
            "CREATE INDEX audit_extra ON mini_players(shards)",
            "CREATE UNIQUE INDEX audit_extra ON mini_players(username)",
            "CREATE INDEX audit_extra ON mini_players(shards) WHERE shards>0",
            "CREATE INDEX audit_extra ON mini_players(lower(username))",
            "CREATE UNIQUE INDEX audit_extra ON mini_players(lower(username))",
            "CREATE UNIQUE INDEX audit_extra ON mini_players(shards) WHERE shards>0",
        ):
            with self.subTest(statement=statement):
                self.sql(statement);self.blocked();self.sql("DROP INDEX audit_extra")

    def test_added_expression_index_blocks_before_valid_credit(self):
        self.seed_player()
        self.sql("CREATE INDEX audit_extra ON mini_players(CASE WHEN shards=1 THEN abs(-9223372036854775808) ELSE shards END)")
        self.blocked()
        self.assertEqual(self.sql("SELECT shards FROM mini_players WHERE id=9002"),[(0,)])

    def test_removal_of_every_declared_index_is_blocked(self):
        indexes=[(o["name"],o["sql"]) for o in self.metadata["snapshots"]["migrated_v15"]["objects"] if o["type"]=="index" and o["sql"]]
        for name,statement in indexes:
            with self.subTest(index=name):
                self.sql("DROP INDEX "+q(name));self.blocked();self.sql(statement)

    def test_nonunique_direction_collation_expression_and_partial_changes_block(self):
        name="idx_mini_players_world"
        original=self.sql("SELECT sql FROM sqlite_master WHERE name=?",(name,))[0][0]
        variants=("world_id DESC,telegram_user_id",
                  "world_id,telegram_user_id COLLATE NOCASE",
                  "world_id,(telegram_user_id+1)",
                  "world_id,telegram_user_id) WHERE shards>0 --")
        for fields in variants:
            with self.subTest(fields=fields):
                self.sql("DROP INDEX "+q(name))
                self.sql("CREATE INDEX "+q(name)+" ON mini_players("+fields+")")
                self.blocked();self.sql("DROP INDEX "+q(name));self.sql(original)

    def test_unique_partial_predicate_remains_exact(self):
        name="idx_mini_item_uses_operation"
        original=self.sql("SELECT sql FROM sqlite_master WHERE name=?",(name,))[0][0]
        self.sql("DROP INDEX "+q(name))
        self.sql("CREATE UNIQUE INDEX "+q(name)+" ON mini_item_uses(player_id,operation_key) WHERE operation_key=''")
        self.blocked()

    def test_missing_each_required_trigger_fails_closed(self):
        triggers=[(o["name"],o["sql"]) for o in self.metadata["snapshots"]["migrated_v15"]["objects"] if o["type"]=="trigger"]
        self.assertEqual(len(triggers),7)
        for name,statement in triggers:
            with self.subTest(trigger=name):
                self.sql("DROP TRIGGER "+q(name));self.blocked("MISSING_DATABASE_OBJECT");self.sql(statement)

    def test_unknown_or_modified_trigger_is_blocked(self):
        self.sql("CREATE TRIGGER audit_extra AFTER INSERT ON mini_players BEGIN SELECT 1;END")
        self.blocked("UNKNOWN_DATABASE_OBJECT");self.sql("DROP TRIGGER audit_extra")
        self.sql("DROP TRIGGER inventory_item_scope_insert")
        self.sql("CREATE TRIGGER inventory_item_scope_insert BEFORE INSERT ON inventory BEGIN SELECT 1;END")
        self.blocked("UNKNOWN_DATABASE_OBJECT")

    def test_legacy_profile_does_not_require_v15_triggers(self):
        shutil.copyfile(self.profiles["original_v14"],self.db)
        self.assertTrue(self.inspect()["rollback_ready"])
        self.sql("DROP TRIGGER dnd_currency_keep_category")
        self.blocked("MISSING_DATABASE_OBJECT")

    def test_mini_only_profile_does_not_require_dnd_objects(self):
        case=MiniCase();case.setUp()
        self.addCleanup(case.doCleanups);self.addCleanup(case.tearDown)
        with closing(sqlite3.connect(case.db)) as c,c:c.execute("UPDATE mini_event_pvp_control SET paused=1")
        old=self.db;self.db=case.db
        try:self.assertTrue(self.inspect()["rollback_ready"])
        finally:self.db=old

    def test_first_check_on_all_tables_cannot_be_weakened(self):
        tables=self.sql("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        count=0
        for table,sql in tables:
            span=first_check(sql)
            if span is None:continue
            with self.subTest(table=table):
                count+=1;self.schema_change(table,lambda text:sql[:span[0]]+"CHECK(1)"+sql[span[1]:])
                self.blocked()
                self.schema_change(table,lambda text:sql)
        self.assertGreaterEqual(count,60)

    def test_removed_inline_check_is_blocked(self):
        self.schema_change("mini_inventory",lambda sql:sql.replace("CHECK (quantity >= 0)",""))
        self.blocked()

    def test_added_check_on_table_without_check_is_blocked(self):
        self.schema_change("mini_items",lambda sql:sql[:sql.rindex(")")]+",CHECK(active IN (0,1))"+sql[sql.rindex(")"):])
        self.blocked()

    def test_weakened_check_cannot_hide_negative_inventory(self):
        self.seed_player()
        self.sql("INSERT INTO mini_items(id,code,name,category) VALUES(9004,'fixture','synthetic','fixture')")
        self.schema_change("mini_inventory",lambda sql:sql.replace("CHECK (quantity >= 0)","CHECK (quantity >= -1)"))
        self.sql("INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(9002,9004,-1)")
        self.blocked()

    def test_invalid_mini_inventory_and_copy_storage_are_blocked(self):
        self.seed_player();self.sql("INSERT INTO mini_items(id,code,name,category) VALUES(9004,'fixture','synthetic','fixture')")
        self.sql("INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(9002,9004,1)")
        for value in (1.5,"not-integer",sqlite3.Binary(b"1")):
            with self.subTest(value_type=type(value).__name__):
                self.sql("UPDATE mini_inventory SET quantity=?",(value,))
                self.blocked("INVALID_MINI_INVENTORY")
                self.sql("UPDATE mini_inventory SET quantity=1")
                self.sql("UPDATE mini_player_heroes SET copies=?",(value,))
                self.blocked("INVALID_MINI_INVENTORY")
                self.sql("UPDATE mini_player_heroes SET copies=1")
        self.sql("UPDATE mini_player_heroes SET copies=-1")
        self.blocked("INVALID_MINI_INVENTORY")

    def test_integrity_foreign_key_and_resource_failures_remain_blocked(self):
        self.seed_player()
        self.sql("PRAGMA ignore_check_constraints=ON")  # connection-scoped; use one explicit writer below
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute("PRAGMA ignore_check_constraints=ON");c.execute("UPDATE mini_players SET shards=-1")
        with self.assertRaisesRegex(guard.GuardError,"INTEGRITY|INVALID_SHARDS_OR_STARS"):self.inspect()
        self.sql("UPDATE mini_players SET shards=0")
        self.sql("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(9002,999999)")
        self.blocked("FOREIGN_KEYS")

    def test_guard_reopens_do_not_change_db_or_wal(self):
        self.seed_player()
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE mini_players SET shards=shards+1");writer.commit()
            files=[self.db,Path(str(self.db)+"-wal")]
            before={str(p):p.read_bytes() for p in files}
            first=self.inspect();self.assertTrue(first["rollback_ready"])
            self.assertEqual(first,self.inspect())
            self.assertEqual(before,{str(p):p.read_bytes() for p in files})
            self.sql("CREATE INDEX audit_extra ON mini_players(shards)")
            before={str(p):p.read_bytes() for p in files}
            self.blocked();self.assertEqual(before,{str(p):p.read_bytes() for p in files})

    def test_incomplete_inventory_cannot_disable_new_checks(self):
        base=json.loads((ROOT/"deploy/release_schema.json").read_text())
        for field in ("exact_table_contracts","object_requirements"):
            changed=json.loads(json.dumps(base));changed["_baseline"][field]={}
            with self.subTest(field=field),patch.object(Path,"read_text",return_value=json.dumps(changed)):
                self.blocked("SCHEMA_INVENTORY")

    def test_original_table_signature_implementation_is_unchanged(self):
        import ast
        old=subprocess.check_output(["git","show","8e894735:deploy/release_guard.py"],cwd=ROOT).decode()
        new=(ROOT/"deploy/release_guard.py").read_text()
        def extract(text):
            return ast.dump(next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=="_table_signature"),include_attributes=False)
        self.assertEqual(extract(old),extract(new))

    def test_real_cli_rejects_missing_trigger_without_checkout_or_db_changes(self):
        project=Path(self.tmp.name)/"project"
        subprocess.run(["git","clone","--shared","--no-checkout",str(ROOT),str(project)],check=True,capture_output=True)
        database=project/"shpakdnd.db";shutil.copyfile(self.db,database)
        head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip()
        def run(target):
            before=database.read_bytes()
            result=subprocess.run([sys.executable,str(ROOT/"deploy/release_guard.py"),"--project",str(project),"--db",str(database),"--target",target,"--current",head],capture_output=True,text=True)
            self.assertEqual(before,database.read_bytes());return result
        for target in (LEGACY,PREVIOUS,head):self.assertEqual(run(target).returncode,0)
        with closing(sqlite3.connect(database)) as c,c:c.execute("DROP TRIGGER mini_boss_timeline_actions")
        for target in (LEGACY,PREVIOUS,head):
            result=run(target);self.assertEqual(result.returncode,2);self.assertIn("MISSING_DATABASE_OBJECT",result.stdout)
        self.assertEqual(subprocess.check_output(["git","rev-parse","HEAD"],cwd=project,text=True).strip(),head)

    def test_null_inventory_and_copy_values_are_blocked_without_repair(self):
        self.seed_player()
        self.sql("INSERT INTO mini_items(id,code,name,category) VALUES(9004,'fixture','synthetic','fixture')")
        self.sql("INSERT INTO mini_inventory(player_id,item_id,quantity) VALUES(9002,9004,1)")
        valid=self.db.read_bytes()
        for table,column in (("mini_inventory","quantity"),("mini_player_heroes","copies")):
            with self.subTest(table=table):
                self.db.write_bytes(valid)
                sql=self.sql("SELECT sql FROM sqlite_master WHERE name=?",(table,))[0][0]
                definition=column+" INTEGER NOT NULL"
                self.schema_change(table,lambda text:text.replace(definition,column+" INTEGER"))
                self.sql("UPDATE "+q(table)+" SET "+q(column)+"=NULL")
                with closing(sqlite3.connect(self.db)) as c,c:
                    c.execute("PRAGMA writable_schema=ON")
                    c.execute("UPDATE sqlite_master SET sql=? WHERE name=?",(sql,table))
                    c.execute("PRAGMA schema_version=952")
                self.blocked("INTEGRITY")
