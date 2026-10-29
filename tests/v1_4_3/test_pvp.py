"""Financial, race, restart and Telegram recovery contracts for Events PvP."""
import asyncio
import itertools
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from tests.v1_3.support import MiniCase
from app.mini.db import connect_mini_db
from app.mini.events import pvp,service as solo
from app.mini.events import pvp_publication as publication
from app.mini.events.pvp_handlers import public_screen,personal_screen
from app.mini.players import create_mini_player
from app.mini.wallet import InsufficientFundsError
from app.mini.schema import init_mini_db

class PvpCase(MiniCase):
    def setUp(self):
        super().setUp()
        import os
        self.feature_modes=patch.dict(os.environ, {"MINI_PVP_ENABLED":"1","MINI_DUELS_ENABLED":"1"})
        self.feature_modes.start();self.addCleanup(self.feature_modes.stop)
        self.wid=self.world["id"]
        self.other=create_mini_player(self.wid,900124,"@other","Соперник",self.db)["id"]
        self.third=create_mini_player(self.wid,900125,"@third","Третий",self.db)["id"]
        self.sql("UPDATE mini_players SET coins=100,shards=0")
    def tearDown(self):
        self.feature_modes.stop()
        super().tearDown()
    def create(self,key="create"):
        return pvp.create(self.pid,self.wid,key,self.db,now=1000)
    def published(self,sid):
        # Economy-only fixtures acknowledge a synthetic successful publication.
        self.sql("UPDATE mini_event_pvp SET publication='sent',message_id=77,published_revision=revision WHERE id=?",(sid,))
    def accept(self,sid,pid=None):
        self.published(sid)
        return pvp.accept(pid or self.other,self.wid,sid,self.db,now=1001)
    def choose(self,sid,pid,choice,now=1002):
        return pvp.choose(pid,self.wid,sid,choice,self.db,now=now)
    def resources(self):
        return [(r["id"],r["coins"],r["shards"]) for r in self.sql("SELECT id,coins,shards FROM mini_players ORDER BY id")]
    def balance(self,pid):
        return self.sql("SELECT coins,shards FROM mini_players WHERE id=?",(pid,))[0]
    def check_integrity(self):
        self.assertEqual(self.sql("PRAGMA integrity_check"),[{"integrity_check":"ok"}])
        self.assertEqual(self.sql("PRAGMA foreign_key_check"),[])
        self.assertFalse(self.sql("SELECT 1 FROM mini_players WHERE coins<0 OR shards<0"))
