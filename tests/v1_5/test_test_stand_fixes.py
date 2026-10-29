"""Real services and Telegram routes for the nine TEST findings; no live IO."""
import asyncio
import copy
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch, Mock
from tests.v1_3.support import MiniCase
from tests.v1_5 import test_completion_audit as audit_fixture
from app.mini.mythic.raid import service as raids, handlers as rh
from app.mini.mythic import pets, handlers as ph
from app.mini.mythic.wallet import transaction
from app.mini.features import UNAVAILABLE


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


class RaidFixes(MiniCase):
    def setUp(self):
        super().setUp()
        self.template = dict(code="fixture",name="Fixture",max_hp=100000,element="ice",
            counter_elements=["fire"],features=[],regeneration_percent=0,shadow_extractable=False)
        self.enterContext(patch.object(raids,"load",return_value=[self.template]))
        self.raid = raids.start(self.pid,"fixture","start",self.db,now=100)
        self.heroes = self.sql("SELECT * FROM mini_heroes WHERE rarity IN ('common','uncommon') ORDER BY id LIMIT 8")
        for h in self.heroes:
            self.sql("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",(self.pid,h["id"]))
        for h in self.heroes:
            self.sql("UPDATE mini_heroes SET attack=100,damage_type='magical',attack_range='ranged',passive_key='none',passives_json='{}' WHERE id=?",(h["id"],))
        self.selection = lambda now=100: raids.selection(self.pid,self.raid["id"],self.db,now=now)

    def hit(self,index,key=None,now=100):
        return raids.attack(self.pid,self.raid["id"],self.heroes[index]["id"],key or str(index),self.db,now=now)

    def test_used_hero_hidden_and_server_rechecks(self):
        self.hit(0)
        self.assertNotIn(self.heroes[0]["id"],{h["id"] for h in self.selection()["heroes"]})
        with self.assertRaisesRegex(ValueError,"уже атаковал"):
            self.hit(0,"stale")
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")),1)

    def test_used_hero_returns_next_moscow_day(self):
        self.hit(0,now=75599) # 23:59:59 Moscow
        self.assertNotIn(self.heroes[0]["id"],{h["id"] for h in self.selection(75599)["heroes"]})
        self.assertIn(self.heroes[0]["id"],{h["id"] for h in self.selection(75600)["heroes"]})
        self.hit(0,"tomorrow",75600)

    def test_used_hero_scoped_to_player_and_actual_raid(self):
        self.hit(0)
        self.sql("UPDATE mini_mythic_raids SET status='cancelled'")
        other=raids.start(self.pid,"fixture","other",self.db,now=100)
        self.assertIn(self.heroes[0]["id"],{h["id"] for h in raids.selection(self.pid,other["id"],self.db,now=100)["heroes"]})
        self.assertEqual(raids.selection(self.pid,other["id"],self.db,now=100)["attempts_left"],4)

    def test_five_attack_budget_and_duplicate_keeps_receipt(self):
        for i in range(5):
            self.assertEqual(self.hit(i)["attempts_left"],4-i)
        before=self.sql("SELECT * FROM mini_mythic_raid_attacks")
        with self.assertRaisesRegex(ValueError,"пяти"):
            self.hit(5)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_attacks"),before)
        self.assertEqual(self.hit(4)["attempts_left"],0)
        self.assertEqual(self.selection()["attempts_left"],0)

    def test_old_six_or_more_rows_are_preserved_and_block_new(self):
        for i in range(5):self.hit(i)
        self.sql("INSERT INTO mini_mythic_raid_attacks(raid_id,player_id,hero_id,game_day,operation_key,payload,hero_json,events_json,result_json,created_at) SELECT raid_id,player_id,?,game_day,'old-sixth','[]',hero_json,events_json,result_json,created_at FROM mini_mythic_raid_attacks LIMIT 1",(self.heroes[5]["id"],))
        with self.assertRaisesRegex(ValueError,"пяти"):self.hit(6)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")),6)

    def test_concurrent_fifth_and_sixth_serialized(self):
        for i in range(4):self.hit(i)
        def hit(i):
            try:self.hit(i);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(hit,[4,5])),[False,True])
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")),5)

    def test_owned_village_hero_excluded(self):
        self.sql("INSERT INTO mini_village_residents(player_id,hero_id) VALUES(?,?)",(self.pid,self.heroes[0]["id"]))
        self.assertNotIn(self.heroes[0]["id"],{h["id"] for h in self.selection()["heroes"]})
        with self.assertRaises(ValueError):
            self.hit(0)

    def test_raid_attack_first_egg_restart_and_reward_once(self):
        result=self.hit(0)
        self.assertIsNotNone(result["egg"])
        self.assertEqual(self.sql("SELECT location FROM mini_mythic_pets")[0]["location"],"inventory")
        from app.mini.schema import init_mini_db
        init_mini_db(self.db)
        self.sql("UPDATE mini_mythic_raids SET current_hp=1")
        result=self.hit(1)
        self.assertIn("final",result)
        rows=self.sql("SELECT * FROM mini_mythic_raid_rewards")
        self.assertEqual(self.hit(1),result)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_rewards"),rows)
        self.assertEqual(self.sql("PRAGMA foreign_key_check"),[])
        self.assertEqual(self.sql("PRAGMA integrity_check")[0]["integrity_check"],"ok")


