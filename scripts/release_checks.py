"""Run release checks against a clean disposable snapshot, never a live DB."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

if __package__:
    from .release_guard import BASELINE_SHA, git, tree_files
else:
    from release_guard import BASELINE_SHA, git, tree_files

CHECK_NAMES = ("release guard", "git diff --check", "deploy shell syntax", "compileall",
               "check_bot (disposable DB)", "hero abilities", "boss abilities", "tower",
               "JSON and content validators", "full unittest")
NETWORK_GUARD = '''import os
import socket
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
def _allowed(address):
    # Windows asyncio socketpair may use loopback TCP; Unix sockets are local.
    return not isinstance(address, tuple) or address[0] in {"127.0.0.1", "::1", "localhost"}
def _connect(self, address):
    if not _allowed(address):
        raise RuntimeError("External networking forbidden in release checks")
    return _original_connect(self, address)
def _connect_ex(self, address):
    if not _allowed(address):
        raise RuntimeError("External networking forbidden in release checks")
    return _original_connect_ex(self, address)
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
if os.environ.get("RELEASE_TEST_ISOLATED_TOPICS") == "1":
    import app.topics
    app.topics.TOPIC_SETTINGS = {}
'''
CONTENT_CHECK = '''import json
from pathlib import Path
files = sorted(Path(".").rglob("*.json"))
for path in files:
    json.loads(path.read_text(encoding="utf-8-sig"))
from app.mini.catalog import validate_content
from app.mini.content_safety import validate_combat_content
from app.mini.village.balance import load_balance
validate_content()
validate_combat_content()
load_balance()
print(f"OK: JSON files={len(files)}; shop, heroes, combat safety, village validators")
'''
DB_CHECK = '''from pathlib import Path
from app.config import BASE_DIR, DB_PATH, LEGACY_DB_PATH
assert BASE_DIR.resolve() == Path.cwd().resolve(), (BASE_DIR, Path.cwd())
assert DB_PATH.resolve() == (Path.cwd() / "shpakdnd.db").resolve(), DB_PATH
assert LEGACY_DB_PATH.resolve() == (Path.cwd() / "timers.db").resolve(), LEGACY_DB_PATH
assert not DB_PATH.exists() and not LEGACY_DB_PATH.exists(), "Fresh snapshot required"
import check_bot
assert check_bot.DB_PATH == DB_PATH, "check_bot must import app.config.DB_PATH"
print("OK: check_bot uses app.config.DB_PATH in a fresh disposable checkout")
'''


def execute(command, cwd, env) -> tuple[int, str]:
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    print(result.stdout, end="", flush=True)
    return result.returncode, result.stdout


def run_checks(repo: Path, report_path: Path) -> int:
    report = {"baseline": BASELINE_SHA, "head": git(repo, "rev-parse", "HEAD").strip(),
              "checks": {name: "NOT RUN" for name in CHECK_NAMES}, "tests": None}

    def save():
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def step(name, commands, cwd=repo, env=None):
        print(f"\n=== {name} ===", flush=True)
        report["checks"][name] = "RUNNING"
        save()
        for command in commands:
            code, output = execute(command, cwd, env)
            if name == "full unittest":
                count = re.search(r"^Ran (\d+) tests? in ", output, re.M)
                value = lambda key: int(re.search(rf"\b{key}=(\d+)", output).group(1)) if re.search(rf"\b{key}=(\d+)", output) else 0
                report["tests"] = {"count": int(count.group(1)) if count else None,
                                   "failures": value("failures"), "errors": value("errors"),
                                   "skips": value("skipped"), "expected_failures": value("expected failures"),
                                   "unexpected_successes": value("unexpected successes")}
                if not count or int(count.group(1)) == 0:
                    code = 1
                if report["tests"]["expected_failures"] or report["tests"]["unexpected_successes"]:
                    print("ERROR: unexplained expected failure/unexpected success", flush=True)
                    code = 1
                if report["tests"]["skips"] and os.name == "posix":
                    print("ERROR: Linux release checks require zero skipped tests", flush=True)
                    code = 1
            if code:
                report["checks"][name] = f"FAIL (exit {code})"
                save()
                raise RuntimeError(f"{name} failed")
        report["checks"][name] = "OK"
        save()

    save()
    try:
        step("release guard", [[sys.executable, str(repo / "scripts/release_guard.py")]])
        step("git diff --check", [["git", "diff", "--check", BASELINE_SHA, "HEAD"]])
        bash = shutil.which("bash") if os.name != "nt" else r"C:\Program Files\Git\bin\bash.exe"
        shells = sorted(path for path in tree_files(repo, "HEAD") if path.startswith("deploy/") and path.endswith(".sh"))
        if not shells:
            raise RuntimeError("deploy shell inventory is empty")
        step("deploy shell syntax", [[bash, "-n", path] for path in shells])
        with tempfile.TemporaryDirectory(prefix="shpakdnd-release-") as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            archive = root / "snapshot.tar"
            git(repo, "archive", "--format=tar", f"--output={archive}", "HEAD")
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                if any(Path(m.name).name in {".env", "shpakdnd.db", "timers.db"} or m.name.endswith((".sqlite", ".sqlite3", ".db")) for m in members):
                    raise RuntimeError("snapshot must not contain tracked credentials or databases")
                tar.extractall(checkout, filter="data")
            network = root / "network-guard"
            network.mkdir()
            (network / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
            env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(network) + os.pathsep + str(checkout))
            # BOT_TOKEN is supplied only by the CI job (or the local caller).
            if env.get("BOT_TOKEN") != "ci-test-token":
                raise RuntimeError("release checks require the dummy BOT_TOKEN=ci-test-token")
            step("compileall", [[sys.executable, "-m", "compileall", "-q", "bot.py", "app"]], checkout, env)
            # Refuse unsafe configuration before check_bot can create any database.
            step("check_bot (disposable DB)", [[sys.executable, "-c", DB_CHECK],
                                               [sys.executable, "check_bot.py"]], checkout, env)
            step("hero abilities", [[sys.executable, "-m", "app.mini.combat.hero_abilities.validate"]], checkout, env)
            step("boss abilities", [[sys.executable, "-m", "app.mini.boss.boss_abilities.validate"]], checkout, env)
            step("tower", [[sys.executable, "-m", "app.mini.tower.validate"]], checkout, env)
            step("JSON and content validators", [[sys.executable, "-c", CONTENT_CHECK]], checkout, env)
            test_env = dict(env, RELEASE_TEST_ISOLATED_TOPICS="1")
            step("full unittest", [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]], checkout, test_env)
        return 0
    except (RuntimeError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        report["error"] = str(error)
        save()
        return 1


def summary(report_path: Path, output: Path):
    report = json.loads(report_path.read_text()) if report_path.exists() else {
        "baseline": BASELINE_SHA, "head": os.environ.get("GITHUB_SHA", "unknown"),
        "checks": {name: "NOT RUN" for name in CHECK_NAMES}, "tests": None}
    lines = ["## Release checks", "", f"Baseline: `{report['baseline']}`", f"HEAD: `{report['head']}`", "",
             "| Check | Result |", "| --- | --- |"]
    for key in ("checkout", "python", "dependencies"):
        outcome = os.environ.get(f"{key.upper()}_OUTCOME", "not run")
        lines.append(f"| {key} | {outcome} |")
    lines.extend(f"| {name} | {status} |" for name, status in report["checks"].items())
    tests = report.get("tests")
    lines.extend(["", "Tests: " + (json.dumps(tests, sort_keys=True) if tests else "NOT RUN (count unknown)")])
    if "error" in report:
        lines.extend(["", "Error: " + report["error"]])
    with output.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if args.summary:
        summary(args.report, args.summary)
        return 0
    return run_checks(Path(__file__).resolve().parents[1], args.report.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
