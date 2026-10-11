#!/usr/bin/env python3
"""Read-only SQLite backup, additive migration on the copy, old-row proof.

Never points application DB_PATH at the source. Does not use BOT_TOKEN or send
Telegram messages. Result contains counts/hashes, never inventory content.
"""
import argparse
import hashlib
import json
import os
import pickle
import sqlite3
import sys
import tempfile
from unittest.mock import patch
from contextlib import closing
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['BOT_TOKEN']='123456:offline-migration-verification'


def quote(value):return '"'+value.replace('"','""')+'"'


def snapshot(conn,columns=None):
    if columns is None:
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        columns={t:[r[1] for r in conn.execute(f'PRAGMA table_info({quote(t)})')] for t in tables}
    result={}
    for table,names in columns.items():
        fields=','.join(map(quote,names))
        rows=conn.execute(f'SELECT {fields} FROM {quote(table)} ORDER BY {fields}').fetchall()
        result[table]={'rows':len(rows),'sha256':hashlib.sha256(pickle.dumps(rows,protocol=5)).hexdigest()}
    return columns,result


def verify(source,copy_dir=None):
    from app.db import schema
    from app.mini.schema import init_mini_db
    from app.mini.boss.schema import init_boss_db
    source=Path(source).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='v142-migration-',dir=copy_dir) as folder:
        target=Path(folder)/'copy.db'
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(target)) as copy:
            original.backup(copy)
        with closing(sqlite3.connect(target)) as conn:columns,before=snapshot(conn)
        schema.DB_PATH=target
        for _ in range(3):
            schema.init_db();init_mini_db(target)
            # Startup historically also syncs catalog definitions. Verify DDL
            # separately so intentional definition updates are not mistaken
            # for a migration changing user-owned state.
            with patch('app.mini.boss.schema.sync_boss_reward_items',return_value=0):
                init_boss_db(target)
        with closing(sqlite3.connect(target)) as conn:
            _,after=snapshot(conn,columns)
            if before!=after:
                changed=[table for table in before if before[table]!=after[table]]
                raise ValueError('Old data changed: '+', '.join(changed))
            integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys=conn.execute('PRAGMA foreign_key_check').fetchall()
            if integrity!='ok' or foreign_keys:raise ValueError('SQLite integrity/foreign keys failed.')
            item_ids=dict(conn.execute('SELECT code,id FROM mini_items'))
            from app.mini.item_registry import sync_in_transaction
            conn.execute('BEGIN IMMEDIATE');sync_in_transaction(conn);conn.commit()
            _,synced=snapshot(conn,{k:v for k,v in columns.items() if k!='mini_items'})
            if synced!={k:v for k,v in before.items() if k!='mini_items'}:
                raise ValueError('Catalog sync changed user data.')
            new_ids=dict(conn.execute('SELECT code,id FROM mini_items'))
            if any(new_ids.get(code)!=value for code,value in item_ids.items()):
                raise ValueError('Catalog sync changed existing item IDs.')
            integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys=conn.execute('PRAGMA foreign_key_check').fetchall()
            if integrity!='ok' or foreign_keys:raise ValueError('Synced SQLite integrity/foreign keys failed.')
        return {'source_opened_read_only':True,'passes':3,'old_tables':len(before),
                'old_rows':sum(v['rows'] for v in before.values()),'old_data_preserved':True,
                'catalog_sync_preserves_user_data_and_item_ids':True,
                'integrity_check':integrity,'foreign_key_violations':len(foreign_keys)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--copy-dir')
    args=parser.parse_args();print(json.dumps(verify(args.source,args.copy_dir),ensure_ascii=False,indent=2))
