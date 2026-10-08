"""CI ONLY: pinned baseline adapter on a disposable real game SQLite."""
import hashlib,json,os
from pathlib import Path
import subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'deploy'))
import release_preflight as pref
from legacy_lkg import LegacyVerification
from scripts.release_checks import NETWORK_GUARD
with tempfile.TemporaryDirectory(prefix='legacy-lkg-ci-') as tmp:
    root=Path(tmp);project=root/'baseline'
    subprocess.run(['git','clone','--no-hardlinks',str(ROOT),str(project)],check=True)
    subprocess.run(['git','-C',str(project),'checkout','--detach',pref.BASELINE_SHA],check=True)
    subprocess.run(['git','-C',str(project),'remote','set-url','origin','https://github.com/Aruzento/shpakdnd_bot.git'],check=True)
    guard=root/'guard';guard.mkdir();(guard/'sitecustomize.py').write_text(NETWORK_GUARD)
    env=dict(os.environ,BOT_TOKEN='ci-test-token',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(guard),RELEASE_TEST_ISOLATED_TOPICS='1')
    seed="""from app.config import DB_PATH
import check_bot
check_bot.main()
from app.mini.players import create_mini_player
from app.mini.worlds import sync_configured_mini_worlds
from app.mini.wallet import add_coins
world=sync_configured_mini_worlds(DB_PATH)[0]
p=create_mini_player(world['id'],900123,'@fixture','Baseline fixture',DB_PATH)
add_coins(p['id'],100,'Legacy LKG isolated fixture',db_path=DB_PATH)
"""
    subprocess.run([sys.executable,'-c',seed],cwd=project,env=env,check=True)
    (project/'.env').write_text('BOT_TOKEN=ci-test-token\n')
    db=project/'shpakdnd.db';before=hashlib.sha256(db.read_bytes()).hexdigest()
    op=LegacyVerification(project=project,db=db,old=pref.BASELINE_SHA,target=pref.BASELINE_SHA,
                          evidence=root/'evidence'/'baseline.json',workspace=root/'workspace',tools=ROOT/'deploy',python=sys.executable)
    try:op.perform()
    except BaseException:
        if op.log and op.log.exists():print(op.log.read_text(encoding="utf-8"))
        raise
    pref.validate_evidence(op.evidence,old=pref.BASELINE_SHA,target=pref.BASELINE_SHA,tools=ROOT/'deploy')
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before,'Fixture source DB was written'
    print('LEGACY_BASELINE_RESULT='+json.dumps(dict(status=op.report['status'],sha=pref.BASELINE_SHA,tests=op.report['tests'],checks=op.report['release_checks'],source_db_unchanged=True,lkg_published=False)))
