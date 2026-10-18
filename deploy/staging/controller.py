"""Frozen privileged orchestration. No application imports, shell or feature helper execution."""
import hashlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from contextlib import closing, contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate import GitHub, Refused, verify, sha_value
from inventory import CHECKS, MANUAL
from ssh_entry import parse

BASE = Path('/opt/shpakdnd-staging')
CONFIG = Path('/etc/shpakdnd-staging')
STATE = Path('/var/lib/shpakdnd-staging')
BUNDLE = Path('/usr/local/lib/shpakdnd-staging')
BOT = 'shpakdnd-bot.service'
WATCH = 'shpakdnd-bot-watch.path'
UPDATE = 'shpakdnd-bot-update.service'
UNITS = (WATCH, UPDATE, BOT)
TEST_SCOPE = {(-1003883723333, 2), (-5320347021, 0)}
MINI_SCOPE = {(-1003883723333, 2)}


def run(args, *, check=True, timeout=120):
    return subprocess.run([str(a) for a in args], check=check, timeout=timeout,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'},
                          encoding='utf-8', errors='replace')


def trusted(path, directory=False):
    path = Path(path)
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('Control path is not root-owned and immutable to application/SSH user')
    if directory and not path.is_dir():
        raise Refused('Required control directory unavailable')
    return path


def db_check(path, scope=False):
    with closing(sqlite3.connect(Path(path).resolve(strict=True).as_uri() + '?mode=ro', uri=True, timeout=30)) as conn:
        deadline = time.monotonic() + 120
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise Refused('SQLite integrity failed')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise Refused('SQLite foreign keys failed')
        if scope:
            worlds = set(conn.execute('SELECT DISTINCT chat_id,thread_id FROM mini_worlds LIMIT 2'))
            enabled = set(conn.execute('SELECT DISTINCT chat_id,thread_id FROM mini_worlds WHERE enabled=1 LIMIT 2'))
            if worlds != MINI_SCOPE or enabled != MINI_SCOPE:
                raise Refused('SQLite contains enabled non-TEST Mini scope')
    return {'integrity': 'ok', 'foreign_keys': 0, 'test_scope': scope}


def backup(source, destination):
    destination = Path(destination)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(Path(source).resolve(strict=True).as_uri() + '?mode=ro', uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(destination)) as target:
            deadline = time.monotonic() + 120
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise Refused('SQLite backup exceeded time budget')
            src.backup(target, pages=1024, progress=progress)
    checks = db_check(destination, scope=True)
    with destination.open('rb') as verified:
        digest = hashlib.file_digest(verified, 'sha256').hexdigest()
    return {'path': str(destination), 'sha256': digest, **checks}


@contextmanager
def lock(path):
    with open(path, 'a+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refused('Another staging deployment is running') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def safe_extract(data, target):
    # Never permit symlinks, gitlinks, devices, escaping paths or server state from Git.
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        total = 0
        for index, member in enumerate(archive):
            total += member.size
            if index > 50000 or total > 512 * 1024 * 1024:
                raise Refused('Release archive exceeds resource limits')
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or any(part in {'.git', '.env', '.venv', 'venv', '.revision'} for part in path.parts)
                    or any(part.endswith(('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3')) for part in path.parts)
                    or not (member.isfile() or member.isdir())):
                raise Refused('Unsafe release archive entry')
            destination = target / path
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                destination.chmod(0o755)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as output:
                    shutil.copyfileobj(archive.extractfile(member), output)
                destination.chmod(0o644)


def topics_scope(path):
    # Parse a literal dictionary; never execute even local topics.py as root.
    import ast
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    nodes = [n for n in tree.body if isinstance(n, ast.Assign)
             and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id == 'TOPIC_SETTINGS']
    if len(nodes) != 1 or len(tree.body) != 1:
        raise Refused('TEST topics must contain only one literal TOPIC_SETTINGS assignment')
    topics = ast.literal_eval(nodes[0].value)
    scope = {(chat, thread) for chat, threads in topics.items() for thread in threads}
    mini = {(chat, thread) for chat, threads in topics.items() for thread, cfg in threads.items() if cfg.get('mini')}
    if scope != TEST_SCOPE or mini != MINI_SCOPE:
        raise Refused('Topics contain non-TEST scope')


