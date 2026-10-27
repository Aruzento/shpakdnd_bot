"""Second-wave real SQLite and Telegram-boundary regressions; no live Telegram."""
import asyncio
from contextlib import closing
import json
import random
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup, EphemeralMessageParameters
from tests.v1_3.support import MiniCase
from app.mini.ui import windows
from app.stories import service, feedback, handlers, publication
from app.stories.repository import transaction
from app.stories.presentation import render, chunks

class Windows(MiniCase):
    def setUp(self):
        super().setUp()
        self.wid=self.world["id"]
    def reserve(self): return windows.reserve(1,self.wid,900123,self.db)
    def current(self): return windows.window(1,self.wid,900123,self.db)
    def activate(self,g,chat=-1001,mid=10,eid=20):
        return windows.activate(1,self.wid,900123,g,chat,mid,eid,self.db)
    def test_first_delivery_and_reopen_invalidates_previous(self):
        g,old=self.reserve();self.assertIsNone(old);self.activate(g)
        old=self.current();g2,_=self.reserve()
        self.assertFalse(windows.matches(self.current(),NS(chat=NS(id=-1001),ephemeral_message_id=20)))
        self.activate(g2,mid=11,eid=21)
        self.assertEqual(self.current()["generation"],2)
        self.assertEqual(old["ephemeral_id"],20)
    def test_late_delivery_cannot_revive_old_generation(self):
        a,_=self.reserve();b,_=self.reserve()
        self.assertTrue(self.activate(b));self.assertFalse(self.activate(a))
        self.assertEqual(self.current()["generation"],b)
    def test_concurrent_reservations_and_restart(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            generations=list(pool.map(lambda _: self.reserve()[0],range(2)))
        self.assertEqual(sorted(generations),[1,2])
        from app.mini.schema import init_mini_db
        init_mini_db(self.db)
        self.assertEqual(self.current()["generation"],2)
    def test_group_to_dm_retires_group_identity(self):
        g,_=self.reserve();self.activate(g)
        g,_=self.reserve();self.activate(g,chat=900123,mid=12,eid=None)
        self.assertTrue(windows.matches(self.current(),NS(chat=NS(id=900123),message_id=12,ephemeral_message_id=None)))
        self.assertFalse(windows.matches(self.current(),NS(chat=NS(id=-1001),ephemeral_message_id=20)))
    def test_foreign_and_old_actual_middleware(self):
        # Keep the real journal function outside the patch to avoid recursive mocks.
        real=windows.window
        async def checked():
            g,_=self.reserve();self.activate(g)
            handler=AsyncMock()
            cb=NS(data=f"mini:home:{self.wid}:900123",from_user=NS(id=900124),
                message=NS(chat=NS(id=-1001),ephemeral_message_id=20),bot=NS(id=1),answer=AsyncMock())
            guard=windows.WindowGuard()
            with patch.object(windows,"window",side_effect=lambda *a:real(*a,self.db)):
                await guard(handler,NS(callback_query=cb),{})
                cb.from_user.id=900123;cb.message.ephemeral_message_id=19
                await guard(handler,NS(callback_query=cb),{})
                handler.assert_not_awaited()
                cb.message.ephemeral_message_id=20
                await guard(handler,NS(callback_query=cb),{})
                handler.assert_awaited_once()
        asyncio.run(checked())
    def test_shared_boss_ids_are_not_mistaken_for_menu_owner(self):
        for value in ("miniboss:history:1:77:0","miniboss:hit:1:77:1:1","miniboss:skip:1:77:x","mini:raidopen:1:77"):
            self.assertIsNone(windows.personal(value))
        self.assertEqual(windows.personal("miniboss:heroes:1:900123:77:0"),(1,900123))
        async def run():
            cb=NS(data="miniboss:history:1:77:0",from_user=NS(id=900123),
                  message=NS(chat=NS(id=-1001,type="group"),ephemeral_message_id=None))
            handler=AsyncMock()
            await windows.WindowGuard()(handler,NS(callback_query=cb),{})
            handler.assert_awaited_once()
        asyncio.run(run())

    def test_failed_send_leaves_old_window_inactive(self):
        async def run():
            g,_=self.reserve();self.activate(g)
            markup=InlineKeyboardMarkup(inline_keyboard=[[Button(text="Home",callback_data=f"mini:home:{self.wid}:900123")]])
            method=SendMessage(chat_id=-1001,text="Private",reply_markup=markup,
                ephemeral_message_parameters=EphemeralMessageParameters(receiver_user_id=900123))
            real=windows.reserve
            with patch.object(windows,"reserve",side_effect=lambda *a:real(*a,self.db)):
                with self.assertRaises(RuntimeError):
                    await windows.WindowDelivery()(AsyncMock(side_effect=RuntimeError("uncertain")),NS(id=1),method)
            self.assertIsNone(self.current()["chat_id"])
        asyncio.run(run())
    def test_confirmed_delivery_survives_delete_failure_and_keeps_new_identity(self):
        async def run():
            g,_=self.reserve();self.activate(g)
            method=SendMessage(chat_id=-1001,text="Private",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[Button(text="Home",callback_data=f"mini:home:{self.wid}:900123")]]),
                ephemeral_message_parameters=EphemeralMessageParameters(receiver_user_id=900123))
            bot=NS(id=1,delete_ephemeral_message=AsyncMock(side_effect=RuntimeError("delete unavailable")))
            reserve,activate=windows.reserve,windows.activate
            with patch.object(windows,"reserve",side_effect=lambda *a:reserve(*a,self.db)),patch.object(windows,"activate",side_effect=lambda *a:activate(*a,self.db)):
                await windows.WindowDelivery()(AsyncMock(return_value=NS(message_id=11,ephemeral_message_id=21)),bot,method)
            self.assertEqual(self.current()["ephemeral_id"],21)
            self.assertFalse(windows.matches(self.current(),NS(chat=NS(id=-1001),ephemeral_message_id=20)))
        asyncio.run(run())

    def test_public_card_not_reserved_or_deleted(self):
        async def run():
            method=SendMessage(chat_id=-1001,text="Public",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[Button(text="Open",callback_data=f"mini:home:{self.wid}:900123")]]))
            bot=NS(id=1,delete_message=AsyncMock(),delete_ephemeral_message=AsyncMock())
            await windows.WindowDelivery()(AsyncMock(return_value=NS(message_id=10)),bot,method)
            self.assertIsNone(self.current());bot.delete_message.assert_not_awaited();bot.delete_ephemeral_message.assert_not_awaited()
        asyncio.run(run())
    def test_explicit_delete_handles_missing_receiver_and_error(self):
        from app.mini.ui.transport import delete_current_ephemeral
        async def run():
            cb=NS(message=NS(chat=NS(id=-1001),ephemeral_message_id=20),from_user=NS(id=900123),
                bot=NS(delete_ephemeral_message=AsyncMock()))
            self.assertTrue(await delete_current_ephemeral(cb))
            cb.bot.delete_ephemeral_message.assert_awaited_once_with(chat_id=-1001,receiver_user_id=900123,ephemeral_message_id=20)
            cb.bot.delete_ephemeral_message.side_effect=TelegramBadRequest(method=SendMessage(chat_id=1,text="x"),message="not found")
            self.assertFalse(await delete_current_ephemeral(cb))
        asyncio.run(run())

