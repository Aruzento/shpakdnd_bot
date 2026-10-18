"""Generate verifiable review/release manifest without touching any runtime database."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import sha_value
from inventory import CHECKS, MANUAL


def git(*args):
    return subprocess.check_output(['git', *args], text=True, encoding='utf-8').strip()


def manifest(sha):
    sha = sha_value(sha)
    if git('rev-parse', sha + '^{commit}') != sha:
        raise ValueError('Exact Git commit required')
    files = git('ls-tree', '-r', '--name-only', sha).splitlines()
    requirements = subprocess.check_output(['git', 'show', sha + ':requirements.txt'])
    return {
        'schema_version': 1, 'sha': sha, 'tree': git('rev-parse', sha + '^{tree}'),
        'test_files': [p for p in files if p.startswith('tests/') and p.endswith('.py')],
        'checks': CHECKS + [
            ['deploy/verify-v142-migration.py', '--source', '<disposable TEST copy>'],
            ['deploy/verify-v142-rollback.py', '--source', '<disposable TEST copy>', '--old-ref', '<previous SHA>'],
            ['git', 'diff', '--check'],
        ],
        'migrations': [p for p in files if '/migrations/' in p and p.endswith('.py')],
        'dependencies': {'requirements_sha256': hashlib.sha256(requirements).hexdigest(),
                         'requirements': requirements.decode('utf-8'),
                         'automatic_install': False, 'require_admin_prepared_venv_and_image': True},
        'manual_telegram_scenarios': MANUAL, 'telegram_status': 'NOT_RUN',
        'production_authorized': False,
        'rollback': {'automatic_database_restore': False, 'automatic_code_rollback': False,
                     'procedure': 'Preserve current DB; pause-new/drain and reconcile PvP; frozen pvp_ops rollback-check before preparation, checkout and start. Run full checks against retained current DB on a copy; operator approves code recovery. DB restore is a separate approved recovery after writers stop.'},
        'production_preflight': [
            'Independent review of exact SHA; successful Linux Python 3.11/3.13 and TEST manual acceptance',
            'Merge authorized feature to main; record resulting origin/main SHA and rerun relevant release evidence',
            'Verify production hostname, root-owned installed deploy bundle including frozen pvp_ops, unit paths and dependencies',
            'Online SQLite backup + integrity/FK; copy config and external editorial; test migrations and rollback only on disposable copies',
            'Exclude external writers; retain confirmations, notices, watcher stop, backup, all checks and three PvP rollback gates',
            'Separate approval then sudo deploy-shpakdnd from origin/main; never this feature workflow on production',
        ],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(manifest(args.sha), indent=2) + '\n', encoding='utf-8')
