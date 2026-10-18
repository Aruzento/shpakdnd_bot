"""Extract ONLY allowlisted report files; provide FAIL evidence for transport/admission errors."""
import json
import os
from pathlib import Path
import sys


def collect(folder):
    folder = Path(folder)
    folder.mkdir(exist_ok=True)
    response = folder / 'server-reports.json'
    if response.exists() and response.stat().st_size:
        try:
            payload = json.loads(response.read_text(encoding='utf-8'))
            if not isinstance(payload, dict):
                payload = {}
        except (ValueError, OSError):
            payload = {}
        for name in ('report.json', 'report.md', 'preflight.log', 'startup.log', 'journal.log'):
            if isinstance(payload.get(name), str):
                (folder / name).write_text(payload[name], encoding='utf-8')
    try:
        existing = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        valid = isinstance(existing, dict) and existing.get('acceptance_ready') in ('PASS', 'FAIL')
        if os.getenv('PR') and os.getenv('SHA'):
            valid = valid and str(existing.get('pr')) == os.environ['PR'] and existing.get('sha') == os.environ['SHA']
    except (OSError, ValueError):
        valid = False
    if not valid:
        failure = {'schema_version': 1, 'pr': os.getenv('PR'), 'sha': os.getenv('SHA'),
                   'acceptance_ready': 'FAIL', 'manual_acceptance': 'NOT_RUN',
                   'errors': ['Admission, SSH transport or server controller did not supply a final report'],
                   'risks': ['Inspect Actions step failures; TEST state may require operator review']}
        (folder / 'report.json').write_text(json.dumps(failure, indent=2) + '\n', encoding='utf-8')
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    if not valid or not (folder / 'report.md').exists():
        (folder / 'report.md').write_text('# STAGING report\n\nReadiness: **FAIL**\n\n```json\n'
                                         + json.dumps(report, indent=2) + '\n```\n', encoding='utf-8')
    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as output:
            # Do not render arbitrary feature output into the Actions summary.
            output.write('STAGING evidence uploaded. Inspect report.json/report.md; Telegram acceptance is manual.\n')
    return report


if __name__ == '__main__':
    report = collect(sys.argv[1])
    if '--require-ready' in sys.argv and report.get('acceptance_ready') != 'PASS':
        raise SystemExit(1)
