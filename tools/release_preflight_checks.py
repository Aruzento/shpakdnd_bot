"""All expensive release gates, called only inside the exact-SHA offline sandbox."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-stories-dir", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    if not os.getenv("DND_CHECK_SANDBOX"):
        parser.error("isolated environment required")
    sandbox = Path(os.environ["DND_CHECK_SANDBOX"])
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if args.sha != actual:
        raise ValueError("BLOCKED_PREFLIGHT_SHA")
    # Target content goes first: a missing production Story must not cost a full
    # test run, and the CI-only unpublished candidate is never a substitute.
    commands = [
        ["tools/validate_release_content.py", "--directory", args.target_stories_dir, "--output", str(sandbox/"target-content.json")],
        ["-m", "unittest", "discover", "-s", "tests", "-q"],
        ["tools/check_v15.py"],
        ["tools/validate_story_candidate.py"],
        ["tools/validate_release_hardening.py"],
        ["tools/validate_v15_migration.py", "--output", str(sandbox/"v15-migration.json")],
        ["tools/validate_dnd_shop_release.py"],
        ["tools/validate_dnd_shop_test_b.py"],
        ["tools/validate_dnd_coins_release.py"],
        ["tools/validate_dnd_loot_cli.py"],
        ["tools/benchmark_dnd_shop.py", "--output", str(sandbox/"shop-benchmark.json")],
        ["-m", "unittest", "tests.v1_4_3_1.test_deploy_confirm", "-q"],
        ["-m", "unittest", "tests.v1_5.test_release_hardening", "-q"],
    ]
    checks = []
    for number, command in enumerate(commands):
        began = time.monotonic()
        log = sandbox / ("gate-" + str(number) + ".log")
        env = dict(os.environ)
        # Only the explicit content gate reads the target. Unit tests/default
        # diagnostics use their empty sandbox Stories, not the release directory.
        with log.open("w", encoding="utf-8") as stream:
            proc = subprocess.run([sys.executable, *command], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        checks.append(dict(command=command, exit_code=proc.returncode, seconds=round(time.monotonic()-began, 3), log=str(log)))
        report = dict(target_sha=actual, python=sys.version, sandbox=str(sandbox), checks=checks)
        (sandbox/"release-gates.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(checks[-1]), flush=True)
        if proc.returncode:
            print(log.read_text(encoding="utf-8")[-8000:])
            return proc.returncode
    for command in (["git", "diff", "--check"], ["git", "diff", "--check", "origin/main...HEAD"]):
        subprocess.run(command, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
