"""Private TEST evidence wrapper; never invokes the previous option-B migration."""
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from app.dnd_shop import coin_migration as cm
from app.dnd_shop import test_migration as guard
from app.dnd_shop.builder import read_master,REFERENCE_SHA256
from app.dnd_shop.service import Shop
from app.dnd_shop.catalog import CatalogError,bounded_bytes

BASE='db0d5f5aa13554e15bf5cf1ffa89b13df8a6e0f8'


def prepare(backup,metadata,runtime,loot,*,fresh=True):
    backup,metadata,runtime=map(Path,(backup,metadata,runtime))
    if backup.stat().st_size>64*1024*1024 or metadata.stat().st_size>524288:
        raise CatalogError('Превышен размер проверенного TEST snapshot.')
    meta=json.loads(metadata.read_text(encoding='utf-8'))
    hashes={str(p):cm.digest(p.read_bytes()) for p in (backup,metadata,runtime)}
    if (meta['hostname']!=guard.HOST or meta['git_sha']!=BASE or tuple(meta['scope'])!=guard.SCOPE
            or meta['db_path']!=guard.DB.as_posix() or hashes[str(backup)]!=meta['sha256']
            or hashes[str(runtime)]!=meta['runtime_sha256']
            or cm.digest(meta['topics_source'].encode())!=meta['topics_sha256']
            or read_master(loot).digest!=REFERENCE_SHA256 or meta['master_sha256']!=REFERENCE_SHA256):
        raise CatalogError('Происхождение TEST snapshot/каталогов не подтверждено.')
    if fresh:
        guard.fresh(meta)
    topics=guard.literal_topics(meta['topics_source'])
    shop=Shop(backup,runtime,topics,loot_path=loot)
    with closing(sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(':memory:')) as conn:
        source.execute('PRAGMA query_only=ON')
        source.backup(conn)
        conn.row_factory=sqlite3.Row
        plan=cm.prepare(shop,conn,guard.SCOPE)
    plan.update(target_sha=guard.revision(),topics_sha256=meta['topics_sha256'],backup_sha256=meta['sha256'],
                metadata_sha256=hashes[str(metadata)],captured_utc=meta['captured_utc'])
    if any(cm.digest(Path(p).read_bytes())!=value for p,value in hashes.items()):
        raise CatalogError('Snapshot изменился во время dry-run.')
    return plan,topics


def apply(backup,metadata,runtime,plan,approved_sha):
    if cm.digest(cm.canonical(plan).encode())!=approved_sha:
        raise CatalogError('Нужен точный явно утверждённый SHA плана.')
    checked,_=prepare(backup,metadata,runtime,guard.LOOT,fresh=False)
    if checked!=plan:
        raise CatalogError('План не соответствует snapshot и окончательному коду.')
    shop=Shop(guard.DB,guard.RUNTIME,loot_path=guard.LOOT)
    meta=json.loads(Path(metadata).read_text(encoding='utf-8'))
    guard.require_target(shop,meta,plan)
    try:
        subprocess.run(['git','-c','safe.directory='+str(guard.ROOT),'diff','--exit-code','HEAD','--',
                        'tools/normalize_dnd_coins_test.py'],cwd=guard.ROOT,check=True,
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        raise CatalogError('Непроверенные изменения CLI нормализации; apply запрещён.') from error
    with closing(sqlite3.connect(shop.db_path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        table=conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",(cm.TABLE,)).fetchone()
        existing=conn.execute('SELECT plan_sha256 FROM dnd_coin_migrations WHERE id=?',(plan['id'],)).fetchone() if table else None
    if not existing or existing[0]!=approved_sha:
        guard.fresh(meta)
    return cm.apply(shop,plan)
