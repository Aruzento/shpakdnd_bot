"""Read-only TEST inventory. Never starts services, polls, opens SQLite or prints tokens."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

HOST = 'VM-338333'
PROJECT = Path('/opt/shpakdnd-v144-stage')
LEGACY_DB = Path('/opt/shpakdnd-bot/shpakdnd.db')
TEST_SCOPES = {(-1003883723333, 2), (-5320347021, 0)}
PRODUCTION_CHATS = {-1003376315265, 694384548}


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE,
                                   env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'), timeout=15).strip()


def literal_topics(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'TOPIC_SETTINGS' for target in node.targets)]
    if len(values) != 1:
        raise ValueError('BLOCKED_DYNAMIC_TOPICS')
    data = ast.literal_eval(values[0])
    if not isinstance(data, dict):
        raise ValueError('BLOCKED_TOPICS_TYPE')
    return data


def token_id(token):
    if not isinstance(token, str) or not re.fullmatch(r'[1-9][0-9]{4,19}:[A-Za-z0-9_-]{20,100}', token):
        raise ValueError('BLOCKED_TOKEN_FORMAT')
    return int(token.split(':', 1)[0])


def inventory(*, hostname=None, project=PROJECT, run=command, proc=Path('/proc'), now=None, saved_topics=None, legacy_db=LEGACY_DB):
    if (hostname or socket.gethostname()) != HOST:
        raise ValueError('BLOCKED_NOT_TEST_HOST')
    project = Path(project)
    if not project.is_absolute() or '..' in project.parts or any(parent.is_symlink() for parent in project.parents):
        raise ValueError('BLOCKED_TEST_PATH')
    for path in (project, project / '.env', project / 'app/topics.py'):
        if path.is_symlink() or not path.exists():
            raise ValueError('BLOCKED_TEST_PATH')
    db = project / 'shpakdnd.db'
    if not db.is_file() or (db.is_symlink() and (any(p.is_symlink() for p in (Path(legacy_db), *Path(legacy_db).parents)) or db.resolve(strict=True) != Path(legacy_db))):
        raise ValueError('BLOCKED_TEST_DB_LINK')
    from dotenv import dotenv_values
    values = dotenv_values(project / '.env', interpolate=False)
    bot = token_id(values.get('BOT_TOKEN'))
    settings = literal_topics(project / 'app/topics.py')
    if not all(type(chat) is int and isinstance(entries, dict) and all(type(thread) is int for thread in entries) for chat, entries in settings.items()):
        raise ValueError('BLOCKED_TEST_SCOPE_TYPES')
    scopes = {(chat, thread) for chat, topics in settings.items() for thread in topics}
    if scopes != TEST_SCOPES or set(settings) & PRODUCTION_CHATS:
        raise ValueError('BLOCKED_TEST_SCOPES_CHANGED')
    service = {}
    for key in ('ActiveState', 'SubState', 'MainPID', 'NRestarts', 'User', 'WorkingDirectory', 'FragmentPath'):
        service[key] = run('systemctl', 'show', 'shpakdnd-bot.service', '-p', key, '--value')
    if service['WorkingDirectory'] != str(project):
        raise ValueError('BLOCKED_SERVICE_PROJECT')
    # Inspect unit identity without logging Environment or ExecStart arguments.
    unit = run('systemctl', 'cat', 'shpakdnd-bot.service')
    service['definition_sha256'] = hashlib.sha256(unit.encode()).hexdigest()
    service['execstart_sha256'] = hashlib.sha256(run('systemctl', 'show', 'shpakdnd-bot.service', '-p', 'ExecStart', '--value').encode()).hexdigest()
    environment = run('systemctl', 'show', 'shpakdnd-bot.service', '-p', 'Environment', '--value')
    import shlex
    overrides = dict(item.split('=', 1) for item in shlex.split(environment) if '=' in item)
    if overrides.get('BOT_TOKEN', values['BOT_TOKEN']) != values['BOT_TOKEN']:
        raise ValueError('BLOCKED_SERVICE_TOKEN_OVERRIDE')
    env_files = run('systemctl', 'show', 'shpakdnd-bot.service', '-p', 'EnvironmentFiles', '--value')
    if env_files and env_files != str(project / '.env') + ' (ignore_errors=no)':
        raise ValueError('BLOCKED_UNREVIEWED_ENVIRONMENT_FILES')
    processes, unknown, unreadable = [], 0, 0
    for directory in proc.iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            args = [part.decode(errors='replace') for part in (directory / 'cmdline').read_bytes().split(b'\0') if part]
            if not any(Path(arg).name in ('bot.py', 'staging-launch.py', 'staging_launcher.py') for arg in args):
                continue
            cwd = (directory / 'cwd').resolve(strict=True)
            if cwd == project:
                processes.append(int(directory.name))
            else:
                unknown += 1
        except FileNotFoundError:
            continue  # Process exited during the read-only scan.
        except PermissionError:
            unreadable += 1
    active_pid = int(service['MainPID'] or 0)
    active = service['ActiveState'] == 'active' and active_pid in processes and len(processes) == 1
    stopped = service['ActiveState'] == 'inactive' and active_pid == 0 and not processes
    if not (active or stopped) or unknown or unreadable:
        raise ValueError('BLOCKED_POLLING_INVENTORY_INCOMPLETE')
    db = project / 'shpakdnd.db'
    saved = saved_topics if saved_topics is not None else Path('/etc/shpakdnd-staging/topics.py')
    saved_hash = None
    if saved.is_file() and not saved.is_symlink():
        if literal_topics(saved) != settings:
            raise ValueError('BLOCKED_SAVED_TOPICS_DIFFER')
        saved_hash = hashlib.sha256(saved.read_bytes()).hexdigest()
    else:
        raise ValueError('BLOCKED_SAVED_TOPICS_MISSING')
    def git(*args):
        return run('git', '--no-optional-locks', '-C', str(project), *args)
    return dict(schema_version=1, hostname=HOST, observed_at=int(time.time() if now is None else now),
                existing_bot_id=bot, existing_scopes=[list(s) for s in sorted(scopes)],
                service=service, process_ids=processes, polling_inventory_complete=True,
                unknown_bot_processes=0, git_sha=git('rev-parse', 'HEAD'),
                git_status=git('status', '--porcelain', '--untracked-files=no'),
                topics_sha256=hashlib.sha256((project / 'app/topics.py').read_bytes()).hexdigest(),
                saved_topics_sha256=saved_hash, env_sha256=hashlib.sha256((project / '.env').read_bytes()).hexdigest(),
                paths={key: values.get(key) for key in ('STORIES_DIR', 'MINI_EDITORIAL_DIR', 'DND_SHOP_CSV_PATH',
                       'DND_LOOT_CSV_PATH', 'DND_SHOP_CODE_MAP_PATH', 'DND_SHOP_OFFERS_PATH')},
                sqlite_metadata=dict(path=str(db), resolved_path=str(db.resolve(strict=True)), symlink=db.is_symlink(), bytes=db.stat().st_size, mtime_ns=db.stat().st_mtime_ns),
                sqlite_opened=False, services_changed=False, token_disclosed=False)


def main():
    try:
        print(json.dumps(inventory(), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # No raw subprocess stderr, config values, command arguments or tokens.
        print(json.dumps(dict(status='BLOCKED', reason=str(error) if isinstance(error, ValueError) and re.fullmatch(r'BLOCKED_[A-Z_]+', str(error)) else 'TEST_INVENTORY_NOT_VERIFIED',
                              services_changed=False, sqlite_opened=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
