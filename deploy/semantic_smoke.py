"""Isolated import/router/read-only DB smoke. Never evidence of real Telegram."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from deploy_helpers import DeployError, backup_database
from preflight_data import inventory_database, compare_copy, schema_digest
import release_state as store

ROUTERS = {'common','mini_titles','mini_boss','mini','mini_events','create_character','mini_superadmin','characters','inventory','admin','timers'}
PROBE = r"""
import json,sqlite3,socket
from pathlib import Path
original=sqlite3.connect
def readonly(database,*args,**kwargs):
    name=Path(database).resolve()
    if name!=Path.cwd()/'shpakdnd.db':raise RuntimeError('Smoke only permits isolated SQLite')
    kwargs['uri']=True
    conn=original(name.as_uri()+'?mode=ro',*args,**kwargs)
    conn.execute('PRAGMA query_only=ON')
    return conn
sqlite3.connect=readonly
def blocked(*args,**kwargs):raise RuntimeError('Polling/network forbidden in automatic smoke')
socket.socket.connect=blocked;socket.socket.connect_ex=blocked
from aiogram import Dispatcher
Dispatcher.start_polling=blocked
import bot
from app.config import DB_PATH
assert DB_PATH.resolve()==Path.cwd()/'shpakdnd.db'
from app.handlers import ROUTERS
names={r.name for r in ROUTERS}
assert len(names)==len(ROUTERS)
expected=set(json.loads(__import__('sys').argv[1]))
assert names==expected,(names,expected)
dp=Dispatcher()
for router in ROUTERS:
    assert router.message.handlers or router.callback_query.handlers,router.name
    dp.include_router(router)
from app.mini.events.handlers import _screen
player={'coins':100,'shards':10}
for screen,fragment in [('events','rps'),('events','lab')]:
    text,keyboard=_screen(1,2,player,screen)
    assert any(fragment in (b.callback_data or '') for row in keyboard.inline_keyboard for b in row)
from app.mini.catalog import validate_content
from app.mini.content_safety import validate_combat_content
from app.mini.boss.catalog import load_boss_catalog,load_boss_item_catalog
from app.mini.boss.boss_abilities.catalog import load_ability_catalog
from app.mini.combat.hero_abilities.validate import missing_hero_passives
assert not missing_hero_passives()
from app.mini.tower.catalog import load_catalog
from app.mini.equipment.catalog import load_catalog as equipment_catalog
from app.mini.village.balance import load_balance
validate_content();validate_combat_content();load_boss_catalog();load_boss_item_catalog();load_ability_catalog()
load_catalog();equipment_catalog();load_balance()
from app.mini.schema import MINI_TABLES
from app.topics import TOPIC_SETTINGS
with sqlite3.connect(DB_PATH) as conn:
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(MINI_TABLES)<=tables,'Missing required game tables'
    worlds={(r[0],r[1]) for r in conn.execute('SELECT chat_id,thread_id FROM mini_worlds WHERE enabled=1')}
    configured={(chat,thread) for chat,topics in TOPIC_SETTINGS.items() for thread,settings in topics.items() if settings.get('mini')}
    assert configured and configured<=worlds,'World/topic configuration incompatible'
    for table in MINI_TABLES:conn.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchall()
