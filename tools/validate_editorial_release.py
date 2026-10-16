"""Run check_bot only in a disposable checkout with its own SQLite and no .env."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    count = 0
    for path in (ROOT / "app").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8-sig"))
        count += 1
    print(f"OK: JSON catalogs={count}", flush=True)
    env = dict(os.environ, BOT_TOKEN="test-token", PYTHONIOENCODING="utf-8", MINI_CUSTOM_EMOJI_ENABLED="false")
    env.pop("MINI_EDITORIAL_DIR", None)
    with tempfile.TemporaryDirectory(prefix="mini-editorial-check-") as directory:
        target = Path(directory)
        shutil.copytree(ROOT / "app", target / "app", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "check_bot.py", target / "check_bot.py")
        result = subprocess.run([sys.executable, "check_bot.py"], cwd=target, env=env)
        if result.returncode:
            return result.returncode
        database = target / "shpakdnd.db"
        with closing(sqlite3.connect(database)) as conn:
            before = {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                      for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            schema_before = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        result = subprocess.run([sys.executable, "check_bot.py"], cwd=target, env=env, capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            print(result.stdout, result.stderr)
            return result.returncode
        with closing(sqlite3.connect(database)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            after = {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in before}
            schema_after = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        assert integrity == "ok" and not foreign_keys
        assert before == after and schema_before == schema_after
        print("OK: existing disposable SQLite integrity=ok, foreign_keys=0; schema/row counts retained")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
