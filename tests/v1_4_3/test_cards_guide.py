import asyncio
from functools import partial
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from tests.v1_3.support import MiniCase
from tests.v1_4_2.test_navigation_flows import callback
from tests.v1_4_3.test_editorial import EditorialCase
from app.mini.village import service as village
from app.mini.ui.hero_village import with_village
from app.mini.ui import hero_cards,heroes,context
from app.mini.heroes import get_player_hero
from app.mini.availability import assert_available
from app.mini.db import connect_mini_db
from app.mini.ui.combat_guide import guide_screen,CHAPTERS
from app.mini.ui.activities import rules_menu
from app.mini.players import create_mini_player
from app.mini.ui.callbacks import number

class VillageCardTests(MiniCase):
    def setUp(self):
        super().setUp()
        self.h=self.hero()
        self.sql("UPDATE mini_players SET coins=100")
        village.mutate(self.pid,"buy",self.db,expected_houses=0,operation_key="house",now=1000)
    def mutation(self,action,key,**kwargs):
        return village.mutate(self.pid,action,self.db,operation_key=key,now=kwargs.pop("now",1000),**kwargs)
    def resident(self,building=None):
        self.mutation("settle","settle",hero_id=self.h["id"])
        if building:self.mutation("assign","assign",hero_id=self.h["id"],building=building)
    def card(self):
        return with_village(get_player_hero(self.pid,self.h["id"],self.db),self.pid,self.db)
    def menu(self,hero,profile=False):
        return hero_cards.hero_card_menu(self.world["id"],900123,hero,{},collection=not profile,profile=profile)
    def buttons(self,markup):
        return [b for row in markup.inline_keyboard for b in row]
    def test_no_residence_no_status_or_eviction_button(self):
        hero=self.card()
        self.assertNotIn("В деревне",hero_cards.hero_caption(hero))
        self.assertFalse(any(":hevict:" in b.callback_data for b in self.buttons(self.menu(hero))))
    def test_owner_scoped_same_catalog_hero(self):
        self.resident("mine")
        other=create_mini_player(self.world["id"],900124,"other","Другой",self.db)["id"]
        self.sql("INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)",(other,self.h["id"]))
        self.assertIsNone(with_village(self.h,other,self.db)["village_resident"])
        self.assertEqual(self.card()["village_resident"]["building"],"mine")
    def test_job_updates_and_evict_clears_both_card_variants(self):
        self.resident()
        for full in (False,True):
            self.assertIn("В деревне (Без работы)",hero_cards.hero_caption(self.card(),full=full))
        self.mutation("assign","mine",hero_id=self.h["id"],building="mine")
        self.assertIn("В деревне (Шахта)",hero_cards.hero_caption(self.card()))
        self.mutation("assign","unassign",hero_id=self.h["id"],building=None)
        self.mutation("assign","market",hero_id=self.h["id"],building="market")
        self.assertIn("В деревне (Рынок)",hero_cards.hero_caption(self.card()))
        for profile in (False,True):
            self.assertEqual(sum(":hevict:" in b.callback_data for b in self.buttons(self.menu(self.card(),profile))),1)
        self.mutation("evict","evict",hero_id=self.h["id"])
        for profile in (False,True):
            hero=self.card()
            self.assertNotIn("В деревне",hero_cards.hero_caption(hero,full=not profile))
            self.assertFalse(any(":hevict:" in b.callback_data for b in self.buttons(self.menu(hero,profile))))
    def test_public_and_gacha_do_not_expose_residence(self):
        self.resident("hunt");hero=self.card()
        self.assertNotIn("В деревне",hero_cards.hero_share_caption(hero,"Игрок"))
        self.assertFalse(any(":hevict:" in b.callback_data for b in self.buttons(
            hero_cards.hero_card_menu(1,900123,hero,{},allow_share=True))))
        self.assertIn("В деревне (Охота)",hero_cards.hero_caption(hero))
    def test_evict_settles_production_preserves_hero_and_resources(self):
        # Feeding worker keeps the market running before eviction.
        self.resident("market")
        hunter=self.hero("reborn_crypt_keeper",2)
        self.mutation("settle","hunter",hero_id=hunter["id"])
        self.mutation("assign","hunt",hero_id=hunter["id"],building="hunt")
        before=self.sql("SELECT * FROM mini_player_heroes WHERE player_id=? AND hero_id=?",(self.pid,self.h["id"]))
        wallet=self.sql("SELECT * FROM mini_wallet_transactions")
        state=village.get_state(self.pid,self.db,now=4600)
        revision=state["revision"]
        result=self.mutation("evict","evict-once",hero_id=self.h["id"],expected_revision=revision,now=4600)
        self.assertTrue(result["applied"])
        after=village.get_state(self.pid,self.db,now=8200)
        self.assertEqual(after["houses"],state["houses"])
        self.assertEqual(after["coins_units"],state["coins_units"])
        self.assertFalse(self.mutation("evict","evict-once",hero_id=self.h["id"],expected_revision=revision,now=8200)["applied"])
        self.assertEqual(before,self.sql("SELECT * FROM mini_player_heroes WHERE player_id=? AND hero_id=?",(self.pid,self.h["id"])))
        self.assertEqual(wallet,self.sql("SELECT * FROM mini_wallet_transactions"))
        with connect_mini_db(self.db) as conn:assert_available(conn,self.pid,self.h["id"])
    def test_old_revision_cannot_evict_resettled_hero(self):
        self.resident();old=self.card()["village_resident"]["revision"]
        self.mutation("evict","evict",hero_id=self.h["id"],expected_revision=old)
        self.mutation("settle","resettle",hero_id=self.h["id"])
        with self.assertRaises(ValueError):
            self.mutation("evict","old",hero_id=self.h["id"],expected_revision=old)
        self.assertIsNotNone(self.card()["village_resident"])
    def test_new_cards_and_closed_sale_renderer_have_no_sale_path(self):
        hero=self.card()
        for markup in (self.menu(hero),self.menu(hero,True),heroes._shard_sell_menu(1,900123,hero)):
            self.assertFalse(any(":heroshards:" in b.callback_data or ":shardsell:" in b.callback_data for b in self.buttons(markup)))
        self.assertIn("закрыта",heroes._format_shard_sell(hero,100))
    def test_eviction_confirmation_and_refresh_real_service(self):
        self.resident("mine");hero=self.card();rev=hero["village_resident"]["revision"]
        tail=f"{number(hero['id'])}.{number(rev)}.c"
        cb=callback(data=f"mini:hevict:{self.world['id']}:900123:{tail}")
        with patch.object(heroes,"_load_extended_context",return_value=(self.world,self.player,tail)),patch.object(
            heroes,"get_player_hero",side_effect=partial(get_player_hero,db_path=self.db)),patch.object(
            heroes,"with_village",side_effect=partial(with_village,db_path=self.db)),patch.object(
            heroes,"_send_private_text_from_callback",new_callable=AsyncMock) as send:
            asyncio.run(heroes.hero_evict_callback(cb))
            self.assertIn("Выселить героя",send.call_args.args[2])
        self.assertIsNotNone(self.card()["village_resident"])
        with patch.object(heroes,"_load_extended_context",return_value=(self.world,self.player,tail+".yes")),patch.object(
            heroes,"get_player_hero",side_effect=partial(get_player_hero,db_path=self.db)),patch.object(
            heroes,"with_village",side_effect=partial(with_village,db_path=self.db)),patch(
            "app.mini.village.service.mutate",side_effect=partial(village.mutate,db_path=self.db,now=4600)),patch.object(
            heroes,"get_gacha_state",return_value={}),patch.object(heroes,"is_favorite",return_value=False),patch.object(
            heroes,"send_hero_card",new_callable=AsyncMock) as send:
            asyncio.run(heroes.hero_evict_callback(cb));asyncio.run(heroes.hero_evict_callback(cb))
            self.assertNotIn("В деревне",send.call_args.args[3])
        self.assertIsNone(self.card()["village_resident"])
    def test_foreign_owner_and_wrong_location_cannot_evict(self):
        self.resident();rev=self.card()["village_resident"]["revision"]
        tail=f"{number(self.h['id'])}.{number(rev)}.c.yes"
        for user,chat,thread in ((900124,self.world["chat_id"],self.world["thread_id"]),
                                 (900123,-999,1),(900123,self.world["chat_id"],999)):
            cb=callback(user=user,data=f"mini:hevict:{self.world['id']}:900123:{tail}",chat=chat,thread=thread)
            with patch.object(context,"get_mini_world_by_id",return_value=dict(self.world,enabled=1)),patch.object(
                context,"is_mini_topic",return_value=True),patch("app.mini.village.service.mutate") as mutate:
                asyncio.run(heroes.hero_evict_callback(cb))
                mutate.assert_not_called()
                self.assertTrue(cb.answer.call_args.kwargs["show_alert"])
        self.assertIsNotNone(self.card()["village_resident"])
    def test_long_caption_photo_and_fallback_preserve_ability_and_job(self):
        self.resident("mine")
        hero=dict(self.card(),description="Описание "*100,passive_text="ВАЖНАЯ_СПОСОБНОСТЬ "*40,name="Очень длинное имя "*8)
        body=hero_cards.hero_caption(hero,full=True)
        self.assertIn("ВАЖНАЯ_СПОСОБНОСТЬ",body);self.assertIn("В деревне (Шахта)",body)
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import SendPhoto
        for image,reject in ((None,False),("test.png",False),("test.png",True)):
            cb=callback();cb.bot.send_photo=AsyncMock(return_value=NS(message_id=15))
            if reject:cb.bot.send_photo.side_effect=TelegramBadRequest(method=SendPhoto(chat_id=1,photo="x"),message="invalid image")
            with patch.object(hero_cards,"get_hero_image",return_value=image),patch.object(hero_cards,"FSInputFile",side_effect=lambda x:x):
                asyncio.run(hero_cards.send_hero_card(cb,self.world,hero,body,self.menu(hero)))
            if cb.bot.send_photo.called:
                self.assertLessEqual(len(cb.bot.send_photo.call_args.kwargs["caption"].encode("utf-16-le"))//2,1024)
            # Full text is available whenever the card exceeds caption capacity.
            if len(body.encode("utf-16-le"))//2>1024 or image is None or reject:
                self.assertEqual(cb.bot.send_message.call_args.kwargs["text"],body)
    def test_legacy_sell_callbacks_are_inert_and_keep_history(self):
        before=self.sql("SELECT * FROM mini_players");history=self.sql("SELECT * FROM mini_wallet_transactions")
        for fn,tail in ((heroes.hero_shards_callback,str(self.h["id"])),(heroes.shard_sell_callback,f"{self.h['id']},all,old")):
            cb=callback()
            with patch.object(heroes,"_load_extended_context",return_value=(self.world,self.player,tail)),patch.object(
                heroes,"sell_hero_shards") as sell:
                asyncio.run(fn(cb));sell.assert_not_called()
                self.assertIn("закрыта",cb.answer.call_args.args[0])
        self.assertEqual(before,self.sql("SELECT * FROM mini_players"))
        self.assertEqual(history,self.sql("SELECT * FROM mini_wallet_transactions"))

    def test_very_long_plain_card_is_split_without_losing_any_text(self):
        body="⚔️"*2500+"\nВАЖНАЯ СПОСОБНОСТЬ\n🏘 В деревне (Шахта)"
        cb=callback()
        with patch.object(hero_cards,"get_hero_image",return_value=None):
            asyncio.run(hero_cards.send_hero_card(cb,self.world,self.card(),body,self.menu(self.card())))
        texts=[call.kwargs["text"] for call in cb.bot.send_message.call_args_list]
        self.assertGreater(len(texts),1)
        self.assertEqual("".join(texts),body)
        self.assertTrue(all(len(t.encode("utf-16-le"))//2<=4096 for t in texts))

class GuideTests(EditorialCase):
    def test_all_chapters_navigation_and_plain_text_limits(self):
        for i,chapter in enumerate(CHAPTERS):
            body,markup=guide_screen(2147483647,9999999999999,i)
            self.assertIn(str(i+1)+".",body)
            self.assertLessEqual(len(body.encode("utf-16-le"))//2,4096)
            self.assertNotIn("<b>",body)
            callbacks=[b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertIn("mini:rules:2147483647:9999999999999",callbacks)
            if i:self.assertIn(f"mini:guide:2147483647:9999999999999:{i-1}",callbacks)
            if i<4:self.assertIn(f"mini:guide:2147483647:9999999999999:{i+1}",callbacks)
            self.assertTrue(all(len(c.encode())<=64 for c in callbacks))
        for page in (-1,5,99):
            with self.assertRaises(ValueError):guide_screen(1,2,page)
    def test_rules_menu_keeps_old_sections_and_adds_guide(self):
        from app.mini.rules import SECTIONS
        markup=rules_menu(1,2)
        callbacks=[b.callback_data for row in markup.inline_keyboard for b in row]
        for section in SECTIONS:self.assertIn("mini:rulespage:1:2:"+section,callbacks)
        self.assertIn("mini:guide:1:2:0",callbacks)
    def test_hot_reload_body_and_glossary(self):
        before=guide_screen(1,2,0)[0]
        key="guide.factions.body"
        old=self.registry.loader.defaults[key]
        self.write("combat_guide.json",{key:old+"\nТекст обновлён."})
        self.write("glossary.json",{"faction.commoners":{"full":"Народ"}})
        after=guide_screen(1,2,0)[0]
        self.assertNotEqual(before,after)
        self.assertIn("Народ",after);self.assertIn("Текст обновлён",after)
    def test_numeric_rules_and_mode_explanations(self):
        bodies=[guide_screen(1,2,i)[0] for i in range(5)]
        for fragment in ("×2","+100%","×0.25","−75%","×1"):self.assertIn(fragment,bodies[0])
        self.assertIn("−10%",bodies[1]);self.assertIn("20%",bodies[2])
        for fragment in ("5%","1%","15%","100%, 102%, 104%, 106%","class_tag","class_name","округляются вверх","Испытаниях"):
            self.assertIn(fragment,bodies[3])
        for fragment in ("15%","10%","25%","2%","+1","demon","demonic","Испытаниях"):
            self.assertIn(fragment,bodies[4])


    def test_none_class_emoji_uses_validated_registry_fallback(self):
        from app.mini.editorial.validation import validate_entry
        entry=self.registry.loader.defaults["icon.class.none"]
        validate_entry("icons.json","icon.class.none",entry,{"kind":"icon"})
        self.assertEqual(entry["fallback"],"▪️")
        self.assertIn("▪️",guide_screen(1,2,3)[0])
