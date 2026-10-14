"""Regressions across every mapped screen and real disposable game sessions."""
import asyncio
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.v1_4_3.test_editorial import EditorialCase
from tests.v1_4_2.test_screen_contracts import SOURCE,render_screen,button_handlers
from app.mini.editorial.registry import Registry
from app.mini.equipment.handlers import equipment_label,render_equipment

class GlossaryTests(EditorialCase):
    def test_all_hero_codes_have_labels(self):
        from app.mini.catalog import load_hero_catalog
        fields={"faction":"faction","rarity":"rarity","damage_type":"damage","class_tag":"class",
                "attack_range":"range","special_trait":"trait"}
        for hero in load_hero_catalog()["heroes"]:
            for field,group in fields.items():
                with self.subTest(hero=hero["code"],field=field):
                    key=group+"."+hero[field]
                    self.assertTrue(self.registry.glossary(key))
                    self.assertTrue(self.registry.glossary(key,"short"))

    def test_all_boss_and_tower_codes_have_labels(self):
        from app.mini.boss.catalog import load_boss_catalog
        from app.mini.tower.catalog import load_catalog
        rows=load_boss_catalog()["bosses"]+load_catalog()["floors"]
        for row in rows:
            self.assertTrue(self.registry.glossary("faction."+row.get("faction","commoners")))
            for feature in row.get("features",[]):
                self.assertTrue(self.registry.glossary("trait."+feature))

    def test_legacy_demon_and_demonic_are_retained(self):
        from app.mini.presentation import TRAIT_LABELS,TRAIT_DESCRIPTIONS
        self.assertEqual(TRAIT_LABELS["demon"],"Демон")
        self.assertEqual(TRAIT_LABELS["demonic"],"Демонический")
        self.assertEqual(TRAIT_DESCRIPTIONS["demon"],TRAIT_DESCRIPTIONS["demonic"])

    def test_display_edit_does_not_rename_internal_code(self):
        from app.mini.presentation import FACTION_LABELS,faction_name
        self.write("glossary.json",{"faction.commoners":{"full":"Обычные люди","short":"Люди"}})
        self.assertEqual(faction_name("commoners"),"Обычные люди")
        self.assertIn("commoners",FACTION_LABELS)
        self.assertNotIn("Обычные люди",FACTION_LABELS)
        self.assertEqual(self.registry.glossary("faction.commoners","short"),"Люди")

    def test_imported_rules_mapping_is_live(self):
        from app.mini.rules import SECTIONS
        old=SECTIONS["start"]
        self.write("rules.json",{"rules.start.title":"🚀 Новый старт","rules.start.body":"Инструкция"})
        self.assertEqual(SECTIONS["start"],("🚀 Новый старт","Инструкция"))
        self.assertNotEqual(old,SECTIONS["start"])

    def test_onboarding_accessor_is_live(self):
        from app.mini.onboarding import starter_message
        old=starter_message()
        self.write("general.json",{"onboarding.starter_message":"Подарок уже готов"})
        self.assertNotEqual(starter_message(),old)
        self.assertEqual(starter_message(),"Подарок уже готов")

    def test_all_live_ui_keys_exist(self):
        import ast
        root=Path(__file__).resolve().parents[2]
        for path in (root/"app/mini").rglob("*.py"):
            if "editorial" in path.parts:continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=="editorial_text":
                    if isinstance(node.args[0],ast.Name):
                        self.assertEqual(path.relative_to(root).as_posix(),"app/mini/effects/registry.py")
                        self.assertEqual(node.args[0].id,"target")
                        continue
                    self.assertIsInstance(node.args[0],ast.Constant,str(path))
                    self.assertIn(node.args[0].value,self.registry.loader.schemas,str(path))