class Screens(unittest.TestCase):
    def world(self):return {"id":1}
    def raid(self):return {"id":7,"snapshot":{"name":"Fixture","element":"ice"},"status":"active","current_hp":10,"max_hp":10,"ranking":[],"state":{}}

    def render(self,**kw):
        heroes=[dict(id=i,name=r,rarity=r) for i,r in enumerate(("common","uncommon","rare","legendary","shadow","mythic","future"),1)]
        with patch.object(raids,"state",return_value=self.raid()),patch.object(raids,"selection",return_value={"heroes":heroes,"attempts_left":5}):
            return rh.screen(self.world(),{"id":2},42,**kw)

    def test_rarities_two_by_two_and_unbounded_pages(self):
        _,first=self.render(pick=True)
        cats=[r for r in first.inline_keyboard if r[0].callback_data.split(":")[-1].startswith("rheroes")]
        self.assertEqual([len(r) for r in cats],[2,2])
        self.assertTrue(any(b.text=="Далее" for b in buttons(first)))
        _,second=self.render(pick=True,page=1)
        cats=[b for b in buttons(second) if ":rheroes." in b.callback_data]
        self.assertEqual(len(cats),3)
        self.assertTrue(any(b.text=="Назад" for b in buttons(second)))
        self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons(first)+buttons(second)))

    def test_only_selected_rarity_heroes(self):
        _,markup=self.render(pick=True,rarity=rh.short_code("mythic"))
        choices=[b for b in buttons(markup) if ":hitask." in b.callback_data]
        self.assertEqual([b.text for b in choices],["mythic"])

    def test_empty_and_exhausted_selection(self):
        for left in (0,5):
            with patch.object(raids,"state",return_value=self.raid()),patch.object(raids,"selection",return_value={"heroes":[],"attempts_left":left}):
                text,markup=rh.screen(self.world(),{"id":2},42,pick=True)
            self.assertFalse(any(":hitask." in b.callback_data or ":rheroes." in b.callback_data for b in buttons(markup)))
            self.assertIn("лимит" if left==0 else "Нет доступных",text)

    def test_care_two_two_one_and_no_duplicate_raid_or_purchase(self):
        pet=dict(id=1,location="active",element="fire",stage="egg",mood=50,stage_seconds=0,
                 evolution_pending=0,cooldowns_json="{}",active_seconds=0)
        state={"pets":[pet],"mythic_shards":75}
        text,markup=ph.screen(2,1,42,view="pet",pet_id=1,state=state)
        care=[r for r in markup.inline_keyboard if ":care." in r[0].callback_data]
        self.assertEqual([len(r) for r in care],[2,2,1])
        _,home=ph.screen(2,1,42,state=state)
        self.assertFalse(any(":raids:" in b.callback_data or ":buyask." in b.callback_data for b in buttons(home)))

    def test_battle_menu_and_story_entry(self):
        from app.mini.handlers import submenu
        _,markup=submenu("adventures",1,42)
        targets=[b.callback_data.split(":")[1] for b in buttons(markup)]
        self.assertEqual(targets,["boss","tower","daily","raids","home"])
        _,markup=submenu("more",1,42)
        self.assertIn("stories",[b.callback_data.split(":")[1] for b in buttons(markup)])

    def test_pve_events_remain_pvp_hidden_by_default(self):
        from app.mini.events.handlers import _screen
        with patch.dict(os.environ,{"MINI_PVP_ENABLED":""}):
            _,markup=_screen(1,42,{"coins":1,"shards":0},"rps")
        self.assertTrue(any(":bets" in b.callback_data for b in buttons(markup)))
        self.assertFalse(any(":pvp:" in b.callback_data for b in buttons(markup)))


