"""Authored v2 route contracts, group SQLite journal and offline Telegram integration."""

import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from app.stories import catalog, schema, service
from app.stories.presentation import render
from app.stories.routing import available_choices, ending_reachable_nodes
from tests.v1_3.support import MiniCase
from tests.v1_5 import test_completion_audit as audit

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "content/stories/unpublished/tayna_propavshey_ovtsy"
PAYLOAD = PACKAGE / "tayna_propavshey_ovtsy.json"


def author_story():
    return catalog.loads(PAYLOAD.read_text(encoding="utf-8"))


class StoryOnceCatalog(unittest.TestCase):
    def test_author_source_exact_text_choices_order_targets_and_manifest(self):
        story = author_story()
        source = (PACKAGE / "AUTHOR_SOURCE.md").read_text(encoding="utf-8")
        sections = re.findall(r"^### ((?:Сцена \d+|Концовка К\d+)\.[^\n]*)\n(.*?)(?=^### |\Z)", source, re.M | re.S)
        actual = {}
        for heading, body in sections:
            body = re.split(r"^---|^## ", body, maxsplit=1, flags=re.M)[0].strip()
            ending = heading.startswith("Концовка")
            number = re.search(r"(?:Сцена |Концовка К)(\d+)", heading)[1]
            nid = ("ending_k" if ending else "scene_") + number
            if ending:
                actual[nid] = {"text": heading + "\n\n" + body.replace("**", ""), "end": True}
            else:
                text, options = body.split("**Выборы:**")
                choices = []
                for index, line in enumerate(options.strip().splitlines(), 1):
                    match = re.fullmatch(r"- (.*?) → \*\*(сцена |концовка К)(\d+)\*\*\.", line)
                    self.assertIsNotNone(match, line)
                    choices.append({"id": "choice_" + str(index), "text": match[1],
                                    "next": ("scene_" if match[2] == "сцена " else "ending_k") + match[3]})
                actual[nid] = {"text": heading + "\n\n" + text.strip().replace("**", ""), "choices": choices}
        self.assertEqual(len(actual), 53)
        self.assertEqual(story["nodes"], actual)
        self.assertEqual(sum(len(n.get("choices", [])) for n in actual.values()), 113)
        for name, digest in json.loads((PACKAGE / "MANIFEST.json").read_text()).items():
            self.assertEqual(hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest(), digest, name)

    def test_v1_dag_contract_and_v2_cycles_once(self):
        story = author_story()
        self.assertIs(catalog.validate(story), story)
        self.assertEqual(ending_reachable_nodes(story), set(story["nodes"]))
        story["schema_version"] = 1
        del story["visit_policy"]
        with self.assertRaisesRegex(ValueError, "cycles"):
            catalog.validate(story)
        v1 = json.loads((ROOT / "docs/v1.5/STORY_AUTHOR_TEMPLATE.json").read_text(encoding="utf-8"))
        self.assertEqual(len(available_choices(v1, v1["start_node"], set(v1["nodes"]))),
                         len(v1["nodes"][v1["start_node"]]["choices"]))
        catalog.validate(v1)

    def test_strict_fields_types_targets_limits_and_traps(self):
        changes = [lambda s: s.update(schema_version=True), lambda s: s.pop("visit_policy"),
                   lambda s: s.update(visit_policy="always"), lambda s: s.update(award=1),
                   lambda s: s["nodes"]["scene_1"].update(script="x"),
                   lambda s: s["nodes"]["scene_1"]["choices"][0].update(next="missing"),
                   lambda s: s["nodes"]["scene_1"]["choices"][0].update(items=[]),
                   lambda s: s["nodes"].update(ghost={"text": "Unreachable", "end": True}),
                   lambda s: s.update(title="😀" * 76),
                   lambda s: s["nodes"]["scene_1"].update(text="😀" * 1751),
                   lambda s: s["nodes"]["scene_1"]["choices"][0].update(text="😀" * 61),
                   lambda s: s["nodes"]["ending_k1"].update(end=1),
                   lambda s: s["nodes"]["ending_k1"].update(choices={}),
                   lambda s: s["nodes"]["scene_1"].update(choices=s["nodes"]["scene_1"]["choices"] * 7),
                   lambda s: s["nodes"]["scene_1"]["choices"][1].update(id="choice_1")]
        for change in changes:
            data = author_story()
            change(data)
            with self.subTest(change=change), self.assertRaises(ValueError):
                catalog.validate(data)
        trap = {"schema_version": 2, "visit_policy": "once", "code": "trap", "title": "Trap",
                "description": "Test only", "start_node": "s", "nodes": {
                    "s": {"text": "S", "choices": [{"id": "a", "text": "A", "next": "t"},
                                                       {"id": "b", "text": "B", "next": "e"}]},
                    "t": {"text": "T", "choices": [{"id": "c", "text": "C", "next": "t"}]},
                    "e": {"text": "E", "end": True}}}
        with self.assertRaisesRegex(ValueError, "ending"):
            catalog.validate(trap)
        # 1001 distinct nodes and missing choices fail independently.
        trap["nodes"].update({str(i): {"text": "X", "end": True} for i in range(1001)})
        with self.assertRaises(ValueError):
            catalog.validate(trap)

    def test_duplicate_keys_invalid_files_and_duplicate_codes_isolated(self):
        raw = PAYLOAD.read_text(encoding="utf-8")
        for broken in (raw.replace('"schema_version": 2', '"schema_version":2,"schema_version":2'),
                       raw.replace('"scene_1": {', '"scene_1":{},"scene_1":{'),
                       raw.replace('"id": "choice_1"', '"id":"a","id":"choice_1"', 1)):
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                catalog.loads(broken)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "author.json").write_bytes(PAYLOAD.read_bytes())
            (directory / "broken.json").write_text("{")
            (directory / "large.json").write_bytes(b" " * 2000001)
            good, errors = catalog.discover(directory)
            self.assertEqual(set(good), {"tayna_propavshey_ovtsy"})
            self.assertEqual(set(errors), {"broken.json", "large.json"})
            (directory / "same.json").write_bytes(PAYLOAD.read_bytes())
            self.assertEqual(catalog.discover(directory)[0], {})

    def test_known_dead_end_and_source_indices(self):
        story = author_story()
        path = ["scene_" + str(n) for n in (1, 2, 6, 13, 24, 25, 26, 16, 10, 11, 21)]
        choices = available_choices(story, path[-1], path)
        self.assertEqual([i for i, _ in choices], [0, 1])
        self.assertNotIn("scene_14", {c["next"] for _, c in choices})
        self.assertEqual(available_choices(story, "scene_14", path + ["scene_14"]), [])
        # At 40 the third edge returns to 39; the two real exits remain.
        indices = [i for i, _ in available_choices(story, "scene_40", {"scene_39", "scene_40"})]
        self.assertEqual(indices, [0, 1])
        self.assertNotIn("scene_40", {c["next"] for _, c in available_choices(story, "scene_39", {"scene_40"})})

    def test_seeded_ten_thousand_runs_finish_without_repeats_or_deadends(self):
        story = author_story()
        rng = random.Random(20261026)
        endings = set()
        max_length = 0
        for _ in range(10000):
            node = story["start_node"]
            visited = set()
            while True:
                self.assertNotIn(node, visited)
                visited.add(node)
                if story["nodes"][node].get("end"):
                    endings.add(node)
                    break
                choices = available_choices(story, node, visited)
                self.assertTrue(choices)
                node = rng.choice(choices)[1]["next"]
            max_length = max(max_length, len(visited))
        self.assertEqual(endings, {"ending_k" + str(i) for i in range(1, 8)})
        self.assertLessEqual(max_length, 53)


