"""Publication, persisted retries, drain and independent rollback proofs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError,TelegramRetryAfter
from aiogram.methods import EditMessageText,SendMessage
from tests.v1_4_3.test_pvp import PvpCase
from app.mini.events import pvp,pvp_publication as pub
from app.mini.schema import init_mini_db
from deploy import pvp_ops as ops

ROOT=Path(__file__).resolve().parents[2]

class SafetyCase(PvpCase):
    def setUp(self):
        super().setUp();self.now=1000
        self.clock=patch.object(pub,"timestamp",side_effect=lambda value=None:self.now if value is None else value)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.bot=NS(send_message=AsyncMock(return_value=NS(message_id=77)),edit_message_text=AsyncMock())
    def new(self,key="safe",published=True):
        s=pvp.create(self.pid,self.wid,key,self.db,now=self.now)
        if published:asyncio.run(pub.publish(self.bot,s["id"],self.db))
        return s
    def row(self,sid):
        return self.sql("SELECT * FROM mini_event_pvp WHERE id=?",(sid,))[0]
    def run_pub(self,sid):
        asyncio.run(pub.publish(self.bot,sid,self.db))
    def tick(self):asyncio.run(pub.tick(self.bot,self.db))
    def revise(self,sid):
        self.sql("UPDATE mini_event_pvp SET revision=revision+1 WHERE id=?",(sid,))
    def accepted(self):
        s=self.new()
        pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
        return s
    def finished(self):
        s=self.accepted()
        pvp.choose(self.pid,self.wid,s["id"],"saint",self.db,now=self.now)
        pvp.choose(self.other,self.wid,s["id"],"demon",self.db,now=self.now)
        self.run_pub(s["id"])
        return s
    def pause(self):return pvp.maintenance(True,self.db,now=self.now)

class SendSafetyTests(SafetyCase):
    def test_pending_send_cannot_be_accepted_or_spend_second_stake(self):
        s=self.new(published=False);before=self.resources()
        with self.assertRaises(pvp.PvpError):pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
        self.assertEqual(self.resources(),before)
    def test_claim_concurrency_one_sender_and_live_restart_does_not_steal(self):
        s=self.new(published=False)
        with ThreadPoolExecutor(2) as pool:
            claims=list(pool.map(lambda _:pub.claim_send(s["id"],self.db,now=self.now),range(2)))
        self.assertEqual(sum(x is not None for x in claims),1)
        pub.recover(self.db,now=self.now+1)
        self.assertEqual(self.row(s["id"])["publication"],"sending")
        self.assertIsNone(pub.claim_send(s["id"],self.db,now=self.now+2))
    def test_crash_after_claim_before_api_ends_once_after_lease(self):
        s=self.new(published=False);pub.claim_send(s["id"],self.db,now=self.now)
        self.now+=pub.LEASE_SECONDS+1;pub.recover(self.db,now=self.now);pub.recover(self.db,now=self.now)
        self.tick();self.bot.send_message.assert_not_called()
        self.assertEqual(self.balance(self.pid)["coins"],100)
        row=self.row(s["id"]);self.assertEqual((row["status"],row["publication"]),("publication_failed","uncertain"))
        self.assertEqual(row["last_error_type"],"InterruptedSend")
    def test_timeout_after_possible_success_has_one_send_refund_and_diagnostics(self):
        s=self.new(published=False)
        self.bot.send_message.side_effect=TimeoutError("https://api.telegram.org/botSECRET/private Alice")
        with self.assertLogs(pub.log,level="WARNING") as log:
            self.run_pub(s["id"]);self.tick();self.tick()
        self.assertEqual(self.bot.send_message.await_count,1)
        self.assertEqual(len(log.output),1)
        row=self.row(s["id"])
        self.assertNotIn("SECRET",json.dumps(row));self.assertNotIn("Alice",json.dumps(row))
        record=ops.inspect(self.db)["sessions"][0]
        self.assertTrue(record["refunds_confirmed"]);self.assertTrue(record["delivery_uncertain"])
        self.assertEqual(record["send_attempt_at"],1000)
        self.assertNotIn("SECRET",str(log.output));self.assertNotIn("Alice",str(log.output))
    def test_missing_message_id_is_uncertain_and_never_edit_unknown(self):
        s=self.new(published=False);self.bot.send_message.return_value=NS(message_id=None)
        self.run_pub(s["id"]);self.tick()
        self.assertEqual(self.row(s["id"])["publication"],"uncertain")
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.bot.edit_message_text.assert_not_called()
    def test_confirmed_api_rejection_is_failed_not_uncertain(self):
        s=self.new(published=False)
        self.bot.send_message.side_effect=TelegramBadRequest(method=SendMessage(chat_id=1,text="private"),message="bad request")
        self.run_pub(s["id"]);self.tick()
        row=self.row(s["id"])
        self.assertEqual((row["publication"],row["delivery_uncertain"]),("failed",0))
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.assertEqual(self.bot.send_message.await_count,1)
    def test_send_rate_limit_is_confirmed_rejection_without_resend(self):
        s=self.new(published=False)
        self.bot.send_message.side_effect=TelegramRetryAfter(method=SendMessage(chat_id=1,text="x"),message="rate",retry_after=30)
        self.run_pub(s["id"]);self.now+=31;self.tick()
        self.assertEqual(self.row(s["id"])["publication"],"failed")
        self.assertEqual(self.row(s["id"])["delivery_uncertain"],0)
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.assertEqual(self.bot.send_message.await_count,1)
    def test_cancelled_api_request_is_uncertain_and_refunded(self):
        s=self.new(published=False);self.bot.send_message.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):self.run_pub(s["id"])
        self.assertEqual(self.row(s["id"])["publication"],"uncertain")
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_acceptance_during_uncertain_send_is_rejected_without_payout(self):
        s=self.new(published=False)
        async def send(**kwargs):
            with self.assertRaises(pvp.PvpError):
                pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
            raise TimeoutError()
        self.bot.send_message.side_effect=send
        self.run_pub(s["id"])
        self.assertEqual(self.balance(self.other),{"coins":100,"shards":0})
        self.assertEqual(self.balance(self.pid),{"coins":100,"shards":0})
        self.assertEqual(self.row(s["id"])["status"],"publication_failed")
    def test_timeout_deadline_during_send_late_success_never_reopens(self):
        s=self.new(published=False)
        async def send(**kwargs):
            self.now+=pvp.ACCEPT_SECONDS+1
            pvp.expire(self.db,now=self.now)
            return NS(message_id=77)
        self.bot.send_message.side_effect=send
        self.run_pub(s["id"])
        row=self.row(s["id"])
        self.assertEqual((row["status"],row["publication"]),("expired","sent"))
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.assertEqual(self.bot.send_message.await_count,1)
        self.assertIn("закрыт",self.bot.edit_message_text.call_args.kwargs["text"])
        with self.assertRaises(pvp.PvpError):pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
    def test_late_ack_after_recovery_keeps_refunds(self):
        s=self.new(published=False);claim=pub.claim_send(s["id"],self.db,now=self.now)
        self.now+=pub.LEASE_SECONDS+1;pub.recover(self.db,now=self.now)
        pub.acknowledge(s["id"],77,self.db,token=claim["send_token"],revision=1,now=self.now)
        self.assertEqual(self.row(s["id"])["status"],"publication_failed")
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.assertEqual(self.row(s["id"])["message_id"],77)
    def test_closed_callback_is_inert_after_failed_publication(self):
        s=self.new(published=False);pub.claim_send(s["id"],self.db,now=self.now)
        pub.failed_send(s["id"],TimeoutError(),self.db,now=self.now)
        before=self.resources()
        for _ in range(3):
            with self.assertRaises(pvp.PvpError):pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
            pvp.cancel(self.pid,self.wid,s["id"],self.db,now=self.now)
        self.assertEqual(self.resources(),before)
    def test_latest_failed_session_available_from_personal_menu(self):
        s=self.new(published=False);pub.claim_send(s["id"],self.db,now=self.now)
        pub.failed_send(s["id"],TimeoutError(),self.db,now=self.now)
        recent=pvp.latest_session(self.pid,self.wid,self.db)
        self.assertEqual(recent,s["id"])
        from app.mini.events.pvp_handlers import lobby_screen
        _,markup=lobby_screen(self.wid,900123,self.pid,None,[],recent=recent)
        self.assertIn(f"mini:pvp:{self.wid}:900123:view.{s['id']}",
                      [b.callback_data for row in markup.inline_keyboard for b in row])

class RetrySafetyTests(SafetyCase):
    def pending_edit(self):
        s=self.new();self.revise(s["id"]);return s
    def fail(self,sid,error=None):
        self.bot.edit_message_text.side_effect=error or TimeoutError("secret")
        self.run_pub(sid)
    def test_persisted_backoff_sequence_and_terminal_limit(self):
        s=self.pending_edit();before=self.resources()
        for i,delay in enumerate((*pub.BACKOFF,0),1):
            self.fail(s["id"])
            row=self.row(s["id"]);self.assertEqual(row["retry_count"],i)
            expected="exhausted" if i==pub.MAX_EDIT_FAILURES else "retrying"
            self.assertEqual(row["edit_status"],expected)
            count=self.bot.edit_message_text.await_count
            self.tick();self.assertEqual(self.bot.edit_message_text.await_count,count)
            if expected=="retrying":
                self.assertEqual(row["next_retry_at"],self.now+delay)
                self.now=row["next_retry_at"]
        for _ in range(3):self.now+=10000;self.tick()
        self.assertEqual(self.bot.edit_message_text.await_count,pub.MAX_EDIT_FAILURES)
        self.assertEqual(self.resources()[1:],before[1:]) # creator may expire normally, never from edit.
        self.assertEqual(len(self.sql("SELECT * FROM mini_wallet_transactions WHERE operation_key LIKE '%winner%'")),0)
    def test_retry_after_respected_beyond_backoff(self):
        s=self.pending_edit()
        self.fail(s["id"],TelegramRetryAfter(method=EditMessageText(text="x"),message="rate",retry_after=600))
        self.assertEqual(self.row(s["id"])["next_retry_at"],1600)
        self.now=1599;self.tick();self.assertEqual(self.bot.edit_message_text.await_count,1)
        self.now=1600;self.bot.edit_message_text.side_effect=None;self.tick()
        self.assertEqual(self.row(s["id"])["retry_count"],0)
    def test_success_resets_count_and_backoff(self):
        s=self.pending_edit();self.fail(s["id"]);self.now+=10
        self.bot.edit_message_text.side_effect=None;self.run_pub(s["id"])
        row=self.row(s["id"])
        self.assertEqual((row["retry_count"],row["next_retry_at"],row["last_error_at"]),(0,0,0))
        self.assertEqual(row["edit_status"],"synced")
    def test_not_modified_confirms_only_captured_revision(self):
        s=self.pending_edit();claim=pub.claim_edit(s["id"],self.db,now=self.now)
        self.revise(s["id"])
        pub.finish_edit(s["id"],claim["revision"],ok=True,db_path=self.db,token=claim["edit_token"],now=self.now)
        row=self.row(s["id"])
        self.assertLess(row["published_revision"],row["revision"])
        self.assertIsNotNone(pub.claim_edit(s["id"],self.db,now=self.now))
    def test_not_modified_error_is_success(self):
        s=self.pending_edit()
        self.bot.edit_message_text.side_effect=TelegramBadRequest(method=EditMessageText(text="x"),message="message is not modified")
        self.run_pub(s["id"])
        self.assertEqual(self.row(s["id"])["edit_status"],"synced")
        self.assertEqual(self.row(s["id"])["retry_count"],0)
    def test_deleted_message_is_permanent_no_retries_or_finance(self):
        s=self.pending_edit();before=self.resources()
        self.fail(s["id"],TelegramBadRequest(method=EditMessageText(text="x"),message="message to edit not found"))
        self.now+=30
        for _ in range(3):self.tick()
        self.assertEqual(self.bot.edit_message_text.await_count,1)
        self.assertEqual(self.row(s["id"])["edit_status"],"permanent")
        self.assertEqual(self.resources(),before)
    def test_forbidden_is_permanent(self):
        s=self.pending_edit()
        self.fail(s["id"],TelegramForbiddenError(method=EditMessageText(text="x"),message="private"))
        self.assertEqual(self.row(s["id"])["edit_status"],"permanent")
    def test_restart_preserves_live_lease_and_retry_time(self):
        s=self.pending_edit();claim=pub.claim_edit(s["id"],self.db,now=self.now)
        init_mini_db(self.db);pub.recover(self.db,now=self.now+1)
        self.assertEqual(self.row(s["id"])["edit_token"],claim["edit_token"])
        self.assertIsNone(pub.claim_edit(s["id"],self.db,now=self.now+1))
        self.now+=pub.LEASE_SECONDS+1;pub.recover(self.db,now=self.now)
        row=self.row(s["id"]);self.assertEqual(row["retry_count"],1)
        self.assertEqual(row["next_retry_at"],self.now+10)
        init_mini_db(self.db);pub.recover(self.db,now=self.now+1)
        self.assertEqual(self.row(s["id"])["next_retry_at"],row["next_retry_at"])
    def test_concurrent_edit_claim_only_one(self):
        s=self.pending_edit()
        with ThreadPoolExecutor(2) as pool:
            claims=list(pool.map(lambda _:pub.claim_edit(s["id"],self.db,now=self.now),range(2)))
        self.assertEqual(sum(x is not None for x in claims),1)
    def test_handler_and_watcher_do_not_bypass_backoff(self):
        s=self.pending_edit();self.fail(s["id"])
        async def run():await asyncio.gather(pub.publish(self.bot,s["id"],self.db),pub.tick(self.bot,self.db))
        asyncio.run(run())
        self.assertEqual(self.bot.edit_message_text.await_count,1)
    def test_old_failure_preserves_delay_for_new_revision(self):
        s=self.pending_edit();claim=pub.claim_edit(s["id"],self.db,now=self.now)
        self.revise(s["id"])
        pub.finish_edit(s["id"],claim["revision"],ok=False,error=TimeoutError(),db_path=self.db,token=claim["edit_token"],now=self.now)
        self.assertIsNone(pub.claim_edit(s["id"],self.db,now=self.now+9))
        self.assertIsNotNone(pub.claim_edit(s["id"],self.db,now=self.now+10))
    def test_stale_success_cannot_release_new_worker_lease(self):
        s=self.pending_edit();first=pub.claim_edit(s["id"],self.db,now=self.now)
        self.now+=pub.LEASE_SECONDS+1;pub.recover(self.db,now=self.now)
        self.now+=10;second=pub.claim_edit(s["id"],self.db,now=self.now)
        pub.finish_edit(s["id"],first["revision"],ok=True,db_path=self.db,token=first["edit_token"],now=self.now)
        self.assertEqual(self.row(s["id"])["edit_token"],second["edit_token"])
        self.assertIsNone(pub.claim_edit(s["id"],self.db,now=self.now))
    def test_log_is_once_per_attempt_not_per_tick(self):
        s=self.pending_edit()
        with self.assertLogs(pub.log,level="WARNING") as log:
            self.fail(s["id"])
            for _ in range(5):self.tick()
        self.assertEqual(len(log.output),1)
        self.assertNotIn("secret",str(log.output))

class DrainAndOpsTests(SafetyCase):
    def test_pause_closes_open_with_one_refund_and_blocks_stale_callbacks(self):
        s=self.new();self.pause();before=self.resources()
        for _ in range(2):
            self.pause()
            with self.assertRaises(pvp.PvpError):pvp.create(self.third,self.wid,"new",self.db,now=self.now)
            with self.assertRaises(pvp.PvpError):pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
        self.assertEqual(self.resources(),before)
        report=ops.inspect(self.db)
        self.assertEqual((report["open"],report["choosing"],report["active_stake_coins"]),(0,0,0))
        self.assertEqual((report["blocked_create"],report["blocked_accept"]),(2,2))
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_choosing_finishes_in_pause_and_restart_retains_mode(self):
        s=self.accepted();self.pause();init_mini_db(self.db)
        pvp.choose(self.pid,self.wid,s["id"],"saint",self.db,now=self.now)
        pvp.choose(self.other,self.wid,s["id"],"demon",self.db,now=self.now)
        self.run_pub(s["id"])
        report=ops.inspect(self.db)
        self.assertTrue(report["paused"]);self.assertTrue(report["rollback_ready"],report)
        self.assertEqual(self.balance(self.pid)["coins"],103)
        self.assertEqual(self.balance(self.other)["shards"],2)
    def test_choosing_timeout_refunds_under_pause(self):
        s=self.accepted();self.pause();self.now+=pvp.CHOICE_SECONDS+1;self.tick();self.tick()
        self.assertEqual((self.balance(self.pid)["coins"],self.balance(self.other)["coins"]),(100,100))
        self.assertTrue(ops.inspect(self.db)["rollback_ready"])
    def test_resume_requires_explicit_operator_command(self):
        self.pause()
        result=subprocess.run([sys.executable,str(ROOT/"tools/pvp_ops.py"),"--db",str(self.db),
            "--project",str(ROOT),"resume"],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertTrue(ops.inspect(self.db)["paused"])
        result=subprocess.run([sys.executable,str(ROOT/"tools/pvp_ops.py"),"--db",str(self.db),
            "--project",str(ROOT),"resume","--confirm"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(ops.inspect(self.db)["paused"]);self.new()
    def test_pause_does_not_block_solo_or_hero_duels(self):
        from app.mini.events.service import start_session
        from app.mini.duels.service import challenge
        self.pause();hero=self.hero()
        start_session(self.third,self.wid,"rps",5,"solo",self.db)
        challenge(self.pid,self.other,hero["id"],"duel",self.db,now=self.now)
        self.assertEqual(self.balance(self.pid)["coins"],90)
    def test_pause_create_concurrency_cannot_leave_new_open_escrow(self):
        def run(action):
            if action=="pause":return self.pause()
            try:return pvp.create(self.pid,self.wid,"race",self.db,now=self.now)
            except pvp.PvpError:return None
        with ThreadPoolExecutor(2) as pool:list(pool.map(run,("pause","create")))
        report=ops.inspect(self.db)
        self.assertTrue(report["paused"]);self.assertEqual(report["open"],0)
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_rollback_active_open_choosing_and_unpaused_block(self):
        self.assertIn("pause_new_required",ops.inspect(self.db)["blockers"])
        s=self.new();self.assertFalse(ops.inspect(self.db)["rollback_ready"])
        pvp.accept(self.other,self.wid,s["id"],self.db,now=self.now)
        self.pause();self.assertFalse(ops.inspect(self.db)["rollback_ready"])
    def test_terminal_history_allows_rollback_with_proven_ledger(self):
        self.finished();self.pause()
        report=ops.inspect(self.db)
        self.assertTrue(report["rollback_ready"],report)
        before=self.db.read_bytes();ops.inspect(self.db);self.assertEqual(self.db.read_bytes(),before)
    def test_missing_winner_payout_blocks_without_repair(self):
        s=self.finished();self.pause()
        self.sql("DELETE FROM mini_wallet_transactions WHERE operation_key=?",(f"pvp:{s['id']}:winner:{self.pid}",))
        before=self.resources()
        report=ops.inspect(self.db)
        self.assertFalse(report["rollback_ready"])
        self.assertIn("wallet_transition_mismatch",report["sessions"][0]["errors"])
        self.assertEqual(before,self.resources())
    def test_partial_refund_and_duplicate_unexpected_wallet_operations_block(self):
        s=self.accepted();self.pause();self.now+=121;self.tick()
        self.sql("DELETE FROM mini_wallet_transactions WHERE operation_key=?",(f"pvp:{s['id']}:refund:{self.other}",))
        self.assertFalse(ops.inspect(self.db)["rollback_ready"])
        self.sql("""INSERT INTO mini_wallet_transactions
            (player_id,amount,balance_after,reason,reference_type,reference_id,operation_key)
            VALUES(?,5,100,'test','mini_event_pvp',?,'unexpected-duplicate')""",(self.pid,s["id"]))
        self.assertIn("wallet_transition_mismatch",ops.inspect(self.db)["sessions"][0]["errors"])
    def test_shard_receipt_mismatch_and_repeat_result_detected(self):
        s=self.finished();self.pause();before=self.resources()
        pvp.choose(self.pid,self.wid,s["id"],"villager",self.db,now=self.now)
        self.assertEqual(self.resources(),before)
        self.assertEqual(len(self.sql("SELECT * FROM mini_event_pvp_shard_awards")),1)
        self.sql("UPDATE mini_event_pvp_shard_awards SET player_id=?",(self.pid,))
        self.assertIn("shard_receipt_mismatch",ops.inspect(self.db)["sessions"][0]["errors"])
    def test_legacy_shard_uncertainty_requires_explicit_review_not_balance_guess(self):
        s=self.finished();self.pause()
        self.sql("DELETE FROM mini_event_pvp_shard_awards")
        self.sql("UPDATE mini_event_pvp SET economy_version=0")
        self.assertFalse(ops.inspect(self.db)["rollback_ready"])
        before=self.resources()
        report=ops.review(self.db,s["id"],"review-economy","Reviewed external incident evidence")
        self.assertTrue(report["rollback_ready"],report)
        self.assertEqual(self.resources(),before)
    def test_unknown_delivery_needs_review_after_confirmed_refund(self):
        s=self.new(published=False);pub.claim_send(s["id"],self.db,now=self.now)
        pub.failed_send(s["id"],TimeoutError(),self.db,now=self.now);self.pause()
        report=ops.inspect(self.db)
        self.assertFalse(report["rollback_ready"]);self.assertTrue(report["sessions"][0]["refunds_confirmed"])
        before=self.resources();report=ops.review(self.db,s["id"],"review-publication","Orphan announcement acknowledged")
        self.assertTrue(report["rollback_ready"],report);self.assertEqual(self.resources(),before)
    def test_cannot_review_active_or_ongoing_edit_as_resolved(self):
        s=self.new()
        with self.assertRaises(ops.OpsError):ops.review(self.db,s["id"],"review-publication","review")
        self.pause();claim=pub.claim_edit(s["id"],self.db,now=self.now)
        # review() uses the real wall clock; ensure the lease is still live there.
        self.sql("UPDATE mini_event_pvp SET editing_until=?",(int(pub.pvp.timestamp())+100,))
        with self.assertRaises(ops.OpsError):ops.review(self.db,s["id"],"abandon-edit","review")
    def test_explicit_abandon_of_terminal_failed_edit_is_economically_inert(self):
        s=self.finished();self.pause();self.revise(s["id"])
        self.bot.edit_message_text.side_effect=TelegramBadRequest(method=EditMessageText(text="x"),message="not found")
        self.run_pub(s["id"])
        self.assertFalse(ops.inspect(self.db)["rollback_ready"])
        before=self.resources()
        self.assertTrue(ops.review(self.db,s["id"],"abandon-edit","Message removed by operator")["rollback_ready"])
        self.assertEqual(self.resources(),before)
    def test_corrupt_missing_unreadable_and_partial_db_fail_closed(self):
        missing=Path(self.tmp.name)/"missing.db"
        with self.assertRaises(OSError):ops.inspect(missing)
        self.assertFalse(missing.exists())
        corrupt=Path(self.tmp.name)/"corrupt.db";corrupt.write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.Error):ops.inspect(corrupt)
        with patch.object(ops.sqlite3,"connect",side_effect=PermissionError("secret")):
            with self.assertRaises(PermissionError):ops.inspect(self.db)
        empty=Path(self.tmp.name)/"empty.db"
        with closing(sqlite3.connect(empty)):pass
        self.assertTrue(ops.inspect(empty)["rollback_ready"])
        self.sql("DROP TABLE mini_event_pvp_members")
        with self.assertRaises((ops.OpsError,sqlite3.Error)):ops.inspect(self.db)
    def test_readonly_sees_committed_wal(self):
        self.pause()
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE mini_event_pvp_control SET paused=0");writer.commit()
            self.assertIn("pause_new_required",ops.inspect(self.db)["blockers"])
    def test_pause_can_still_block_entries_with_an_ambiguous_stake(self):
        s=self.new(published=False)
        self.sql("DELETE FROM mini_wallet_transactions WHERE operation_key=?",(f"pvp:{s['id']}:stake:{self.pid}",))
        result=self.pause()
        self.assertEqual(result["unresolved"],[s["id"]]);self.assertTrue(ops.inspect(self.db)["paused"])
        self.assertEqual(self.balance(self.pid)["coins"],95)


class AmbiguousActiveLedgerTests(SafetyCase):
    def test_partial_refund_in_active_game_is_not_automatically_completed(self):
        s=self.new()
        self.sql("""INSERT INTO mini_wallet_transactions
            (player_id,amount,balance_after,reason,reference_type,reference_id,operation_key)
            VALUES(?,5,100,'historical ambiguity','mini_event_pvp',?,?)""",
            (self.pid,s["id"],f"pvp:{s['id']}:refund:{self.pid}"))
        before=self.resources()
        result=self.pause()
        self.assertEqual(result["unresolved"],[s["id"]])
        self.assertEqual(self.resources(),before)
        self.assertEqual(self.row(s["id"])["status"],"open")
        self.now+=121
        with self.assertRaises(pvp.PvpLedgerError):pvp.expire(self.db,now=self.now)
        self.assertEqual(self.resources(),before)

    def test_partial_safety_columns_cannot_pass_even_with_empty_history(self):
        self.pause()
        self.sql("ALTER TABLE mini_event_pvp DROP COLUMN send_token")
        with self.assertRaises(ops.OpsError):ops.inspect(self.db)


    def test_proven_unsent_open_drain_is_ready_without_fake_delivery(self):
        s=self.new(published=False)
        self.pause()
        report=ops.inspect(self.db)
        self.assertTrue(report["rollback_ready"],report)
        self.assertEqual(self.row(s["id"])["publication"],"failed")
        self.assertTrue(report["sessions"][0]["refunds_confirmed"])
        self.bot.send_message.assert_not_called()

    def test_isolated_control_table_is_partial_not_pre_pvp(self):
        file=Path(self.tmp.name)/"partial-control.db"
        with closing(sqlite3.connect(file)) as conn:
            conn.execute("CREATE TABLE mini_event_pvp_control(id INTEGER)")
        with self.assertRaises(ops.OpsError):ops.inspect(file)
