"""Editorial failures, live snapshots and Telegram formatting with disposable files."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

os.environ.setdefault("BOT_TOKEN", "test-token")
from app.mini.editorial import registry as module
from app.mini.editorial.registry import Registry, screen
from app.mini.editorial.loader import Loader, DEFAULTS
from app.mini.editorial.validation import EditorialError, validate_entry
from app.mini.editorial.buttons import button
from app.mini.editorial.renderer import render, render_icon

class EditorialCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/"ru"
        self.directory.mkdir()
        self.registry = Registry(self.directory)
        self.scope = patch.object(module, "_registry", self.registry)
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.env = patch.dict(os.environ, {"MINI_CUSTOM_EMOJI_ENABLED": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, filename, value):
        path = self.directory/filename
        # Admin workflow: write a sibling temporary file, then atomic replace.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        temporary.replace(path)

class LoaderTests(EditorialCase):
    def test_defaults_complete_and_valid(self):
        self.assertGreater(len(self.registry.loader.defaults), 800)
        self.assertEqual(self.registry.text("gacha.summon"), "✨ Призвать героя")

    def test_absent_directory_and_file_use_defaults(self):
        registry = Registry(self.directory/"missing")
        self.assertEqual(registry.text("general.back"), "Назад")

    def test_partial_file_key_fallback(self):
        self.write("buttons.json", {"gacha.summon": "✨ Позвать героя"})
        self.assertEqual(self.registry.text("gacha.summon"), "✨ Позвать героя")
        self.assertEqual(self.registry.text("general.back"), "Назад")

    def test_hot_reload_then_revert_without_restart(self):
        pid = os.getpid()
        self.write("buttons.json", {"gacha.summon": "✨ Позвать героя"})
        self.assertIn("Позвать", self.registry.text("gacha.summon"))
        self.write("buttons.json", {"gacha.summon": "✨ Призвать героя"})
        self.assertIn("Призвать", self.registry.text("gacha.summon"))
        self.assertEqual(os.getpid(), pid)

    def test_deleted_override_returns_default(self):
        self.write("buttons.json", {"general.back": "Возврат"})
        self.assertEqual(self.registry.text("general.back"), "Возврат")
        (self.directory/"buttons.json").unlink()
        self.assertEqual(self.registry.text("general.back"), "Назад")

    def test_invalid_initial_file_falls_back_with_warning(self):
        (self.directory/"buttons.json").write_text("{", encoding="utf-8")
        with self.assertLogs("app.mini.editorial.loader", "WARNING"):
            registry = Registry(self.directory)
        self.assertEqual(registry.text("general.back"), "Назад")

    def test_rejected_revision_logs_once_and_can_be_repaired(self):
        self.write("buttons.json", {"general.back": "Возврат"})
        accepted = self.registry.loader.refresh()
        (self.directory/"buttons.json").write_text("{", encoding="utf-8")
        with self.assertLogs("app.mini.editorial.loader", "WARNING") as logs:
            self.assertIs(self.registry.loader.refresh(), accepted)
            self.assertIs(self.registry.loader.refresh(), accepted)
        self.assertEqual(len(logs.output), 1)
        self.write("buttons.json", {"general.back": "Исправлено"})
        self.assertEqual(self.registry.text("general.back"), "Исправлено")

    def test_all_files_are_applied_atomically(self):
        self.write("buttons.json", {"general.back": "Старое"})
        accepted = self.registry.loader.refresh()
        self.write("buttons.json", {"general.back": "Новое"})
        self.write("glossary.json", {"faction.beasts": {"full": 7}})
        with self.assertLogs("app.mini.editorial.loader", "WARNING"):
            self.assertIs(self.registry.loader.refresh(), accepted)
        self.assertEqual(self.registry.text("general.back"), "Старое")
        self.write("glossary.json", {"faction.beasts": {"full": "Зверушки"}})
        updated = self.registry.loader.refresh()
        self.assertIsNot(updated, accepted)
        self.assertEqual(updated["general.back"], "Новое")
        self.assertEqual(updated["faction.beasts"]["full"], "Зверушки")

    def test_unchanged_files_are_never_parsed_again(self):
        self.write("buttons.json", {"general.back": "Возврат"})
        self.registry.loader.refresh()
        with patch("app.mini.editorial.loader.read_json", side_effect=AssertionError("unnecessary IO")):
            for _ in range(20):
                self.assertEqual(self.registry.text("general.back"), "Возврат")

    def test_snapshot_is_deeply_immutable(self):
        snapshot = self.registry.snapshot()
        with self.assertRaises(TypeError):
            snapshot["general.back"] = "bad"
        with self.assertRaises(TypeError):
            snapshot["faction.beasts"]["full"] = "bad"

    def test_concurrent_readers_pin_one_generation(self):
        self.write("buttons.json", {"general.back": "Старое"})
        first = self.registry.loader.refresh()
        entered = threading.Event()
        release = threading.Event()
        def reader():
            with self.registry.read(first):
                entered.set()
                release.wait(5)
                return self.registry.text("general.back"), self.registry.glossary("faction.beasts")
        with ThreadPoolExecutor(max_workers=4) as pool:
            future = pool.submit(reader)
            self.assertTrue(entered.wait(5))
            self.write("buttons.json", {"general.back": "Новое"})
            self.write("glossary.json", {"faction.beasts": {"full": "Зверушки"}})
            self.registry.loader.refresh()
            release.set()
            self.assertEqual(future.result(5), ("Старое", "Звери"))
            results = list(pool.map(lambda _: self.registry.text("general.back"), range(12)))
            self.assertEqual(results, ["Новое"]*12)

    def test_metadata_change_during_read_is_not_published(self):
        original = self.registry.loader.snapshot
        self.write("buttons.json", {"general.back": "Новое"})
        signature = self.registry.loader._stat()
        with patch.object(self.registry.loader, "_stat", side_effect=[signature, ()]):
            self.assertIs(self.registry.loader.refresh(), original)
        self.assertEqual(self.registry.text("general.back"), "Новое")

    def test_duplicate_json_keys_rejected(self):
        (self.directory/"buttons.json").write_text('{"general.back":"A","general.back":"B"}',encoding="utf-8")
        with self.assertLogs("app.mini.editorial.loader", "WARNING"):
            self.assertEqual(self.registry.text("general.back"), "Назад")

    def test_corrupt_built_in_defaults_are_build_error(self):
        defaults = self.directory/"defaults"
        defaults.mkdir()
        (defaults/"buttons.json").write_text("{",encoding="utf-8")
        with self.assertRaises(EditorialError):
            Loader(None, defaults=defaults)

    def test_invalid_default_icon_reference_is_build_error(self):
        import shutil
        defaults=self.directory/"defaults"
        shutil.copytree(DEFAULTS,defaults)
        glossary=json.loads((defaults/"glossary.json").read_text(encoding="utf-8"))
        glossary["faction.beasts"]["icon_key"]="does.not.exist"
        (defaults/"glossary.json").write_text(json.dumps(glossary),encoding="utf-8")
        with self.assertRaisesRegex(EditorialError,"unknown default icon_key"):
            Loader(None,defaults=defaults)

    def test_checkout_external_path_is_rejected(self):
        with self.assertRaisesRegex(EditorialError, "outside Git"):
            Registry(Path(__file__).resolve().parents[2]/"overrides")

    def test_cli_rejects_invalid_external_revision(self):
        self.write("buttons.json", {"general.back": 4})
        result = subprocess.run([sys.executable, "-m", "app.mini.editorial.validator", "--directory", str(self.directory)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertIn("general.back", result.stdout)
        self.assertIn("buttons.json", result.stdout)

    def test_partial_glossary_field_is_merged(self):
        self.write("glossary.json", {"faction.commoners": {"short": "Прост."}})
        self.assertEqual(self.registry.glossary("faction.commoners","short"),"Прост.")
        self.assertEqual(self.registry.glossary("faction.commoners"),"Простолюдины")

def bad_revision_test(filename, values):
    def test(self):
        before = self.registry.loader.snapshot
        self.write(filename, values)
        with self.assertLogs("app.mini.editorial.loader", "WARNING"):
            self.assertIs(self.registry.loader.refresh(), before)
    return test

for name, filename, value in [
    ("type", "buttons.json", {"general.back": 3}),
    ("null", "buttons.json", {"general.back": None}),
    ("empty", "buttons.json", {"general.back": ""}),
    ("long", "buttons.json", {"general.back": "x"*300}),
    ("placeholder", "buttons.json", {"general.back": "{unknown}"}),
    ("attribute", "buttons.json", {"general.back": "{player.name}"}),
    ("index", "buttons.json", {"general.back": "{player[0]}"}),
    ("expression", "buttons.json", {"general.back": "{1+2}"}),
    ("conversion", "buttons.json", {"general.back": "{value!r}"}),
    ("format", "buttons.json", {"general.back": "{value:04d}"}),
    ("html_plain", "buttons.json", {"general.back": "<b>Назад</b>"}),
    ("legacy_control_message", "errors.json", {"errors.admin_grants.grant_mini_item.04": "Новое сообщение"}),
    ("unknown_key", "buttons.json", {"does.not.exist": "Назад"}),
    ("wrong_file", "buttons.json", {"rules.overview": "Правила"}),
    ("unknown_catalog", "unknown.json", {"general.back": "Назад"}),
    ("invalid_id", "icons.json", {"icon.gacha.summon": {"custom_emoji_id": "abc"}}),
    ("integer_id", "icons.json", {"icon.gacha.summon": {"custom_emoji_id": 123}}),
    ("empty_id", "icons.json", {"icon.gacha.summon": {"custom_emoji_id": ""}}),
    ("negative_id", "icons.json", {"icon.gacha.summon": {"custom_emoji_id": "-1"}}),
    ("control", "buttons.json", {"general.back": "x\x00y"}),
    ("surrogate", "glossary.json", {"faction.beasts": {"full": "\ud800"}}),
    ("icon_text", "icons.json", {"icon.gacha.summon": {"fallback": "✨abc"}}),
    ("icon_markup", "icons.json", {"icon.gacha.summon": {"fallback": "<b>x</b>"}}),
    ("icon_key", "glossary.json", {"faction.beasts": {"icon_key": "general.back"}}),
    ("unknown_field", "glossary.json", {"faction.beasts": {"bad": "wrong"}}),
]:
    setattr(LoaderTests,"test_reject_"+name,bad_revision_test(filename,value))

class RenderingTests(EditorialCase):
    def test_custom_button_preserves_callback_style_and_only_one_icon(self):
        self.write("icons.json", {"icon.gacha.summon": {"custom_emoji_id":"123456789"}})
        with patch.dict(os.environ, {"MINI_CUSTOM_EMOJI_ENABLED":"true"}):
            result = button(key="gacha.summon",callback_data="mini:gacha:1:2",style="success")
        self.assertEqual(result.callback_data, "mini:gacha:1:2")
        self.assertEqual(result.style,"success")
        self.assertEqual(result.icon_custom_emoji_id,"123456789")
        self.assertEqual(result.text,"Призвать героя")

    def test_custom_disabled_keeps_fallback(self):
        self.write("icons.json", {"icon.gacha.summon":{"custom_emoji_id":"123456789"}})
        result = button(key="gacha.summon",callback_data="action")
        self.assertEqual(result.text,"✨ Призвать героя")
        self.assertIsNone(result.icon_custom_emoji_id)

    def test_missing_custom_id_is_working_fallback(self):
        with patch.dict(os.environ, {"MINI_CUSTOM_EMOJI_ENABLED":"true"}):
            result=button(key="gacha.summon",callback_data="action")
        self.assertEqual(result.text,"✨ Призвать героя")
        self.assertIsNone(result.icon_custom_emoji_id)

    def test_replaced_fallback_does_not_duplicate_original_icon(self):
        self.write("icons.json", {"icon.gacha.summon":{"fallback":"⭐"}})
        result=button(key="gacha.summon",callback_data="action")
        self.assertEqual(result.text,"⭐ Призвать героя")

    def test_unsupported_aiogram_field_uses_fallback(self):
        self.write("icons.json", {"icon.gacha.summon":{"custom_emoji_id":"123456789"}})
        from aiogram.types import InlineKeyboardButton
        fields={k:v for k,v in InlineKeyboardButton.model_fields.items() if k!="icon_custom_emoji_id"}
        with patch.object(InlineKeyboardButton,"model_fields",fields), patch.dict(os.environ,{"MINI_CUSTOM_EMOJI_ENABLED":"true"}):
            result=button(key="gacha.summon",callback_data="action")
        self.assertEqual(result.text,"✨ Призвать героя")
        self.assertIsNone(result.icon_custom_emoji_id)

    def test_utf8_callback_limit(self):
        self.assertEqual(button(text="OK",callback_data="я"*32).callback_data,"я"*32)
        with self.assertRaises(ValueError):
            button(text="OK",callback_data="я"*33)

    def test_html_icon_uses_escaped_fallback(self):
        self.write("icons.json",{"icon.section.duels":{"custom_emoji_id":"123456789"}})
        with patch.dict(os.environ,{"MINI_CUSTOM_EMOJI_ENABLED":"true"}):
            self.assertEqual(self.registry.icon("icon.section.duels",html_mode=True),
                             '<tg-emoji emoji-id="123456789">⚔️</tg-emoji>')
            self.assertEqual(self.registry.icon("icon.section.duels"),"⚔️")

    def test_real_html_duel_escapes_names_and_renders_icon(self):
        from app.mini.duels.handlers import public_challenge_text
        self.write("icons.json",{"icon.section.duels":{"custom_emoji_id":"123456789"}})
        with patch.dict(os.environ,{"MINI_CUSTOM_EMOJI_ENABLED":"true"}):
            result=public_challenge_text({"character_name":"<b>A</b>"},{"character_name":"<script>B</script>","telegram_user_id":5},10)
        self.assertIn("&lt;b&gt;A&lt;/b&gt;",result)
        self.assertIn('href="tg://user?id=5"',result)
        self.assertIn("&lt;script&gt;B&lt;/script&gt;",result)
        self.assertIn("<tg-emoji",result)
        self.assertNotIn("<script>",result)

    def test_runtime_values_over_limit_revert_to_default(self):
        self.write("equipment.json",{"equipment.item":"[{bonus}] "+"x"*180+"{name} · {slot}"})
        with self.assertLogs("app.mini.editorial.renderer","WARNING"):
            result=self.registry.text("equipment.item",bonus="+56",name="a"*100,slot="Плащ")
        self.assertTrue(result.startswith("[+56]"))

    def test_empty_custom_id_is_not_null(self):
        with self.assertRaises(EditorialError):
            validate_entry("icons.json","key",{"custom_emoji_id":"","fallback":"⭐"},{"kind":"icon"})

    def test_schema_requires_original_dynamic_values(self):
        key="equipment.item"
        with self.assertRaisesRegex(EditorialError,"required placeholders"):
            validate_entry("equipment.json",key,"[{bonus}] ",self.registry.loader.schemas[key])

    def test_html_malformed_and_unclosed_tags_rejected(self):
        spec={"kind":"text","parameters":[],"limit":1024,"parse_mode":"HTML"}
        for value in ("<b>bad","<b","<b>x</i>","<script>x</script>",'<a href="javascript:bad">x</a>',
                      '<tg-emoji emoji-id="bad">⭐</tg-emoji>',"&bad;","x & y"):
            with self.subTest(value=value), self.assertRaises(EditorialError):
                validate_entry("test.json","test",value,spec)

    def test_valid_html_accepted(self):
        spec={"kind":"text","parameters":["name"],"limit":1024,"parse_mode":"HTML"}
        validate_entry("test.json","test",'<b>{name}</b> &amp; <tg-emoji emoji-id="123">⭐</tg-emoji>',spec)

class AsyncRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_refreshes_off_event_loop_and_pins(self):
        from app.mini.editorial.middleware import EditorialMiddleware
        with tempfile.TemporaryDirectory() as directory:
            registry=Registry(directory)
            event_thread=threading.get_ident()
            threads=[]
            original=registry.loader.refresh
            def tracked():
                threads.append(threading.get_ident())
                return original()
            with patch.object(module,"_registry",registry), patch.object(registry.loader,"refresh",tracked):
                async def handler(event,data):
                    for _ in range(10):registry.text("general.back")
                    return registry.text("general.back")
                result=await EditorialMiddleware()(handler,None,{})
            self.assertEqual(result,"Назад")
            self.assertEqual(len(threads),1)
            self.assertNotEqual(threads[0],event_thread)

    async def test_background_screen_refreshes_without_middleware(self):
        with tempfile.TemporaryDirectory() as directory:
            registry=Registry(directory)
            with patch.object(module,"_registry",registry):
                @screen
                async def publish():
                    return registry.text("general.back")
                (Path(directory)/"buttons.json").write_text('{"general.back":"Changed"}',encoding="utf-8")
                self.assertEqual(await publish(),"Changed")