class StoryOnceSQLite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "stories.db"
        self.directory = Path(self.tmp.name) / "external"
        self.directory.mkdir()
        (self.directory / "author.json").write_bytes(PAYLOAD.read_bytes())
        with closing(sqlite3.connect(self.db)) as conn, conn:
            schema.apply(conn)
            conn.execute("CREATE TABLE characters(chat_id,thread_id,username,name)")
            conn.executemany("INSERT INTO characters VALUES(-100,0,?,?)", [("@u2", "Two"), ("@u3", "Three")])
        self.enterContext(patch("app.context.get_topic_admin", return_value="@admin"))
        self.sid = self.new_session("create")

    def new_session(self, key, thread=0, code="tayna_propavshey_ovtsy"):
        return service.create(-100, thread, 1, "admin", code, key, self.db,
                              catalog_dir=self.directory)["session_id"]

    def begin(self, users=(2, 3), sid=None):
        sid = self.sid if sid is None else sid
        for uid in users:
            service.join(sid, -100, 0, uid, "dnd", {"thread_id": 0}, f"join:{sid}:{uid}",
                         self.db, username=f"u{uid}")
        service.start(sid, -100, 0, 1, "admin", f"start:{sid}", self.db, catalog_dir=self.directory)

    def state(self, sid=None):
        return service.status(-100, 0, self.db, session_id=self.sid if sid is None else sid)

    def go(self, target):
        state = self.state()
        choice = next(c for _, c in state["choices"] if c["next"] == target)
        for uid in (2, 3):
            result = service.vote(self.sid, -100, 0, uid, state["step"], choice["id"],
                                  f"vote:{self.sid}:{state['step']}:{uid}", self.db)
        self.assertTrue(result["advanced"])
        return result

    def test_quorum_commit_restart_snapshot_and_forged_choice(self):
        self.begin()
        state = self.state()
        self.assertEqual(state["visited"], ["scene_1"])
        result = service.vote(self.sid, -100, 0, 2, 1, "choice_1", "first", self.db)
        self.assertFalse(result["advanced"])
        self.assertEqual(self.state()["visited"], ["scene_1"])
        self.assertEqual(service.vote(self.sid, -100, 0, 2, 1, "choice_1", "first", self.db), result)
        with self.assertRaises(ValueError):
            service.vote(self.sid, -100, 0, 2, 1, "choice_2", "change", self.db)
        # Catalog removal cannot alter frozen active data. New connection reconstructs journal.
        (self.directory / "author.json").write_text("{}")
        self.go("scene_2")
        self.assertEqual(set(self.state()["visited"]), {"scene_1", "scene_2"})
        self.assertEqual(self.state()["snapshot_json"], state["snapshot_json"])
        self.go("scene_6"); self.go("scene_13"); self.go("scene_24")
        self.go("scene_25"); self.go("scene_26"); self.go("scene_16")
        self.go("scene_10"); self.go("scene_11"); self.go("scene_21")
        state = self.state()
        self.assertEqual([i for i, _ in state["choices"]], [0, 1])
        with self.assertRaises(ValueError):
            service.vote(self.sid, -100, 0, 2, state["step"], "choice_3", "forged", self.db)
        self.assertEqual(self.state()["node_id"], "scene_21")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            rows = conn.execute("SELECT node_id,next_node FROM story_steps WHERE session_id=? ORDER BY step", (self.sid,)).fetchall()
        route = [rows[0][0]] + [r[1] for r in rows]
        self.assertEqual(len(route), len(set(route)))
        self.assertEqual(set(route), set(state["visited"]))
        restarted = subprocess.run([sys.executable, "-c",
            "import json,sys;from app.stories.service import status;"
            "s=status(-100,0,sys.argv[1]);print(json.dumps([s['visited'],s['choices']]))", str(self.db)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", check=True)
        restored = json.loads(restarted.stdout)
        self.assertEqual(restored[0], state["visited"])
        self.assertEqual([i for i, _ in restored[1]], [0, 1])

    def test_last_vote_races_one_step_one_tie_and_exact_replay(self):
        self.begin()
        service.vote(self.sid, -100, 0, 2, 1, "choice_1", "first", self.db)
        picks = []
        def chooser(leaders):
            picks.append(tuple(leaders))
            return leaders[-1]
        def last(_):
            return service.vote(self.sid, -100, 0, 3, 1, "choice_2", "last", self.db, chooser=chooser)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(last, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(picks), 1)
        self.assertEqual(self.state()["node_id"], "scene_3")
        self.assertEqual(results[0]["percent"], {"choice_1": 50, "choice_2": 50, "choice_3": 0})
        with self.assertRaises(ValueError):
            service.vote(self.sid, -100, 0, 3, 1, "choice_2", "stale", self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM story_steps").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM story_publications").fetchone()[0], 3)

    def test_competing_different_last_operation_keys_and_kick_race(self):
        self.begin()
        service.vote(self.sid, -100, 0, 2, 1, "choice_1", "first", self.db)
        def attempt(which):
            try:
                if which == "kick":
                    return service.kick(self.sid, -100, 0, 1, "admin", 3, "kick", self.db)
                return service.vote(self.sid, -100, 0, 3, 1, "choice_1", "last", self.db)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(attempt, ("kick", "vote")))
        self.assertEqual(self.state()["node_id"], "scene_2")
        self.assertEqual(self.state()["step"], 2)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM story_steps").fetchone()[0], 1)

    def test_corrupt_dead_end_diagnostic_cancel_and_fresh_group_markers(self):
        self.begin()
        blocked = ["scene_" + str(n) for n in (1, 2, 6, 13, 24, 25, 26, 16, 10, 11, 21, 14)]
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for step, (src, dst) in enumerate(zip(blocked, blocked[1:]), 1):
                conn.execute("INSERT INTO story_steps VALUES(?,?,?,?,?,?)", (self.sid, step, src, "corrupt-fixture", dst, "{}"))
            conn.execute("UPDATE story_sessions SET node_id='scene_14',step=12 WHERE id=?", (self.sid,))
        with self.assertLogs("app.stories.service", level="ERROR"):
            state = self.state()
        self.assertIn("route_error", state)
        self.assertIsNone(render(state)[1])
        self.assertIn("/story cancel", render(state)[0])
        with self.assertLogs("app.stories.service", level="ERROR"), self.assertRaises(ValueError):
            service.vote(self.sid, -100, 0, 2, 12, "choice_1", "unsafe", self.db)
        service.cancel(self.sid, -100, 0, 1, "admin", "cancel", self.db)
        self.assertEqual(self.state()["status"], "cancelled")
        new_sid = self.new_session("new")
        self.begin(sid=new_sid)
        self.assertEqual(self.state(new_sid)["visited"], ["scene_1"])
        self.assertEqual(self.state()["visited"], sorted(blocked))

    def test_v1_v2_sessions_isolated_schema_replay_integrity_and_scope(self):
        self.begin()
        v1 = json.loads((ROOT / "docs/v1.5/STORY_AUTHOR_TEMPLATE.json").read_text(encoding="utf-8"))
        (self.directory / "legacy.json").write_text(json.dumps(v1), encoding="utf-8")
        other = self.new_session("v1-create", thread=1, code=v1["code"])
        service.join(other, -100, 1, 2, "dnd", {"thread_id": 0}, "v1-join", self.db, username="u2")
        service.start(other, -100, 1, 1, "admin", "v1-start", self.db, catalog_dir=self.directory)
        before = self.state()
        for args in ((-101, 0, 2), (-100, 9, 2), (-100, 0, 999)):
            with self.assertRaises(ValueError):
                service.vote(self.sid, *args, 1, "choice_1", "foreign", self.db)
        self.assertEqual(self.state(), before)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            saved = conn.execute("SELECT * FROM story_sessions ORDER BY id").fetchall()
            for _ in range(3):
                schema.apply(conn)
            self.assertEqual(conn.execute("SELECT * FROM story_sessions ORDER BY id").fetchall(), saved)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(service.status(-100, 1, self.db)["visited"], [v1["start_node"]])
        self.assertEqual(self.state()["visited"], ["scene_1"])
        # A v1 session still advances with its original unfiltered choices.
        old = service.status(-100, 1, self.db)
        service.vote(other, -100, 1, 2, old["step"], old["choices"][0][1]["id"], "v1-vote", self.db)
        self.assertEqual(service.status(-100, 1, self.db)["step"], 2)

    def test_different_last_vote_keys_and_cancel_race_are_serialized(self):
        self.begin()
        service.vote(self.sid, -100, 0, 2, 1, "choice_1", "first", self.db)
        def last(key):
            try:
                return service.vote(self.sid, -100, 0, 3, 1, "choice_1", key, self.db)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(last, ("last-a", "last-b")))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.state()["step"], 2)
        service.vote(self.sid, -100, 0, 2, 2, "choice_1", "second-first", self.db)
        def race(kind):
            try:
                if kind == "cancel":
                    return service.cancel(self.sid, -100, 0, 1, "admin", "cancel", self.db)
                return service.vote(self.sid, -100, 0, 3, 2, "choice_1", "second-last", self.db)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(race, ("vote", "cancel")))
        state = self.state()
        self.assertEqual(state["status"], "cancelled")
        self.assertLessEqual(state["step"], 3)
        self.assertIsNone(render(state)[1])


