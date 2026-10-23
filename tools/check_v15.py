"""Offline V1.5 validators. Invoke through scripts/run_isolated_checks.py."""

import json
import os
from pathlib import Path
import subprocess
import sys

COMMANDS = [
    [
        "-m",
        "compileall",
        "-q",
        "bot.py",
        "check_bot.py",
        "app",
        "tests/v1_5",
        "tools/validate_v15_migration.py",
    ],
    ["check_bot.py"],
    ["tools/validate_deploy_bundle.py"],
    ["-m", "app.mini.combat.hero_abilities.validate"],
    ["-m", "app.mini.boss.boss_abilities.validate"],
    ["-m", "app.mini.tower.validate"],
    ["-m", "app.mini.validate_v142"],
    ["-m", "app.mini.editorial.validator"],
    ["-m", "app.mini.mythic.content"],
    ["tools/validate_editorial_release.py"],
    ["tools/validate_pvp_migration.py"],
]


def main():
    if not os.getenv("DND_CHECK_SANDBOX"):
        raise SystemExit("Use scripts/run_isolated_checks.py for SQLite isolation")
    results = []
    for args in COMMANDS:
        proc = subprocess.run(
            [sys.executable, *args],
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        results.append(
            {
                "command": [sys.executable, *args],
                "exit_code": proc.returncode,
                "output": proc.stdout,
            }
        )
        print("PASS" if proc.returncode == 0 else "FAIL", " ".join(args), flush=True)
        if proc.returncode:
            print(proc.stdout[-6000:], flush=True)
    output = Path(os.environ["DND_CHECK_SANDBOX"]) / "validators.json"
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Evidence:", output)
    return int(any(r["exit_code"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
