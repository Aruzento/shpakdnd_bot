"""Only v2 structured metadata enters artifacts; incoming transport is outside artifacts."""
import json
import os
import re
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from public_report import project, markdown


def collect(folder, incoming=None):
    folder = Path(folder)
    folder.mkdir(exist_ok=True)
    source = incoming or os.getenv('STAGING_RESPONSE')
    payload = {}
    try:
        if source and Path(source).stat().st_size <= 65536:
            payload = json.loads(Path(source).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    report = project(payload)
    if ((os.getenv('PR') and str(report['pr']) != os.environ['PR'])
            or (os.getenv('SHA') and report['sha'] != os.environ['SHA'])
            or (os.getenv('STAGING_REQUEST') and report['request_id'] != os.environ['STAGING_REQUEST'])):
        report = project({})
    if report['sha'] is None:
        candidate = dict(report, pr=int(os.environ['PR']) if re.fullmatch(r'[1-9][0-9]{0,9}',os.getenv('PR','')) else None,
                         sha=os.getenv('SHA'), acceptance_ready='FAIL')
        report = project(candidate)
    (folder/'report.json').write_text(json.dumps(report, ensure_ascii=True, indent=2)+'\n',encoding='utf-8')
    (folder/'report.md').write_text(markdown(report),encoding='utf-8')
    return report


if __name__ == '__main__':
    report = collect(sys.argv[1])
    if '--require-ready' in sys.argv and report['acceptance_ready'] != 'PASS':
        raise SystemExit(1)