def unit_state(unit):
    output = run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,MainPID,NRestarts,User,WorkingDirectory,ExecStart,FragmentPath,DropInPaths,ProtectSystem,ProtectHome,NoNewPrivileges,PrivateTmp,PrivateDevices,ReadWritePaths,SupplementaryGroups,CapabilityBoundingSet,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,EnvironmentFiles']).stdout
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def stop_stack():
    for unit in UNITS:
        if unit_state(unit).get('LoadState') not in ('not-found', None):
            run(['systemctl', 'stop', unit])
            if unit_state(unit).get('ActiveState') not in ('inactive', 'failed'):
                raise Refused('TEST unit did not stop')


def redact(text):
    # No runtime stdout/journal enters reports before complete env-value redaction.
    env = (CONFIG / '.env').read_text(encoding='utf-8')
    for line in env.splitlines():
        if '=' in line:
            value = line.split('=', 1)[1].strip().strip('\'"')
            if len(value) >= 4:
                text = text.replace(value, '[REDACTED]')
    return re.sub(r'\b\d{5,20}:[A-Za-z0-9_-]+', '[REDACTED]', text)


def save_report(folder, report):
    report['manual_telegram_scenarios'] = MANUAL
    report['manual_acceptance'] = 'NOT_RUN'
    report['release_manifest'] = {
        'sha': report.get('sha'), 'ci': report.get('ci'), 'test_commands': CHECKS,
        'test_files': report.get('test_files', []), 'migration_files': report.get('migration_files', []),
        'dependencies': report.get('dependencies'),
        'migrations': ['check_bot.py (TEST only)', 'verify-v142-migration.py on copies',
                       'tools/validate_pvp_migration.py', 'verify-v142-rollback.py on copy of TEST'],
        'rollback': 'Manual only: pause-new/drain, frozen pvp_ops rollback-check at all three gates; preserve current DB; no automatic DB restore',
        'production_preflight': 'docs/deployment.md and docs/operations/STAGING-AUTOMATION.md; production only origin/main via sudo deploy-shpakdnd after separate approval',
    }
    encoded = json.dumps(report, ensure_ascii=True, indent=2) + '\n'
    temporary = folder / 'report.json.tmp'
    temporary.write_text(encoded, encoding='utf-8')
    temporary.replace(folder / 'report.json')
    markdown = '# STAGING report\n\n'
    markdown += f"Readiness for manual acceptance: **{report['acceptance_ready']}**\n\n"
    markdown += 'Machine-readable evidence:\n\n```json\n' + encoded + '```\n\n'
    markdown += 'Telegram checks (NOT RUN):\n\n' + '\n'.join('- ' + item for item in MANUAL) + '\n'
    (folder / 'report.md').write_text(markdown, encoding='utf-8')


