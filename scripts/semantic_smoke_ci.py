"""Disposable real-code DB fixture for automated import/route smoke; no Telegram."""
import io,json,subprocess,sys,tarfile,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
import semantic_smoke as smoke
import release_preflight as pref
import release_state as store


def main():
    sha=store.command(['git','-C',ROOT,'rev-parse','HEAD'])
    archive=subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'archive',sha])
    with tempfile.TemporaryDirectory(prefix='shpakdnd-d-ci-') as temporary:
        base=Path(temporary);checkout=base/'game';checkout.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(checkout,filter='data')
        (checkout/'.env').write_text('BOT_TOKEN=123456:disposable-ci-smoke-token\n')
        env=pref.safe_environment(base);env['BOT_TOKEN']='123456:disposable-ci-smoke-token'
        result=subprocess.run([sys.executable,'check_bot.py'],cwd=checkout,env=env,capture_output=True,text=True,encoding='utf-8')
        if result.returncode:raise RuntimeError('Disposable check_bot initialization failed')
        report=smoke.run(ROOT,sha,checkout/'shpakdnd.db',base/'private'/'smoke.json')
        print('AUTOMATIC_SMOKE_RESULT='+json.dumps(report,sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
