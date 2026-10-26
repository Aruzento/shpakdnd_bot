"""Atomic root-owned deploy bundles. No application/service/SQLite operations."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid

FILES = ('deploy-shpakdnd.sh', 'deploy_helpers.py', 'pvp_ops.py', 'sqlite-deploy.py',
         'telegram-deploy-notice.py', 'release_guard.py', 'release_content.py',
         'release_preflight.py', 'release_schema.json')
SOURCE_FILES = FILES + ('install-deploy-shpakdnd.sh', 'install_bundle.py')

# This verifier is embedded in launch.sh; no code is imported from the bundle
# until the manifest and every helper have been checked. Root remains trusted.
VERIFY = r'''import hashlib,json,os,stat,sys
from pathlib import Path
root=Path(sys.argv[1]); expected=sys.argv[2]
def safe(path,mode):
 s=path.lstat()
 assert not stat.S_ISLNK(s.st_mode), 'symlink'
 assert s.st_uid==OWNER and stat.S_IMODE(s.st_mode)==mode, 'owner/mode'
 assert s.st_nlink==1 or stat.S_ISDIR(s.st_mode), 'hardlink'
 assert (stat.S_ISDIR(s.st_mode) if mode==0o555 and path==root else stat.S_ISREG(s.st_mode)), 'type'
for parent in root.parents:
 s=parent.lstat()
 assert stat.S_ISDIR(s.st_mode) and s.st_uid in (0, OWNER) and (not s.st_mode&0o022 or s.st_mode&stat.S_ISVTX), 'unsafe parent'
safe(root,0o555)
p=root/'bundle.json';safe(p,0o444);raw=p.read_bytes()
assert hashlib.sha256(raw).hexdigest()==expected, 'manifest hash'
m=json.loads(raw)
assert set(m)=={'schema','sha','files'} and m['schema']==1, 'manifest schema'
assert set(m['files'])==set(NAMES), 'file set'
assert set(p.name for p in root.iterdir())==set(NAMES)|{'bundle.json','launch.sh'}, 'extra files'
for name,digest in m['files'].items():
 p=root/name;safe(p,0o555 if name=='deploy-shpakdnd.sh' else 0o444)
 assert hashlib.sha256(p.read_bytes()).hexdigest()==digest, 'helper hash: '+name
safe(root/'launch.sh',0o555)
'''.replace('NAMES', repr(FILES))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def launcher(manifest, uid=0):
    # $0 is resolved once; exec preserves the controlling TTY and stdin.
    return ("#!/bin/bash\nset -euo pipefail\n"
            'BUNDLE="$(dirname -- "$(readlink -f -- "$0")")"\n'
            '/usr/bin/python3 -I -B - "$BUNDLE" ' + digest(manifest) + " <<'VERIFY_BUNDLE'\n" +
            VERIFY.replace("OWNER", str(uid)) + "\nVERIFY_BUNDLE\n" +
            'exec /bin/bash "$BUNDLE/deploy-shpakdnd.sh" "$@"\n').encode()


def source_snapshot(source, sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('BLOCKED_APPROVED_EXACT_SHA_REQUIRED')
    source = Path(source).absolute()
    secure_path(source, os.getuid() if os.name == 'posix' else None, source=True)
    root = source.parent
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
    if git('rev-parse', 'HEAD').decode().strip() != sha:
        raise ValueError('BLOCKED_SOURCE_HEAD')
    snapshot = {}
    for name in SOURCE_FILES:
        file = source / name
        if file.is_symlink() or not file.is_file():
            raise ValueError('BLOCKED_SOURCE_FILE:' + name)
        mode = git('ls-tree', sha, '--', 'deploy/' + name).split()[0]
        if mode not in (b'100644', b'100755'):
            raise ValueError('BLOCKED_SOURCE_TYPE:' + name)
        raw = git('show', sha + ':deploy/' + name)
        if raw != file.read_bytes():
            raise ValueError('BLOCKED_SOURCE_MODIFIED:' + name)
        if name in FILES:
            snapshot[name] = raw
    return snapshot


def secure_path(path, uid, source=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('BLOCKED_PATH')
    for item in (*reversed(path.parents), path):
        if item.is_symlink():
            raise ValueError('BLOCKED_SYMLINK:' + str(item))
        if item.exists():
            s = item.stat()
            # /tmp sticky directory is acceptable only above an owned test dir;
            # production paths never pass through it. Source can be user-owned.
            if os.name == 'posix' and not source:
                sticky = stat.S_ISDIR(s.st_mode) and s.st_mode & stat.S_ISVTX
                if (s.st_uid not in (0, uid) or s.st_mode & 0o022) and not sticky:
                    raise ValueError('BLOCKED_PARENT_PERMISSIONS:' + str(item))


def sync_dir(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_file(path, raw, mode):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def validate(directory, snapshot, sha, uid):
    directory = Path(directory)
    secure_path(directory, uid)
    if set(p.name for p in directory.iterdir()) != set(FILES) | {'bundle.json', 'launch.sh'}:
        raise ValueError('BLOCKED_BUNDLE_FILE_SET')
    raw = (json.dumps(dict(schema=1, sha=sha, files={n: digest(snapshot[n]) for n in FILES}),
                      sort_keys=True, indent=2) + '\n').encode()
    expected = dict(snapshot, **{'bundle.json': raw, 'launch.sh': launcher(raw, uid)})
    for name, data in expected.items():
        file = directory / name
        s = file.lstat()
        mode = 0o555 if name in ('deploy-shpakdnd.sh', 'launch.sh') else 0o444
        if not stat.S_ISREG(s.st_mode) or file.is_symlink() or s.st_nlink != 1:
            raise ValueError('BLOCKED_BUNDLE_FILE_TYPE:' + name)
        if os.name == 'posix' and (s.st_uid != uid or stat.S_IMODE(s.st_mode) != mode):
            raise ValueError('BLOCKED_BUNDLE_PERMISSIONS:' + name)
        if file.read_bytes() != data:
            raise ValueError('BLOCKED_BUNDLE_HASH:' + name)
    if os.name == 'posix' and stat.S_IMODE(directory.stat().st_mode) != 0o555:
        raise ValueError('BLOCKED_BUNDLE_DIRECTORY_MODE')


@contextmanager
def deploy_lock(path, uid):
    import fcntl
    secure_path(path.parent, uid)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != uid or s.st_nlink != 1 or s.st_mode & 0o022:
            raise ValueError('BLOCKED_LOCK')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def previous_entry(entry, dest):
    if not os.path.lexists(entry):
        return None
    if not entry.is_symlink():
        raise ValueError('BLOCKED_ENTRYPOINT_TYPE')
    target = Path(os.readlink(entry))
    if not target.is_absolute():
        raise ValueError('BLOCKED_ENTRYPOINT_LINK')
    legacy = dest / 'deploy-shpakdnd.sh'
    if target != legacy:
        if target.parent.parent != dest / 'releases' or target.name != 'launch.sh' or not re.fullmatch('[0-9a-f]{40}', target.parent.name):
            raise ValueError('BLOCKED_ENTRYPOINT_LINK')
    if not target.is_file() or target.is_symlink():
        raise ValueError('BLOCKED_ENTRYPOINT_TARGET')
    secure_path(target.parent, os.getuid() if os.name == 'posix' else None)
    return target


def install(snapshot, sha, dest, entry, uid, event=lambda phase: None):
    """Caller holds the deploy lock. API paths are for disposable test fixtures."""
    if set(snapshot) != set(FILES) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('BLOCKED_SNAPSHOT')
    dest, entry = Path(dest), Path(entry)
    secure_path(dest, uid)
    secure_path(entry.parent, uid)
    old = previous_entry(entry, dest)
    dest.mkdir(mode=0o755, parents=True, exist_ok=True)
    releases = dest / 'releases'
    secure_path(releases, uid)
    releases.mkdir(mode=0o755, exist_ok=True)
    event('prepared_directories')
    version = releases / sha
    secure_path(version, uid)
    if not version.exists():
        if shutil.disk_usage(releases).free < sum(map(len, snapshot.values())) * 3 + 1048576:
            raise OSError('BLOCKED_INSUFFICIENT_SPACE')
        stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=releases))
        stage.chmod(0o755)
        # An interrupted stage is intentionally retained for diagnosis, never
        # reused or activated. No previous release is automatically removed.
        for name in FILES:
            write_file(stage / name, snapshot[name], 0o555 if name == 'deploy-shpakdnd.sh' else 0o444)
            event('copied:' + name)
        manifest = (json.dumps(dict(schema=1, sha=sha, files={n: digest(snapshot[n]) for n in FILES}),
                               sort_keys=True, indent=2) + '\n').encode()
        write_file(stage / 'bundle.json', manifest, 0o444)
        event('manifest')
        write_file(stage / 'launch.sh', launcher(manifest, uid), 0o555)
        event('launcher')
        stage.chmod(0o555)
        sync_dir(stage)
        validate(stage, snapshot, sha, uid)
        event('validated')
        os.rename(stage, version)
        sync_dir(releases)
        event('published')
    validate(version, snapshot, sha, uid)
    if old == version / 'launch.sh':
        return version
    # Same filesystem as entrypoint: replace is a single rename(2). Existing
    # legacy directory and its files are never overwritten or unlinked.
    temporary = entry.parent / ('.deploy-shpakdnd-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(version / 'launch.sh')
        sync_dir(entry.parent)
        event('before_switch')
        if previous_entry(entry, dest) != old:
            raise ValueError('BLOCKED_ENTRYPOINT_CHANGED')
        os.replace(temporary, entry)
        sync_dir(entry.parent)
        event('activated')
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', required=True, help='independently reviewed full source commit')
    parser.add_argument('--activate-existing', help='verified retained bundle SHA; installer rollback only')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        parser.error('Linux root required')
    source = Path(__file__).absolute().parent
    with deploy_lock(Path('/run/lock/shpakdnd-deploy.lock'), 0):
        snapshot = source_snapshot(source, args.sha)
        selected = args.sha
        if args.activate_existing:
            selected = args.activate_existing
            if not re.fullmatch('[0-9a-f]{40}', selected):
                raise ValueError('BLOCKED_RETAINED_SHA')
            retained = Path('/usr/local/lib/shpakdnd-deploy/releases') / selected
            if not retained.is_dir() or retained.is_symlink():
                raise ValueError('BLOCKED_RETAINED_BUNDLE_MISSING')
            snapshot = {name: subprocess.check_output(
                ['git', '-C', str(source.parent), 'show', selected + ':deploy/' + name],
                stderr=subprocess.PIPE) for name in FILES}
            validate(retained, snapshot, selected, 0)
        result = install(snapshot, selected, Path('/usr/local/lib/shpakdnd-deploy'),
                         Path('/usr/local/bin/deploy-shpakdnd'), 0)
    print('Installed verified bundle:', result)
    print('No bot/service/deployment was started. sudo deploy-shpakdnd --dry-run')


if __name__ == '__main__':
    main()
