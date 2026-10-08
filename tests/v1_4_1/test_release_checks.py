import json
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


if __name__ == "__main__":
    unittest.main()