class Controller:
    def __init__(self, api):
        self.api = api

    def evidence(self, pr, sha):
        return verify(self.api, pr, sha)

    def preflight(self, folder, sha, old, image):
        args = ['docker', 'run', '--rm', '--name', 'shpakdnd-preflight', '--pull=never',
                '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--user=65534:65534', '--pids-limit=256', '--memory=3g', '--memory-swap=3g',
                '--log-driver=none', '--cpus=2',
                '--tmpfs=/tmp:rw,nosuid,nodev,size=4g,mode=1777',
                '--mount', f'type=bind,src={BASE / "repo.git"},dst=/repo,readonly',
                '--mount', f'type=bind,src={BUNDLE},dst=/trusted,readonly',
                '--mount', f'type=bind,src={folder / "input"},dst=/input,readonly',
                '--entrypoint=python', image, '-I', '/trusted/preflight.py', sha, old]
        log_path = folder / 'preflight.log'
        started = time.monotonic()
        try:
            with log_path.open('xb') as log:
                process = subprocess.Popen([str(a) for a in args], stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
                try:
                    while process.poll() is None:
                        if time.monotonic() - started > 3600 or log_path.stat().st_size > 20_000_000:
                            raise Refused('Preflight exceeded time/log budget')
                        time.sleep(0.25)
                    code = process.wait(timeout=10)
                except BaseException:
                    run(['docker', 'rm', '-f', 'shpakdnd-preflight'], check=False)
                    process.kill()
                    process.wait(timeout=10)
                    raise
            if code:
                raise Refused('Isolated full preflight failed; see preflight.log')
            output = log_path.read_text(encoding='utf-8', errors='replace')
            result = json.loads(output.splitlines()[-1])
            if result.get('preflight') != 'PASS' or any(r['exit_code'] for r in result['checks']):
                raise Refused('Preflight did not supply complete successful evidence')
            return {'status': 'PASS', 'checks': result['checks'], 'log': 'preflight.log',
                    'migration_copy': 'PASS', 'old_code_startup_copy': 'PASS', 'network': 'none',
                    'unittest_results': re.findall(r'Ran [0-9]+ tests? in [^\n]+', output),
                    'validators': [c for c in CHECKS if 'validate' in ' '.join(c)]}
        except BaseException:
            run(['docker', 'rm', '-f', 'shpakdnd-preflight'], check=False)
            raise

    def deploy(self, pr, sha, request_id, folder):
        report = {'schema_version': 1, 'version': 'staging-automation-v1', 'pr': int(pr), 'sha': sha,
                  'request_id': request_id, 'started_at': datetime.now(timezone.utc).isoformat(), 'acceptance_ready': 'FAIL', 'errors': [],
                  'risks': ['Feature code is untrusted; shared-kernel container isolation is required',
                            'Old-code startup compatibility does not authorize economic rollback',
                            'Telegram manual acceptance is outstanding'], 'phase': 'admission'}
        stopping = False
        try:
            if socket.gethostname() != 'VM-338333':
                raise Refused('Hostname is not VM-338333')
            report['hostname'] = socket.gethostname()
            for path in (BASE, BASE / 'repo.git', CONFIG, BUNDLE, folder):
                trusted(path, directory=True)
            for path in (CONFIG / 'settings.json', CONFIG / '.env', CONFIG / 'topics.py',
                         CONFIG / 'runtime.env', CONFIG / 'requirements.txt', CONFIG / 'pip-freeze.txt', BASE / 'venv'):
                trusted(path)
            for path in (CONFIG / 'editorial', *(CONFIG / 'editorial').rglob('*')):
                trusted(path)
            settings = json.loads((CONFIG / 'settings.json').read_text())
            image = settings['preflight_image']
            if not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}', image):
                raise Refused('Preflight image must be an administrator-built digest-pinned image')
            topics_scope(CONFIG / 'topics.py')
            report['ci'] = self.evidence(pr, sha)
            current = (BASE / 'current').resolve(strict=True)
            if current.parent != BASE / 'releases':
                raise Refused('Current release is outside TEST releases')
            trusted(current, directory=True)
            old = sha_value((current / '.revision').read_text().strip())
            report['previous_sha'] = old
            before = {u: unit_state(u) for u in UNITS}
            required = {'ProtectSystem': 'strict', 'ProtectHome': 'yes', 'NoNewPrivileges': 'yes',
                        'PrivateTmp': 'yes', 'PrivateDevices': 'yes',
                        'ReadWritePaths': str(STATE / 'data'), 'SupplementaryGroups': '', 'CapabilityBoundingSet': '',
                        'ExecStartPre': '', 'ExecStartPost': '', 'ExecStop': '', 'ExecStopPost': ''}
            if any(before[BOT].get(key) != value for key, value in required.items()):
                raise Refused('TEST service hardening differs from required isolation')
            report['systemd_before'] = before
            if (before[BOT].get('User') != 'shpakbot'
                    or before[BOT].get('WorkingDirectory') != str(BASE / 'current')
                    or str(BASE / 'venv/bin/python') + ' bot.py' not in before[BOT].get('ExecStart', '')):
                raise Refused('Unexpected TEST service identity, path or executable')
            # Watcher remains disabled: its legacy updater switches to main and is not a feature deployer.
            for u in (WATCH, UPDATE):
                if run(['systemctl', 'is-enabled', u], check=False).stdout.strip() != 'masked':
                    raise Refused('Legacy TEST watcher/update must be masked during one-time setup')
            database = STATE / 'data/shpakdnd.db'
            report['sqlite_before'] = db_check(database, scope=True)
            run(['git', '--git-dir', BASE / 'repo.git', 'fetch', 'origin',
                 f'+refs/pull/{pr}/head:refs/staging/{request_id}', '+refs/heads/main:refs/heads/main'])
            actual = run(['git', '--git-dir', BASE / 'repo.git', 'rev-parse', f'refs/staging/{request_id}']).stdout.strip()
            if actual != sha:
                raise Refused('Fetched PR HEAD differs from approved SHA')
            archive = subprocess.run(['git', '--git-dir', str(BASE / 'repo.git'), 'archive', sha],
                                     check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120).stdout
            release = BASE / 'releases' / request_id
            release.mkdir(mode=0o755)
            release.chmod(0o755)
            safe_extract(archive, release)
            (release / '.revision').write_text(sha + '\n')
            paths = run(['git', '--git-dir', BASE / 'repo.git', 'ls-tree', '-r', '--name-only', sha]).stdout.splitlines()
            report['test_files'] = [p for p in paths if p.startswith('tests/') and p.endswith('.py')]
            report['migration_files'] = [p for p in paths if '/migrations/' in p and p.endswith('.py')]
            report['git_tree'] = run(['git', '--git-dir', BASE / 'repo.git', 'rev-parse', sha + '^{tree}']).stdout.strip()
            approved_freeze = (CONFIG / 'pip-freeze.txt').read_text().strip().splitlines()
            actual_freeze = run(['runuser', '-u', 'shpakbot', '--', BASE / 'venv/bin/python', '-I', '-m', 'pip', 'freeze', '--all']).stdout.strip().splitlines()
            if sorted(actual_freeze) != sorted(approved_freeze):
                raise Refused('TEST virtualenv differs from approved dependency freeze')
            report['dependencies'] = {'requirements_sha256': hashlib.sha256((release / 'requirements.txt').read_bytes()).hexdigest(),
                                      'preflight_image': image, 'automatic_install': False,
                                      'approved_freeze_sha256': hashlib.sha256((CONFIG / 'pip-freeze.txt').read_bytes()).hexdigest()}
            # Dependency drift must be prepared by administrator, never pip install feature code as root.
            if (release / 'requirements.txt').read_bytes() != (CONFIG / 'requirements.txt').read_bytes():
                raise Refused('Dependencies differ from administrator-approved TEST environment')
            input_dir = folder / 'input'
            input_dir.mkdir(mode=0o755)
            input_dir.chmod(0o755)
            report['preflight_snapshot'] = backup(database, input_dir / 'source.db')
            (input_dir / 'source.db').chmod(0o644)
            shutil.copyfile(CONFIG / 'topics.py', input_dir / 'topics.py')
            shutil.copyfile(CONFIG / 'pip-freeze.txt', input_dir / 'pip-freeze.txt')
            (input_dir / 'pip-freeze.txt').chmod(0o644)
            (input_dir / 'topics.py').chmod(0o644)
            shutil.copytree(CONFIG / 'editorial', input_dir / 'editorial')
            for item in (input_dir / 'editorial', *(input_dir / 'editorial').rglob('*')):
                item.chmod(0o755 if item.is_dir() else 0o644)
            report['phase'] = 'preflight'
            save_report(folder, report)
            report['preflight'] = self.preflight(folder, sha, old, image)
            # A final evidence collection closes force-push/rerun races before stopping TEST.
            report['ci'] = self.evidence(pr, sha)
            report['backup'] = backup(database, folder / 'before.db')
            private = folder / 'config'
            private.mkdir(mode=0o700)
            for name in ('.env', 'topics.py', 'settings.json', 'requirements.txt', 'runtime.env', 'pip-freeze.txt'):
                shutil.copyfile(CONFIG / name, private / name)
            shutil.copytree(CONFIG / 'editorial', private / 'editorial')
            report['saved_test_configuration'] = True
            report['phase'] = 'stop'
            save_report(folder, report)
            stopping = True
            stop_stack()
            # Re-snapshot after the writers stop: before.db remains the verified online backup.
            report['quiescent_backup'] = backup(database, folder / 'stopped.db')
            shutil.copyfile(CONFIG / 'topics.py', release / 'app/topics.py')
            (release / '.env').symlink_to(CONFIG / '.env')
            (release / 'shpakdnd.db').symlink_to(database)
            (release / 'timers.db').symlink_to(STATE / 'data/timers.db')
            pending = BASE / 'next'
            pending.symlink_to(release)
            pending.replace(BASE / 'current')
            report['installed_sha'] = sha
            report['phase'] = 'startup_checks'
            # Feature code only runs under the hardened TEST service account. Root imports nothing.
            startup = run(['systemd-run', '--unit=shpakdnd-staging-startup', '--wait', '--pipe', '--collect',
                           '--property=User=shpakbot', '--property=Group=shpakbot',
                           '--property=WorkingDirectory=' + str(BASE / 'current'),
                           '--property=ProtectSystem=strict', '--property=ProtectHome=yes',
                           '--property=PrivateTmp=yes', '--property=PrivateDevices=yes',
                           '--property=NoNewPrivileges=yes', '--property=RestrictSUIDSGID=yes',
                           '--property=CapabilityBoundingSet=', '--property=ProtectKernelTunables=yes',
                           '--property=ProtectControlGroups=yes', '--property=RestrictAddressFamilies=AF_UNIX',
                           '--property=ReadWritePaths=' + str(STATE / 'data'),
                           '--property=EnvironmentFile=' + str(CONFIG / 'runtime.env'),
                           '--setenv=BOT_TOKEN=123456:offline-staging-check',
                           BASE / 'venv/bin/python', '-I', '-B', BUNDLE / 'startup.py'], check=False, timeout=180)
            (folder / 'startup.log').write_text(redact(startup.stdout + startup.stderr), encoding='utf-8')
            if startup.returncode:
                raise Refused('TEST startup/migration failed')
            report['startup_checks'] = 'PASS'
            report['sqlite_after_migration'] = db_check(database, scope=True)
            since = str(int(time.time()))
            run(['systemctl', 'start', BOT])
            first = None
            for _ in range(2):
                time.sleep(10)
                state = unit_state(BOT)
                if (state.get('ActiveState') != 'active' or int(state.get('MainPID', '0')) <= 0
                        or state.get('NRestarts') != '0'
                        or first and any(state[k] != first[k] for k in ('MainPID', 'NRestarts'))):
                    raise Refused('TEST bot failed PID/restart stability check')
                first = state
            journal = run(['journalctl', '-u', BOT, '--since=@' + since, '--no-pager', '-o', 'cat']).stdout
            (folder / 'journal.log').write_text(redact(journal), encoding='utf-8')
            if re.search(r'traceback|failed to start|start request repeated|main process exited', journal, re.I):
                raise Refused('TEST journal reports startup failure')
            report['systemd_after'] = {u: unit_state(u) for u in UNITS}
            report['sqlite_final'] = db_check(database, scope=True)
            report['phase'] = 'ready_for_manual_acceptance'
            report['acceptance_ready'] = 'PASS'
        except BaseException as error:
            report['errors'].append(str(error) if isinstance(error, Refused) else type(error).__name__)
            if stopping:
                try:
                    stop_stack()
                except Exception:
                    report['errors'].append('Could not confirm stopped TEST stack; operator action required')
            report['recovery'] = 'Manual investigation required. No code rollback or DB restoration performed.'
        finally:
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            save_report(folder, report)
        return report