class PvpTests(PvpCase):
    def test_creation_and_idempotency(self):
        s=self.create()
        self.assertEqual(s["status"],"open")
        self.assertEqual(self.balance(self.pid)["coins"],95)
        self.assertEqual(self.create()["id"],s["id"])
        self.assertEqual(self.balance(self.pid)["coins"],95)
        self.check_integrity()
    def test_insufficient_creator_rolls_back_everything(self):
        self.sql("UPDATE mini_players SET coins=4 WHERE id=?",(self.pid,))
        before=self.resources()
        with self.assertRaises(InsufficientFundsError):self.create()
        self.assertEqual(self.resources(),before)
        self.assertEqual(self.sql("SELECT * FROM mini_event_pvp"),[])
        self.assertEqual(self.sql("SELECT * FROM mini_event_pvp_members"),[])
    def test_insufficient_acceptor_keeps_open(self):
        s=self.create();self.sql("UPDATE mini_players SET coins=4 WHERE id=?",(self.other,))
        before=self.resources()
        with self.assertRaises(InsufficientFundsError):self.accept(s["id"])
        self.assertEqual(self.resources(),before)
        self.assertEqual(pvp.get_session(self.wid,s["id"],self.db,now=1002)["status"],"open")
    def test_accept_and_repeat(self):
        s=self.create();accepted=self.accept(s["id"])
        self.assertEqual(accepted["acceptor_id"],self.other)
        self.assertEqual(accepted["choose_until"],1121)
        self.accept(s["id"])
        self.assertEqual(self.balance(self.other)["coins"],95)
        self.assertEqual(len(self.sql("SELECT * FROM mini_wallet_transactions WHERE reference_type='mini_event_pvp'")),2)
    def test_self_rejected(self):
        with self.assertRaises(pvp.PvpError):self.accept(self.create()["id"],self.pid)
        self.assertEqual(self.balance(self.pid)["coins"],95)
    def test_other_world_rejected(self):
        s=self.create()
        self.sql("INSERT INTO mini_worlds(chat_id,thread_id,name) VALUES(-12345,1,'Other')")
        wid=self.sql("SELECT MAX(id) AS id FROM mini_worlds")[0]["id"]
        foreign=create_mini_player(wid,900126,"@foreign","Другой мир",self.db)["id"]
        self.sql("UPDATE mini_players SET coins=100 WHERE id=?",(foreign,))
        with self.assertRaises(pvp.PvpError):self.accept(s["id"],foreign)
        with self.assertRaises(pvp.PvpError):pvp.get_session(wid,s["id"],self.db,now=1002)
        self.assertEqual(self.balance(foreign)["coins"],100)
    def test_two_concurrent_acceptors_only_one_debit(self):
        s=self.create()
        def run(pid):
            try:return self.accept(s["id"],pid)["acceptor_id"]
            except pvp.PvpError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,(self.other,self.third)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(sorted(self.balance(p)["coins"] for p in (self.other,self.third)),[95,100])
        self.assertEqual(len(self.sql("SELECT * FROM mini_event_pvp_members")),2)
        self.check_integrity()
    def test_concurrent_creator_buttons_one_session(self):
        def run(key):
            try:return self.create(key)["id"]
            except pvp.PvpError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,("one","two")))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.balance(self.pid)["coins"],95)
    def test_secret_choices_are_not_in_public_or_opponent_screen(self):
        s=self.accept(self.create()["id"])
        chosen=self.choose(s["id"],self.pid,"demon")
        public=public_screen(chosen)[0]
        waiting=personal_screen(self.wid,900123,self.pid,chosen)[0]
        rival=personal_screen(self.wid,900124,self.other,chosen)[0]
        self.assertNotIn("выбрал",public)
        self.assertIn("Выбор принят",waiting)
        self.assertNotIn("выбрал",rival)
        self.assertNotIn("choice",public)
        self.assertEqual(self.balance(self.pid)["coins"],95)
    def test_choice_cannot_change(self):
        s=self.accept(self.create()["id"])
        self.choose(s["id"],self.pid,"saint")
        self.choose(s["id"],self.pid,"villager")
        result=self.choose(s["id"],self.other,"demon")
        self.assertEqual(result["winner_id"],self.pid)
    def test_unauthorized_choice_cannot_reveal_or_change(self):
        s=self.accept(self.create()["id"]);before=self.resources()
        with self.assertRaises(pvp.PvpError):self.choose(s["id"],self.third,"demon")
        with self.assertRaises(pvp.PvpError):personal_screen(self.wid,900125,self.third,s)
        self.assertEqual(self.resources(),before)
    def test_invalid_choice_no_mutation(self):
        s=self.accept(self.create()["id"]);before=self.resources()
        with self.assertRaises(pvp.PvpError):self.choose(s["id"],self.pid,"unknown")
        self.assertEqual(self.resources(),before)
    def test_cancel_open_exact_refund_and_replay(self):
        s=self.create()
        for _ in range(3):pvp.cancel(self.pid,self.wid,s["id"],self.db,now=1002)
        self.assertEqual(self.balance(self.pid),{"coins":100,"shards":0})
        self.assertEqual(len(self.sql("SELECT * FROM mini_wallet_transactions WHERE operation_key LIKE 'pvp:%:refund:%'")),1)
    def test_cancel_after_accept_rejected_without_refund(self):
        s=self.accept(self.create()["id"]);before=self.resources()
        with self.assertRaises(pvp.PvpError):pvp.cancel(self.pid,self.wid,s["id"],self.db,now=1002)
        self.assertEqual(self.resources(),before)
    def test_opponent_cannot_cancel(self):
        s=self.create()
        with self.assertRaises(pvp.PvpError):pvp.cancel(self.other,self.wid,s["id"],self.db,now=1002)
        self.assertEqual(self.balance(self.pid)["coins"],95)
    def test_open_timeout_persists_through_reinitialization(self):
        s=self.create()
        init_mini_db(self.db)
        self.assertEqual(pvp.expire(self.db,now=1120),1)
        self.assertEqual(pvp.expire(self.db,now=1120),0)
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.assertEqual(pvp.get_session(self.wid,s["id"],self.db,now=1120)["status"],"expired")
    def test_choice_timeout_after_restart_refunds_both(self):
        s=self.accept(self.create()["id"])
        self.choose(s["id"],self.pid,"saint")
        init_mini_db(self.db)
        pvp.expire(self.db,now=1121);pvp.expire(self.db,now=1122)
        self.assertEqual(self.balance(self.pid),{"coins":100,"shards":0})
        self.assertEqual(self.balance(self.other),{"coins":100,"shards":0})
        self.assertEqual(self.choose(s["id"],self.other,"demon",now=1122)["status"],"expired")
    def test_timeout_without_either_choice_refunds_both(self):
        s=self.accept(self.create()["id"]);pvp.expire(self.db,now=1121)
        self.assertEqual(self.balance(self.pid)["coins"],100)
        self.assertEqual(self.balance(self.other)["coins"],100)
    def test_accept_at_deadline_cannot_spend(self):
        s=self.create()
        with self.assertRaises(pvp.PvpError):pvp.accept(self.other,self.wid,s["id"],self.db,now=1120)
        pvp.expire(self.db,now=1120)
        self.assertEqual(self.balance(self.other)["coins"],100)
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_solo_and_pvp_mutually_exclude_both_directions(self):
        s=self.create()
        for game in ("rps","labyrinth"):
            with self.assertRaises(pvp.PvpError):solo.start_session(self.pid,self.wid,game,5,"solo",self.db)
        pvp.cancel(self.pid,self.wid,s["id"],self.db,now=1002)
        solo.start_session(self.pid,self.wid,"rps",5,"solo",self.db)
        with self.assertRaises(pvp.PvpError):self.create("again")
    def test_acceptor_in_solo_is_rejected_without_spend(self):
        s=self.create();solo.start_session(self.other,self.wid,"rps",5,"solo",self.db)
        with self.assertRaises(pvp.PvpError):self.accept(s["id"])
        self.assertEqual(self.balance(self.other)["coins"],95)
    def test_hero_duel_can_coexist_without_shared_locks(self):
        from app.mini.duels.service import challenge,act
        hero=self.hero()
        duel=challenge(self.pid,self.other,hero["id"],"duel",self.db,now=1000)
        s=self.accept(self.create()["id"])
        self.choose(s["id"],self.pid,"saint")
        self.choose(s["id"],self.other,"demon")
        act(duel["id"],self.pid,"cancel",self.db,now=1003)
        self.assertEqual(self.balance(self.pid)["coins"],103)
        self.assertEqual(self.balance(self.other),{"coins":95,"shards":2})
    def test_failure_during_shard_reward_rolls_back_second_choice_and_coins(self):
        s=self.accept(self.create()["id"]);self.choose(s["id"],self.pid,"saint")
        before=self.resources()
        with patch.object(pvp,"change_shards_in_transaction",side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):self.choose(s["id"],self.other,"demon")
        self.assertEqual(self.resources(),before)
        row=self.sql("SELECT choice FROM mini_event_pvp_members WHERE player_id=?",(self.other,))[0]
        self.assertIsNone(row["choice"])
        self.choose(s["id"],self.other,"demon")
        self.assertEqual(self.balance(self.pid)["coins"],103)
    def test_simultaneous_second_choices_only_one_payout(self):
        s=self.accept(self.create()["id"])
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda pair:self.choose(s["id"],*pair),((self.pid,"saint"),(self.other,"demon"))))
        self.assertEqual(self.balance(self.pid)["coins"],103)
        self.assertEqual(self.balance(self.other)["shards"],2)
        self.check_integrity()

    def test_solo_and_pvp_concurrent_start_only_one_stake(self):
        def run(mode):
            try:
                if mode=="pvp":self.create()
                else:solo.start_session(self.pid,self.wid,"rps",5,"concurrent",self.db)
                return True
            except pvp.PvpError:return False
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,("solo","pvp")))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.balance(self.pid)["coins"],95)
        self.check_integrity()
    def test_cancel_accept_race_has_one_valid_financial_outcome(self):
        s=self.create()
        def run(action):
            try:
                if action=="cancel":return pvp.cancel(self.pid,self.wid,s["id"],self.db,now=1002)["status"]
                return self.accept(s["id"])["status"]
            except pvp.PvpError:return "rejected"
        with ThreadPoolExecutor(2) as pool:list(pool.map(run,("cancel","accept")))
        row=pvp.get_session(self.wid,s["id"],self.db,now=1003)
        if row["status"]=="cancelled":
            self.assertEqual((self.balance(self.pid)["coins"],self.balance(self.other)["coins"]),(100,100))
        else:
            self.assertEqual(row["status"],"choosing")
            self.assertEqual((self.balance(self.pid)["coins"],self.balance(self.other)["coins"]),(95,95))
        self.check_integrity()

