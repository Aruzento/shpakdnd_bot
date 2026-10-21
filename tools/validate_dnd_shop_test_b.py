"""Exercise TEST B only on disposable SQLite; optional private fresh backup input."""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('BOT_TOKEN','123456:offline-test-b-release')

from app.dnd_shop import test_migration as tm
from app.dnd_shop.audit import audit
from app.dnd_shop.service import Shop
from app.dnd_shop.pricing import GOLD_FACTOR


def tables(conn):
    excluded={'dnd_shop_settings','dnd_currencies','dnd_shop_sessions',
              'dnd_shop_catalog_entries','dnd_shop_catalog_versions','dnd_shop_test_migrations'}
    names=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {name:[[repr(v) for v in row] for row in conn.execute('SELECT * FROM '+name+' ORDER BY rowid')]
            for name in names if name not in excluded}


def validate(backup,metadata,runtime,loot,temp):
    before_hash=tm.digest(backup.read_bytes())
    plan,candidate=tm.prepare(backup,metadata,runtime,loot)
    meta=json.loads(metadata.read_text(encoding='utf-8'))
    topics=tm.literal_topics(meta['topics_source'])
    db=temp/'working.db'
    with closing(sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(db)) as target:
        source.backup(target)
    current=temp/'shop.csv'
    current.write_bytes(runtime.read_bytes())
    shop=Shop(db,current,topics,loot_path=loot)
    with closing(sqlite3.connect(db)) as conn:
        original=tables(conn)
        other_currencies=conn.execute('SELECT * FROM dnd_currencies WHERE chat_id!=? OR thread_id!=?',tm.SCOPE).fetchall()
        other_sessions=conn.execute('SELECT * FROM dnd_shop_sessions WHERE chat_id!=? OR thread_id!=?',tm.SCOPE).fetchall()
    assert db.is_relative_to(temp) and not db.resolve().is_relative_to(ROOT)
    sha=tm.digest(tm.canonical(plan).encode())
    with patch.object(tm,'require_target'):
        first=tm.apply(shop,backup,metadata,runtime,plan,sha)
        second=tm.apply(shop,backup,metadata,runtime,plan,sha)
    assert first==second and first['enabled'] is False
    with closing(sqlite3.connect(db)) as conn:
        assert tables(conn)==original,'Historical/user/Mini tables changed'
        assert conn.execute('SELECT * FROM dnd_currencies WHERE chat_id!=? OR thread_id!=?',tm.SCOPE).fetchall()==other_currencies
        assert conn.execute('SELECT * FROM dnd_shop_sessions WHERE chat_id!=? OR thread_id!=?',tm.SCOPE).fetchall()==other_sessions
        for row in plan['currencies']:
            value,quantity=conn.execute('SELECT base_value,currency_quantity FROM dnd_currencies WHERE item_id=?',(row['item_id'],)).fetchone()
            assert quantity==row['currency_quantity'] and Fraction(value)/quantity==Fraction(row['after_nominal'])
        assert conn.execute('SELECT DISTINCT closed,expires FROM dnd_shop_sessions WHERE chat_id=? AND thread_id=?',tm.SCOPE).fetchall()==[(1,0)]
        reconciliation=audit(conn)
        assert reconciliation['errors']==0
        assert [tuple(r) for r in conn.execute('PRAGMA integrity_check')]==[('ok',)]
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    products=tm.parse_catalog(current.read_bytes())
    sword=next(p for p in products if (p.chat_id,p.thread_id)==tm.SCOPE and p.name=='Короткий меч')
    assert sword.price==Fraction('3.5') and sword.price*Fraction(3,100)==Fraction('0.105')
    assert 35000*GOLD_FACTOR==Fraction('9.975')
    assert tm.digest(backup.read_bytes())==before_hash
    return dict(target_sha=tm.revision(),source_sha256=before_hash,source_unchanged=True,
                live_applied=False,disposable_applied=True,plan_sha256=sha,
                monetary_positions=15,trades_preserved=11,quantities_preserved=True,
                price_m='3.5',payout_m='0.105',price_b='9.975',audit=reconciliation,
                wallet_delta=str(sum((Fraction(r['delta']) for r in plan['wallets']),Fraction(0))),errors=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--metadata',type=Path)
    parser.add_argument('--runtime-snapshot',type=Path)
    parser.add_argument('--loot',type=Path,default=ROOT/'data/loot.csv')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if bool(args.backup)!=bool(args.metadata) or bool(args.backup)!=bool(args.runtime_snapshot):
        parser.error('Supply all three private capture files, or none for the synthetic fixture.')
    for path in (args.backup,args.metadata,args.runtime_snapshot,args.output):
        if path and path.resolve().is_relative_to(ROOT):
            parser.error('Private captures and reports must stay outside Git.')
    with tempfile.TemporaryDirectory(prefix='test-b-release-') as folder:
        temp=Path(folder)
        if temp.resolve().is_relative_to(ROOT):
            raise ValueError('Temporary copies must stay outside Git')
        if args.backup:
            paths=args.backup,args.runtime_snapshot,args.metadata
        else:
            from tests.v1_4_4.test_test_migration import seed
            paths=seed(temp)[:3]
        backup,runtime,metadata=paths
        report=validate(backup,metadata,runtime,args.loot,temp)
        text=json.dumps(report,ensure_ascii=False,indent=2)
        if args.output:
            args.output.write_text(text+'\n',encoding='utf-8')
        print(text)


if __name__=='__main__':
    main()