class DisabledEconomy(MiniCase):
    def test_disabled_creation_does_not_touch_any_resources(self):
        from app.mini.events import pvp
        from app.mini.duels import service
        hero=self.hero()
        self.sql("UPDATE mini_players SET coins=100")
        before=self.sql("SELECT * FROM mini_players")
        with patch.dict(os.environ,{"MINI_PVP_ENABLED":"","MINI_DUELS_ENABLED":""}):
            for fn in (lambda:pvp.create(self.pid,self.world["id"],"x",self.db),
                       lambda:service.challenge(self.pid,999,hero["id"],"x",self.db)):
                with self.assertRaisesRegex(ValueError,UNAVAILABLE):fn()
        self.assertEqual(self.sql("SELECT * FROM mini_players"),before)
        self.assertEqual(self.sql("SELECT * FROM mini_wallet_transactions"),[])
        self.assertEqual(self.sql("SELECT * FROM mini_duels"),[])

    def test_existing_duel_expiry_preserves_once_refund_policy(self):
        from app.mini.duels import service
        from app.mini.players import create_mini_player
        other=create_mini_player(self.world["id"],900124,"","Other",self.db)["id"]
        hero=self.hero();self.sql("UPDATE mini_players SET coins=100")
        with patch.dict(os.environ,{"MINI_DUELS_ENABLED":"1"}):
            service.challenge(self.pid,other,hero["id"],"old",self.db,now=100)
        with patch.dict(os.environ,{"MINI_DUELS_ENABLED":""}):
            self.assertEqual(service.expire(self.db,now=219),0)
            self.assertEqual(service.expire(self.db,now=220),1)
            self.assertEqual(service.expire(self.db,now=220),0)
        self.assertEqual(self.sql("SELECT coins FROM mini_players WHERE id=?",(self.pid,))[0]["coins"],100)


