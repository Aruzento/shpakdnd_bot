"""Read-only disk budgets and retention inventory; never delete a release or backup."""
import argparse
import json
from pathlib import Path
import os
import re
import shutil
import stat
import time
from gate import Refused

MIB = 1024**2
GIB = 1024**3


def tree_bytes(root):
    total = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(folder)/name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                # Release .env/SQLite links are metadata only; never dereference them.
                total += info.st_size
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def check_space(base, state, docker_root, database):
    size = 0
    for suffix in ('', '-wal', '-shm'):
        path = Path(str(database)+suffix)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise Refused('Unexpected live SQLite storage path')
            size += info.st_size
    # Reserve max-size release/fetch, three complete host snapshots, private logs,
    # Docker working storage and a 1 GiB floor on every distinct filesystem.
    budgets = [(base, GIB), (state, 3*max(size, MIB)+128*MIB), (docker_root, 4*GIB)]
    devices = {}
    for path, needed in budgets:
        path = Path(path)
        device = path.stat().st_dev
        entry = devices.setdefault(device, {'required': GIB, 'free': shutil.disk_usage(path).free})
        entry['required'] += needed
        entry['free'] = min(entry['free'], shutil.disk_usage(path).free)
    if any(v['free'] < v['required'] for v in devices.values()):
        raise Refused('Insufficient staging disk space; preserve backups and request operator retention review')
    return {'status': 'PASS', 'required_mib': sum(v['required'] for v in devices.values())//MIB,
            'free_mib': sum(v['free'] for v in devices.values())//MIB}


def inventory(base, state):
    # Local administrator output only; not part of the SSH/public report surface.
    current = (base/'current').resolve(strict=True)
    rows = []
    for kind, directory in (('report', state/'reports'), ('release', base/'releases')):
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_dir():
                raise Refused('Unexpected retention entry')
            rows.append({'kind': kind, 'path': str(path), 'bytes': tree_bytes(path),
                         'age_days': int((time.time()-path.stat().st_mtime)/86400),
                         'protected': True, 'current': path == current, 'pinned': (path/'KEEP').exists(),
                         'action': 'KEEP_UNTIL_EXPLICIT_OPERATOR_ARCHIVE_REVIEW'})
    return {'automatic_deletion': False, 'entries': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path('/opt/shpakdnd-staging'))
    parser.add_argument('--state', type=Path, default=Path('/var/lib/shpakdnd-staging'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise Refused('Retention inventory requires local root')
    import fcntl
    with (args.state/'deploy.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(inventory(args.base, args.state), indent=2))
