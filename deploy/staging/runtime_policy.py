"""Strict effective systemd/config policy. Rejected values never leave TEST."""
import hashlib
from pathlib import Path
import re
import shlex
from gate import Refused

EMPTY = ('Environment', 'PassEnvironment', 'UnsetEnvironment', 'PAMName', 'ExecCondition', 'ExecStartPre',
         'ExecStartPost', 'ExecReload', 'ExecStop', 'ExecStopPost', 'SupplementaryGroups', 'AmbientCapabilities',
         'CapabilityBoundingSet', 'RootDirectory', 'RootImage', 'BindPaths', 'BindReadOnlyPaths', 'TemporaryFileSystem',
         'StateDirectory', 'CacheDirectory', 'LogsDirectory', 'RuntimeDirectory', 'ConfigurationDirectory',
         'ReadOnlyPaths', 'InaccessiblePaths', 'ExecSearchPath', 'LoadCredential', 'LoadCredentialEncrypted',
         'ImportCredential', 'SetCredential', 'SetCredentialEncrypted')
FIXED = {'LoadState': 'loaded', 'User': 'shpakbot', 'Group': 'shpakbot', 'Type': 'simple', 'UMask': '0077',
         'Restart': 'on-failure', 'ProtectSystem': 'strict', 'ProtectHome': 'yes', 'NoNewPrivileges': 'yes',
         'PrivateTmp': 'yes', 'PrivateDevices': 'yes', 'RestrictSUIDSGID': 'yes', 'ProtectKernelTunables': 'yes',
         'ProtectKernelModules': 'yes', 'ProtectControlGroups': 'yes', 'DynamicUser': 'no', 'Delegate': 'no',
         'KillMode': 'control-group', 'TasksMax': '256', 'MemoryMax': '3221225472', 'CPUQuotaPerSecUSec': '2s',
         'TimeoutStopUSec': '30s', 'NeedDaemonReload': 'no'}
PROPERTIES = set(EMPTY) | set(FIXED) | {'ActiveState', 'MainPID', 'NRestarts', 'ExecStart', 'ExecStartEx',
    'WorkingDirectory', 'EnvironmentFiles', 'ReadWritePaths', 'RestrictAddressFamilies', 'FragmentPath', 'DropInPaths'}


def exec_record(value, executable, extended=False):
    if not isinstance(value, str) or not value.startswith('{ ') or not value.endswith(' }') or value.count('{') != 1 or value.count('}') != 1:
        raise Refused('Unexpected TEST ExecStart structure')
    fields = {}
    for part in value[2:-2].split(' ; '):
        if '=' not in part:
            raise Refused('Unexpected TEST ExecStart structure')
        key, item = part.split('=', 1)
        if key in fields:
            raise Refused('Duplicate TEST ExecStart field')
        fields[key] = item
    kind = 'flags' if extended else 'ignore_errors'
    if set(fields) != {'path', 'argv[]', kind, 'start_time', 'stop_time', 'pid', 'code', 'status'}:
        raise Refused('Unexpected TEST ExecStart fields')
    if fields['path'] != str(executable) or fields['argv[]'] != str(executable) + ' bot.py' or fields[kind] != ('' if extended else 'no'):
        raise Refused('TEST ExecStart command or privilege flags differ')


def expected(base, config, state):
    result = dict(FIXED, **{key: '' for key in EMPTY})
    result.update(WorkingDirectory=str(base/'current'), ReadWritePaths=str(state/'data'),
                  EnvironmentFiles=str(config/'runtime.env') + ' (ignore_errors=no)',
                  RestrictAddressFamilies='AF_UNIX AF_INET AF_INET6',
                  FragmentPath='/etc/systemd/system/shpakdnd-bot.service',
                  DropInPaths='/etc/systemd/system/shpakdnd-bot.service.d/90-staging.conf')
    return result


def validate_unit(actual, base, config, state):
    for key, value in expected(base, config, state).items():
        candidate = actual.get(key)
        if key == 'RestrictAddressFamilies' and isinstance(candidate, str):
            if len(candidate.split()) == 3 and set(candidate.split()) == set(value.split()):
                continue
        if candidate != value:
            raise Refused('TEST runtime policy mismatch')
    exec_record(actual.get('ExecStart'), base/'venv/bin/python')
    exec_record(actual.get('ExecStartEx'), base/'venv/bin/python', extended=True)


