"""Read actual GitHub CI/protection. Never change repository settings."""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from datetime import datetime,timezone

REPOSITORY='Aruzento/shpakdnd_bot'
REQUIRED_CHECKS={'Linux release checks','systemd-staging','legacy-baseline'}
WORKFLOWS={'release-checks.yml':{'Linux release checks'},'systemd-staging.yml':{'systemd-staging','legacy-baseline'}}


def gh(*args):
    result=subprocess.run(['gh',*args],capture_output=True,text=True,encoding='utf-8',timeout=90)
    if result.returncode:raise RuntimeError('GitHub API unavailable')
    return result.stdout


def api(path):return json.loads(gh('api','repos/'+REPOSITORY+'/'+path))


def protection():
    try:
        raw=api('branches/main/protection');rules=api('rules/branches/main')
    except Exception:return {'status':'UNKNOWN','reason':'Branch protection NOT VERIFIED; GitHub API unavailable','source':'github_api'}
    required=raw.get('required_status_checks',{});contexts=set(required.get('contexts',[]))|{c['context'] for c in required.get('checks',[])}
    # Effective rulesets can add checks; bypass actors make enforcement uncertain.
    for rule in rules:
        if rule.get('type')=='required_status_checks':contexts|={c['context'] for c in rule.get('parameters',{}).get('required_status_checks',[])}
    reviews=raw.get('required_pull_request_reviews') or {}
    facts={'required_checks':sorted(contexts),'missing_checks':sorted(REQUIRED_CHECKS-contexts),
           'strict_up_to_date':required.get('strict') is True,'pull_request_required':reviews.get('required_approving_review_count',0)>=1,
           'dismiss_stale_reviews':reviews.get('dismiss_stale_reviews') is True,'enforce_admins':raw.get('enforce_admins',{}).get('enabled') is True,
           'force_push_forbidden':raw.get('allow_force_pushes',{}).get('enabled') is False,
           'deletion_forbidden':raw.get('allow_deletions',{}).get('enabled') is False,
           'rulesets_present':bool(rules)}
    blockers=[key for key,value in facts.items() if key not in {'required_checks','missing_checks','rulesets_present'} and value is not True]
    if facts['missing_checks']:blockers.append('missing required checks: '+', '.join(facts['missing_checks']))
    if rules:blockers.append('Effective ruleset bypass actors require independent verification')
    return {'status':'FAIL' if blockers else 'PASS','source':'github_api','observed_at':datetime.now(timezone.utc).isoformat(),
            'source_hash':hashlib.sha256(json.dumps([raw,rules],sort_keys=True).encode()).hexdigest(),'facts':facts,'blockers':blockers}


