import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from scripts import release_checks


class ReleaseCheckSafetyTests(unittest.TestCase):
    def test_network_guard_rejects_external_connect_without_contact(self):
        namespace = {"__name__": "release_network_probe"}
        original = socket.socket.connect
        original_ex = socket.socket.connect_ex
        try:
            exec(release_checks.NETWORK_GUARD, namespace)
            with socket.socket() as sock:
                with self.assertRaisesRegex(RuntimeError, "External networking forbidden"):
                    sock.connect(("api.telegram.org", 443))
                with self.assertRaisesRegex(RuntimeError, "External networking forbidden"):
                    sock.connect_ex(("149.154.167.220", 443))
        finally:
            socket.socket.connect = original
            socket.socket.connect_ex = original_ex

    def test_summary_distinguishes_not_run_from_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            report, output = Path(directory)/"report.json", Path(directory)/"summary.md"
            data = {"baseline": "a"*40, "head": "b"*40,
                    "checks": {"guard": "FAIL (exit 1)", "validator": "NOT RUN"}, "tests": None}
            report.write_text(json.dumps(data), encoding="utf-8")
            release_checks.summary(report, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn("FAIL (exit 1)", text)
            self.assertIn("NOT RUN (count unknown)", text)
            self.assertNotIn("| validator | OK |", text)
            self.assertIn("a"*40, text)
            self.assertIn("b"*40, text)

    def test_summary_when_runner_never_started_has_unknown_test_count(self):
        with tempfile.TemporaryDirectory() as directory:
            report, output = Path(directory)/"missing.json", Path(directory)/"summary.md"
            with patch.dict("os.environ", {"GITHUB_SHA": "c"*40}):
                release_checks.summary(report, output)
            text = output.read_text(encoding="utf-8")
            self.assertIn("NOT RUN (count unknown)", text)
            self.assertIn("c"*40, text)
            self.assertNotIn("| full unittest | OK |", text)

    def test_discovery_rejects_missing_ids_even_when_total_count_matches(self):
        before = [{"id": "Events.test_restart", "path": "tests/test_events.py", "symbol": "Events.test_restart"}]
        after = [{"id": "Events.test_new", "path": "tests/test_events.py", "symbol": "Events.test_new"}]
        with self.assertRaisesRegex(RuntimeError, "missing discovered baseline tests"):
            release_checks.verify_discovery(before, after, [])

    def test_discovery_rejects_loss_of_duplicate_test_execution(self):
        record = {"id": "Events.test_restart", "path": "tests/test_events.py", "symbol": "Events.test_restart"}
        with self.assertRaisesRegex(RuntimeError, "missing discovered baseline tests"):
            release_checks.verify_discovery([record, record], [record], [])

    def test_discovery_allows_only_exact_reviewed_removal(self):
        record = {"id": "Events.test_restart", "path": "tests/test_events.py", "symbol": "Events.test_restart"}
        approval = {"kind": "test", "path": record["path"], "symbol": record["symbol"], "reason": "Moved to reviewed replacement suite."}
        release_checks.verify_discovery([record], [], [approval])
        approval["symbol"] = "Events.test_other"
        with self.assertRaisesRegex(RuntimeError, "missing discovered baseline tests"):
            release_checks.verify_discovery([record], [], [approval])

    def _archived_squash_test(self, inherited_workspace):
        # Exercise the actual runner archive/env handoff and real squash guard.
        # Expensive unrelated commands/discovery use a one-test integration fixture;
        # the complete release pipeline runs separately without these adapters.
        repo = Path(release_checks.__file__).resolve().parents[1]
        if not (repo / ".git").exists():
            repo = Path(os.environ["GITHUB_WORKSPACE"])
        repo = repo.resolve()
        observed = []
        record = {"id": "squash", "path": "tests/v1_4_1/test_release_guard.py", "symbol": "squash"}

        def execute(command, cwd, env):
            if "test_inventory.py" in str(command[1]):
                Path(command[2]).write_text(json.dumps([record]), encoding="utf-8")
                observed.append(env["GITHUB_WORKSPACE"])
                return 0, ""
            if command[1:3] == ["-m", "unittest"]:
                self.assertFalse((Path(cwd) / ".git").exists())
                self.assertEqual(env["GITHUB_WORKSPACE"], str(repo))
                self.assertEqual(env["BOT_TOKEN"], "ci-test-token")
                self.assertEqual(env["RELEASE_TEST_ISOLATED_TOPICS"], "1")
                observed.append(env["GITHUB_WORKSPACE"])
                result = subprocess.run(
                    [command[0], "-m", "unittest",
                     "tests.v1_4_1.test_release_guard.SquashGuardTests", "-q"],
                    cwd=cwd, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.returncode, result.stdout + result.stderr
            return 0, ""

        environment = dict(os.environ, BOT_TOKEN="ci-test-token")
        environment.pop("GITHUB_WORKSPACE", None)
        if inherited_workspace is not None:
            environment["GITHUB_WORKSPACE"] = inherited_workspace
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(release_checks, "execute", side_effect=execute), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = release_checks.run_checks(repo, report)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(code, 0, data)
            self.assertEqual(data["tests"]["count"], 1)
            self.assertEqual(data["tests"]["errors"], 0)
            self.assertEqual(data["tests"]["skips"], 0)
        self.assertEqual(observed, [str(repo)] * 3)

    def test_archived_squash_guard_without_inherited_github_workspace(self):
        self._archived_squash_test(None)

    def test_archived_squash_guard_overrides_unrelated_github_workspace(self):
        self._archived_squash_test("unrelated-nonexistent-workspace")


if __name__ == "__main__":
    unittest.main()
