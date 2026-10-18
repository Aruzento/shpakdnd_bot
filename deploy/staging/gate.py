"""Strict, fail-closed GitHub evidence gate shared by Actions and frozen controller."""
import base64
import json
import os
import re
from urllib.request import Request, urlopen

REPO = 'Aruzento/shpakdnd_bot'
WORKFLOW = 'linux-release.yml'
JOBS = {f'Linux release checks (Python {v})' for v in ('3.11', '3.13')}
STEPS = {
    'Record exact tested PR HEAD', 'Audit all 109 offline screens', 'Create review release manifest', 'Full unittest suite', 'Startup on isolated SQLite',
    'Compile Python modules', 'Validate hero abilities', 'Validate boss abilities',
    'Validate Tower', 'Validate V1.4.2 catalogs', 'Validate editorial defaults',
    'Validate all JSON and repeated disposable SQLite startup',
    'Verify PvP migrations and old-code startup on disposable copies',
    'Verify unchanged gameplay and the authorized PvP integration',
    'Verify repeated SQLite migration on a copy',
    'Verify old-code rollback compatibility on a separate copy', 'Check diff whitespace',
}


class Refused(RuntimeError):
    """Messages are fixed control-plane diagnostics, safe for reports."""


def sha_value(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise Refused('Expected exact lowercase 40-character Git SHA')
    return value


def pr_value(value):
    if not re.fullmatch('[1-9][0-9]{0,9}', str(value)):
        raise Refused('Invalid PR number')
    return int(value)


class GitHub:
    def __init__(self, token):
        if not token:
            raise Refused('GitHub read token unavailable')
        self.token = token

    def __call__(self, path):
        # Only repository-relative, hard-coded API routes; never follow supplied URLs.
        request = Request('https://api.github.com/repos/' + REPO + '/' + path,
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            raise Refused('GitHub evidence unavailable') from None


def pages(api, path, key):
    rows = []
    for page in range(1, 101):
        result = api(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        batch = result[key]
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise Refused('GitHub pagination limit exceeded')


def verify(api, pr, sha, trusted_ref='main'):
    pr, sha = pr_value(pr), sha_value(sha)
    pull = api(f'pulls/{pr}')
    if (pull['state'] != 'open' or pull['base']['repo']['full_name'] != REPO
            or pull['base']['ref'] != 'main' or pull['head']['repo']['full_name'] != REPO
            or pull['head']['sha'] != sha):
        raise Refused('SHA must be the current HEAD of an open same-repository PR to main')
    path = '.github/workflows/' + WORKFLOW
    trusted = api(f'contents/{path}?ref={trusted_ref}')
    candidate = api(f'contents/{path}?ref={sha}')
    if trusted['sha'] != candidate['sha']:
        raise Refused('Linux CI workflow differs from trusted main; review and merge infrastructure first')
    workflow = api('actions/workflows/' + WORKFLOW)
    runs = pages(api, f'actions/workflows/{workflow["id"]}/runs?head_sha={sha}', 'workflow_runs')
    runs = [r for r in runs if r['head_sha'] == sha and r['event'] == 'pull_request'
            and r['head_repository']['full_name'] == REPO
            and r['workflow_id'] == workflow['id']
            and any(p['number'] == pr for p in r['pull_requests'])]
    if not runs:
        raise Refused('No Linux CI for exact PR HEAD')
    run = max(runs, key=lambda r: (r['run_number'], r['run_attempt']))
    if run['status'] != 'completed' or run['conclusion'] != 'success':
        raise Refused('Latest exact-SHA Linux CI is pending or unsuccessful')
    jobs = pages(api, f'actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs', 'jobs')
    if {j['name'] for j in jobs} != JOBS or len(jobs) != 2:
        raise Refused('Both Python matrix jobs are required')
    for job in jobs:
        if job['status'] != 'completed' or job['conclusion'] != 'success':
            raise Refused('Python matrix is incomplete or failed')
        successful = {s['name'] for s in job['steps'] if s['status'] == 'completed' and s['conclusion'] == 'success'}
        if not STEPS <= successful:
            raise Refused('Required Linux checks are absent, skipped or failed')
    # A force-push during evidence collection must fail as well.
    if api(f'pulls/{pr}')['head']['sha'] != sha:
        raise Refused('PR HEAD changed while collecting evidence')
    return {'pr': pr, 'sha': sha, 'ci_run': run['id'], 'ci_attempt': run['run_attempt'],
            'ci_url': f'https://github.com/{REPO}/actions/runs/{run["id"]}',
            'jobs': [{'name': j['name'], 'conclusion': j['conclusion']} for j in jobs],
            'required_steps': sorted(STEPS), 'trusted_workflow_blob': trusted['sha']}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--pr', required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--trusted-ref', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from pathlib import Path
    report = {'acceptance_ready': 'FAIL'}
    try:
        report.update(verify(GitHub(os.environ.get('GH_TOKEN')), args.pr, args.sha, args.trusted_ref))
        report['ci_gate'] = 'PASS'
    except Exception as error:
        report['error'] = str(error) if isinstance(error, Refused) else type(error).__name__
        raise
    finally:
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