class StoryOnceTelegram(MiniCase, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = audit.TelegramAudit.asyncSetUp
    callback = audit.TelegramAudit.callback
    message = audit.TelegramAudit.message

    async def test_real_seven_endings_filtered_indices_restart_publication_and_no_rewards(self):
        from app.stories import handlers, publication
        from app.mini.players import create_mini_player
        directory = Path(self.tmp.name) / "external"
        directory.mkdir()
        path = directory / "author.json"
        path.write_bytes(PAYLOAD.read_bytes())
        self.enterContext(patch.dict("os.environ", STORIES_DIR=str(directory)))
        self.enterContext(patch("app.context.get_topic_admin", return_value="@tester"))
        other = create_mini_player(self.world["id"], 900124, "other", "Other", self.db)["id"]
        chat = self.world["chat_id"]
        thread = self.world["thread_id"] or 0
        wallet_before = self.sql("SELECT id,coins,shards,mythic_shards FROM mini_players ORDER BY id")
        routes = json.loads((PACKAGE / "GRAPH_AUDIT.json").read_text(encoding="utf-8"))["endings_shortest_routes"]
        # Both directions of the author cycle are tested without modifying the graph.
        routes["ending_k3"] = ["scene_1", "scene_2", "scene_5", "scene_12", "scene_22", "scene_36", "scene_40", "scene_39", "scene_41", "ending_k3"]
        routes["ending_k4"] = ["scene_1", "scene_2", "scene_5", "scene_12", "scene_22", "scene_35", "scene_39", "scene_40", "ending_k4"]
        mid = 300
        for ending, route in routes.items():
            mid += 1
            await handlers.command(self.message("/story tayna_propavshey_ovtsy", mid=mid, chat=chat, thread=thread))
            state = service.status(chat, thread, self.db)
            sid = state["id"]
            for uid, pid in ((900123, self.pid), (900124, other)):
                service.join(sid, chat, thread, uid, "mini", {"id": pid}, f"join:{sid}:{uid}", self.db)
            mid += 1
            await handlers.command(self.message("/story start", mid=mid, chat=chat, thread=thread))
            snapshot = service.status(chat, thread, self.db)["snapshot_json"]
            path.write_text("{}", encoding="utf-8")
            for target in route[1:]:
                state = service.status(chat, thread, self.db)
                self.assertEqual(state["snapshot_json"], snapshot)
                text, markup = render(state)
                self.assertIn(json.loads(snapshot)["nodes"][state["node_id"]]["text"], text)
                source = next((i, c) for i, c in state["choices"] if c["next"] == target)
                index, choice = source
                button = next(row[0] for row in markup.inline_keyboard if row[0].callback_data.endswith(f":{index}"))
                if state["node_id"] == "scene_40" and "scene_39" in state["visited"]:
                    self.assertEqual([int(row[0].callback_data.rsplit(":", 1)[1]) for row in markup.inline_keyboard], [0, 1])
                    forged = self.callback(f"story:vote:{sid}:{state['step']}:2", chat=chat)
                    forged = forged.model_copy(update={"id": f"forged:{sid}"})
                    await handlers.callback_action(forged)
                    self.assertTrue(self.answered.call_args.kwargs.get("show_alert"))
                    self.assertEqual(service.status(chat, thread, self.db)["node_id"], "scene_40")
                if state["node_id"] == "scene_39" and "scene_40" in state["visited"]:
                    self.assertEqual([i for i, _ in state["choices"]], [0])
                for uid in (900123, 900124):
                    callback = self.callback(button.callback_data, uid=uid, chat=chat)
                    callback = callback.model_copy(update={"id": f"vote:{sid}:{state['step']}:{uid}"})
                    if thread:
                        callback = callback.model_copy(update={"message": callback.message.model_copy(update={"message_thread_id": thread})})
                    await handlers.callback_action(callback)
                    self.assertFalse(self.answered.call_args.kwargs.get("show_alert", False))
                after = service.status(chat, thread, self.db)
                self.assertEqual(after["node_id"], target)
                count = len(self.sql("SELECT * FROM story_steps WHERE session_id=?", (sid,)))
                await handlers.callback_action(callback)
                self.assertEqual(len(self.sql("SELECT * FROM story_steps WHERE session_id=?", (sid,))), count)
            finished = service.status(chat, thread, self.db)
            self.assertEqual(finished["status"], "finished")
            self.assertEqual(finished["node_id"], ending)
            self.assertEqual(set(finished["visited"]), set(route))
            self.assertEqual(len(route), len(set(route)))
            self.assertIsNone(render(finished)[1])
            sent = self.sent.call_count
            await handlers.recover_pending(self.bot)
            await handlers.publish(self.bot, finished)
            self.assertEqual(self.sent.call_count, sent)
            path.write_bytes(PAYLOAD.read_bytes())
        self.assertEqual(self.sql("SELECT id,coins,shards,mythic_shards FROM mini_players ORDER BY id"), wallet_before)
        self.assertEqual(len(self.sql("SELECT * FROM story_sessions WHERE status='finished'")), 7)

    async def test_filtered_first_choice_retains_second_third_callback_indices(self):
        from app.stories import handlers
        directory = Path(self.tmp.name) / "external"
        directory.mkdir()
        story = {"schema_version": 2, "visit_policy": "once", "code": "fixture_indices", "title": "Fixture only",
                 "description": "Test only", "start_node": "s", "nodes": {
                     "s": {"text": "S", "choices": [{"id": "back", "text": "Back", "next": "s"},
                                                       {"id": "second", "text": "Second", "next": "e"},
                                                       {"id": "third", "text": "Third", "next": "e"}]},
                     "e": {"text": "E", "end": True}}}
        (directory / "fixture.json").write_text(json.dumps(story), encoding="utf-8")
        self.enterContext(patch.dict("os.environ", STORIES_DIR=str(directory)))
        self.enterContext(patch("app.context.get_topic_admin", return_value="@tester"))
        chat = self.world["chat_id"]
        sid = service.create(chat, 0, 900123, "tester", story["code"], "create", self.db)["session_id"]
        service.join(sid, chat, 0, 900123, "mini", {"id": self.pid}, "join", self.db)
        service.start(sid, chat, 0, 900123, "tester", "start", self.db)
        state = service.status(chat, 0, self.db)
        # Recover pending v2 publication once, then recover a crash during sending
        # as uncertain; the external API must not receive an automatic duplicate.
        await handlers.recover_pending(self.bot)
        sent = self.sent.call_count
        await handlers.recover_pending(self.bot)
        self.assertEqual(self.sent.call_count, sent)
        self.sql("UPDATE story_publications SET status='sending',message_id=NULL WHERE session_id=? AND step=1", (sid,))
        from app.stories import publication
        publication.recover(self.db)
        await handlers.recover_pending(self.bot)
        self.assertEqual(self.sent.call_count, sent)
        self.assertEqual(self.sql("SELECT status FROM story_publications WHERE session_id=? AND step=1", (sid,))[0]["status"], "uncertain")
        _, markup = render(state)
        self.assertEqual([row[0].callback_data for row in markup.inline_keyboard],
                         [f"story:vote:{sid}:1:1", f"story:vote:{sid}:1:2"])
        forged = self.callback(f"story:vote:{sid}:1:0", chat=chat)
        await handlers.callback_action(forged)
        self.assertTrue(self.answered.call_args.kwargs["show_alert"])
        self.assertEqual(service.status(chat, 0, self.db)["visited"], ["s"])
        third = self.callback(f"story:vote:{sid}:1:2", chat=chat).model_copy(update={"id": "third"})
        await handlers.callback_action(third)
        self.assertEqual(self.sql("SELECT choice_id FROM story_steps")[0]["choice_id"], "third")
        self.assertEqual(service.status(chat, 0, self.db)["visited"], ["e", "s"])