class StoryFeedback(MiniCase):
    def setUp(self):
        super().setUp()
        self.admin=self.enterContext(patch.object(service,"_admin"))
        self.chat,self.thread=self.world["chat_id"],self.world["thread_id"]
        self.story={"schema_version":2,"visit_policy":"once","code":"fixture","title":"Title","description":"Fixture","start_node":"s",
            "nodes":{"s":{"text":"Сцена 1. Лес\n\nАвторский текст","choices":[{"id":"x","text":"Полный выбор","next":"e"}]},
                     "e":{"text":"Конец","end":True},"e2":{"text":"Другой конец","end":True}}}
        self.sid=self.make_session()
    def make_session(self,ending="e"):
        story=json.loads(json.dumps(self.story));story["nodes"]["s"]["choices"][0]["next"]=ending
        self.sql("INSERT INTO story_sessions(chat_id,thread_id,code,status,snapshot_json,node_id,step,creator_id,created_at) VALUES(?,?,?,'active',?,'s',1,1,100)",
            (self.chat,self.thread,"fixture",json.dumps(story)))
        sid=self.sql("SELECT MAX(id) id FROM story_sessions")[0]["id"]
        for uid,mode in ((900123,"mini"),(900124,"dnd"),(900125,"mini")):
            self.sql("INSERT INTO story_participants VALUES(?,?,?,'{}',?)",(sid,uid,mode,int(uid!=900125)))
        self.sql("INSERT INTO story_publications(session_id,step) VALUES(?,1)",(sid,))
        return sid
    def status(self): return service.status(self.chat,self.thread,self.db,session_id=self.sid)
    def finish(self,key="v"):
        service.vote(self.sid,self.chat,self.thread,900123,1,"x",key+"1",self.db)
        return service.vote(self.sid,self.chat,self.thread,900124,1,"x",key+"2",self.db)
    def test_achievements_atomic_unique_and_no_currency(self):
        before=self.sql("SELECT coins,shards,mythic_shards FROM mini_players")
        self.finish()
        self.assertEqual({r["user_id"] for r in self.sql("SELECT * FROM story_achievements")},{900123,900124})
        self.assertEqual(before,self.sql("SELECT coins,shards,mythic_shards FROM mini_players"))
        self.sid=self.make_session();self.finish("repeat")
        self.assertEqual(len(self.sql("SELECT * FROM story_achievements")),2)
        self.sid=self.make_session("e2");self.finish("other")
        self.assertEqual(len(self.sql("SELECT * FROM story_achievements")),4)
    def test_concurrent_last_vote_and_replay_only_one_achievement(self):
        service.vote(self.sid,self.chat,self.thread,900123,1,"x","one",self.db)
        def last(_):
            return service.vote(self.sid,self.chat,self.thread,900124,1,"x","last",self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(last,range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(self.sql("SELECT * FROM story_achievements")),2)
    def test_uncertain_publication_restart_does_not_repeat_achievement(self):
        self.finish()
        publication.claim(self.sid,2,self.db);publication.finish(self.sid,2,db_path=self.db)
        from app.mini.schema import init_mini_db
        init_mini_db(self.db);publication.recover(self.db)
        self.assertEqual(len(self.sql("SELECT * FROM story_achievements")),2)
        self.assertIsNone(publication.claim(self.sid,2,self.db))
    def test_cancel_and_left_player_never_earn(self):
        service.cancel(self.sid,self.chat,self.thread,1,"admin","cancel",self.db)
        self.assertEqual(self.sql("SELECT * FROM story_achievements"),[])
    def reminder(self,key="r",now=100,step=1,chat=None):
        return feedback.claim_reminder(self.sid,self.chat if chat is None else chat,self.thread,1,"admin",step,key,self.db,now=now)
    def test_reminder_only_pending_active_and_scoped(self):
        service.vote(self.sid,self.chat,self.thread,900123,1,"x","one",self.db)
        self.assertEqual(self.reminder(),[900124])
        self.assertIn("tg://user?id=900124",feedback.reminder_text([900124]))
        with self.assertRaises(ValueError):self.reminder("wrong",chat=99)
        with self.assertRaises(ValueError):self.reminder("stale",step=2)
    def test_reminder_throttle_replay_and_restart(self):
        self.reminder()
        feedback.finish_reminder("r",db_path=self.db)
        with self.assertRaises(ValueError):self.reminder("r",1000)
        with self.assertRaises(ValueError):self.reminder("r2",999)
        self.assertEqual(self.reminder("r2",1000),[900123,900124])
        self.assertEqual(self.sql("SELECT status FROM story_reminders ORDER BY claimed_at")[0]["status"],"uncertain")
    def test_reminder_concurrency_one_send_claim(self):
        def claim(i):
            try:self.reminder(str(i));return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(claim,range(2))),[False,True])
    def test_reminder_denies_non_admin_and_finished(self):
        with patch.object(service,"_admin",side_effect=ValueError("admin")):
            with self.assertRaises(ValueError):self.reminder()
        self.finish()
        with self.assertRaises(ValueError):self.reminder()
    def test_all_voted_reply_without_mutation(self):
        self.sql("INSERT INTO story_votes VALUES(?,1,900123,'x')",(self.sid,))
        self.sql("INSERT INTO story_votes VALUES(?,1,900124,'x')",(self.sid,))
        with self.assertRaisesRegex(ValueError,"Все участники"):self.reminder()
        self.assertEqual(self.sql("SELECT * FROM story_reminders"),[])
    def test_rollback_guard_blocks_old_code_when_feedback_state_exists(self):
        from deploy.release_guard import inspect_connection
        with closing(sqlite3.connect(self.db)) as conn:
            result=inspect_connection(conn,"v15-once-raid-pets-economy")
        self.assertNotIn("BLOCKED_FEEDBACK_STATE_UNSUPPORTED",result["blockers"])
        self.reminder()
        with closing(sqlite3.connect(self.db)) as conn:
            result=inspect_connection(conn,"v15-once-raid-pets-economy")
        self.assertIn("BLOCKED_FEEDBACK_STATE_UNSUPPORTED",result["blockers"])
        self.assertFalse(result["rollback_ready"])

    def test_short_buttons_map_filtered_original_indices(self):
        state=self.status()
        state["choices"]=[(3,{"id":"z","text":"Весь длинный выбор","next":"e"})]
        state["tally"]["percent"]={"z":100}
        body,markup=render(state)
        self.assertNotIn("Сцена 1",body);self.assertNotIn("Title",body)
        self.assertIn("1. Весь длинный выбор",body)
        self.assertEqual(markup.inline_keyboard[0][0].text,"1")
        self.assertTrue(markup.inline_keyboard[0][0].callback_data.endswith(":3"))
    def test_chunks_utf16_lossless(self):
        value="😀Авторский текст"*1400
        parts=chunks(value)
        self.assertEqual("".join(parts),value)
        self.assertTrue(all(len(p.encode("utf-16-le"))//2<=4000 for p in parts))
    def test_publication_parts_and_no_retry_after_partial_send(self):
        async def run():
            state=self.status();story=json.loads(state["snapshot_json"])
            story["nodes"]["s"]["text"]="😀"*2100
            state["snapshot_json"]=json.dumps(story)
            real=transaction
            def tx():return real(self.db)
            claim=publication.claim;finish=publication.finish
            with patch.object(handlers,"transaction",side_effect=tx),patch.object(publication,"claim",side_effect=lambda sid,step:claim(sid,step,self.db)),patch.object(publication,"finish",side_effect=lambda sid,step,mid=None:finish(sid,step,mid,self.db)):
                bot=NS(send_message=AsyncMock(side_effect=[NS(message_id=10),RuntimeError("uncertain")]))
                with self.assertRaises(RuntimeError):await handlers.publish(bot,state)
                await handlers.publish(bot,state)
                self.assertEqual(bot.send_message.await_count,2)
            self.assertEqual(len(self.sql("SELECT * FROM story_publication_parts")),1)
            self.assertEqual(self.sql("SELECT status FROM story_publications")[0]["status"],"uncertain")
        asyncio.run(run())

class DailyAndGacha(MiniCase):
    def test_daily_overview_read_only_cycle_claim_and_miss(self):
        from app.mini.ui.activities import daily_overview
        from app.mini.daily import claim_daily
        first=daily_overview(self.pid,day="2026-01-01",db_path=self.db)
        self.assertFalse(first["claimed"])
        self.assertEqual(self.sql("SELECT * FROM mini_daily_claims"),[])
        claim_daily(self.pid,"Fixture","2026-01-01",self.db)
        self.assertTrue(daily_overview(self.pid,day="2026-01-01",db_path=self.db)["claimed"])
        self.assertEqual(daily_overview(self.pid,day="2026-01-02",db_path=self.db)["cycle"],2)
        self.assertEqual(daily_overview(self.pid,day="2026-01-03",db_path=self.db)["cycle"],1)
    def test_real_ticket_inventory_and_visible_button(self):
        from app.mini.gacha import get_gacha_state
        from app.mini.ui.heroes import _gacha_menu
        state=get_gacha_state(self.pid,self.db)
        self.assertEqual(state["tickets"],3)
        labels=[b.text for row in _gacha_menu(self.world["id"],900123,state).inline_keyboard for b in row]
        self.assertIn("Призвать за билет (3)",labels)
        self.sql("UPDATE mini_inventory SET quantity=0 WHERE player_id=? AND item_id IN (SELECT id FROM mini_items WHERE code='summon_ticket')",(self.pid,))
        state=get_gacha_state(self.pid,self.db)
        self.assertEqual(state["tickets"],0)
        self.assertFalse(any("за билет" in b.text for row in _gacha_menu(self.world["id"],900123,state).inline_keyboard for b in row))
    def test_fixed_seed_actual_picker_distribution_without_pity(self):
        from app.mini import gacha
        from app.mini.catalog import load_hero_catalog
        catalog=load_hero_catalog()
        rarities={h["code"]:h["rarity"] for h in catalog["heroes"]}
        counts={r:0 for r in ("common","uncommon","rare","legendary")}
        rng=random.Random(20261027);n=30000
        with patch.object(gacha,"load_hero_catalog",return_value=catalog),patch.object(gacha.secrets,"randbelow",side_effect=rng.randrange),patch.object(gacha.secrets,"choice",side_effect=rng.choice):
            for _ in range(n):counts[rarities[gacha._choose_hero_code()]]+=1
        for rarity,weight in catalog["settings"]["rarity_weights"].items():
            p=weight/100
            self.assertLess(abs(counts[rarity]-n*p),6*(n*p*(1-p))**.5,(rarity,counts))
    def test_pity_boundaries_and_exact_rarity_reset(self):
        from app.mini import pity
        from app.mini.db import connect_mini_db
        self.assertEqual(pity.required_rarity(dict(uncommon=9,rare=24,legendary=84)),"legendary")
        with connect_mini_db(self.db) as conn:
            result=pity.advance(conn,self.pid,dict(uncommon=3,rare=4,legendary=5),"rare")
        self.assertEqual(result,dict(uncommon=4,rare=0,legendary=6))


from tests.v1_5 import test_completion_audit as audit_fixture

class TelegramWave2(MiniCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await audit_fixture.TelegramAudit.asyncSetUp(self)
        self.world["currency_name"]="монет"
        self.world["enabled"]=1
    message = audit_fixture.TelegramAudit.message
    callback = audit_fixture.TelegramAudit.callback

    async def test_real_reminder_route_is_scoped_group_only_and_duplicate_safe(self):
        from app.mini.ui import context
        self.chat,self.thread=self.world["chat_id"],self.world["thread_id"]
        self.story={"schema_version":2,"visit_policy":"once","code":"fixture","title":"Fixture","start_node":"s",
            "nodes":{"s":{"text":"Text","choices":[{"id":"x","text":"Choice","next":"e"}]},
                     "e":{"text":"End","end":True}}}
        sid=StoryFeedback.make_session(self)
        payload=f"r.{sid}.1.abcdef"
        cb=self.callback(f"mini:stui:{self.world['id']}:900123:{payload}",chat=self.chat)
        with patch.object(context,"load_extended_context",AsyncMock(return_value=(self.world,self.player,payload))),patch.object(feedback,"transaction",side_effect=lambda *a,**k:self.tx(self.db)),patch.object(service,"_admin"),patch.object(handlers,"discover",return_value=({},{})):
            matches=[]
            for handler in handlers.router.callback_query.handlers:
                if (await handler.check(cb))[0]:matches.append(handler.callback)
            self.assertIn(handlers.story_menu,matches)
            await handlers.story_menu(cb)
            public=[call for call in self.sent.call_args_list if call.kwargs.get("parse_mode")=="HTML"]
            self.assertEqual(len(public),1)
            self.assertEqual(public[0].kwargs["chat_id"],self.chat)
            self.assertNotIn("900125",public[0].kwargs["text"])
            await handlers.story_menu(cb)
            self.assertEqual(len([c for c in self.sent.call_args_list if c.kwargs.get("parse_mode")=="HTML"]),1)
        self.assertEqual(self.sql("SELECT status FROM story_reminders")[0]["status"],"sent")

    async def test_dnd_admin_reminder_callback_checks_scope_and_permission(self):
        self.chat,self.thread=self.world["chat_id"],self.world["thread_id"]
        self.story={"schema_version":2,"visit_policy":"once","code":"fixture","title":"Fixture","start_node":"s",
            "nodes":{"s":{"text":"Text","choices":[{"id":"x","text":"Choice","next":"e"}]},
                     "e":{"text":"End","end":True}}}
        sid=StoryFeedback.make_session(self)
        cb=self.callback(f"story:remind:{sid}:1:abcdef",chat=self.chat)
        cb=cb.model_copy(update={"message":cb.message.model_copy(update={"message_thread_id":self.thread or None})})
        with patch.object(feedback,"transaction",side_effect=lambda *a,**k:self.tx(self.db)),patch.object(service,"_admin",side_effect=ValueError("admin")):
            await handlers.callback_action(cb)
        self.assertEqual(self.sql("SELECT * FROM story_reminders"),[])
        with patch.object(feedback,"transaction",side_effect=lambda *a,**k:self.tx(self.db)),patch.object(service,"_admin"):
            await handlers.callback_action(cb)
            await handlers.callback_action(cb)
        self.assertEqual(len([c for c in self.sent.call_args_list if c.kwargs.get("parse_mode")=="HTML"]),1)

    async def test_daily_real_route_previews_before_claim_and_claim_replays(self):
        from app.mini.ui import activities
        from app.mini import daily
        cb=self.callback(f"mini:daily:{self.world['id']}:900123")
        overview=activities.daily_overview;claim=daily.claim_daily
        with patch.object(activities,"_load_personal_context",AsyncMock(return_value=(self.world,self.player))),patch.object(activities,"daily_overview",side_effect=lambda pid:overview(pid,db_path=self.db)),patch.object(activities,"_edit_private",AsyncMock()) as display:
            await activities.daily_overview_callback(cb)
            self.assertEqual(self.sql("SELECT * FROM mini_daily_claims"),[])
            self.assertIn("доступна",display.call_args.args[1])
            markup=display.call_args.args[2]
            data=markup.inline_keyboard[0][0].callback_data
            self.assertTrue(data.startswith("mini:dailyclaim:"))
            with patch.object(activities,"claim_daily",side_effect=lambda pid,name:claim(pid,name,db_path=self.db)):
                await activities.daily_callback(self.callback(data))
                rows=self.sql("SELECT * FROM mini_wallet_transactions")
                await activities.daily_callback(self.callback(data))
                self.assertEqual(rows,self.sql("SELECT * FROM mini_wallet_transactions"))

    async def test_hero_change_uses_only_owned_ephemeral_not_public_share(self):
        from app.mini.ui import heroes
        from app.mini.heroes import get_player_hero
        from app.mini.gacha import get_gacha_state
        hero=self.hero()
        cb=self.callback(f"mini:heroactive:{self.world['id']}:900123:{hero['id']}",chat=self.world["chat_id"])
        with patch.object(heroes,"_load_extended_context",AsyncMock(return_value=(self.world,self.player,str(hero["id"])))),patch.object(heroes,"set_active_hero"),patch.object(heroes,"get_player_hero",return_value=get_player_hero(self.pid,hero["id"],self.db)),patch.object(heroes,"get_gacha_state",return_value=get_gacha_state(self.pid,self.db)),patch.object(heroes,"send_hero_card",AsyncMock()) as private,patch.object(heroes,"send_public_hero_share",AsyncMock()) as public:
            await heroes.hero_active_callback(cb)
            private.assert_awaited_once();public.assert_not_awaited()

    async def test_first_launcher_ack_and_active_hero_read_once(self):
        from app.mini import handlers as mini
        from app.mini.heroes import get_active_hero
        hero=self.hero()
        active=get_active_hero(self.pid,self.db)
        cb=self.callback(f"mini:launch:{self.world['id']}",chat=self.world["chat_id"])
        cb=cb.model_copy(update={"message":cb.message.model_copy(update={"message_thread_id":self.world["thread_id"] or None})})
        with patch.object(mini,"get_mini_world_by_id",return_value=self.world),patch.object(mini,"get_mini_player",return_value=self.player),patch.object(mini,"touch_mini_player"),patch.object(mini,"get_active_hero",return_value=active) as read,patch.object(mini,"send_hero_card",AsyncMock()) as photo:
            await mini.launch_callback(cb)
            read.assert_called_once_with(self.pid)
            photo.assert_awaited_once()
            self.assertTrue(photo.call_args.kwargs["cache_image"])