def pair_test(first,second):
    def test(self):
        s=self.accept(self.create()["id"])
        self.choose(s["id"],self.pid,first)
        init_mini_db(self.db)
        result=self.choose(s["id"],self.other,second)
        expected=solo.rps_outcome(first,second)
        if expected=="draw":
            self.assertEqual((self.balance(self.pid),self.balance(self.other)),
                             ({"coins":100,"shards":0},{"coins":100,"shards":0}))
        else:
            winner=self.pid if expected=="win" else self.other
            loser=self.other if expected=="win" else self.pid
            self.assertEqual(result["winner_id"],winner)
            self.assertEqual(self.balance(winner),{"coins":103,"shards":0})
            self.assertEqual(self.balance(loser),{"coins":95,"shards":2})
        before=self.resources()
        init_mini_db(self.db)
        for _ in range(3):
            self.choose(s["id"],self.pid,second);self.choose(s["id"],self.other,first)
        self.assertEqual(before,self.resources())
        self.assertEqual(public_screen(result)[1].inline_keyboard,[])
        self.check_integrity()
    return test
for a,b in itertools.product(solo.RPS_CHOICES,repeat=2):
    setattr(PvpTests,f"test_pair_{a}_{b}",pair_test(a,b))

class PublicationTests(PvpCase):
    # Dedicated methods below are independent from inherited economic regression.
    def create_current(self):
        return pvp.create(self.pid,self.wid,"publication",self.db)
    def bot(self,**kwargs):
        return SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
                               edit_message_text=AsyncMock(),**kwargs)
    def test_concurrent_publication_has_one_send(self):
        s=self.create_current();bot=self.bot()
        asyncio.run(self.publish_twice(bot,s["id"]))
        self.assertEqual(bot.send_message.await_count,1)
        row=pvp.get_session(self.wid,s["id"],self.db)
        self.assertEqual((row["publication"],row["message_id"]),("sent",77))
        self.assertEqual(bot.send_message.call_args.kwargs["chat_id"],self.world["chat_id"])
        self.assertEqual(bot.send_message.call_args.kwargs["message_thread_id"],self.world["thread_id"] or None)
    async def publish_twice(self,bot,sid):
        await asyncio.gather(publication.publish(bot,sid,self.db),publication.publish(bot,sid,self.db))
    def test_uncertain_network_send_refunds_never_resends(self):
        s=self.create_current();bot=self.bot();bot.send_message.side_effect=TimeoutError("unknown delivery")
        asyncio.run(publication.publish(bot,s["id"],self.db))
        asyncio.run(publication.tick(bot,self.db))
        self.assertEqual(bot.send_message.await_count,1)
        row=pvp.get_session(self.wid,s["id"],self.db)
        self.assertEqual((row["publication"],row["status"]),("uncertain","publication_failed"))
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_restart_after_claim_refunds_without_republish(self):
        s=self.create_current()
        self.assertIsNotNone(publication.claim_send(s["id"],self.db))
        publication.recover(self.db,now=pvp.timestamp()+publication.LEASE_SECONDS+1);publication.recover(self.db,now=pvp.timestamp()+publication.LEASE_SECONDS+2)
        bot=self.bot();asyncio.run(publication.tick(bot,self.db))
        bot.send_message.assert_not_awaited()
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_restart_before_send_claim_publishes_once(self):
        s=self.create_current();publication.recover(self.db)
        bot=self.bot();asyncio.run(publication.tick(bot,self.db));asyncio.run(publication.tick(bot,self.db))
        self.assertEqual(bot.send_message.await_count,1)
    def test_restart_finished_before_result_edit_no_second_reward(self):
        s=self.create_current();bot=self.bot();asyncio.run(publication.publish(bot,s["id"],self.db))
        pvp.accept(self.other,self.wid,s["id"],self.db)
        pvp.choose(self.pid,self.wid,s["id"],"saint",self.db)
        pvp.choose(self.other,self.wid,s["id"],"demon",self.db)
        before=self.resources();publication.recover(self.db)
        asyncio.run(publication.tick(bot,self.db));asyncio.run(publication.tick(bot,self.db))
        self.assertEqual(self.resources(),before)
        self.assertEqual(bot.send_message.await_count,1)
        self.assertIn("Дуэль завершена",bot.edit_message_text.call_args.kwargs["text"])
        self.assertEqual(bot.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard,[])
    def test_edit_failure_retries_known_message_only(self):
        s=self.create_current();bot=self.bot()
        asyncio.run(publication.publish(bot,s["id"],self.db))
        self.sql("UPDATE mini_event_pvp SET revision=revision+1 WHERE id=?",(s["id"],))
        bot.edit_message_text.side_effect=TimeoutError("edit")
        asyncio.run(publication.publish(bot,s["id"],self.db))
        bot.edit_message_text.side_effect=None
        asyncio.run(publication.tick(bot,self.db))
        self.assertEqual(bot.edit_message_text.await_count,1)
        with patch.object(publication,"timestamp",return_value=pvp.timestamp()+11):
            asyncio.run(publication.tick(bot,self.db))
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(bot.edit_message_text.await_count,2)


    def test_late_edit_ack_keeps_current_revision_pending(self):
        s=self.create_current()
        self.sql("UPDATE mini_event_pvp SET publication='sent',message_id=77,revision=4,published_revision=4 WHERE id=?",(s["id"],))
        publication.finish_edit(s["id"],1,ok=True,db_path=self.db)
        self.assertIsNotNone(publication.claim_edit(s["id"],self.db))

