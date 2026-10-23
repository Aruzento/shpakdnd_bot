"""CI-only systemd serialization/integration probe. No TEST units, SSH or DB data."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.staging import controller as c
from deploy.staging.runtime_policy import expected, validate_unit


def command(*args):
    return subprocess.run(list(args),check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout


def main():
    if os.geteuid()!=0:
        raise RuntimeError('Isolated Linux CI probe requires root')
    account=pwd.getpwnam('nobody')
    name='shpakdnd-policy-probe-'+uuid.uuid4().hex+'.service'
    unit_file=Path('/run/systemd/system')/name
    with tempfile.TemporaryDirectory(prefix='staging-policy-',dir='/opt') as tmp:
        root=Path(tmp);root.chmod(0o755);base=root/'base';config=root/'config';state=root/'state'
        for path in (base/'current',base/'venv/bin',config/'editorial',state/'data'):
            path.mkdir(parents=True)
        (base/'venv/bin/python').symlink_to('/usr/bin/python3')
        (base/'current/bot.py').write_text('import time;time.sleep(90)\n')
        (config/'runtime.env').write_text('MINI_EDITORIAL_DIR='+str(config/'editorial')+'\nMINI_CUSTOM_EMOJI_ENABLED=false\nPYTHONDONTWRITEBYTECODE=1\n')
        text=(Path(__file__).resolve().parents[2]/'deploy/staging/test-service.conf').read_text()
        text=text.replace('/opt/shpakdnd-staging',str(base)).replace('/etc/shpakdnd-staging',str(config)).replace('/var/lib/shpakdnd-staging',str(state))
        text=text.replace('User=shpakbot','User=nobody').replace('Group=shpakbot','Group='+str(account.pw_gid))
        def loaded():
            actual=c.unit_state(name)
            assert actual['User']=='nobody' and actual['Group']==str(account.pw_gid)
            assert actual['FragmentPath']==str(unit_file) and actual['DropInPaths']==''
            actual.update(User='shpakbot',Group='shpakbot',FragmentPath=expected(base,config,state)['FragmentPath'],
                          DropInPaths=expected(base,config,state)['DropInPaths'])
            return actual
        try:
            unit_file.write_text(text)
            command('systemctl','daemon-reload');command('systemctl','start',name)
            for _ in range(30):
                if c.unit_state(name).get('ActiveState')=='active':break
                time.sleep(0.1)
            assert c.unit_state(name)['ActiveState']=='active'
            actual=loaded()
            try:validate_unit(actual,base,config,state)
            except c.Refused:
                print(json.dumps({'mismatched_properties':{k:{'actual':actual.get(k),'expected':v} for k,v in expected(base,config,state).items() if actual.get(k)!=v}}))
                raise
            # Change effective metadata only, never restart into a modified command.
            for directive in ('Environment=BOT_TOKEN=synthetic-foreign-token',
                              'EnvironmentFile=-'+str(config/'unexpected.env'),
                              'ReadWritePaths='+str(config),
                              'ExecStart=\nExecStart=/bin/sh -c "echo unexpected"'):
                unit_file.write_text(text+'\n'+directive+'\n');command('systemctl','daemon-reload')
                try:validate_unit(loaded(),base,config,state)
                except c.Refused:pass
                else:raise AssertionError('Effective runtime drift was accepted')
            print(json.dumps({'native_systemd_policy':'PASS','foreign_environment_file':'REFUSED',
                              'foreign_execstart':'REFUSED','additional_writable_path':'REFUSED'}))
        finally:
            subprocess.run(['systemctl','stop',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            unit_file.unlink(missing_ok=True)
            command('systemctl','daemon-reload')
    return 0


if __name__=='__main__':raise SystemExit(main())
