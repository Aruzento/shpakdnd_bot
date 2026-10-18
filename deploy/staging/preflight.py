"""Frozen container driver. Input snapshots are read-only; no token or host controls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, '/trusted')
from inventory import CHECKS


def main():
    sha, old = sys.argv[1:3]
    env = dict(os.environ, BOT_TOKEN='123456:offline-staging-check',
               PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
               MINI_CUSTOM_EMOJI_ENABLED='false', MINI_EDITORIAL_DIR='/tmp/editorial')
    expected_freeze = Path('/input/pip-freeze.txt').read_text().strip().splitlines()
    actual_freeze = subprocess.check_output(['python', '-I', '-m', 'pip', 'freeze', '--all'], text=True).strip().splitlines()
    if sorted(expected_freeze) != sorted(actual_freeze):
        raise RuntimeError('Preflight image dependency freeze differs from approved TEST environment')
    subprocess.run(['git', '-c', 'safe.directory=/repo', 'clone', '--no-hardlinks', '/repo', '/tmp/work'], check=True, env=env)
    os.chdir('/tmp/work')
    subprocess.run(['git', 'checkout', '--detach', sha], check=True, env=env)
    shutil.copyfile('/input/topics.py', 'app/topics.py')
    shutil.copytree('/input/editorial', '/tmp/editorial')
    results = []
    commands = [['python', *c] for c in CHECKS] + [
        ['python', 'deploy/verify-v142-migration.py', '--source', '/input/source.db'],
        ['python', 'deploy/verify-v142-rollback.py', '--source', '/input/source.db', '--old-ref', old],
        ['git', 'diff', '--check'], ['git', 'diff', '--check', old, sha],
    ]
    for command in commands:
        print('\nCHECK ' + json.dumps(command), flush=True)
        completed = subprocess.run(command, env=env)
        results.append({'command': command, 'exit_code': completed.returncode})
        if completed.returncode:
            print(json.dumps({'checks': results, 'preflight': 'FAIL'}), flush=True)
            return 1
    print(json.dumps({'checks': results, 'preflight': 'PASS'}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