class PvpHandlerTests(PvpCase):
    def patches(self):
        from contextlib import ExitStack
        from functools import partial
        from app.mini.ui import context
        from app.mini.players import get_mini_player,touch_mini_player
        from app.mini.events import pvp_handlers as ui
        stack=ExitStack()
        stack.enter_context(patch.object(context,"get_mini_world_by_id",return_value=dict(self.world,enabled=1)))
        stack.enter_context(patch.object(context,"get_mini_player",side_effect=partial(get_mini_player,db_path=self.db)))
        stack.enter_context(patch.object(context,"touch_mini_player",side_effect=partial(touch_mini_player,db_path=self.db)))
        stack.enter_context(patch.object(context,"is_mini_topic",return_value=True))
        for name in ("create","accept","choose","cancel","get_session","lobby","latest_session"):
            original=getattr(pvp,name)
            stack.enter_context(patch.object(pvp,name,side_effect=partial(original,db_path=self.db)))
        stack.enter_context(patch.object(ui,"publish",new_callable=AsyncMock))
        return stack
    def cb(self,user=900123,owner=None,payload="home",chat=None,thread=None):
        from tests.v1_4_2.test_navigation_flows import callback
        return callback(user=user,data=f"mini:pvp:{self.wid}:{user if owner is None else owner}:{payload}",
                        chat=self.world["chat_id"] if chat is None else chat,
                        thread=self.world["thread_id"] if thread is None else thread)
    def test_handler_accept_opens_two_private_screens(self):
        from app.mini.events import pvp_handlers as ui
        s=pvp.create(self.pid,self.wid,"ui",self.db)
        self.published(s["id"])
        cb=self.cb(user=900124,owner=0,payload=f"join.{s['id']}")
        with self.patches():asyncio.run(ui.action(cb))
        calls=cb.bot.send_message.call_args_list
        receivers={c.kwargs["ephemeral_message_parameters"].receiver_user_id for c in calls}
        self.assertEqual(receivers,{900123,900124})
        self.assertTrue(all("Святоша" in c.kwargs["text"] for c in calls))
        self.assertEqual(self.balance(self.other)["coins"],95)
    def test_handler_create_publication_failure_is_visible(self):
        from app.mini.events import pvp_handlers as ui
        cb=self.cb(payload="create.aabbccdd")
        async def fail(bot,sid):
            with connect_mini_db(self.db) as conn:
                conn.execute("UPDATE mini_event_pvp SET publication='sending' WHERE id=?",(sid,))
            publication.failed_send(sid,"timeout",self.db)
        with self.patches(),patch.object(ui,"publish",side_effect=fail):
            asyncio.run(ui.action(cb))
        self.assertIn("ставка возвращена",cb.bot.send_message.call_args.kwargs["text"])
        self.assertEqual(self.balance(self.pid)["coins"],100)
    def test_personal_button_owner_wrong_chat_and_wrong_topic_rejected(self):
        from app.mini.events import pvp_handlers as ui
        s=pvp.create(self.pid,self.wid,"ui",self.db)
        self.published(s["id"])
        variants=[self.cb(user=900124,owner=900123,payload=f"cancel.{s['id']}"),
                  self.cb(payload=f"cancel.{s['id']}",chat=-999),
                  self.cb(payload=f"cancel.{s['id']}",thread=999)]
        for cb in variants:
            with self.patches():asyncio.run(ui.action(cb))
            self.assertTrue(cb.answer.call_args.kwargs["show_alert"])
        self.assertEqual(self.balance(self.pid)["coins"],95)
    def test_open_choice_button_rejects_nonparticipant(self):
        from app.mini.events import pvp_handlers as ui
        s=pvp.create(self.pid,self.wid,"ui",self.db)
        self.published(s["id"])
        pvp.accept(self.other,self.wid,s["id"],self.db)
        cb=self.cb(user=900125,owner=0,payload=f"view.{s['id']}")
        with self.patches():asyncio.run(ui.action(cb))
        self.assertTrue(cb.answer.call_args.kwargs["show_alert"])
        cb.bot.send_message.assert_not_called()
    def test_public_callback_cannot_submit_choice_for_anyone(self):
        from app.mini.events import pvp_handlers as ui
        s=pvp.create(self.pid,self.wid,"ui",self.db)
        self.published(s["id"])
        pvp.accept(self.other,self.wid,s["id"],self.db)
        cb=self.cb(owner=0,payload=f"pick.{s['id']}.s")
        with self.patches():asyncio.run(ui.action(cb))
        self.assertTrue(cb.answer.call_args.kwargs["show_alert"])
        self.assertTrue(all(r["choice"] is None for r in self.sql("SELECT choice FROM mini_event_pvp_members")))
    def test_public_invitation_wrong_location_cannot_spend(self):
        from app.mini.events import pvp_handlers as ui
        s=pvp.create(self.pid,self.wid,"ui",self.db)
        self.published(s["id"])
        for chat,thread in ((-999,self.world["thread_id"]),(self.world["chat_id"],999)):
            cb=self.cb(user=900124,owner=0,payload=f"join.{s['id']}",chat=chat,thread=thread)
            with self.patches():asyncio.run(ui.action(cb))
            self.assertTrue(cb.answer.call_args.kwargs["show_alert"])
        self.assertEqual(self.balance(self.other)["coins"],100)