print(json.dumps({'status':'PASS','routers':sorted(names),'events_games':['rps','lab'],'tables_read':len(MINI_TABLES),'world_topics_compatible':True,'catalogs':'PASS','polling':False,'external_api':False}))
"""


def run(project, sha, db, output,*,staging=None):
    import release_preflight as pref
    project=Path(project).resolve();db=Path(db);output=Path(output)
    if db.is_symlink():raise DeployError('Smoke source must not be symlink')
    db=db.resolve()
    store.full_sha(sha)
    if store.command(['git','-C',project,'rev-parse','HEAD'])!=sha or store.command(['git','-C',project,'status','--porcelain','--untracked-files=all']):
        raise DeployError('Smoke requires exact clean checkout SHA')
    store.outside_project(output.parent,project)
    if not db.is_file() or db.is_symlink():raise DeployError('Smoke requires actual regular SQLite')
    started=store.utc_now()
    report=dict(version=1,kind='AUTOMATIC_READONLY_SMOKE',sha=sha,status='UNKNOWN',started_at=started,real_telegram=False)
    store.save_record(output,report)
    try:
        archive=subprocess.run(['git','-c','safe.directory='+str(project),'-C',str(project),'archive',sha],check=True,capture_output=True).stdout
        with tempfile.TemporaryDirectory(prefix='shpakdnd-smoke-') as temporary:
            checkout=Path(temporary)/'checkout';checkout.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                for member in tar.getmembers():
                    if member.name.split('/')[0] in {'.env','.venv','shpakdnd.db','timers.db'} or member.issym() or member.islnk():
                        raise DeployError('Unsafe smoke archive member')
                tar.extractall(checkout,filter='data')
            copied=checkout/'shpakdnd.db';backup_database(db,copied)
            before=inventory_database(copied,strict=True)
            (checkout/'.env').write_text('BOT_TOKEN=123456:disposable-smoke-token\n',encoding='utf-8')
            env=pref.safe_environment(Path(temporary));env['PYTHONPATH']=str(checkout)
            probe=PROBE
            if staging:
                import staging_topics
                settings=staging_topics.configuration(staging['topics_path'])
                env['SHPAKDND_STAGING_ONLY']='1'
                probe="import staging_topics; staging_topics.install("+repr(staging['topics_path'])+")\n"+PROBE
                env['PYTHONPATH']=str(Path(__file__).parent)+os.pathsep+str(checkout)
                report['environment_kind']='isolated_staging';report['topics_hash']=store.digest(settings)
            result=subprocess.run([sys.executable,'-c',probe,json.dumps(sorted(ROUTERS))],cwd=checkout,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
            log=(result.stdout+result.stderr).encode()
            store.atomic_bytes(output.with_suffix('.probe.log'),log)
            report['probe_log_hash']=hashlib.sha256(log).hexdigest()
            if result.returncode:raise DeployError('Automatic smoke import/router/catalog/DB probe failed')
            summary=json.loads(result.stdout.splitlines()[-1])
            compare_copy(before,copied,strict=True)
            report.update(status='PASS',checks=summary,schema=schema_digest(copied),data_preserved=True,probe_log_hash=hashlib.sha256((result.stdout+result.stderr).encode()).hexdigest())
    except BaseException as error:
        report.update(status='FAIL' if isinstance(error,Exception) else 'UNKNOWN',error=type(error).__name__)
        raise
    finally:
        report['finished_at']=store.utc_now();store.save_record(output,report)
    return report


def validate(path,sha):
    value=store.read_record(path)
    if (value.get('sha')!=sha or value.get('kind')!='AUTOMATIC_READONLY_SMOKE' or value.get('status')!='PASS'
            or value.get('real_telegram') is not False or value.get('data_preserved') is not True
            or value.get('checks',{}).get('status')!='PASS' or set(value['checks'].get('routers',[]))!=ROUTERS
            or len(value.get('probe_log_hash',''))!=64):
        raise DeployError('Automatic read-only smoke not verified for exact SHA')
    return value


def markdown(report):
    return '# Automatic read-only smoke\n\nSHA: `'+report['sha']+'`\n\nStatus: '+report['status']+'\n\nReal Telegram: NOT_RUN. Import/router/SQLite probes do not verify Telegram delivery.\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('project','sha','db','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    try:
        report=run(args.project,args.sha,args.db,args.output)
        Path(args.output).with_suffix('.md').write_text(markdown(report),encoding='utf-8')
        print('Automatic read-only smoke PASS; real Telegram NOT_RUN');return 0
    except Exception as error:print('Smoke FAIL: '+type(error).__name__,file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
