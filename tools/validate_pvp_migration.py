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
PRE_SAFETY="677398665f302127db84dad3d9d010de4bbf1582"
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
        for ref in (PRE_EDITORIAL,EDITORIAL,PRE_SAFETY):
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
            if ref==PRE_SAFETY:
                seed+="""
from app.mini.events import pvp
players_pvp=[create_mini_player(world['id'],920001+i,'','Safety '+str(i)) for i in range(18)]
for player in players_pvp:add_coins(player['id'],100,'Fixture')
def game(i,kind):
    first,second=players_pvp[i*2]['id'],players_pvp[i*2+1]['id']
    row=pvp.create(first,world['id'],'legacy-pvp-'+kind,now=1000)
    sid=row['id']
    if kind in ('choosing','win','draw'):
        pvp.accept(second,world['id'],sid,now=1001)
    if kind in ('win','draw'):
        pvp.choose(first,world['id'],sid,'saint',now=1002)
        pvp.choose(second,world['id'],sid,'demon' if kind=='win' else 'saint',now=1003)
    elif kind in ('refund','uncertain','edit_failed','inflight_edit','known'):
        pvp.cancel(first,world['id'],sid,now=1004)
    with connect_mini_db() as conn:
        if kind=='uncertain':
            conn.execute("UPDATE mini_event_pvp SET publication='uncertain',last_error='https://secret-token/private' WHERE id=?",(sid,))
        elif kind in ('choosing','win','draw','edit_failed','inflight_edit','known'):
            conn.execute("UPDATE mini_event_pvp SET publication='sent',message_id=?,published_revision=revision WHERE id=?",(100+sid,sid))
            if kind in ('edit_failed','inflight_edit'):
                conn.execute("UPDATE mini_event_pvp SET published_revision=0,last_error='private old error',editing_until=? WHERE id=?",(int(__import__('time').time())+100 if kind=='inflight_edit' else 0,sid))
for i,kind in enumerate(('open','choosing','win','draw','refund','uncertain','edit_failed','inflight_edit','known')):
    game(i,kind)
"""
            run(code,source,seed)
            before=snapshot(source)
            copy=base/(ref+"-migrated.db");shutil.copy2(source,copy)
            init_mini_db(copy)
            migrated_once=snapshot(copy)
            for _ in range(2):init_mini_db(copy)
            after=snapshot(copy)
            assert after==migrated_once,"Repeated safety init reset persisted state"
            for table,rows in before.items():
                if table=="mini_event_pvp":
                    with closing(sqlite3.connect(source)) as conn:
                        columns=[r[1] for r in conn.execute("PRAGMA table_info(mini_event_pvp)")]
                    allowed={columns.index("last_error"),columns.index("editing_until")}
                    assert len(after[table])==len(rows)
                    assert all(all(new[i]==old[i] for i in range(len(old)) if i not in allowed)
                               for old,new in zip(rows,after[table])),"Historical PvP economics changed"
                else:
                    with closing(sqlite3.connect(source)) as old_conn:
                        historical_columns=[r[1] for r in old_conn.execute(f'PRAGMA table_info("{table}")')]
                    projection=','.join('"'+c+'"' for c in historical_columns)
                    with closing(sqlite3.connect(copy)) as migrated_conn:
                        projected=migrated_conn.execute(f'SELECT {projection} FROM "{table}" ORDER BY rowid').fetchall()
                    assert projected==rows,"Historical data changed: "+table
            assert "mini_event_pvp" in after and "mini_event_pvp_members" in after
            if ref!=PRE_SAFETY:assert not after["mini_event_pvp"]
            else:
                with closing(sqlite3.connect(copy)) as conn:
                    conn.row_factory=sqlite3.Row
                    safety=[dict(r) for r in conn.execute("SELECT * FROM mini_event_pvp")]
                    assert len(safety)==9
                    assert {r["status"] for r in safety}>={"open","choosing","finished","cancelled"}
                    assert {r["publication"] for r in safety}>={"pending","sent","uncertain"}
                    retry=[r for r in safety if r["edit_status"]=="retrying"]
                    assert len(retry)==2 and all(r["retry_count"]==1 and r["next_retry_at"]>0 and r["editing_until"]==0 for r in retry)
                    old_lease={r[0]:r[columns.index("editing_until")] for r in before["mini_event_pvp"]}
                    assert all(r["next_retry_at"]>=old_lease[r["id"]] for r in retry)
                    assert conn.execute("SELECT paused FROM mini_event_pvp_control").fetchone()[0]==0
                    assert not conn.execute("SELECT * FROM mini_event_pvp_shard_awards").fetchall()
                    assert all("private" not in r["last_error"] and "secret" not in r["last_error"] for r in safety)
                from deploy.pvp_ops import inspect
                report=inspect(copy)
                assert any("legacy_shards_unverified" in r["errors"] for r in report["sessions"])
                assert not report["rollback_ready"]
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
            assert any(r[0]==sid for r in snapshot(rollback)["mini_event_pvp"])
            pvp.expire(copy,now=pvp.timestamp()+pvp.ACCEPT_SECONDS+1)
            assert pvp.get_session(players[3][1],sid,copy)["status"]=="expired"
            integrity(copy)
            result.append(dict(source_ref=ref,old_tables_retained=len(before),repeat_init=3,
                solo_preserved=True,duel_preserved=True,populated_pvp_states=9 if ref==PRE_SAFETY else 0,
                retry_migration_idempotent=True,normal_mode_default=True,integrity="ok",foreign_keys=0,
                old_code_startup=0,old_code_preserved_new_tables=True,
                automatic_rollback_allowed=False,reason="Old code cannot expire active PvP escrow; drain/refund before rollback"))
        fresh=base/"fresh.db"
        for _ in range(3):init_mini_db(fresh)
        integrity(fresh)
    print(json.dumps(dict(fresh_install="ok",results=result),ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