def assignments(text):
    result = {}
    for raw in text.splitlines():
        words = shlex.split(raw, comments=True)
        if not words:
            continue
        if len(words) != 1 or '=' not in words[0]:
            raise Refused('Non-canonical TEST environment file')
        key, value = words[0].split('=', 1)
        if key in result or not re.fullmatch('[A-Z][A-Z0-9_]*', key) or any(c in value for c in '$`\\\n\r'):
            raise Refused('Unsupported TEST environment assignment')
        result[key] = value
    return result


def validate_config(config, settings, manager_environment):
    if any((config/name).stat().st_size > 16384 for name in ('.env', 'runtime.env')):
        raise Refused('TEST environment file exceeds policy limit')
    runtime = assignments((config/'runtime.env').read_text(encoding='utf-8'))
    required = {'MINI_EDITORIAL_DIR': (config/'editorial').as_posix(), 'PYTHONDONTWRITEBYTECODE': '1'}
    if set(runtime) != set(required) | {'MINI_CUSTOM_EMOJI_ENABLED'} or any(runtime[k] != v for k,v in required.items()) or runtime['MINI_CUSTOM_EMOJI_ENABLED'] not in ('false', 'true'):
        raise Refused('TEST runtime environment differs from approved configuration')
    dotenv = assignments((config/'.env').read_text(encoding='utf-8'))
    if set(dotenv) - {'BOT_TOKEN', 'BOT_TIMEZONE'} or dotenv.get('BOT_TIMEZONE', 'Europe/Moscow') != 'Europe/Moscow':
        raise Refused('Unapproved TEST dotenv configuration')
    token = dotenv.get('BOT_TOKEN', '')
    bot_id = settings.get('test_bot_id')
    if type(bot_id) is not int or bot_id <= 0 or not re.fullmatch(str(bot_id) + r':[A-Za-z0-9_-]{20,}', token):
        raise Refused('TEST token does not match pinned TEST bot identity')
    inherited = assignments(manager_environment)
    for key, value in inherited.items():
        if key in ('LANG', 'LC_ALL', 'LC_CTYPE') and re.fullmatch(r'[A-Za-z0-9_.@-]{1,48}', value):
            continue
        if key == 'PATH' and value == '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin':
            continue
        if key == 'TERM' and re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', value):
            continue
        raise Refused('Unapproved systemd manager environment')
    return {name: hashlib.sha256((config/name).read_bytes()).hexdigest() for name in ('.env', 'runtime.env', 'topics.py')}


def validate_identity(uid, gid, groups):
    if not re.fullmatch(r'[1-9][0-9]{0,9}', uid.strip()) or not re.fullmatch(r'[1-9][0-9]{0,9}', gid.strip()) or groups.split() != [gid.strip()]:
        raise Refused('TEST identity must be non-root without supplementary groups')


# systemctl omits empty Exec* arrays and renders credential arrays as [unprintable].
# Require typed D-Bus proof of an empty array, never treat a missing/redacted value as empty.
STRUCTURED_EMPTY = {name:'a(sasbttttuii)' for name in
                    ('ExecCondition','ExecStartPre','ExecStartPost','ExecReload','ExecStop','ExecStopPost')}
STRUCTURED_EMPTY.update(LoadCredential='a(ss)',LoadCredentialEncrypted='a(ss)',
                        SetCredential='a(say)',SetCredentialEncrypted='a(say)')


def unit_object_path(unit):
    return '/org/freedesktop/systemd1/unit/' + ''.join(chr(b) if chr(b).isascii() and chr(b).isalnum() else '_'+format(b,'02x') for b in unit.encode('utf-8'))


def structured_empty_values(output):
    lines=output.strip().splitlines()
    if len(lines)!=len(STRUCTURED_EMPTY):
        raise Refused('Typed systemd property evidence unavailable')
    return {name: '' if line==signature+' 0' else 'NONEMPTY_OR_UNSUPPORTED'
            for (name,signature),line in zip(STRUCTURED_EMPTY.items(),lines)}