class AdditionalPresentationTests(EditorialCase):
    def test_effect_descriptions_and_titles_are_live_without_changing_effect_handler(self):
        from app.mini.effects.registry import effect_description,active_effect_title,get_effect
        original=get_effect("gacha_luck")
        self.write("effects.json",{"effect.gacha_luck.description":"Используется при следующем призыве",
                                  "effect.gacha_luck.title":"Удача призыва"})
        self.assertEqual(effect_description("gacha_luck"),"Используется при следующем призыве")
        self.assertEqual(active_effect_title("gacha_luck"),"Удача призыва")
        self.assertIs(get_effect("gacha_luck"),original)

    def test_user_error_can_change_without_changing_equip_validation(self):
        from app.mini.equipment.service import unequip
        key=next(k for k in self.registry.loader.schemas if k.startswith("errors.equipment.service.unequip."))
        default=self.registry.loader.defaults[key]
        self.write("errors.json",{key:"Выбери корректный слот"})
        self.assertEqual(self.registry.text(key),"Выбери корректный слот")
        self.assertNotEqual(self.registry.text(key),default)

    def test_large_editorial_caption_retains_hp_and_reward_using_default_screen(self):
        from app.mini.boss.public import format_public_boss
        from app.mini.boss.catalog import list_boss_templates
        boss=dict(list_boss_templates()[0],id=1,current_hp=123,status="announced",features_json="[]",reward_items_json="[]")
        before=format_public_boss(boss,[])
        entries={key:value+"X"*180 for key,value in self.registry.loader.defaults.items()
                 if key.startswith("boss.format_public_boss.") and isinstance(value,str)}
        self.write("boss.json",entries)
        with self.assertLogs("app.mini.boss.public","WARNING"):
            after=format_public_boss(boss,[])
        self.assertEqual(after,before)
        self.assertIn("HP: 123/",after)
        self.assertIn("Награда:",after)

    def test_bonus_prefix_cannot_be_moved_by_editor(self):
        self.write("equipment.json",{"equipment.item":"{name} · {slot} · {bonus}"})
        with self.assertLogs("app.mini.editorial.loader","WARNING"):
            self.assertEqual(self.registry.text("equipment.item",bonus="+56",name="Покрывало",slot="Плащ"),
                             "[+56] Покрывало · Плащ")

class EquipmentDisplayTests(EditorialCase):
    def item(self, bonus=56, slot="cloak", index=1):
        return dict(code=f"item_{index}",name="Покрывало дракона с икотой"*3,slot=slot,attack_bonus=bonus,quantity=1)

    def test_bonus_is_first_for_long_name_and_correct_slot(self):
        item=self.item()
        label=equipment_label(item)
        self.assertTrue(label.startswith("[+56] "))
        self.assertTrue(label.endswith(" · Плащ"))
        self.assertIn(item["name"],label)
        self.assertEqual(item["attack_bonus"],56)

    def test_zero_and_negative_sign(self):
        self.assertTrue(equipment_label(self.item(0)).startswith("[+0] "))
        self.assertTrue(equipment_label(self.item(-12)).startswith("[-12] "))

    def test_equip_remove_and_inventory_use_one_format(self):
        item=self.item()
        state=dict(equipped={"cloak":item},owned=[item])
        before=copy.deepcopy(state)
        text,_=render_equipment(state,1,2)
        self.assertIn("[+56] ",text)
        text,markup=render_equipment(state,1,2,mode="choose_remove")
        self.assertTrue(markup.inline_keyboard[0][0].text.startswith("[+56] "))
        self.assertIn("unequip.cloak",markup.inline_keyboard[0][0].callback_data)
        state["equipped"]={}
        _,markup=render_equipment(state,1,2,mode="choose_equip")
        self.assertTrue(markup.inline_keyboard[0][0].text.startswith("[+56] "))
        self.assertIn("equip.item_1",markup.inline_keyboard[0][0].callback_data)
        text,_=render_equipment(state,1,2,mode="inventory")
        self.assertIn("[+56]",text)
        self.assertEqual(state["owned"],before["owned"])

    def test_pagination_and_tower_parent_preserved(self):
        state=dict(equipped={},owned=[self.item(i,index=i) for i in range(1,21)])
        _,markup=render_equipment(state,1,2,page=1,mode="choose_equip",parent="tower")
        self.assertTrue(markup.inline_keyboard[0][0].text.startswith("[+9] "))
        self.assertTrue(all(b.callback_data.endswith("~t") for row in markup.inline_keyboard for b in row))
        _,home=render_equipment(state,1,2,parent="tower")
        self.assertEqual(home.inline_keyboard[-1][0].callback_data,"mini:tower:1:2")