def collect(sha):
    output={'sha':sha,'source':'github_api','observed_at':datetime.now(timezone.utc).isoformat(),'jobs':{},'release':None,'automatic_smoke':None}
    try:
        runs=api('actions/runs?head_sha='+sha+'&event=push&per_page=100')['workflow_runs']
        for workflow,names in WORKFLOWS.items():
            candidates=[r for r in runs if r['head_sha']==sha and r.get('path')=='.github/workflows/'+workflow and r.get('event')=='push']
            if not candidates:
                for name in names:output['jobs'][name]={'status':'NOT_RUN','reason':'No push CI for exact SHA'}
                continue
            run=max(candidates,key=lambda r:(r.get('run_number',0),r.get('run_attempt',0)))
            jobs=api('actions/runs/'+str(run['id'])+'/jobs?per_page=100')['jobs']
            for name in names:
                matching=[j for j in jobs if j['name']==name]
                if len(matching)!=1:output['jobs'][name]={'status':'UNKNOWN','reason':'Missing/ambiguous required job'};continue
                job=matching[0]
                status='UNKNOWN' if job['status']!='completed' else 'PASS' if job['conclusion']=='success' else 'FAIL'
                evidence={'status':status,'run_id':run['id'],'job_id':job['id'],'url':run['html_url'],'checkout_sha':run['head_sha'],'run_attempt':run.get('run_attempt',1)}
                if status=='PASS':
                    log=gh('run','view',str(run['id']),'--job',str(job['id']),'--log')
                    evidence['log_hash']=hashlib.sha256(log.encode()).hexdigest()
                    if name=='systemd-staging':
                        matches=re.findall(r'SYSTEMD_STAND_RESULT=(\{[^\r\n]+\})',log)
                        if not matches:raise ValueError('Systemd stand proof absent')
                        value=json.loads(matches[-1])
                        if (value.get('source_sha')!=sha or value.get('status')!='PASS' or value.get('bot_restarts')!=0
                                or value.get('sqlite_unchanged') is not True or value.get('persistent_quarantine') is not True
                                or value.get('unit_drift_detected') is not True or 'REAL_PROCESS_INVOCATION_HEALTH=PASS' not in log):
                            raise ValueError('Systemd stand result not verified')
                        evidence['stand_result_hash']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
                    if name=='legacy-baseline':
                        matches=re.findall(r'LEGACY_BASELINE_RESULT=(\{[^\r\n]+\})',log)
                        if not matches:raise ValueError('Legacy baseline proof absent')
                        value=json.loads(matches[-1]);tests=value.get('tests',{})
                        if (value.get('sha')!='f06c0d129fdad0fef4fde0889915faac26b94f4a' or value.get('status')!='PASS'
                                or tests.get('count')!=979 or any(tests.get(k)!=0 for k in ('failures','errors','skips'))
                                or value.get('source_db_unchanged') is not True or value.get('lkg_published') is not False):
                            raise ValueError('Legacy baseline result not verified')
                        evidence['baseline_result_hash']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
                    if name=='Linux release checks':
                        guard_matches=re.findall(r'OK: release guard (\{[^\r\n]+\})',log)
                        count=re.findall(r'Ran (\d+) tests in ([\d.]+)s',log)
                        preserved=re.search(r'discovered baseline tests preserved: (\d+) -> (\d+)',log)
                        smoke=re.findall(r'AUTOMATIC_SMOKE_RESULT=(\{[^\r\n]+\})',log)
                        if not guard_matches or not count or not preserved or not smoke:raise ValueError('Incomplete release log')
                        guard=json.loads(guard_matches[0]);auto=json.loads(smoke[-1])
                        if guard['head']!=sha or auto['sha']!=sha or auto['status']!='PASS' or auto['real_telegram'] is not False:raise ValueError('CI evidence SHA/smoke mismatch')
                        try:from scripts.release_checks import CHECK_NAMES
                        except ImportError:from release_checks import CHECK_NAMES
                        if any('=== '+name+' ===' not in log for name in CHECK_NAMES):raise ValueError('Release check missing')
                        executed=int(count[-1][0]);base,head=map(int,preserved.groups())
                        if executed!=head or guard['head_test_symbols']!=head or guard['baseline_test_symbols']!=base or base!=979:
                            raise ValueError('Inventory/test count mismatch')
                        if re.search(r'FAILED \(.*(?:failures|errors)=',log) or re.search(r'OK \(skipped=[1-9]',log):raise ValueError('Test failures/skips')
                        output['release']={'status':'PASS','sha':sha,'checks':dict.fromkeys(CHECK_NAMES,'PASS'),'guard':guard,
                            'tests':{'baseline':base,'discovered':head,'executed':executed,'failures':0,'errors':0,'skips':0},'source':evidence.copy()}
                        output['automatic_smoke']=dict(auto,source=evidence.copy())
                output['jobs'][name]=evidence
    except Exception:
        for name in REQUIRED_CHECKS:output['jobs'][name]={'status':'UNKNOWN','reason':'GitHub evidence unavailable/incomplete; not a PASS'}
        output['release']=None;output['automatic_smoke']=None
    output['protection']=protection()
    return output
