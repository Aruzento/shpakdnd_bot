"""Coin normalization on disposable SQLite only; optional private TEST capture."""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('BOT_TOKEN','123456:offline-coins-validator')
from app.dnd_shop import coin_migration as cm, coin_evidence as evidence
from app.dnd_shop.audit import audit
from app.dnd_shop.builder import encode_products,read_master
from app.dnd_shop.catalog import parse_catalog
from app.dnd_shop.control import persist_state
from app.dnd_shop.money import exact_text
from app.dnd_shop.pricing import MASTER_MONEY,preflight
from app.dnd_shop.service import Shop
from app.dnd_shop.test_migration import SCOPE,literal_topics


def seed_post_b(temp):
    """Original 4450 rows; synthetic post-B state, never invokes option-B apply."""
    from tests.v1_4_4.test_test_migration import seed
    db,runtime,metadata,topics,mapping,offers=seed(temp,credit_last=True)
    shop=Shop(db,runtime,topics,loot_path=ROOT/'data/loot.csv',map_path=mapping,offers_path=offers)
    with closing(sqlite3.connect(db)) as conn,conn:
        conn.row_factory=sqlite3.Row
        for name,(unit,count) in MASTER_MONEY.items():
            value=Fraction({'Медная монета':'0.0001','Серебряная монета':'0.01','Золотая монета':'1'}[unit])*count
            row=conn.execute('SELECT id FROM dnd_items WHERE name=?',(name,)).fetchone()
            conn.execute('UPDATE dnd_currencies SET base_value=?,currency_quantity=1 WHERE item_id=?',(exact_text(value),row[0]))
        profile=preflight(shop,conn,SCOPE,'m',read_master(shop.loot_path))
        persist_state(conn,SCOPE,profile,False)
        for name in ('50 серебряных монет','5 золотых монет','50 медных монет'):
            conn.execute("""INSERT INTO inventory(chat_id,thread_id,username,name,quantity,description,item_id)
                SELECT chat_id,thread_id,'@fixture',name,2,'synthetic legacy bundle',id FROM dnd_items WHERE name=?""",(name,))
    runtime.write_bytes(encode_products([replace(p,price=p.price/10000) for p in parse_catalog(runtime.read_bytes())]))
    with shop.transaction() as (conn,catalog):
        shop.sync(conn,catalog,force=True)
    meta=json.loads(metadata.read_text(encoding='utf-8'))
    meta.update(git_sha=evidence.BASE,sha256=cm.digest(db.read_bytes()),runtime_sha256=cm.digest(runtime.read_bytes()))
    metadata.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
    return db,metadata,runtime


def validate(backup,metadata,runtime,loot,temp):
    hashes={str(p):cm.digest(p.read_bytes()) for p in (backup,metadata,runtime)}
    plan,topics=evidence.prepare(backup,metadata,runtime,loot)
    db=temp/'coins-working.db'
    current=temp/'coins-runtime.csv'
    assert db.resolve().is_relative_to(temp.resolve()) and not db.resolve().is_relative_to(ROOT)
    with closing(sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(db)) as target:
        source.backup(target)
    current.write_bytes(runtime.read_bytes())
    shop=Shop(db,current,topics,loot_path=loot)
    with closing(sqlite3.connect(db)) as conn:
        protected=cm.snapshot(conn,protected=True)
        trades=conn.execute('SELECT count(*) FROM dnd_shop_trades').fetchone()[0]
        credit=conn.execute('SELECT * FROM dnd_shop_credit ORDER BY rowid').fetchall()
        other_settings=conn.execute('SELECT * FROM dnd_shop_settings WHERE chat_id!=? OR thread_id!=?',SCOPE).fetchall()
        other_sessions=conn.execute('SELECT * FROM dnd_shop_sessions WHERE chat_id!=? OR thread_id!=?',SCOPE).fetchall()
    first=cm.apply(shop,plan)
    assert first==cm.apply(shop,plan)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory=sqlite3.Row
        assert cm.snapshot(conn,protected=True)==protected
        assert [tuple(r) for r in conn.execute('SELECT * FROM dnd_shop_credit ORDER BY rowid')]==credit
        assert [tuple(r) for r in conn.execute('SELECT * FROM dnd_shop_settings WHERE chat_id!=? OR thread_id!=?',SCOPE)]==other_settings
        assert [tuple(r) for r in conn.execute('SELECT * FROM dnd_shop_sessions WHERE chat_id!=? OR thread_id!=?',SCOPE)]==other_sessions
        assert not conn.execute('SELECT 1 FROM dnd_shop_sessions WHERE chat_id=? AND thread_id=? AND (closed!=1 OR expires!=0)',SCOPE).fetchall()
        for w in plan['wallets']:
            assert Fraction(w['before_value'])==Fraction(w['after_value'])
        assert [tuple(r) for r in conn.execute('PRAGMA integrity_check')]==[('ok',)]
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        reconciliation=audit(conn)
    assert all(cm.digest(Path(p).read_bytes())==sha for p,sha in hashes.items())
    return dict(target_sha=plan['target_sha'],source_sha256=hashes[str(backup)],source_unchanged=True,
                live_applied=False,disposable_applied=True,owners=len(plan['wallets']),
                source_money_rows=sum(len(c['before']) for w in plan['wallets'] for c in w['changes']),
                normalized_money_rows=sum(len(w['changes']) for w in plan['wallets']),
                wallet_values_preserved=True,history_trades_preserved=trades,credit_preserved=True,
                registry_nominals_preserved=True,other_topics_preserved=True,ordinary_inventory_preserved=True,
                exact_repeat=True,shop_enabled=False,audit=reconciliation,errors=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('backup','metadata','runtime-snapshot','output'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--loot',type=Path,default=ROOT/'data/loot.csv')
    args=parser.parse_args()
    if len({bool(args.backup),bool(args.metadata),bool(args.runtime_snapshot)})!=1:
        parser.error('Supply all three private capture files, or none for the synthetic fixture.')
    for p in (args.backup,args.metadata,args.runtime_snapshot,args.output):
        if p and p.resolve().is_relative_to(ROOT):
            parser.error('Private captures and reports must remain outside Git.')
    with tempfile.TemporaryDirectory(prefix='coins-release-') as folder:
        temp=Path(folder)
        if temp.resolve().is_relative_to(ROOT):
            parser.error('Disposable copies must remain outside Git.')
        paths=(args.backup,args.metadata,args.runtime_snapshot) if args.backup else seed_post_b(temp)
        report=validate(*paths,args.loot,temp)
        text=json.dumps(report,ensure_ascii=False,indent=2)
        if args.output:
            args.output.write_text(text+'\n',encoding='utf-8')
        print(text)


if __name__=='__main__':
    main()