class AllScreensHotReloadTests(EditorialCase):
    pass

def screen_test(sid):
    def test(self):
        with patch("secrets.token_hex",return_value="11223344"):
            before_text,before_markup,_=render_screen(sid)
        button_file=json.loads((Path(__file__).resolve().parents[2]/"app/mini/editorial/defaults/ru/buttons.json").read_text(encoding="utf-8"))
        self.write("buttons.json",{key:value+"!" for key,value in button_file.items()})
        # Also update a rules page and terminology without changing its code.
        self.write("glossary.json",{"faction.commoners":{"full":"Простые люди"}})
        self.write("rules.json",{"rules.start.body":"Обновлённое обучение"})
        self.registry.loader.refresh()
        with patch("secrets.token_hex",return_value="11223344"):
            after_text,after_markup,_=render_screen(sid)
        self.assertEqual([[b.callback_data for b in row] for row in before_markup.inline_keyboard],
                         [[b.callback_data for b in row] for row in after_markup.inline_keyboard])
        self.assertEqual([[b.style for b in row] for row in before_markup.inline_keyboard],
                         [[b.style for b in row] for row in after_markup.inline_keyboard])
        self.assertTrue(after_text)
        asyncio.run(button_handlers(after_markup))
    return test
for record in SOURCE:
    setattr(AllScreensHotReloadTests,"test_reload_"+record["id"],screen_test(record["id"]))

from tests.v1_3.support import MiniCase
class ActiveGameEditorialTests(MiniCase):
    def test_tower_session_and_sqlite_are_unchanged_by_reload_and_restart(self):
        import tempfile
        from app.mini.editorial import registry as module
        from app.mini.db import connect_mini_db
        from app.mini.tower.service import start_attempt,get_state
        from app.mini.tower.handlers import render_state
        hero=self.hero()
        attempt=start_attempt(self.pid,hero["id"],1,self.db)
        def database_snapshot():
            with connect_mini_db(self.db) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
                self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
                return list(conn.iterdump())
        original=database_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            registry=Registry(directory)
            with patch.object(module,"_registry",registry):
                state=get_state(self.pid,self.db)
                before=render_state(state,1,2)
                (Path(directory)/"buttons.json").write_text('{"navigation.back":"Возврат"}',encoding="utf-8")
                registry.loader.refresh()
                after=render_state(get_state(self.pid,self.db),1,2)
                self.assertNotEqual(before[1].inline_keyboard[-1][-1].text,after[1].inline_keyboard[-1][-1].text)
                # Simulate the process's registry being reconstructed against the same SQLite.
                with patch.object(module,"_registry",Registry(directory)):
                    render_state(get_state(self.pid,self.db),1,2)
        self.assertEqual(original,database_snapshot())
        self.assertEqual(get_state(self.pid,self.db)["attempt"]["id"],attempt["id"])

    def test_active_boss_publication_does_not_rewrite_hp_rewards_or_data(self):
        from app.mini.db import connect_mini_db
        from app.mini.boss.public import format_public_boss
        from app.mini.boss.catalog import list_boss_templates
        import tempfile
        from app.mini.editorial import registry as module
        boss=dict(list_boss_templates()[0],id=1,current_hp=500,status="fighting",features_json="[]",reward_items_json="[]")
        before=copy.deepcopy(boss)
        with tempfile.TemporaryDirectory() as directory:
            registry=Registry(directory)
            (Path(directory)/"glossary.json").write_text('{"faction.monsters":{"full":"Создания"}}',encoding="utf-8")
            with patch.object(module,"_registry",registry):
                self.assertIn("HP: 500/",format_public_boss(boss,[]))
        self.assertEqual(boss,before)
        with connect_mini_db(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
