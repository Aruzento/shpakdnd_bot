"""External, reviewed staging adapter. No game file edits and no implicit polling."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import stat
import subprocess
import sys
import time

STAGE = Path('/srv/shpakdnd-v15-staging')
PRODUCTION_CHATS = {-1003376315265, 694384548}
APP_SHA = 'd795df06207d3f6927dee3378be7cf02fc25b93e'
TEST_SCOPES = {(-1003883723333, 2), (-5320347021, 0)}
STORY_SHA256 = 'f185595d8c89fc1644e331a4ff1ae34c6df61920b4a69c9948654cd60903cce2'
APP_TREE = '64823c903daa7e3cea5d12613f364da67f630a54'
PATHS = {'STORIES_DIR': 'stories', 'MINI_EDITORIAL_DIR': 'runtime/editorial',
         'DND_SHOP_CSV_PATH': 'runtime/shop.csv', 'DND_LOOT_CSV_PATH': 'runtime/loot.csv',
         'DND_SHOP_CODE_MAP_PATH': 'runtime/loot_code_map.csv', 'DND_SHOP_OFFERS_PATH': 'runtime/shop_offers.csv'}


class Blocked(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'BLOCKED_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def absolute(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'BLOCKED_PATH')
    for item in (*path.parents, path):
        require(not item.is_symlink(), 'BLOCKED_SYMLINK')
    return path


def trusted(path, uid):
    path = absolute(path)
    require(path.is_file(), 'BLOCKED_PRIVATE_CONFIG_MISSING')
    if os.name == 'posix':
        info = path.stat()
        require(info.st_uid == uid and not info.st_mode & 0o222 and info.st_nlink == 1, 'BLOCKED_CONFIG_PERMISSIONS')
        # A read-only root file in a bot-writable directory is not trusted.
        for parent in path.parents:
            info = parent.stat()
            require((info.st_uid in (0, uid) and not info.st_mode & 0o022) or info.st_mode & stat.S_ISVTX,
                    'BLOCKED_CONFIG_PARENT_PERMISSIONS')
    return path


def topics(raw, allowed):
    require(isinstance(raw, dict) and raw, 'BLOCKED_EMPTY_TOPICS')
    result = {}
    scope_set = set()
    for chat, entries in raw.items():
        require(isinstance(chat, str) and re.fullmatch(r'-[1-9][0-9]*', chat), 'BLOCKED_CHAT_ID')
        chat = int(chat)
        require(chat not in PRODUCTION_CHATS, 'BLOCKED_PRODUCTION_SCOPE')
        require(isinstance(entries, dict) and entries, 'BLOCKED_TOPIC_MAP')
        result[chat] = {}
        for thread, value in entries.items():
            require(isinstance(thread, str) and re.fullmatch(r'0|[1-9][0-9]*', thread), 'BLOCKED_THREAD_ID')
            thread = int(thread)
            require((chat, thread) in allowed, 'BLOCKED_UNAPPROVED_SCOPE')
            require(isinstance(value, dict) and set(value) <= {'admin', 'characters', 'mini', 'mini_name', 'shop_currency_units'}, 'BLOCKED_TOPIC_FIELDS')
            require(isinstance(value.get('admin'), str) and re.fullmatch(r'@[A-Za-z0-9_]{5,32}', value['admin']), 'BLOCKED_ADMIN')
            require(type(value.get('mini', False)) is bool, 'BLOCKED_MINI_FLAG')
            require(isinstance(value.get('characters'), dict), 'BLOCKED_CHARACTERS')
            for username, name in value['characters'].items():
                require(re.fullmatch(r'@[A-Za-z0-9_]{5,32}', username) and isinstance(name, str) and name.strip(), 'BLOCKED_CHARACTER')
            require(len({k.lower() for k in value['characters']}) == len(value['characters']), 'BLOCKED_USERNAME_ALIAS')
            result[chat][thread] = value
            scope_set.add((chat, thread))
    require(scope_set == allowed, 'BLOCKED_SCOPE_SET')
    require(any(v.get('mini') for entries in result.values() for v in entries.values()) and
            any(not v.get('mini') for entries in result.values() for v in entries.values()), 'BLOCKED_MINI_DND_REQUIRED')
    return result


def validate(policy_file, *, stage_root=STAGE, trusted_uid=0, hostname=None, now=None):
    root = absolute(stage_root)
    policy_file = trusted(policy_file, trusted_uid)
    require(policy_file.parent == root / 'config', 'BLOCKED_POLICY_LOCATION')
    policy = read_json(policy_file)
    fields = {'schema_version', 'candidate_sha', 'env_sha256', 'topics_sha256', 'launcher_sha256',
              'expected_bot_id', 'allowed_scopes', 'mode', 'tooling_sha'}
    require(set(policy) == fields and type(policy['schema_version']) is int and policy['schema_version'] == 1, 'BLOCKED_POLICY_SCHEMA')
    require((hostname or socket.gethostname()) == 'VM-338333', 'BLOCKED_NOT_TEST_HOST')
    code = absolute(root / 'code')
    require(code.is_dir() and Path.cwd().resolve() == code, 'BLOCKED_CODE_CWD')
    require(policy['candidate_sha'] == APP_SHA, 'BLOCKED_CANDIDATE_SHA')
    require(policy['mode'] == 'replace_existing_test' and isinstance(policy['tooling_sha'], str) and re.fullmatch('[0-9a-f]{40}', policy['tooling_sha']), 'BLOCKED_TOOLING_POLICY')
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-C', str(code), *args], stderr=subprocess.PIPE).decode().strip()
    require(git('rev-parse', 'HEAD') == policy['candidate_sha'] and not git('status', '--porcelain', '--untracked-files=all'), 'BLOCKED_CODE_CHANGED')
    require(git('rev-parse', 'HEAD:app') == APP_TREE, 'BLOCKED_GAME_TREE_CHANGED')
    script = trusted(root / 'config/staging-launch.py', trusted_uid)
    require(sha(script) == policy['launcher_sha256'], 'BLOCKED_LAUNCHER_CHANGED')
    env_file = absolute(code / '.env')
    require(env_file.is_file() and sha(env_file) == policy['env_sha256'], 'BLOCKED_ENV_CHANGED')
    if os.name == 'posix':
        require(not env_file.stat().st_mode & 0o077, 'BLOCKED_ENV_PERMISSIONS')
    from dotenv import dotenv_values
    env = dotenv_values(env_file, interpolate=False)
    require(set(env) <= set(PATHS) | {'BOT_TOKEN', 'BOT_TIMEZONE'}, 'BLOCKED_ENV_FIELDS')
    token = env.get('BOT_TOKEN')
    require(isinstance(token, str) and re.fullmatch(r'[1-9][0-9]{4,19}:[A-Za-z0-9_-]{20,100}', token), 'BLOCKED_TOKEN_FORMAT')
    bot_id = int(token.split(':', 1)[0])
    require(type(policy['expected_bot_id']) is int and bot_id == policy['expected_bot_id'], 'BLOCKED_TOKEN_IDENTITY')
    require(os.getenv('BOT_TOKEN', token) == token, 'BLOCKED_TOKEN_OVERRIDE')
    for key, suffix in PATHS.items():
        expected = absolute(root / suffix)
        require(env.get(key) == str(expected) and os.getenv(key, str(expected)) == str(expected), 'BLOCKED_RUNTIME_PATH:' + key)
    story = trusted(root / 'stories/tayna_propavshey_ovtsy.json', trusted_uid)
    require(sha(story) == STORY_SHA256 and sorted(p.name for p in story.parent.iterdir()) == [story.name], 'BLOCKED_STORY_CONTENT')
    inventory_file = trusted(root / 'config/test-inventory.json', trusted_uid)
    observed = read_json(inventory_file)
    timestamp = observed.get('observed_at')
    require(type(timestamp) is int and 0 <= (time.time() if now is None else now) - timestamp <= 300, 'BLOCKED_STALE_INVENTORY')
    require(type(observed.get('schema_version')) is int and observed['schema_version'] == 1 and observed.get('hostname') == 'VM-338333' and observed.get('polling_inventory_complete') is True and observed.get('unknown_bot_processes') == 0, 'BLOCKED_TEST_NOT_VERIFIED')
    require(type(observed.get('existing_bot_id')) is int and bot_id == observed['existing_bot_id'], 'BLOCKED_EXISTING_TEST_TOKEN_MISMATCH')
    service = observed.get('service', {})
    active = service.get('ActiveState') == 'active' and service.get('MainPID') == str(next(iter(observed.get('process_ids', [])), -1)) and len(observed.get('process_ids', [])) == 1
    stopped = service.get('ActiveState') == 'inactive' and service.get('MainPID') == '0' and observed.get('process_ids') == []
    require(active or stopped, 'BLOCKED_EXISTING_TEST_STATE')
    allowed = policy['allowed_scopes']
    require(isinstance(allowed, list) and all(isinstance(pair, list) and len(pair) == 2 and all(type(x) is int for x in pair) for pair in allowed), 'BLOCKED_ALLOWED_SCOPES')
    allow_set = {tuple(pair) for pair in allowed}
    require(len(allow_set) == len(allowed) and not ({chat for chat, _ in allow_set} & PRODUCTION_CHATS), 'BLOCKED_PRODUCTION_SCOPE')
    existing = observed.get('existing_scopes')
    saved = observed.get('saved_scopes')
    for entries in (existing, saved):
        require(isinstance(entries, list) and all(isinstance(pair, list) and len(pair) == 2 and all(type(x) is int for x in pair) for pair in entries), 'BLOCKED_TEST_SCOPE_MISMATCH')
        require(len(entries) == len({tuple(pair) for pair in entries}), 'BLOCKED_TEST_SCOPE_MISMATCH')
    require(allow_set == TEST_SCOPES and {tuple(pair) for pair in saved} == TEST_SCOPES and
            {tuple(pair) for pair in existing} in (TEST_SCOPES, {(-1003883723333, 2)}), 'BLOCKED_TEST_SCOPE_MISMATCH')
    topics_file = trusted(root / 'config/topic-settings.json', trusted_uid)
    require(sha(topics_file) == policy['topics_sha256'], 'BLOCKED_TOPICS_CHANGED')
    settings = topics(read_json(topics_file), allow_set)
    for name in ('shpakdnd.db', 'timers.db'):
        file = absolute(code / name)
        require(not file.exists() or (file.is_file() and file.stat().st_nlink == 1), 'BLOCKED_DB_NOT_PRIVATE')
    return dict(code=code, root=root, env=env, settings=settings, bot_id=bot_id, policy=policy,
                stopped=stopped, observed_at=timestamp,
                fingerprint={name: sha(file) for name, file in [('policy', policy_file), ('env', env_file), ('topics', topics_file), ('inventory', inventory_file)]})


def inject(state):
    # Assignment after importing worlds/context would leave captured aliases.
    require(not any(name.startswith('app.') for name in sys.modules), 'BLOCKED_APP_ALREADY_IMPORTED')
    sys.path.insert(0, str(state['code']))
    import app.topics as module
    require(Path(module.__file__).resolve() == state['code'] / 'app/topics.py', 'BLOCKED_APP_ORIGIN')
    module.TOPIC_SETTINGS.clear()
    module.TOPIC_SETTINGS.update(state['settings'])
    for key, value in state['env'].items():
        if value is not None:
            os.environ[key] = value
    import app.config as config
    require(config.DB_PATH == state['code'] / 'shpakdnd.db' and config.LEGACY_DB_PATH == state['code'] / 'timers.db', 'BLOCKED_DB_PATH')


def no_other_polling(proc=Path('/proc')):
    """Fail closed if any other recognized bot process is present, regardless of token."""
    for directory in proc.iterdir():
        if not directory.name.isdecimal() or int(directory.name) == os.getpid():
            continue
        try:
            args = [part.decode(errors='replace') for part in (directory / 'cmdline').read_bytes().split(b'\0') if part]
            require(not any(Path(arg).name in ('bot.py', 'staging-launch.py', 'staging_launcher.py') for arg in args), 'BLOCKED_OTHER_POLLING_PROCESS')
        except FileNotFoundError:
            continue
        except PermissionError:
            raise Blocked('BLOCKED_UNREADABLE_PROCESS') from None


def main(argv=None, *, stage_root=STAGE, trusted_uid=0, hostname=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--verify', action='store_true', help='pure config check; does not open SQLite')
    group.add_argument('--describe', action='store_true', help='redacted verified fingerprint for root approval; no SQLite or Telegram')
    group.add_argument('--check', action='store_true', help='check_bot on candidate-only SQLite; no polling')
    group.add_argument('--poll', action='store_true', help='requires fresh root approval and per-bot lock')
    args = parser.parse_args(argv)
    try:
        state = validate(Path(stage_root) / 'config/policy.json', stage_root=stage_root, trusted_uid=trusted_uid, hostname=hostname)
        if args.describe:
            print(json.dumps(dict(candidate_sha=state['policy']['candidate_sha'], fingerprint=state['fingerprint'], bot_id=state['bot_id'], stopped=state['stopped'])))
            return 0
        if args.verify:
            print('PASS: candidate configuration; SQLite and Telegram not opened')
            return 0
        if args.poll:
            require(state['stopped'] and 0 <= time.time() - state['observed_at'] <= 60, 'BLOCKED_LEGACY_NOT_STOPPED')
            require(os.name == 'posix' and not os.getenv('PYTHONPATH') and not os.getenv('DND_CHECK_SANDBOX'), 'BLOCKED_POLLING_ENVIRONMENT')
            approval = read_json(trusted(Path(stage_root) / 'config/start-approval.json', trusted_uid))
            require(set(approval) == {'approved_at', 'candidate_sha', 'fingerprint', 'polling_approved', 'bot_id'}, 'BLOCKED_START_APPROVAL')
            require(approval['polling_approved'] is True and approval['candidate_sha'] == state['policy']['candidate_sha'] and approval['bot_id'] == state['bot_id'] and approval['fingerprint'] == state['fingerprint'], 'BLOCKED_START_APPROVAL')
            require(type(approval['approved_at']) is int and 0 <= time.time() - approval['approved_at'] <= 60, 'BLOCKED_STALE_START_APPROVAL')
            import fcntl
            lock_file = Path(stage_root) / ('locks/polling-' + str(state['bot_id']) + '.lock')
            absolute(lock_file)
            parent_info = lock_file.parent.stat()
            require(parent_info.st_uid == trusted_uid and not parent_info.st_mode & 0o022, 'BLOCKED_POLLING_LOCK_DIRECTORY')
            fd = os.open(lock_file, os.O_RDWR | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == trusted_uid and not info.st_mode & 0o007, 'BLOCKED_POLLING_LOCK')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                no_other_polling()
                inject(state)
                runpy.run_path(str(state['code'] / 'bot.py'), run_name='__main__')
            finally:
                os.close(fd)
        else:
            inject(state)
            runpy.run_path(str(state['code'] / 'check_bot.py'), run_name='__main__')
        return 0
    except Exception as error:
        # Never dump .env, token, private config or subprocess stderr.
        reason = str(error) if isinstance(error, Blocked) else 'BLOCKED_STAGING_NOT_VERIFIED'
        print(reason, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
