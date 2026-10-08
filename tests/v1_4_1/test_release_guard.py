"""Unit and real CLI regression probes in a disposable Git checkout."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import release_guard as guard

SCRIPT = Path(guard.__file__).resolve()
EVENTS = "app/mini/events/service.py"
TESTS = "tests/test_mini_events.py"
IMPORT = "from app.mini.events.handlers import router as mini_events_router"


class InventoryTests(unittest.TestCase):
    def test_ast_finds_async_methods_functions_and_scopes(self):
        text = "def test_top(): pass\nclass Events:\n async def test_resume(self): pass\n"
        self.assertEqual(guard.test_symbols(text, TESTS), {"test_top", "Events.test_resume"})

    def test_duplicate_methods_are_rejected_instead_of_silently_hidden(self):
        with self.assertRaisesRegex(guard.GuardError, "duplicate test symbol"):
            guard.test_symbols("def test_one(): pass\ndef test_one(): pass", TESTS)

    def test_invalid_python_fails_closed(self):
        with self.assertRaisesRegex(guard.GuardError, "invalid Python"):
            guard.test_symbols("def test_broken(", TESTS)

    def test_dynamic_router_registry_requires_review(self):
        with self.assertRaisesRegex(guard.GuardError, "explicit list"):
            guard.handler_inventory("ROUTERS = build_routers()")

    def test_router_reassignment_cannot_hide_removal(self):
        with self.assertRaisesRegex(guard.GuardError, "exactly one explicit assignment"):
            guard.handler_inventory("ROUTERS = [mini_events_router]\nROUTERS = []")

    def test_bad_allowlist_structure_and_placeholder_reasons_fail(self):
        key = ("file", EVENTS, "")
        entry = {"kind": "file", "path": EVENTS, "reason": "TODO"}
        for data in ([], {}, {"removals": {}}, {"removals": [None]}, {"removals": [entry]}):
            with self.subTest(data=data), self.assertRaises(guard.GuardError):
                guard.load_allowlist(json.dumps(data), {key})


class ReleaseGuardCLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.write(EVENTS, "def resume(): return 'preserved'\n")
        self.write("bot.py", "# bot entry point\n")
        self.write("app/mini/content/heroes/common.json", '{"heroes": []}\n')
        self.write("app/mini/events/handlers.py", "router = object()\n")
        self.write(guard.HANDLERS_PATH, IMPORT + "\nROUTERS = [mini_events_router]\n")
        self.write(TESTS, "import unittest\nclass MiniEvents(unittest.TestCase):\n    def test_restart(self): pass\n    def test_rewards(self): pass\n")
        self.commit("V1.4 stable fixture")
        self.baseline = self.git("rev-parse", "HEAD").strip()
        self.write(guard.ALLOWLIST_PATH, '{"removals": []}\n')
        self.commit("V1.4.1 development fixture")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message="Development change"):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def run_guard(self, *, baseline=None):
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo),
                                 "--baseline", self.baseline if baseline is None else baseline],
                                capture_output=True, text=True, encoding="utf-8")
        return result.returncode, result.stdout + result.stderr

    def assert_guard(self, exit_code, message, **kwargs):
        code, output = self.run_guard(**kwargs)
        self.assertEqual(code, exit_code, output)
        self.assertIn(message, output)
        return output

    def test_no_removals_pass_both_api_and_cli(self):
        self.assert_guard(0, "OK: release guard")
        report = guard.check(self.repo, self.baseline)
        self.assertEqual(report["baseline_test_symbols"], 2)
        self.assertEqual(report["approved_removals"], 0)

    def test_deleted_events_service_fails(self):
        (self.repo / EVENTS).unlink()
        self.commit()
        self.assert_guard(1, "file: " + EVENTS)

    def test_deleted_events_test_file_fails(self):
        (self.repo / TESTS).unlink()
        self.commit()
        self.assert_guard(1, "file: " + TESTS)

    def test_deleted_existing_method_fails_even_when_another_is_added(self):
        self.write(TESTS, "class MiniEvents:\n def test_rewards(self): pass\n def test_new(self): pass\n")
        self.commit()
        self.assert_guard(1, TESTS + "::MiniEvents.test_restart")

    def test_removed_router_fails_even_with_import_preserved(self):
        self.write(guard.HANDLERS_PATH, IMPORT + "\nROUTERS = []\n")
        self.commit()
        self.assert_guard(1, "router: " + guard.HANDLERS_PATH + "::mini_events_router")

    def test_removed_import_fails_even_with_router_preserved(self):
        self.write(guard.HANDLERS_PATH, "mini_events_router = object()\nROUTERS = [mini_events_router]\n")
        self.commit()
        self.assert_guard(1, "import: " + guard.HANDLERS_PATH + "::" + IMPORT)

    def test_deleted_json_catalog_fails(self):
        (self.repo / "app/mini/content/heroes/common.json").unlink()
        self.commit()
        self.assert_guard(1, "app/mini/content/heroes/common.json")

    def test_deleted_application_entry_point_fails(self):
        (self.repo / "bot.py").unlink()
        self.commit()
        self.assert_guard(1, "file: bot.py")

    def test_stable_baseline_detects_removal_in_earlier_development_commit(self):
        (self.repo / EVENTS).unlink()
        self.commit("Earlier accidental deletion")
        self.write("app/new_feature.py", "# unrelated next development commit\n")
        self.commit("Next dev commit must not reset baseline")
        self.assert_guard(1, EVENTS)

    def test_reviewable_file_removal_passes(self):
        (self.repo / EVENTS).unlink()
        self.approve({"kind": "file", "path": EVENTS, "reason": "Replaced by reviewed unified events service in PR 42."})
        self.assert_guard(0, '"approved_removals": 1')

    def test_reviewable_exact_method_removal_passes(self):
        self.write(TESTS, "class MiniEvents:\n def test_rewards(self): pass\n")
        self.approve({"kind": "test", "path": TESTS, "symbol": "MiniEvents.test_restart",
                      "reason": "Restart coverage moved to reviewed integration suite PR 42."})
        self.assert_guard(0, '"approved_removals": 1')

    def approve(self, *entries):
        self.write(guard.ALLOWLIST_PATH, json.dumps({"removals": list(entries)}))
        self.commit()

    def test_malformed_allowlist_fails_even_without_removal(self):
        self.write(guard.ALLOWLIST_PATH, "{broken json")
        self.commit()
        self.assert_guard(1, "invalid allowlist")

    def test_missing_allowlist_fails_closed(self):
        (self.repo / guard.ALLOWLIST_PATH).unlink()
        self.commit()
        self.assert_guard(1, "allowlist missing")

    def test_nonexistent_baseline_fails_closed(self):
        self.assert_guard(1, "baseline unavailable", baseline="0" * 40)

    def test_relative_baseline_is_forbidden(self):
        self.assert_guard(1, "explicit full stable-release SHA", baseline="HEAD~1")

    def test_wildcard_and_traversal_allowlists_fail_closed(self):
        for path in ("*", "app/*", "../app/service.py", "/app/service.py", "app/../service.py"):
            with self.subTest(path=path):
                self.approve({"kind": "file", "path": path, "reason": "Reviewed consolidation of obsolete event service."})
                self.assert_guard(1, "exact relative path")

    def test_blank_reason_fails_closed(self):
        (self.repo / EVENTS).unlink()
        self.approve({"kind": "file", "path": EVENTS, "reason": "  "})
        self.assert_guard(1, "meaningful reason")

    def test_duplicate_entries_fail_closed(self):
        (self.repo / EVENTS).unlink()
        entry = {"kind": "file", "path": EVENTS, "reason": "Reviewed consolidation of obsolete event service."}
        self.approve(entry, entry)
        self.assert_guard(1, "duplicate allowlist removal")

    def test_duplicate_json_keys_fail_closed(self):
        self.write(guard.ALLOWLIST_PATH, '{"removals": [], "removals": []}')
        self.commit()
        self.assert_guard(1, "duplicate allowlist field")

    def test_stale_allowlist_is_not_future_blanket_permission(self):
        self.approve({"kind": "file", "path": EVENTS, "reason": "Reviewed consolidation of obsolete event service."})
        self.assert_guard(1, "stale or nonexistent")

    def test_invalid_test_ast_fails_closed(self):
        self.write(TESTS, "def test_broken(")
        self.commit()
        self.assert_guard(1, "invalid Python")


if __name__ == "__main__":
    unittest.main()
