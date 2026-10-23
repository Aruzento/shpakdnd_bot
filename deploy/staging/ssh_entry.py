"""Forced-command entry point; unprivileged, no shell, no client environment forwarding."""
import os
from pathlib import Path
import re
import subprocess
import sys


def parse(command):
    match = re.fullmatch(r'deploy-v2 ([1-9][0-9]{0,9}) ([0-9a-f]{40}) ([1-9][0-9]{0,19}-[1-9][0-9]{0,4})', command)
    if match:
        return ('deploy-v2', *match.groups())
    match = re.fullmatch(r'report-v2 ([1-9][0-9]{0,19}-[1-9][0-9]{0,4})', command)
    if match:
        return ('report-v2', match[1])
    raise ValueError('SSH operation refused')


def main():
    try:
        args = parse(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    except ValueError:
        print('SSH operation refused', file=sys.stderr)
        return 2
    return subprocess.call(['/usr/bin/sudo', '-n', '/usr/bin/python3', '-I',
                            '/usr/local/lib/shpakdnd-staging/controller.py', *args],
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'},
                           stdin=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(main())
