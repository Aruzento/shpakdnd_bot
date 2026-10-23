"""Public v2 report boundary: construct values, never forward diagnostic strings."""
import json
import re
from inventory import CHECKS, MANUAL

PHASES = {'admission', 'capacity', 'preflight', 'stop', 'startup_checks', 'ready_for_manual_acceptance'}
STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'}


def number(value, maximum=2**53-1):
    return value if type(value) is int and 0 <= value <= maximum else None


def digest(value, size=40):
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) else None


def project(source):
    if not isinstance(source, dict):
        source = {}
    current = type(source.get('schema_version')) is int and source.get('schema_version') == 2
    phase = source.get('phase') if isinstance(source.get('phase'), str) and source['phase'] in PHASES else 'admission'
    ready = current and source.get('acceptance_ready') == 'PASS' and phase == 'ready_for_manual_acceptance'
    ci = source.get('ci') if isinstance(source.get('ci'), dict) else {}
    result = {
        'schema_version': 2, 'pr': number(source.get('pr'), 9999999999), 'sha': digest(source.get('sha')),
        'request_id': source.get('request_id') if isinstance(source.get('request_id'), str) and re.fullmatch(r'[1-9][0-9]{0,19}-[1-9][0-9]{0,4}', source['request_id']) else None,
        'acceptance_ready': 'PASS' if ready else 'FAIL', 'phase': phase,
        'version': 'git:' + (digest(source.get('sha')) or 'unknown'),
        'ci': {'run_id': number(ci.get('ci_run') or ci.get('run_id')), 'attempt': number(ci.get('ci_attempt') or ci.get('attempt'), 99999)},
        'previous_sha': digest(source.get('previous_sha')), 'installed_sha': digest(source.get('installed_sha')),
        'manual_acceptance': 'NOT_RUN', 'manual_telegram_scenarios': list(MANUAL),
        'errors': [] if ready else ['DEPLOYMENT_NOT_READY'],
        'private_diagnostics': 'ROOT_ONLY_ON_TEST',
        'runtime_verified': source.get('runtime_verified') is True,
        'configuration_verified': source.get('configuration_verified') is True,
        'release_manifest': {'sha': digest(source.get('sha')), 'test_commands': CHECKS,
                             'rollback': 'MANUAL_ONLY_NO_DATABASE_RESTORE', 'production_authorized': False},
    }
    status = source.get('preflight') if isinstance(source.get('preflight'), dict) else {}
    result['preflight'] = {'status': status.get('status') if status.get('status') in ('PASS', 'FAIL') else 'NOT_RUN',
                           'skip_policy': 'PASS' if status.get('skip_policy') == 'PASS' else 'NOT_RUN',
                           'unexplained_skips': 0 if status.get('skip_policy') == 'PASS' else None}
    result['backups'] = {}
    for key in ('preflight_snapshot', 'backup', 'quiescent_backup'):
        value = source.get(key) or (source.get('backups', {}).get(key) if isinstance(source.get('backups'), dict) else None)
        if isinstance(value, dict):
            result['backups'][key] = {'verified': value.get('verified') is True or (value.get('integrity') == 'ok' and value.get('foreign_keys') == 0 and value.get('test_scope') is True),
                                     'sha256': digest(value.get('sha256'), 64)}
    result['sqlite'] = {key: source.get(key, {}).get('integrity') == 'ok' and source.get(key, {}).get('foreign_keys') == 0 and source.get(key, {}).get('test_scope') is True
                        for key in ('sqlite_before', 'sqlite_after_migration', 'sqlite_final') if isinstance(source.get(key), dict)}
    # Never copy ExecStart, environment, paths, unit status descriptions or journal text.
    result['systemd'] = {}
    for key in ('systemd_before', 'systemd_after', 'systemd_failure'):
        units = source.get(key) or (source.get('systemd', {}).get(key) if isinstance(source.get('systemd'), dict) else None)
        if not isinstance(units, dict):
            continue
        result['systemd'][key] = {}
        for name in ('shpakdnd-bot.service', 'shpakdnd-bot-watch.path', 'shpakdnd-bot-update.service', 'shpakdnd-staging-startup.service'):
            state = units.get(name)
            if isinstance(state, dict):
                def counter(field):
                    value = state.get(field)
                    return int(value) if isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value) else number(value, 9999999999)
                result['systemd'][key][name] = {'state': state.get('ActiveState', state.get('state')) if isinstance(state.get('ActiveState', state.get('state')), str) and state.get('ActiveState', state.get('state')) in STATES else 'unknown',
                                               'pid': counter('MainPID') if 'MainPID' in state else number(state.get('pid'), 9999999999), 'restarts': counter('NRestarts') if 'NRestarts' in state else number(state.get('restarts'), 9999999999)}
    if isinstance(source.get('sqlite'), dict):
        result['sqlite'].update({k: v is True for k,v in source['sqlite'].items() if k in ('sqlite_before', 'sqlite_after_migration', 'sqlite_final')})
    disk = source.get('capacity')
    if isinstance(disk, dict):
        result['capacity'] = {'status': 'PASS' if disk.get('status') == 'PASS' else 'FAIL',
                              'required_mib': number(disk.get('required_mib')), 'free_mib': number(disk.get('free_mib')),
                              'retention': 'OPERATOR_REVIEW_NO_AUTOMATIC_DELETION'}
    if not (result['pr'] and result['sha'] and result['request_id'] and result['ci']['run_id']
            and result['preflight']['skip_policy'] == 'PASS' and result['runtime_verified']
            and result['configuration_verified'] and result.get('capacity', {}).get('status') == 'PASS'
            and result['ci']['attempt'] and result['installed_sha'] == result['sha']
            and result['preflight']['status'] == 'PASS' and result['sqlite'].get('sqlite_final') is True
            and result['backups'].get('backup', {}).get('verified') is True
            and result['backups'].get('quiescent_backup', {}).get('verified') is True):
        result['acceptance_ready'] = 'FAIL'
        result['errors'] = ['DEPLOYMENT_NOT_READY']
    return result


def markdown(report):
    return '# STAGING report\n\nReadiness: **' + report['acceptance_ready'] + '**\n\n```json\n' + json.dumps(report, ensure_ascii=True, indent=2) + '\n```\n'
