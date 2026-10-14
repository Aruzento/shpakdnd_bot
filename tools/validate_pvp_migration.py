"""Populated old-release migration and old-code startup on disposable copies."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PRE_EDITORIAL="5ab332e28ebef415a3b0757787109a8d60acd2a8"
EDITORIAL="de3ce625a779a8295b82a08f7a4844d290362e5d"
sys.path.insert(0,str(ROOT))

def snapshot(path):
    with closing(sqlite3.connect(path)) as conn:
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t:conn.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall() for t in tables}

def integrity(path):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()

def run(code,path,script):
    env=dict(os.environ,BOT_TOKEN="123456:offline-pvp-migration",PYTHONIOENCODING="utf-8")
    env.pop("MINI_EDITORIAL_DIR",None)
    # These subprocesses import only the archived code; no inherited startup hook.
    env.pop("PYTHONPATH",None)
    prelude="import sys;from pathlib import Path;import app.config as config;config.DB_PATH=Path(sys.argv[1]);config.LEGACY_DB_PATH=config.DB_PATH.with_name('unused-legacy.db');"
    result=subprocess.run([sys.executable,"-c",prelude+script,str(path)],cwd=code,env=env,capture_output=True,text=True,encoding="utf-8")
    assert result.returncode==0,result.stdout+result.stderr

def main():
    from app.mini.schema import init_mini_db
    from app.mini.events import pvp
    result=[]
    with tempfile.TemporaryDirectory(prefix="mini-pvp-migration-") as folder:
        base=Path(folder)
        for ref in (PRE_EDITORIAL,EDITORIAL):
            code=base/ref;code.mkdir();archive=base/(ref+".zip")
            subprocess.run(["git","archive","--format=zip","--output="+str(archive),ref,"app","check_bot.py"],cwd=ROOT,check=True)
            with zipfile.ZipFile(archive) as z:z.extractall(code)
            source=base/(ref+".db")
            seed="""import check_bot
check_bot.main()
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.players import create_mini_player
from app.mini.wallet import add_coins
from app.mini.events.service import start_session,choose_direction
from app.mini.duels.service import challenge
from app.mini.db import connect_mini_db
from unittest.mock import patch
world=sync_configured_mini_worlds()[0]
players=[create_mini_player(world['id'],910001+i,'','Fixture '+str(i)) for i in range(4)]
for player in players:add_coins(player['id'],100,'Fixture')
with patch('app.mini.events.service.secrets.choice',return_value='left'):
    solo=start_session(players[0]['id'],world['id'],'labyrinth',5,'legacy-event')
choose_direction(players[0]['id'],world['id'],solo['id'],0,'left')
with connect_mini_db() as conn:
    hid=conn.execute("SELECT id FROM mini_heroes WHERE code='Villager'").fetchone()[0]
    conn.execute('INSERT INTO mini_player_heroes(player_id,hero_id,stars) VALUES(?,?,2)',(players[1]['id'],hid))
    conn.execute('UPDATE mini_players SET active_hero_id=? WHERE id=?',(hid,players[1]['id']))
challenge(players[1]['id'],players[2]['id'],hid,'legacy-duel')
"""
            run(code,source,seed)
            before=snapshot(source)
            copy=base/(ref+"-migrated.db");shutil.copy2(source,copy)
            for _ in range(3):init_mini_db(copy)
            after=snapshot(copy)
            assert all(after[t]==rows for t,rows in before.items()),"Historical data changed"
            assert "mini_event_pvp" in after and "mini_event_pvp_members" in after
            assert not after["mini_event_pvp"]
            integrity(copy)
            with closing(sqlite3.connect(copy)) as conn:
                players=conn.execute("SELECT id,world_id FROM mini_players ORDER BY id").fetchall()
            # Seed a new active escrow, copy it, then run the prior check_bot.
            sid=pvp.create(players[3][0],players[3][1],"migration-pvp",copy)["id"]
            rollback=base/(ref+"-rollback.db");shutil.copy2(copy,rollback)
            rollback_before=snapshot(rollback)
            run(code,rollback,"import check_bot;check_bot.main()")
            assert snapshot(rollback)==rollback_before,"Old startup changed new user data"
            integrity(rollback)
            # Old code does not process new escrow deadlines. Data compatibility
            # alone is not a safe operational rollback: require a drain first.
            assert snapshot(rollback)["mini_event_pvp"][0][0]==sid
            pvp.expire(copy,now=pvp.timestamp()+pvp.ACCEPT_SECONDS+1)
            assert pvp.get_session(players[3][1],sid,copy)["status"]=="expired"
            integrity(copy)
            result.append(dict(source_ref=ref,old_tables_retained=len(before),repeat_init=3,
                solo_preserved=True,duel_preserved=True,integrity="ok",foreign_keys=0,
                old_code_startup=0,old_code_preserved_new_tables=True,
                automatic_rollback_allowed=False,reason="Old code cannot expire active PvP escrow; drain/refund before rollback"))
        fresh=base/"fresh.db"
        for _ in range(3):init_mini_db(fresh)
        integrity(fresh)
    print(json.dumps(dict(fresh_install="ok",results=result),ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
