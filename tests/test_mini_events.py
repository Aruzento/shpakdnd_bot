import itertools
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")

from app.mini.db import connect_mini_db
from app.mini.events import service as events
from app.mini.players import create_mini_player, get_mini_player
from app.mini.schema import init_mini_db
from app.mini.wallet import InsufficientFundsError, add_coins, get_wallet_history
from app.mini.worlds import sync_configured_mini_worlds


class MiniEventTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = Path(self.tempdir.name) / "mini.db"
        init_mini_db(self.db)
        self.world = sync_configured_mini_worlds(self.db)[0]["id"]
        self.player = create_mini_player(self.world, 101, "@tester", "Игрок", self.db)["id"]
        add_coins(self.player, 100, "Начальный баланс", db_path=self.db)

    def start(self, game="rps", stake=5, key="first"):
        return events.start_session(self.player, self.world, game, stake, key, self.db)

    def resolve(self, sid, choice="saint", bot="demon"):
        with patch.object(events.secrets, "choice", return_value=bot):
            return events.resolve_rps(self.player, self.world, sid, choice, self.db)

    def turn(self, sid, step, direction="left"):
        return events.choose_direction(self.player, self.world, sid, step, direction, self.db)

    def repeat(self, sid):
        return events.repeat_session(self.player, self.world, sid, self.db)

    def resources(self):
        player = get_mini_player(self.world, 101, self.db)
        return player["coins"], player["shards"]

    def count(self):
        with connect_mini_db(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM mini_event_sessions").fetchone()[0]

    def test_migration_preserves_existing_tables_and_rows(self):
        with connect_mini_db(self.db) as conn:
            conn.execute("DROP TABLE mini_event_requests")
            conn.execute("DROP TABLE mini_event_sessions")
            conn.execute("CREATE TABLE legacy_data (value TEXT)")
            conn.execute("INSERT INTO legacy_data VALUES ('preserved')")
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name <> 'sqlite_sequence'"
            )]
            before = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
        init_mini_db(self.db)
        init_mini_db(self.db)
        with connect_mini_db(self.db) as conn:
            after = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
            columns = {r[1] for r in conn.execute("PRAGMA table_info(mini_event_sessions)")}
        self.assertEqual(before, after)
        self.assertTrue({"id", "player_id", "game_type", "stake", "status", "step",
                         "payload_json", "created_at", "resolved_at", "start_key"} <= columns)

    def test_rps_all_nine_pairs(self):
        wins = {("saint", "demon"), ("demon", "villager"), ("villager", "saint")}
        for choice, bot in itertools.product(events.RPS_CHOICES, repeat=2):
            with self.subTest(choice=choice, bot=bot):
                expected = "draw" if choice == bot else "win" if (choice, bot) in wins else "loss"
                self.assertEqual(events.rps_outcome(choice, bot), expected)

    def test_rps_economy_all_stakes_and_outcomes(self):
        for stake, bot in itertools.product((1, 5, 10), ("demon", "villager", "saint")):
            with self.subTest(stake=stake, bot=bot):
                before = self.resources()[0]
                session = self.start(stake=stake, key=f"{stake}:{bot}")
                self.assertEqual(self.resources()[0], before - stake)
                result = self.resolve(session["id"], bot=bot)
                expected_delta = {"demon": stake, "villager": -stake, "saint": 0}[bot]
                self.assertEqual(self.resources()[0], before + expected_delta)
                self.assertEqual(result["status"], "resolved")
                self.assertIsNotNone(result["resolved_at"])

    def test_invalid_games_stakes_and_choices_change_nothing(self):
        for game, stake in (("rps", 2), ("rps", -5), ("labyrinth", 10), ("unknown", 5)):
            with self.assertRaises(events.EventError):
                self.start(game, stake)
        with self.assertRaises(events.EventError):
            events.rps_outcome("unknown", "saint")
        self.assertEqual(self.resources(), (100, 0))
        self.assertEqual(self.count(), 0)

    def test_insufficient_funds_rolls_back_created_session_and_wallet(self):
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_players SET coins = 0 WHERE id = ?", (self.player,))
        for game in ("rps", "labyrinth"):
            with self.assertRaises(InsufficientFundsError):
                self.start(game)
        self.assertEqual(self.count(), 0)
        self.assertEqual(len(get_wallet_history(self.player, 50, self.db)), 1)
        self.assertEqual(self.resources(), (0, 0))

    def test_start_failure_after_wallet_write_rolls_back_everything(self):
        original = events.change_balance_in_transaction
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("interrupted after debit")
        with patch.object(events, "change_balance_in_transaction", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.start()
        self.assertEqual(self.resources(), (100, 0))
        self.assertEqual(self.count(), 0)
        self.assertEqual(len(get_wallet_history(self.player, 50, self.db)), 1)

    def test_one_active_round_and_duplicate_start_keys(self):
        first = self.start()
        self.assertEqual(self.start()["id"], first["id"])
        self.assertEqual(self.start("labyrinth", key="other-button")["id"], first["id"])
        self.assertEqual(self.resources(), (95, 0))
        self.assertEqual(self.count(), 1)
        with connect_mini_db(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO mini_event_sessions
                    (player_id, game_type, stake, start_key) VALUES (?, 'rps', 1, 'another')""",
                    (self.player,))

    def test_consumed_start_button_cannot_start_after_resolution(self):
        first = self.start()
        self.resolve(first["id"])
        self.assertEqual(self.start()["id"], first["id"])
        self.assertEqual(self.resources(), (105, 0))
        self.assertEqual(self.count(), 1)

    def test_repeat_creates_new_round_once_even_after_it_finishes(self):
        first = self.start(stake=10)
        self.resolve(first["id"])
        second = self.repeat(first["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["stake"], 10)
        self.assertEqual(self.repeat(first["id"])["id"], second["id"])
        self.resolve(second["id"], bot="saint")
        self.assertEqual(self.repeat(first["id"])["id"], second["id"])
        self.assertEqual(self.resources(), (110, 0))
        self.assertEqual(self.count(), 2)

    def test_repeat_rejects_active_and_insufficient_funds(self):
        first = self.start()
        with self.assertRaises(events.EventError):
            self.repeat(first["id"])
        self.resolve(first["id"], bot="villager")
        with connect_mini_db(self.db) as conn:
            conn.execute("UPDATE mini_players SET coins = 4 WHERE id = ?", (self.player,))
        with self.assertRaises(InsufficientFundsError):
            self.repeat(first["id"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.resources(), (4, 0))

    def test_duplicate_rps_resolution_preserves_original_choice_and_reward(self):
        for bot in events.RPS_CHOICES:
            with self.subTest(bot=bot):
                first = self.start(key=bot)
                resolved = self.resolve(first["id"], bot=bot)
                before = self.resources()
                with patch.object(events.secrets, "choice", side_effect=AssertionError("rerolled")):
                    again = events.resolve_rps(self.player, self.world, first["id"], "demon", self.db)
                self.assertEqual(again, resolved)
                self.assertEqual(self.resources(), before)

    def test_rps_rng_receives_all_choices(self):
        first = self.start()
        with patch.object(events.secrets, "choice", return_value="demon") as rng:
            events.resolve_rps(self.player, self.world, first["id"], "saint", self.db)
        rng.assert_called_once_with(("saint", "demon", "villager"))

    def test_labyrinth_all_eight_sequences_persist_once(self):
        for i, sequence in enumerate(itertools.product(events.DIRECTIONS, repeat=3)):
            with self.subTest(sequence=sequence):
                with patch.object(events.secrets, "choice", side_effect=sequence) as rng:
                    session = self.start("labyrinth", key=str(i))
                self.assertEqual(rng.call_count, 3)
                self.assertTrue(all(c.args == (events.DIRECTIONS,) for c in rng.call_args_list))
                stored = events.get_session(self.player, self.world, session["id"], self.db)
                self.assertEqual(stored["payload"]["sequence"], list(sequence))
                opposite = "right" if sequence[0] == "left" else "left"
                self.turn(session["id"], 0, opposite)

    def test_labyrinth_steps_and_victory_economy(self):
        with patch.object(events.secrets, "choice", side_effect=("left", "right", "left")):
            session = self.start("labyrinth")
        with patch.object(events.secrets, "choice", side_effect=AssertionError("rerolled")):
            for step, direction in enumerate(("left", "right", "left")):
                result = self.turn(session["id"], step, direction)
                self.assertEqual(result["step"], step + 1)
                self.assertEqual(result["status"], "resolved" if step == 2 else "active")
                self.assertEqual(self.resources(), (105, 0) if step == 2 else (95, 0))
        self.assertEqual(result["payload"]["coins_awarded"], 10)
        self.assertEqual(self.turn(session["id"], 2, "left"), result)
        self.assertEqual(self.resources(), (105, 0))

    def test_labyrinth_failure_at_each_step_and_duplicate_shards(self):
        for fail_step in (0, 1, 2):
            before_coins, before_shards = self.resources()
            with patch.object(events.secrets, "choice", return_value="left"):
                session = self.start("labyrinth", key=str(fail_step))
            for step in range(fail_step):
                self.turn(session["id"], step)
            result = self.turn(session["id"], fail_step, "right")
            self.assertEqual(result["status"], "resolved")
            self.assertEqual(result["payload"]["outcome"], "loss")
            self.assertEqual(self.resources(), (before_coins - 5, before_shards + 3))
            self.assertEqual(self.turn(session["id"], fail_step, "right"), result)
            self.assertEqual(self.resources(), (before_coins - 5, before_shards + 3))

    def test_duplicate_or_out_of_order_labyrinth_step_cannot_advance_or_fail(self):
        with patch.object(events.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        unchanged = self.turn(session["id"], 1)
        self.assertEqual(unchanged["step"], 0)
        first = self.turn(session["id"], 0)
        self.assertEqual(self.turn(session["id"], 0, "right"), first)
        self.assertEqual(self.resources(), (95, 0))

    def test_resolution_failure_rolls_back_coins_and_shards(self):
        for game in ("rps", "labyrinth"):
            with patch.object(events.secrets, "choice", return_value="left"):
                session = self.start(game, key=game)
            before = self.resources()
            with patch.object(events, "_save", side_effect=RuntimeError("interrupted")):
                with self.assertRaises(RuntimeError):
                    if game == "rps":
                        self.resolve(session["id"])
                    else:
                        self.turn(session["id"], 0, "right")
            self.assertEqual(self.resources(), before)
            self.assertEqual(events.get_active_session(self.player, self.world, self.db)["id"], session["id"])
            if game == "rps":
                self.resolve(session["id"])
            else:
                self.turn(session["id"], 0, "right")

    def test_active_round_restores_after_reinitialization(self):
        with patch.object(events.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        self.turn(session["id"], 0)
        init_mini_db(self.db)
        restored = events.get_active_session(self.player, self.world, self.db)
        self.assertEqual(restored["id"], session["id"])
        self.assertEqual(restored["step"], 1)
        self.assertEqual(restored["payload"]["sequence"], ["left"] * 3)
        self.assertEqual(self.resources(), (95, 0))

    def test_other_player_world_and_wrong_game_are_rejected(self):
        session = self.start()
        other = create_mini_player(self.world, 102, "@other", "Другой", self.db)["id"]
        for player, world in ((other, self.world), (self.player, self.world + 1000)):
            with self.assertRaises(events.EventError):
                events.resolve_rps(player, world, session["id"], "saint", self.db)
            with self.assertRaises(events.EventError):
                events.repeat_session(player, world, session["id"], self.db)
        with self.assertRaises(events.EventError):
            self.turn(session["id"], 0)
        self.assertEqual(self.resources(), (95, 0))

    def test_concurrent_start_resolution_and_repeat_are_serialized(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            starts = list(pool.map(lambda i: self.start(key=f"click:{i}"), range(4)))
            self.assertEqual(len({s["id"] for s in starts}), 1)
            with patch.object(events.secrets, "choice", return_value="demon"):
                results = list(pool.map(lambda _: events.resolve_rps(
                    self.player, self.world, starts[0]["id"], "saint", self.db), range(4)))
            self.assertEqual(self.resources(), (105, 0))
            repeated = list(pool.map(lambda _: self.repeat(results[0]["id"]), range(4)))
            self.assertEqual(len({s["id"] for s in repeated}), 1)
            self.assertEqual(self.resources(), (100, 0))
            self.assertEqual(self.count(), 2)

    def test_concurrent_labyrinth_failure_awards_three_shards_once(self):
        with patch.object(events.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.turn(session["id"], 0, "right"), range(4)))
        self.assertEqual(self.resources(), (95, 3))

    def test_event_wallet_entries_have_unique_session_operation_keys(self):
        session = self.start()
        self.resolve(session["id"])
        self.resolve(session["id"])
        history = [t for t in get_wallet_history(self.player, 50, self.db)
                   if t["reference_type"] == "mini_event"]
        self.assertEqual({t["operation_key"] for t in history},
                         {f"event:{session['id']}:stake", f"event:{session['id']}:reward"})
        self.assertTrue(all(t["reference_id"] == session["id"] for t in history))

    def test_start_button_that_resumed_other_game_is_consumed_forever(self):
        first = self.start()
        self.assertEqual(self.start("labyrinth", key="resumed")["id"], first["id"])
        self.resolve(first["id"])
        self.assertEqual(self.start("labyrinth", key="resumed")["id"], first["id"])
        self.assertEqual(self.resources(), (105, 0))
        self.assertEqual(self.count(), 1)

    def test_concurrent_correct_labyrinth_steps_and_victory_apply_once(self):
        with patch.object(events.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for step in (0, 1, 2):
                results = list(pool.map(lambda _: self.turn(session["id"], step), range(4)))
                self.assertTrue(all(r["step"] == step + 1 for r in results))
                self.assertEqual(self.resources(), (105, 0) if step == 2 else (95, 0))

    def test_labyrinth_victory_failure_rolls_back_payout_and_progress(self):
        with patch.object(events.secrets, "choice", return_value="left"):
            session = self.start("labyrinth")
        self.turn(session["id"], 0)
        self.turn(session["id"], 1)
        with patch.object(events, "_save", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.turn(session["id"], 2)
        restored = events.get_active_session(self.player, self.world, self.db)
        self.assertEqual(restored["step"], 2)
        self.assertEqual(self.resources(), (95, 0))
        self.turn(session["id"], 2)
        self.assertEqual(self.resources(), (105, 0))
