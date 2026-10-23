"""Verify additive V1.5 migration/replay and old-code startup on disposable copies.

Default source is a populated synthetic fixture bootstrapped by origin/main code.
An optional --source is opened read-only through SQLite's backup API.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

BASE = "d077dd2a2c29ad420b138bf07af23c77f4674d0b"
ROOT = Path(__file__).resolve().parents[1]
BOOT = """import sys
from pathlib import Path
import app.config as config
config.DB_PATH=Path(sys.argv[1])
config.LEGACY_DB_PATH=config.DB_PATH.with_name('unused.db')
import check_bot
check_bot.main()
"""
SEED = """
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.players import create_mini_player
from app.mini.heroes import sync_hero_catalog
from app.mini.boss.service import create_boss_event,register_player,close_registration
from app.mini.boss.combat import start_battle
from app.mini.db import connect_mini_db
world=sync_configured_mini_worlds()[0]
player=create_mini_player(world['id'],900123,'migration_fixture','Synthetic')
with connect_mini_db() as conn:
    hid=conn.execute("SELECT id FROM mini_heroes WHERE code='Villager'").fetchone()[0]
    conn.execute('INSERT INTO mini_player_heroes(player_id,hero_id,stars,copies) VALUES(?,?,2,9)',(player['id'],hid))
    conn.execute('UPDATE mini_players SET coins=12345,shards=678,active_hero_id=? WHERE id=?',(hid,player['id']))
    conn.execute("INSERT INTO mini_mythic_fragments VALUES(?, 'legacy_fixture_only', 7)",(player['id'],))
boss=create_boss_event(world['id'],'simple_village_guy',900123)
register_player(boss['id'],player['id'])
# Old code requires two participants; copy a second real Mini fixture account.
second=create_mini_player(world['id'],900124,'migration_second','Synthetic second')
with connect_mini_db() as conn:
    conn.execute('INSERT INTO mini_player_heroes(player_id,hero_id) VALUES(?,?)',(second['id'],hid))
    conn.execute('UPDATE mini_players SET active_hero_id=? WHERE id=?',(hid,second['id']))
register_player(boss['id'],second['id']);close_registration(boss['id']);start_battle(boss['id'])
"""


def run(code, cwd, database, env):
    proc = subprocess.run(
        [sys.executable, "-c", code, str(database)],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode:
        raise RuntimeError(proc.stdout[-12000:])


def snapshot(db):
    with sqlite3.connect(db) as conn:
        return {
            "players": conn.execute(
                "SELECT id,coins,shards,active_hero_id FROM mini_players ORDER BY id"
            ).fetchall(),
            "heroes": conn.execute(
                "SELECT player_id,hero_id,stars,copies FROM mini_player_heroes ORDER BY player_id,hero_id"
            ).fetchall(),
            "fragments": conn.execute(
                "SELECT * FROM mini_mythic_fragments ORDER BY player_id,hero_code"
            ).fetchall(),
            "bosses": conn.execute(
                "SELECT id,status,max_hp,current_hp,reward_coins,skip_after_hours,turn_started_at FROM mini_bosses ORDER BY id"
            ).fetchall(),
            "loadouts": conn.execute(
                "SELECT boss_id,player_id,attack,hero_snapshot_json FROM mini_boss_participants ORDER BY boss_id,player_id"
            ).fetchall(),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    temp = Path(tempfile.mkdtemp(prefix="v15-migration-"))
    old = temp / "old"
    old.mkdir()
    archive = temp / "code.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            "--output=" + str(archive),
            BASE,
            "app",
            "check_bot.py",
        ],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(archive) as source:
        source.extractall(old)
    env = dict(
        os.environ,
        BOT_TOKEN="123456:offline-migration",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    db = temp / "migration.db"
    if args.source:
        with sqlite3.connect(
            args.source.resolve().as_uri() + "?mode=ro", uri=True
        ) as src, sqlite3.connect(db) as dst:
            src.backup(dst)
    else:
        run(BOOT + SEED, old, db, env)
    before = snapshot(db)
    schemas = []
    for _ in range(3):
        run(BOOT, ROOT, db, env)
        if snapshot(db) != before:
            raise AssertionError("Migration mutated historical account/battle data")
        with sqlite3.connect(db) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            schemas.append(
                conn.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
            )
    assert schemas[0] == schemas[1] == schemas[2]
    rollback = temp / "rollback.db"
    with sqlite3.connect(db) as src, sqlite3.connect(rollback) as dst:
        src.backup(dst)
    run(BOOT, old, rollback, env)
    assert snapshot(rollback) == before
    report = {
        "base": BASE,
        "source": (
            "read-only supplied copy"
            if args.source
            else "populated synthetic fixture, NOT production"
        ),
        "migration_replays": 3,
        "integrity_check": "ok",
        "foreign_key_check": [],
        "legacy_fragments_preserved": before["fragments"],
        "old_code_startup": "PASS on separate copy",
        "sandbox": str(temp),
        "production_copy": "NOT RUN unless --source supplied",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