def main(argv=None):
    args = parse(' '.join(argv if argv is not None else sys.argv[1:]))
    if os.geteuid() != 0:
        raise Refused('Controller requires root')
    os.umask(0o077)
    if socket.gethostname() != 'VM-338333':
        raise Refused('Hostname is not VM-338333')
    trusted(STATE, directory=True)
    for item in (BASE, BASE / 'releases', CONFIG, BUNDLE):
        trusted(item, directory=True)
    reports = trusted(STATE / 'reports', directory=True)
    folder = reports / args[-1]
    if args[0] == 'report':
        trusted(folder, directory=True)
        # Allowlist files; never expose backup, database snapshot or saved .env.
        result = {}
        for name in ('report.json', 'report.md', 'preflight.log', 'startup.log', 'journal.log'):
            path = folder / name
            if path.is_file():
                result[name] = path.read_text(encoding='utf-8')[-4_000_000:]
        print(json.dumps(result, ensure_ascii=True))
        return 0
    with lock(STATE / 'deploy.lock'):
        if folder.exists():
            # At-most-once mutation. A crash/incomplete result requires a NEW Actions run after review.
            path = folder / 'report.json'
            if not path.exists():
                raise Refused('Request interrupted; operator review required before a new request')
            report = json.loads(path.read_text())
            if report.get('pr') != int(args[1]) or report.get('sha') != args[2]:
                raise Refused('Request ID already belongs to another target')
        else:
            folder.mkdir(mode=0o700)
            try:
                token = trusted(CONFIG / 'github-read-token').read_text().strip()
                report = Controller(GitHub(token)).deploy(*args[1:], folder)
            except Exception as error:
                report = {'pr': int(args[1]), 'sha': args[2], 'acceptance_ready': 'FAIL',
                          'errors': [str(error) if isinstance(error, Refused) else type(error).__name__]}
                save_report(folder, report)
        print(json.dumps(report, ensure_ascii=True))
        return 0 if report['acceptance_ready'] == 'PASS' else 1


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise Refused('Controller interrupted; manual review required')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(main())