class TelegramFixes(MiniCase,unittest.IsolatedAsyncioTestCase):
    asyncSetUp=audit_fixture.TelegramAudit.asyncSetUp
    message=audit_fixture.TelegramAudit.message
    callback=audit_fixture.TelegramAudit.callback

    async def test_old_duel_and_pvp_callbacks_unavailable(self):
        from app.mini.duels import handlers
        from app.mini.events import pvp_handlers
        with patch.dict(os.environ,{"MINI_DUELS_ENABLED":"","MINI_PVP_ENABLED":""}):
            for module,data in ((handlers,"mini:duel:1:900123:accept.1"),(pvp_handlers,"mini:pvp:1:0:join.1")):
                cb=self.callback(data)
                await module.action(cb)
                self.answered.assert_awaited_with(UNAVAILABLE,show_alert=True)

    async def test_close_history_actual_callback_route_private_and_repeat(self):
        from app.mini.boss import v15_handlers as h
        from aiogram.types import Message
        cb=self.callback(f"mini:close:{self.world['id']}:900123")
        with patch("app.mini.ui.context.load_personal_context",AsyncMock(return_value=(self.world,self.player))),patch.object(Message,"delete",AsyncMock()) as delete:
            await h.router.propagate_event("callback_query",cb)
            await h.router.propagate_event("callback_query",cb)
            self.assertEqual(delete.await_count,2)
        self.assertEqual(self.sql("SELECT * FROM mini_mythic_raid_attacks"),[])

    async def test_close_never_deletes_public_message(self):
        from app.mini.boss import v15_handlers as h
        from aiogram.types import Message
        cb=self.callback(f"mini:close:{self.world['id']}:900123",chat=self.world["chat_id"])
        with patch("app.mini.ui.context.load_personal_context",AsyncMock(return_value=(self.world,self.player))),patch.object(Message,"delete",AsyncMock()) as delete:
            await h.close_history(cb)
        delete.assert_not_awaited()
        self.assertTrue(self.answered.call_args.kwargs["show_alert"])

    async def test_close_foreign_owner_rejected_by_real_context(self):
        from app.mini.boss import v15_handlers as h
        cb=self.callback(f"mini:close:{self.world['id']}:111")
        await h.router.propagate_event("callback_query",cb)
        self.assertTrue(self.answered.call_args.kwargs["show_alert"])

    async def test_shop_egg_uses_existing_buy_and_repeat_confirmation(self):
        from app.mini.ui.shop import _shop_main_menu
        with patch("app.mini.ui.shop.get_shop_categories",return_value=[]):
            menu=_shop_main_menu(self.world["id"],900123)
        entry=next(b for b in buttons(menu) if "Мифическое яйцо" in b.text)
        self.assertIn(":buyask.",entry.callback_data)
        with transaction(self.db) as conn:
            pets.first_egg(conn,self.pid,pets.timestamp(),chooser=lambda v:v[0])
        self.sql("UPDATE mini_players SET mythic_shards=150")
        payload="buy.shop-confirm"
        cb=self.callback(f"mini:myth:{self.world['id']}:900123:{payload}")
        with patch.object(ph,"load_extended_context",AsyncMock(return_value=(self.world,self.player,payload))):
            await ph.action(cb);await ph.action(cb)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_pets")),2)
        self.assertEqual(self.sql("SELECT mythic_shards FROM mini_players")[0]["mythic_shards"],75)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_ledger")),1)

    async def test_story_menu_hides_admin_actions_and_keeps_join(self):
        from app.stories import handlers
        session={"id":1,"status":"registration","snapshot_json":"{}","code":"real"}
        with patch.object(handlers,"discover",return_value=({"real":{"title":"Author"}},{})),patch.object(handlers.service,"status",return_value=session),patch.object(handlers.service,"_admin",side_effect=ValueError),patch.object(handlers,"render",return_value=("Story",None)):
            text,markup=handlers.menu(self.world,NS(id=900123,username="tester"))
        self.assertTrue(any(":j.1" in b.callback_data for b in buttons(markup)))
        self.assertFalse(any(":s." in b.callback_data or ":x." in b.callback_data or ":c." in b.callback_data for b in buttons(markup)))

    async def test_story_create_admin_service_rechecks(self):
        from app.stories import handlers
        payload="c.12345678.abcdef"
        cb=self.callback(f"mini:stui:{self.world['id']}:900123:{payload}")
        with patch.object(handlers.service,"_admin",side_effect=ValueError("No admin")),patch("app.mini.ui.context.load_extended_context",AsyncMock(return_value=(self.world,self.player,payload))),patch.object(handlers.service,"create") as create:
            await handlers.story_menu(cb)
        create.assert_not_called()
        self.answered.assert_awaited_with("No admin",show_alert=True)


    async def test_real_raid_ui_rarity_hero_confirm_attack_reward_and_duplicate(self):
        template=dict(code="fixture",name="Fixture",max_hp=100000,element="ice",counter_elements=["fire"],features=[],regeneration_percent=0,shadow_extractable=False)
        with patch.object(raids,"load",return_value=[template]):
            raid=raids.start(self.pid,"fixture","new-ui",self.db)
        hero=self.hero()
        self.sql("UPDATE mini_heroes SET attack=100,attack_range='ranged',damage_type='magical',passive_key='none',passives_json='{}' WHERE id=?",(hero["id"],))
        self.sql("UPDATE mini_mythic_raids SET current_hp=1")
        async def press(payload):
            cb=self.callback(f"mini:raid:{self.world['id']}:900123:{payload}")
            with patch.object(rh,"load_extended_context",AsyncMock(return_value=(self.world,self.player,payload))),patch.object(rh,"is_admin",return_value=False):
                await rh.action(cb)
        await press(f"rpick.{raid['id']}.0")
        markup=self.sent.call_args.kwargs["reply_markup"]
        category=next(b for b in buttons(markup) if ":rheroes." in b.callback_data)
        await press(category.callback_data.split(":",4)[4])
        markup=self.sent.call_args.kwargs["reply_markup"]
        pick=next(b for b in buttons(markup) if ":hitask." in b.callback_data)
        await press(pick.callback_data.split(":",4)[4])
        markup=self.sent.call_args.kwargs["reply_markup"]
        confirm=next(b for b in buttons(markup) if ":hit." in b.callback_data)
        payload=confirm.callback_data.split(":",4)[4]
        await press(payload);await press(payload)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_attacks")),1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_raid_rewards")),1)
        self.assertEqual(len(self.sql("SELECT * FROM mini_mythic_pets")),1)

    async def test_stale_used_hero_confirm_is_not_offered(self):
        template=dict(code="fixture",name="Fixture",max_hp=100000,element="ice",counter_elements=["fire"],features=[],regeneration_percent=0,shadow_extractable=False)
        with patch.object(raids,"load",return_value=[template]):
            raid=raids.start(self.pid,"fixture","used-ui",self.db)
        hero=self.hero()
        raids.attack(self.pid,raid["id"],hero["id"],"prior",self.db)
        payload=f"hitask.{raid['id']}.{hero['id']}.abcdef"
        cb=self.callback(f"mini:raid:{self.world['id']}:900123:{payload}")
        with patch.object(rh,"load_extended_context",AsyncMock(return_value=(self.world,self.player,payload))):
            await rh.action(cb)
        self.assertTrue(self.answered.call_args.kwargs["show_alert"])
        self.assertIn("недоступен",self.answered.call_args.args[0])

    async def test_story_menu_real_admin_registration_start_and_duplicate(self):
        from app.stories import handlers, service
        from tests.v1_5.test_boss_stories import StoryTests
        # Original fixture defines valid content; production JSON is never changed.
        story=StoryTests.story() if hasattr(StoryTests,"story") else None
        if story is None:
            story={"schema_version":1,"code":"fixture","title":"Fixture","description":"Fixture only",
                "start_node":"a","nodes":{"a":{"text":"A","choices":[{"id":"end","text":"End","next":"z"}]},"z":{"text":"Z","choices":[],"end":True}}}
        code=story["code"]
        catalog={code:story}
        key=rh.short_code(code)
        async def press(payload):
            cb=self.callback(f"mini:stui:{self.world['id']}:900123:{payload}")
            with patch("app.mini.ui.context.load_extended_context",AsyncMock(return_value=(self.world,self.player,payload))):
                await handlers.story_menu(cb)
        with patch.object(service,"_admin"),patch.object(handlers,"discover",return_value=(catalog,{})),patch.object(service,"discover",return_value=(catalog,{})),patch("app.stories.presentation.discover",return_value=(catalog,{})):
            await press("c."+key+".abcdef")
            await press("c."+key+".abcdef")
            sessions=self.sql("SELECT * FROM story_sessions")
            self.assertEqual(len(sessions),1)
            sid=sessions[0]["id"]
            service.join(sid,self.world["chat_id"],self.world["thread_id"],900123,"mini",{"id":self.pid},"join",self.db)
            await press(f"s.{sid}.abcdef")
            self.assertEqual(self.sql("SELECT status FROM story_sessions")[0]["status"],"active")
            service.vote(sid,self.world["chat_id"],self.world["thread_id"],900123,service.status(self.world["chat_id"],self.world["thread_id"],self.db)["step"],"end","vote",self.db)
            self.assertEqual(self.sql("SELECT status FROM story_sessions")[0]["status"],"finished")

    async def test_cached_pet_photo_reuses_handle_and_invalidates_by_bytes(self):
        from app.mini.ui import hero_cards as cards
        from aiogram.types import FSInputFile
        cards._pet_photo_ids.clear()
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/"pet.png";image.write_bytes(b"fixture-image")
            cb=self.callback(f"mini:myth:{self.world['id']}:900123:pet.1")
            self.photos.return_value=NS(photo=[NS(file_id="fixture-file-id")])
            with patch.object(cards,"get_hero_image",return_value=image):
                for _ in range(2):
                    await cards.send_hero_card(cb,self.world,{"name":"Pet"},"Pet",None,cache_image=True)
                self.assertIsInstance(self.photos.call_args_list[0].kwargs["photo"],FSInputFile)
                self.assertEqual(self.photos.call_args_list[1].kwargs["photo"],"fixture-file-id")
                image.write_bytes(b"different-image")
                await cards.send_hero_card(cb,self.world,{"name":"Pet"},"Pet",None,cache_image=True)
                self.assertIsInstance(self.photos.call_args.kwargs["photo"],FSInputFile)


