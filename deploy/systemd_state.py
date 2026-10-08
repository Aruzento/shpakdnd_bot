"""Verify loaded systemd properties and install a versioned observer, never bot."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import time
from deploy_helpers import DeployError
import release_state as store

DEFAULT_MANIFEST='/var/lib/shpakdnd-release/systemd-installation.json'
UNIT_NAMES=('shpakdnd-bot.service','shpakdnd-bot-watch.path','shpakdnd-bot-update.service')
SOURCE_NAMES=('deploy/update-shpakdnd-bot.sh','deploy/shpakdnd-bot-update.service','deploy/shpakdnd-bot-watch.path')
HARDENING={'NoNewPrivileges':'yes','ProtectSystem':'strict','ProtectHome':'yes','PrivateTmp':'yes',
           'PrivateDevices':'yes','PrivateNetwork':'yes','ProtectKernelTunables':'yes','ProtectKernelModules':'yes',
           'ProtectControlGroups':'yes','RestrictSUIDSGID':'yes','LockPersonality':'yes','CapabilityBoundingSet':'',
           'RestrictAddressFamilies':'AF_UNIX','ReadWritePaths':''}


def show(unit, field):
    return store.command(['systemctl','show','--no-pager','-p',field,'--value',unit])


def exec_command(raw):
    entries=re.findall(r'\{\s*path=([^;]+?)\s*;\s*argv\[\]=([^;]+?)\s*;',raw)
    if len(entries)!=1:raise DeployError('Expected exactly one effective ExecStart')
    return [entries[0][0].strip(),entries[0][1].strip()]


def loaded_config(manifest):
    bot,watch,update=manifest['units']
    expected_files=manifest['files']
    for name,hash_value in expected_files.items():
        store.trusted_path(name)
        if store.file_hash(name)!=hash_value:raise DeployError('Installed unit/helper drift')
    config={}
    for unit in manifest['units']:
        if show(unit,'LoadState')!='loaded' or show(unit,'DropInPaths') or show(unit,'NeedDaemonReload')!='no':
            raise DeployError('Unit missing, unexpected drop-in or pending reload')
        fragment=show(unit,'FragmentPath')
        if fragment!=str(Path(manifest['unit_dir'])/unit):raise DeployError('Unexpected effective FragmentPath')
        store.trusted_path(fragment)
        config[unit]={'FragmentPath':fragment,'ExecStart':None}
    if show(bot,'User')!=manifest['bot_user'] or show(bot,'WorkingDirectory')!=manifest['project']:
        raise DeployError('Bot User/WorkingDirectory drift')
    expected_python=manifest['python'];bot_command=expected_python+' '+manifest['project']+'/bot.py'
    if exec_command(show(bot,'ExecStart'))!=[expected_python,bot_command]:raise DeployError('Bot effective ExecStart drift')
    config[bot].update(Environment=show(bot,'Environment'),User=manifest['bot_user'],WorkingDirectory=manifest['project'],ExecStart=bot_command)
    if show(update,'User')!=manifest['bot_user'] or show(update,'WorkingDirectory')!=manifest['project']:
        raise DeployError('Observer user/working directory drift')
    helper=manifest['helper']
    if exec_command(show(update,'ExecStart'))!=[helper,helper]:raise DeployError('Observer effective ExecStart drift')
    for field in ('ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecReload'):
        if show(update,field):raise DeployError('Unexpected observer command')
    for field,value in HARDENING.items():
        if show(update,field)!=value:raise DeployError('Observer hardening drift: '+field)
    if show(update,'Type')!='oneshot':raise DeployError('Unexpected observer service type')
    paths=[]
    for line in show(watch,'Paths').splitlines():
        match=re.fullmatch(r'(.+) \(PathModified\)',line)
        if not match:raise DeployError('Unexpected effective watch rule')
        paths.append(match[1])
    if sorted(paths)!=sorted(manifest['watched']) or show(watch,'Triggers')!=update or show(watch,'Unit')!=update:
        raise DeployError('Watcher paths/Unit/Triggers drift')
    config[update].update(User=manifest['bot_user'],WorkingDirectory=manifest['project'],ExecStart=helper,**HARDENING)
    config[watch].update(Paths=sorted(paths),Unit=update)
    staging=staging_identity(manifest)
    if staging:config['staging']=staging
    return config


def staging_identity(binding):
    import shlex
    values={entry.split('=',1)[0]:entry.split('=',1)[1] for entry in shlex.split(show(binding['units'][0],'Environment')) if '=' in entry}
    if values.get('SHPAKDND_STAGING_ONLY')!='1':return None
    project=Path(binding['project']).resolve()
    if project==Path('/opt/shpakdnd-bot').resolve():raise DeployError('Staging cannot target default production checkout')
    config=Path(values.get('SHPAKDND_STAGING_TOPICS',''))
    runtime=Path(values.get('PYTHONPATH',''))
    store.outside_project(runtime,project);store.trusted_path(runtime,directory=True)
    store.outside_project(config,project);store.trusted_path(config)
    import staging_topics
    settings=staging_topics.configuration(config)
    helper=runtime/'staging_topics.py';hook=runtime/'sitecustomize.py'
    store.trusted_path(helper);store.trusted_path(hook)
    if store.file_hash(helper)!=store.file_hash(Path(__file__).parent/'staging_topics.py') or hook.read_text().strip()!='from staging_topics import install; install()':
        raise DeployError('Staging runtime hook differs from reviewed helper')
    return {'environment_kind':'isolated_staging','topics_path':str(config),'runtime_hash':store.file_hash(helper),'hook_hash':store.file_hash(hook),'topics_hash':store.digest(settings)}


def verify_installed(path=DEFAULT_MANIFEST, *, project=None, units=None, active=False):
    manifest=store.read_record(path)
    if manifest.get('version')!=1 or manifest.get('status')!='CONFIRMED':raise DeployError('Unconfirmed systemd installation')
    store.full_sha(manifest['source_sha'])
    store.outside_project(Path(path).parent,manifest['project'])
    store.outside_project(manifest['unit_dir'],manifest['project'])
    store.outside_project(manifest['helper'],manifest['project'])
    if project is not None and str(Path(project).resolve())!=manifest['project']:raise DeployError('Installed systemd project mismatch')
    if units is not None and list(units)!=manifest['units']:raise DeployError('Installed systemd unit names mismatch')
    if store.digest(loaded_config(manifest))!=manifest['config_hash']:raise DeployError('Loaded systemd configuration drift')
    if active:
        bot,watch,update=manifest['units']
        if show(bot,'ActiveState')!='active' or show(watch,'ActiveState')!='active' or show(update,'ActiveState') not in {'active','inactive','activating'}:
            raise DeployError('Unhealthy bot/watcher/observer state')
    return {'manifest_path':str(Path(path).resolve()),'manifest_hash':store.file_hash(path),
            'config_hash':manifest['config_hash'],'source_sha':manifest['source_sha'],'project':manifest['project'],'tooling_hash':manifest['tooling_hash'],
            'units':manifest['units'],'python':manifest['python'],'bot_user':manifest['bot_user']}


def verify_binding(binding, *, active=False):
    current=verify_installed(binding['manifest_path'],project=binding['project'],units=binding['units'],active=active)
    if current!=binding:raise DeployError('Installed systemd changed since preflight')
    return current


def process_identity(binding):
    bot=binding['units'][0]
    if show(bot,'ActiveState')!='active':raise DeployError('Bot is not active')
    pid=show(bot,'MainPID');restarts=show(bot,'NRestarts');invocation=show(bot,'InvocationID')
    started=show(bot,'ExecMainStartTimestampMonotonic')
    if not pid.isdigit() or int(pid)<=0 or not restarts.isdigit() or not re.fullmatch('[0-9a-f]{32}',invocation) or not started.isdigit():
        raise DeployError('Insufficient process identity properties')
    if os.name!='posix':raise DeployError('Process identity requires Linux /proc')
    proc=Path('/proc')/pid
    if (proc/'cwd').resolve()!=Path(binding['project']).resolve():raise DeployError('Running process cwd differs from checkout')
    args=(proc/'cmdline').read_bytes().split(b'\0')
    if [os.fsdecode(a) for a in args if a]!=[binding['python'],binding['project']+'/bot.py']:
        raise DeployError('Running process command differs from effective bot unit')
    return {'pid':pid,'restarts':restarts,'invocation':invocation,'started_monotonic':started}


def health(binding, *, since, previous=None, before_watcher=False):
    verify_binding(binding,active=not before_watcher)
    if before_watcher and show(binding["units"][1],"ActiveState")!="inactive":
        raise DeployError("Watcher must remain inactive until startup health passes")
    current=process_identity(binding)
    if previous is not None and current!=previous:raise DeployError('PID/restart/invocation changed during health window')
    # No truncation: every message from the confirmed invocation is inspected.
    text=store.command(['journalctl','--no-pager','-o','cat','_SYSTEMD_INVOCATION_ID='+current['invocation']])
    if re.search(r'\b(?:ERROR|CRITICAL|FATAL)\b|Traceback|ModuleNotFoundError|ImportError|RuntimeError|SyntaxError|OperationalError|IntegrityError|Main process exited|Scheduled restart job|Failed to start|Start request repeated too quickly',text):
        raise DeployError('Bot journal has unexplained startup/restart errors')
    return current


def render_sources(blobs, *, sha, project, bot_user, helper):
    if bot_user=='root' or not re.fullmatch('[a-z_][a-z0-9_-]*',bot_user):raise DeployError('Invalid bot user')
    for path in (project,helper):
        if (not path.startswith('/') if os.name=='posix' else not Path(path).is_absolute()) or re.search(r'[\s%]',path) or (os.name=='posix' and '\\' in path):raise DeployError('Unsupported systemd path')
    service=blobs[SOURCE_NAMES[1]].decode().replace('/opt/shpakdnd-bot',project).replace('User=shpakbot','User='+bot_user)
    service=service.replace('/usr/local/lib/shpakdnd-observer/versions/@SOURCE_SHA@/update-shpakdnd-bot.sh',helper)
    service+='Environment=SHPAKDND_PROJECT='+project+'\n'
    path=blobs[SOURCE_NAMES[2]].decode().replace('/opt/shpakdnd-bot',project)
    watched=[line.split('=',1)[1] for line in path.splitlines() if line.startswith('PathModified=')]
    if not watched or any(re.search(r'(?:\.env|\.venv|\.db|__pycache__)',p) for p in watched):raise DeployError('Unsafe watcher coverage')
    return service.encode(),path.encode(),watched


def install(source, sha, *, project='/opt/shpakdnd-bot', python='/opt/shpakdnd-bot/.venv/bin/python',
            bot_user='shpakbot', unit_dir='/etc/systemd/system', helper_dir='/usr/local/lib/shpakdnd-observer',
            manifest_path=DEFAULT_MANIFEST, legacy_entry='/usr/local/bin/update-shpakdnd-bot.sh'):
    import release_preflight as pref
    blobs=store.verify_source(source,sha,SOURCE_NAMES+tuple('deploy/'+n for n in pref.BUNDLE)+tuple('scripts/'+n for n in pref.SHARED_SCRIPTS))
    project=str(Path(project).resolve())
    for path in (source,unit_dir,helper_dir,Path(manifest_path).parent):store.outside_project(path,project)
    store.trusted_path(unit_dir,directory=True)
    store.private_directory(Path(manifest_path).parent)
    helper_root=Path(helper_dir)
    existing=next(p for p in (helper_root,*helper_root.parents) if p.exists() or p.is_symlink())
    store.trusted_path(existing,directory=True)
    if not helper_root.exists():helper_root.mkdir(mode=0o755,parents=True)
    store.trusted_path(helper_root,directory=True)
    store.outside_project(legacy_entry,project);store.trusted_path(Path(legacy_entry).parent,directory=True)
    bot,watch,update=UNIT_NAMES
    bot_file=Path(unit_dir)/bot;store.trusted_path(bot_file)
    helper=helper_root/'versions'/sha/'update-shpakdnd-bot.sh'
    store.outside_project(helper,project)
    existing=next(p for p in (helper.parent,*helper.parent.parents) if p.exists() or p.is_symlink())
    store.trusted_path(existing,directory=True)
    service,path,watched=render_sources(blobs,sha=sha,project=project,bot_user=bot_user,helper=str(helper))
    wrapper=('#!/usr/bin/env bash\nset -euo pipefail\nexec '+str(helper)+'\n').encode()
    manifest={'version':1,'status':'CONFIRMED','source_sha':sha,'tooling_hash':pref.tooling_hash(Path(source)/'deploy'),'project':project,'python':python,
              'bot_user':bot_user,'unit_dir':str(Path(unit_dir).resolve()),'units':list(UNIT_NAMES),
              'helper':str(helper),'watched':watched,'files':{str(bot_file):store.file_hash(bot_file),
              str(Path(unit_dir)/update):__import__('hashlib').sha256(service).hexdigest(),
              str(Path(unit_dir)/watch):__import__('hashlib').sha256(path).hexdigest(),
              str(helper):__import__('hashlib').sha256(blobs[SOURCE_NAMES[0]]).hexdigest(),
              str(legacy_entry):__import__('hashlib').sha256(wrapper).hexdigest()}}
    if Path(manifest_path).exists():
        try:
            old=store.read_record(manifest_path)
            if all(old.get(k)==v for k,v in manifest.items()):
                verify_installed(manifest_path,project=project,active=True)
                print('Observer units already verified; no installation required');return old
        except DeployError:pass  # Repair only from the explicit reviewed source, never legacy restore.
    report_dir=Path(manifest_path).parent/'installations';store.private_directory(report_dir)
    operation=report_dir/(store.utc_now().replace(':','_')+'_'+sha[:12]);store.private_directory(operation)
    report={'version':1,'source_sha':sha,'status':'RUNNING','phase':'VALIDATED_SOURCE','started_at':store.utc_now()}
    report_path=operation/'report.json';store.save_record(report_path,report)
    def phase(value):report['phase']=value;store.save_record(report_path,report)
    def ctl(*args):return store.command(['systemctl',*args])
    try:
        initial_pid=show(bot,'MainPID');initial_restarts=show(bot,'NRestarts')
        if show(bot,'ActiveState')!='active':raise DeployError('Preserve an initially healthy bot only')
        phase('BLOCKING_LEGACY')
        # Persist a boot-safe quarantine BEFORE replacing any executable/unit.
        # A runtime mask alone disappears on power loss.
        quarantine_dir=Path(unit_dir)/(update+'.d')
        quarantine_dir.mkdir(mode=0o755,exist_ok=True);store.trusted_path(quarantine_dir,directory=True)
        quarantine=quarantine_dir/'shpakdnd-quarantine.conf'
        marker=Path(manifest_path).parent/'observer-quarantined'
        guard=('[Unit]\nConditionPathExists=!'+str(marker)+'\n').encode()
        if quarantine.exists() and quarantine.read_bytes()!=guard:raise DeployError('Unexpected quarantine override')
        store.atomic_bytes(marker,b'Installation requires verified observer configuration\n')
        store.atomic_bytes(quarantine,guard,0o644)
        def reject_foreign_dropins():
            for unit in UNIT_NAMES:
                actual=show(unit,'DropInPaths').split()
                allowed={str(quarantine)} if unit==update else set()
                if set(actual)-allowed:raise DeployError('Unexpected effective drop-in')
        ctl('mask','--runtime','--now',update)
        ctl('disable','--now',watch)
        ctl('stop',update)
        if show(watch,'ActiveState')!='inactive' or show(update,'ActiveState')!='inactive':raise DeployError('Legacy jobs not quiescent')
        reject_foreign_dropins()
        phase('STAGING')
        if Path(legacy_entry).exists() or Path(legacy_entry).is_symlink():
            store.trusted_path(legacy_entry)
            store.atomic_bytes(operation/'legacy-helper.txt',Path(legacy_entry).read_bytes())
        for unit in (watch,update):
            file=Path(unit_dir)/unit
            if file.exists() or file.is_symlink():
                store.trusted_path(file)
                store.atomic_bytes(operation/unit,file.read_bytes())
        stage=operation/'staging';stage.mkdir(mode=0o700)
        (stage/update).write_bytes(service);(stage/watch).write_bytes(path)
        (stage/'update-shpakdnd-bot.sh').write_bytes(blobs[SOURCE_NAMES[0]])
        store.command(['bash','-n',stage/'update-shpakdnd-bot.sh'])
        # The referenced immutable executable must exist for systemd-analyze.
        helper.parent.mkdir(mode=0o755,parents=True,exist_ok=True);store.trusted_path(helper.parent,directory=True)
        if helper.exists():
            store.trusted_path(helper)
            if helper.read_bytes()!=blobs[SOURCE_NAMES[0]]:raise DeployError('Observer version already exists with different contents')
        else:store.atomic_bytes(helper,blobs[SOURCE_NAMES[0]],0o755)
        store.command(['systemd-analyze','verify',stage/update,stage/watch])
        phase('INSTALLING')
        store.atomic_bytes(Path(unit_dir)/update,service,0o644)
        store.atomic_bytes(Path(unit_dir)/watch,path,0o644)
        store.atomic_bytes(legacy_entry,wrapper,0o755)
        phase('RELOADING')
        ctl('daemon-reload')
        # Disk/drop-in validation precedes removing the runtime mask.
        reject_foreign_dropins()
        # All executable/unit bytes are now the pinned safe set. Removing this
        # one owned drop-in is safe across a reboot; legacy is never restored.
        for file,expected in manifest['files'].items():
            store.trusted_path(file)
            if store.file_hash(file)!=expected:raise DeployError('Staged installed file mismatch')
        store.trusted_path(quarantine);quarantine.unlink();store.fsync_directory(quarantine_dir)
        ctl('unmask','--runtime',update);ctl('daemon-reload')
        config=loaded_config(manifest);manifest['config_hash']=store.digest(config)
        phase('ACTIVATING_SAFE_OBSERVER')
        ctl('enable','--now',watch)
        # Exercise observer; bot PID/restarts must survive it unchanged.
        ctl('start',update)
        if show(update,'ExecMainStatus')!='0':raise DeployError('Observer diagnostic failed')
        if show(bot,'MainPID')!=initial_pid or show(bot,'NRestarts')!=initial_restarts or show(bot,'ActiveState')!='active':
            raise DeployError('Bot changed during observer installation')
        loaded_config(manifest)
        if show(watch,'ActiveState')!='active':raise DeployError('Safe watcher is not active')
        store.save_record(manifest_path,manifest)
        report.update(status='PASS',phase='CONFIRMED',finished_at=store.utc_now(),config_hash=manifest['config_hash'])
        store.save_record(report_path,report)
        print('Safe observer installed; bot was not stopped. Report: '+str(report_path))
        return manifest
    except BaseException:
        # Retained legacy files are forensic backups, never reactivated automatically.
        for args in (('mask','--runtime','--now',update),('disable','--now',watch),('stop',update)):
            try:ctl(*args)
            except Exception:pass
        report.update(status='FAIL',finished_at=store.utc_now())
        store.save_record(report_path,report)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','verify'])
    parser.add_argument('--manifest',default=os.environ.get('SHPAKDND_SYSTEMD_MANIFEST',DEFAULT_MANIFEST))
    parser.add_argument('--source');parser.add_argument('--sha')
    parser.add_argument('--configuration-only',action='store_true')
    parser.add_argument('--project',default=os.environ.get('SHPAKDND_PROJECT','/opt/shpakdnd-bot'))
    parser.add_argument('--python',default=os.environ.get('SHPAKDND_PYTHON','/opt/shpakdnd-bot/.venv/bin/python'))
    parser.add_argument('--bot-user',default=os.environ.get('SHPAKDND_BOT_USER','shpakbot'))
    parser.add_argument('--legacy-entry',default='/usr/local/bin/update-shpakdnd-bot.sh')
    parser.add_argument('--unit-dir',default='/etc/systemd/system');parser.add_argument('--helper-dir',default='/usr/local/lib/shpakdnd-observer')
    args=parser.parse_args(argv)
    try:
        if os.name!='posix' or os.geteuid()!=0:raise DeployError('Root Linux required')
        if args.action=='install':
            import release_preflight as pref
            pref.require_deployment_lock(Path(args.project)/'shpakdnd.db')
            if not args.source or not args.sha:raise DeployError('Pinned source/SHA required')
            def interrupted(*unused):raise DeployError('Observer installation interrupted')
            signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
            install(args.source,args.sha,project=args.project,python=args.python,bot_user=args.bot_user,
                    unit_dir=args.unit_dir,helper_dir=args.helper_dir,manifest_path=args.manifest,legacy_entry=args.legacy_entry)
        else:print(json.dumps(verify_installed(args.manifest,project=args.project,active=not args.configuration_only),sort_keys=True))
        return 0
    except Exception as error:
        print('ERROR: '+(str(error) if isinstance(error,DeployError) else type(error).__name__));return 1


if __name__=='__main__':raise SystemExit(main())
