"""Private, durable state outside game checkout; checksums cover one envelope."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from deploy_helpers import DeployError

TRUSTED_UID = 0


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def trusted_path(path, *, private=False, directory=False):
    path=Path(path)
    # Check every ancestor too: a protected leaf inside writable /opt/... is unsafe.
    for ancestor in [path, *path.parents]:
        info=ancestor.lstat()
        if ancestor.is_symlink() or (ancestor==path and not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
            raise DeployError('Unsafe symlink/type in trusted path')
        if os.name=='posix' and ((info.st_uid!=TRUSTED_UID if ancestor==path else info.st_uid not in {0,TRUSTED_UID}) or info.st_mode & 0o022):
            # Sticky /tmp is allowed only as an ancestor of private root fixtures/staging.
            if ancestor==path or not (stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX and info.st_uid in {0,TRUSTED_UID}):
                raise DeployError('Unsafe trusted owner/permissions')
        if ancestor==path and private and os.name=='posix' and info.st_mode & 0o077:
            raise DeployError('Private state permissions required')
    return path


def outside_project(path, project):
    physical=Path(path).resolve();project=Path(project).resolve()
    if physical==project or project in physical.parents or physical in project.parents:
        raise DeployError('Control path intersects watched project')
    return physical


def private_directory(path):
    path=Path(path)
    existing=next(p for p in (path,*path.parents) if p.exists() or p.is_symlink())
    trusted_path(existing,directory=True)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return trusted_path(path, private=True, directory=True)


def atomic_bytes(path, content, mode=0o600):
    path=Path(path);trusted_path(path.parent, directory=True)
    if path.exists() or path.is_symlink():trusted_path(path)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.new')
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,mode)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():temporary.unlink()


def fsync_directory(path):
    if os.name=='posix':
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def save_record(path, payload):
    private_directory(Path(path).parent)
    raw=json.dumps({'payload':payload,'sha256':digest(payload)},sort_keys=True,ensure_ascii=False,indent=2)+'\n'
    atomic_bytes(path,raw.encode())


def read_record(path):
    trusted_path(Path(path).parent,private=True,directory=True);trusted_path(path,private=True)
    try:
        envelope=json.loads(Path(path).read_text(encoding='utf-8'))
        payload=envelope['payload']
        if envelope['sha256']!=digest(payload):raise DeployError('State checksum mismatch')
        return payload
    except (ValueError,KeyError,TypeError) as error:raise DeployError('Invalid state record') from error


def full_sha(sha):
    if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{40}',sha):raise DeployError('Full SHA required')
    return sha


def command(args, *, cwd=None):
    args=list(map(str,args))
    if args and args[0]=='git':
        safe=args[args.index('-C')+1] if '-C' in args else str(cwd or Path.cwd())
        args=[args[0],'--no-optional-locks','-c','core.fsmonitor=false','-c','safe.directory='+safe,*args[1:]]
    result=subprocess.run(args,cwd=cwd,capture_output=True,text=True,encoding='utf-8',timeout=60)
    if result.returncode:raise DeployError('Trusted command failed: '+str(args[0]))
    return result.stdout.strip()


def repository_id(project):
    # Stable identity survives code rollback and excludes credentials from state.
    url=command(['git','-C',project,'remote','get-url','origin'])
    if not re.fullmatch(r'(?:https://github\.com/|git@github\.com:)Aruzento/shpakdnd_bot(?:\.git)?/?',url):
        raise DeployError('Unexpected Git repository origin')
    return 'github.com/Aruzento/shpakdnd_bot'


def verify_source(source, sha, names):
    source=Path(source).resolve();full_sha(sha)
    trusted_path(source,directory=True)
    if command(['git','-C',source,'rev-parse','HEAD'])!=sha or command(['git','-C',source,'status','--porcelain','--untracked-files=all']):
        raise DeployError('Source SHA/worktree not pinned and clean')
    repository_id(source)
    result={}
    for name in names:
        file=source/name;trusted_path(file)
        blob=subprocess.run(['git','-C',str(source),'show',sha+':'+name],capture_output=True,timeout=30)
        if blob.returncode or blob.stdout!=file.read_bytes():raise DeployError('Source file differs from pinned Git blob: '+name)
        result[name]=blob.stdout
    return result


def require_root():
    if os.name!="posix" or os.geteuid()!=0:raise DeployError("Root Linux required")


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True);parser.add_argument('--sha',required=True)
    for name in ("dest","bin","lock","project"):parser.add_argument("--"+name)
    args=parser.parse_args(argv)
    try:
        require_root()
        for value in (args.dest,args.bin,args.lock):
            if value:
                outside_project(value,args.project or "/opt/shpakdnd-bot")
                outside_project(value,args.source)
                path=Path(value)
                existing=next(p for p in (path,*path.parents) if p.exists() or p.is_symlink())
                trusted_path(existing,directory=existing.is_dir())
        import release_preflight as pref
        names=tuple('deploy/'+n for n in pref.BUNDLE)+tuple('scripts/'+n for n in pref.SHARED_SCRIPTS)+('deploy/install-deploy-shpakdnd.sh','deploy/install-systemd-units.sh',*__import__('systemd_state').SOURCE_NAMES)
        verify_source(args.source,args.sha,names)
        print('Pinned clean source/files verified: '+args.sha);return 0
    except Exception as error:
        print('ERROR: '+(str(error) if isinstance(error,DeployError) else type(error).__name__));return 1


if __name__=='__main__':raise SystemExit(main())