class CatalogCache(unittest.TestCase):
    def test_strict_cache_no_shared_mutation_and_pixel_changes_revalidate(self):
        from app.mini.mythic.raid import catalog
        from app.mini.mythic import content
        catalog._validated.cache_clear()
        with patch.object(content,"png",return_value={}) as png:
            first=catalog.load()
            calls=png.call_count
            self.assertEqual(calls,4)
            first[0]["name"]="not-author"
            self.assertNotEqual(catalog.load()[0]["name"],"not-author")
            self.assertEqual(png.call_count,calls)
        catalog._validated.cache_clear()
        with tempfile.TemporaryDirectory() as folder:
            images=Path(folder)
            for p in catalog.IMAGES.glob("*.png"):
                (images/p.name).write_bytes(p.read_bytes())
            catalog.load(images=images)
            target=images/"raid_boss_1.png"
            stat=target.stat()
            target.write_bytes(b"not-a-png")
            os.utime(target,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            with self.assertRaises(ValueError):
                catalog.load(images=images)
        catalog._validated.cache_clear()



class Diagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_slow_worker_yields_loop_and_logs_no_payload(self):
        from app.mini.ui.timing import CallbackTiming,run_sync,TelegramTiming
        cb=NS(data="mini:myth:123:456:private-secret")
        event=NS(callback_query=cb)
        pulses=[]
        async def handle(event,data):
            task=asyncio.create_task(asyncio.sleep(.01))
            await run_sync("pet_operation",time.sleep,.08)
            pulses.append(task.done())
        with self.assertLogs("app.mini.ui.timing",level="WARNING") as logs:
            await CallbackTiming()(handle,event,{})
        self.assertEqual(pulses,[True])
        self.assertNotIn("private-secret","".join(logs.output))
        self.assertIn("worker_cpu_ms","".join(logs.output))
