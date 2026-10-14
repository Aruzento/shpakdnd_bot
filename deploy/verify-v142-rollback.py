#!/usr/bin/env python3
"""Old-code startup against an isolated, migrated SQLite copy with new UI rows.

No production commands, tokens, polling, DB replacement, or source writes.
Only explicitly supplied full Git commit SHAs are executed from temporary archives.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['BOT_TOKEN']='123456:offline-rollback-verification'


def snapshot(path):
    definitions={'mini_heroes','mini_items','mini_shop_categories','mini_shop_offers','mini_worlds'}
    with closing(sqlite3.connect(path)) as conn:
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        user_data={};definition_ids={}
        for table in tables:
            quoted='"'+table.replace('"','""')+'"'
            rows=conn.execute('SELECT * FROM '+quoted+' ORDER BY rowid').fetchall()
            if table in definitions:
                definition_ids[table]={r[0] for r in conn.execute('SELECT id FROM '+quoted)}
            else:user_data[table]=hashlib.sha256(pickle.dumps(rows,protocol=5)).hexdigest()
        return user_data,definition_ids


def seed_new_state(path):
    from app.mini.worlds import sync_configured_mini_worlds
    from app.mini.players import create_mini_player
    from app.mini.wallet import add_coins
    from app.mini.gacha import perform_gacha_batch
    from app.mini.ui.state import remember,claim_publication
    from app.mini import registration
    from app.mini.tower import service as tower
    world=sync_configured_mini_worlds(path)[0]
    with closing(sqlite3.connect(path)) as conn:
        uid=conn.execute('SELECT COALESCE(MAX(telegram_user_id),0)+1 FROM mini_players').fetchone()[0]
    player=create_mini_player(world['id'],uid,'','Offline fixture',path)
    pid=player['id'];add_coins(pid,1000,'Offline fixture',db_path=path)
    with patch('app.mini.gacha._choose_hero_code',return_value='Villager'):
        batch=perform_gacha_batch(pid,path,operation_key='offline:batch')
    remember(pid,'collection',dict(view='all',page=0,filter='0000000'),path)
    claim_publication(pid,'offline:publication',path)
    token=registration.begin(world['id'],uid+1,path)
    registration.activate(token,uid+1,path)
    registration.complete(token,uid+1,'','Private fixture',path)
    hero=batch['results'][0]['id']
    tower.select_hero(pid,hero,path,expected_floor=1)
    old=tower.start_selected_attempt(pid,1,hero,path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE mini_tower_attempts SET status='lost',shields=0 WHERE id=?",(old['id'],));conn.commit()
    tower.retry_attempt(pid,old['id'],path)


def verify(source,refs):
    source=Path(source).resolve(strict=True)
    if not refs or any(not re.fullmatch(r'[0-9a-f]{40}',ref) for ref in refs):
        raise ValueError('Supply approved full 40-character commit SHAs.')
    with tempfile.TemporaryDirectory(prefix='v142-rollback-') as folder:
        base=Path(folder);copy=base/'copy.db'
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(copy)) as target:
            original.backup(target)
        # Redirect BEFORE importing any startup modules.
        import app.config as config
        config.DB_PATH=copy
        import check_bot
        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()):check_bot.main()
        seed_new_state(copy)
        results=[]
        for ref in refs:
            archive=base/(ref+'.zip');code=base/ref;code.mkdir()
            subprocess.run(['git','archive','--format=zip','--output='+str(archive),ref,'app','check_bot.py'],check=True,cwd=ROOT)
            with zipfile.ZipFile(archive) as z:z.extractall(code)
            before,ids=snapshot(copy)
            bootstrap="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import app.config as config;config.DB_PATH=Path(sys.argv[2]);import check_bot;check_bot.main()"
            run=subprocess.run([sys.executable,'-c',bootstrap,str(code),str(copy)],cwd=code,encoding='utf-8',capture_output=True)
            after,new_ids=snapshot(copy)
            with closing(sqlite3.connect(copy)) as conn:
                integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
                foreign_keys=conn.execute('PRAGMA foreign_key_check').fetchall()
            result=dict(old_code=ref,check_bot_exit=run.returncode,new_ui_state_seeded=True,
                user_tables_unchanged=before==after,definition_ids_preserved=all(values<=new_ids.get(table,set()) for table,values in ids.items()),
                integrity=integrity,foreign_key_violations=len(foreign_keys))
            results.append(result)
            if run.returncode or before!=after or not result['definition_ids_preserved'] or integrity!='ok' or foreign_keys:
                raise ValueError(json.dumps(result)+' '+run.stderr[-1500:])
        return dict(source_opened_read_only=True,results=results)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--old-ref',action='append',required=True)
    args=parser.parse_args()
    print(json.dumps(verify(args.source,args.old_ref),ensure_ascii=False,indent=2))
