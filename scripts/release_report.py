"""Exact-SHA release readiness; missing proofs are blockers, never successful releases."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
DEPLOY=ROOT/'deploy' if (ROOT/'deploy').is_dir() else ROOT
sys.path.insert(0,str(DEPLOY))
try:
    from scripts import github_release
except ImportError:
    import github_release
import release_state as store
from deploy_helpers import DeployError
import release_preflight as pref
import semantic_evidence as semantic

STATUSES={'PASS','FAIL','NOT_RUN','UNKNOWN','STALE','NOT_APPLICABLE'}
BASELINE=pref.BASELINE_SHA
PR='https://github.com/Aruzento/shpakdnd_bot/pull/1'


def item(status,source=None,reason=None):
    if status not in STATUSES:raise ValueError('Invalid report status')
    if status=='PASS' and not source:raise ValueError('PASS needs verified source')
    value={'status':status}
    if source:value['source']=source
    if reason:value['reason']=reason
    return value


def protected(path,sha,kind,*,age=7*86400):
    if not path or not Path(path).exists():return item('NOT_RUN',reason=kind+' evidence absent'),None
    try:
        value=store.read_record(path)
        if value.get('sha',value.get('target_sha'))!=sha:return item('STALE',reason='Evidence belongs to another SHA'),None
        if value.get('kind')!=kind:return item('UNKNOWN',reason='Unexpected evidence kind '+kind),None
        if value.get('status')!='PASS':return item(value.get('status') if value.get('status') in STATUSES else 'UNKNOWN',reason='Unconfirmed '+kind),None
        if not 0<=time.time()-datetime.fromisoformat(value['finished_at']).timestamp()<=age:return item('STALE',reason='Evidence expired'),None
        return item('PASS',{'evidence_hash':store.file_hash(path),'kind':kind}),value
    except Exception:return item('UNKNOWN',reason='Protected evidence invalid/corrupt'),None


def review_attestation(project,sha,artifact,output,operator_sha,kind):
    store.full_sha(sha);store.outside_project(Path(output).parent,project);store.outside_project(artifact,project)
    store.trusted_path(artifact,private=True)
    if operator_sha!=sha or not Path(artifact).stat().st_size:raise DeployError('Explicit SHA and independent artifact required')
    value=dict(version=1,kind=kind,sha=sha,status='PASS',source='explicit_operator_attestation',
               artifact_hash=store.file_hash(artifact),operator_confirmed=True,finished_at=store.utc_now())
    store.save_record(output,value);return value


def readiness(checks,*,on_main=False):
    auto=('git','ci','guard','tests','validators','automatic_smoke','systemd_ci','legacy_ci')
    merge=auto+('required_ci','staging','independent_review')
    deploy=merge+('merged_main_sha','production_decision','preflight','sqlite','migration','rollback','installed_systemd','backup_readiness','deployment_window')
    confirmation=('startup_health','real_telegram_postdeploy','lkg_promotion')
    requirements={'staging':auto,'merge':merge,'deployment':deploy,'production_confirmation':tuple(n for n in deploy if n!='deployment_window')+('final_backup',)+confirmation}
    blockers={gate:[{'check':name,'status':checks[name]['status'],'reason':checks[name].get('reason','Required proof not PASS'),
                    'next_action':checks[name].get('next_action','Complete '+name+' on the exact SHA/environment; do not override the gate')}
                   for name in names if checks[name]['status']!='PASS'] for gate,names in requirements.items()}
    status='В разработке' if blockers['staging'] else 'Готово к тестовому стенду'
    if not blockers['merge']:status='Готово к объединению с main'
    if not blockers['deployment'] and on_main:status='Можно в прод'
    return {'version_status':status,'merge_ready':not blockers['merge'],'deployment_start_ready':not blockers['deployment'],
            'production_release_confirmed':not blockers['production_confirmation'],'blockers':blockers}


# Dependencies describe independently validated evidence, not execution order.
DEPLOYMENT_CHECKS=('preflight','installed_systemd','sqlite','migration','rollback','backup_readiness',
                   'deployment_window','final_backup','startup_health','real_telegram_postdeploy','lkg_promotion')


class ProofFailure(Exception):
    def __init__(self,status,code):self.status=status;self.code=code


def deployment_evidence(project,sha,evidence,checkout_sha):
    """Publish each dependency group atomically; outcomes never manufacture PASS."""
    import release_lkg as lkg
    import systemd_state as units
    from deploy_helpers import check_database
    from preflight_data import schema_digest
    checks={name:item('NOT_RUN',reason='Evidence absent') for name in DEPLOYMENT_CHECKS}
    details={};diagnostics=[]
    def failed(name,error,affected=()):
        # No exception text, paths, rows or secret-bearing evidence are published.
        status=error.status if isinstance(error,ProofFailure) else 'UNKNOWN'
        code=error.code if isinstance(error,ProofFailure) else 'unreadable_or_invalid_evidence'
        reason=name+': '+code
        checks[name]=item(status,reason=reason)
        for dependent in affected:checks[dependent]=item('STALE' if status=='STALE' else 'UNKNOWN',reason='Depends on '+reason)
        diagnostics.append({'check':name,'status':status,'group':name,'affected':list(affected),
                            'reason':code,'next_action':'Verify '+name+' evidence and its listed dependencies for this SHA; regenerate after repair'})
    def known(error,code):
        # Recognize only constant validator messages; never print their contents.
        text=str(error)
        import sqlite3
        if isinstance(error,sqlite3.DatabaseError) and any(v in text for v in ('not a database','malformed')):return ProofFailure('FAIL',code+'_sqlite_corrupt')
        if isinstance(error,DeployError) and any(v in text for v in ('STALE','stale','SHA/tooling mismatch','another deployment/process/configuration','Telegram bot identity changed','Semantic environment differs')):
            return ProofFailure('STALE',code+'_stale')
        if isinstance(error,DeployError) and any(v in text for v in ('integrity_check failed','foreign_key_check failed','schema differs','schema drift',
                'configuration drift','unit/helper drift','PID/restart/invocation changed','journal has unexplained','Unhealthy bot/watcher/observer state','SOURCE data changed','TARGET data changed','user data changed/lost','Telegram getMe failed')):
            return ProofFailure('FAIL',code+'_validation_failed')
        return error
    if not evidence:return checks,details,diagnostics
    path=Path(evidence)
    followers=tuple(n for n in DEPLOYMENT_CHECKS if n!='preflight')
    preflight_step='preflight_document'
    try:
        if not path.exists():return checks,details,diagnostics
        raw=json.loads(path.read_text(encoding='utf-8'))
        if raw.get('target_sha')!=sha:raise ProofFailure('STALE','target_sha_differs')
        # Candidate must be read before the completed-operation exception can apply.
        preflight_step='bound_lkg_record'
        candidate=lkg.read_lkg(raw['lkg_binding']['path'],project,optional=True)
        completed=candidate if candidate and candidate.get('version')==2 and candidate['sha']==sha and candidate['deployment_id']==path.stem else None
        preflight_step='preflight_authentication'
        report=pref.validate_evidence(path,old=raw['old_sha'],target=sha,tools=Path(pref.__file__).parent,completed_lkg=completed)
        if checkout_sha not in {raw['old_sha'],sha}:raise ProofFailure('STALE','checkout_sha_differs')
        binding=report['installed_systemd']
        source={'evidence_hash':store.file_hash(path),'tooling_hash':report['tooling_hash']}
        # Historical preflight is independently confirmed, not a live DB verdict.
        checks.update({'preflight':item('PASS',source)})
    except Exception as error:
        error=known(error,'preflight')
        if not isinstance(error,ProofFailure):error=ProofFailure('UNKNOWN',preflight_step+'_unavailable_or_invalid')
        failed('preflight',error,followers);return checks,details,diagnostics
    try:
        units.verify_binding(binding,active=True)
        checks.update({'installed_systemd':item('PASS',source)})
        details.update(tooling_hash=report['tooling_hash'],systemd_hash=binding['config_hash'],
                       lkg_sha=candidate['sha'] if candidate else None,lkg_status='CONFIRMED' if candidate else 'NOT_RUN')
    except Exception as error:
        failed('installed_systemd',known(error,'systemd'),('startup_health','real_telegram_postdeploy','lkg_promotion'))
    state=None
    db_dependents=('sqlite','migration','rollback','backup_readiness','deployment_window','final_backup','startup_health','real_telegram_postdeploy','lkg_promotion')
    try:
        state_path=pref.deployment_path(path)
        if state_path.exists():
            state=pref.read_deployment(path,old=raw['old_sha'],target=sha,tools=Path(pref.__file__).parent)
            if state['phase'] not in {'SOURCE','TARGET'}:raise ProofFailure('UNKNOWN','migration_not_confirmed')
            if state['db']!=str((project/'shpakdnd.db').resolve()):raise ProofFailure('UNKNOWN','journal_database_binding_differs')
            details['db_stage']=state['phase']
        elif Path(str(state_path)+'.sha256').exists():raise ProofFailure('UNKNOWN','journal_missing_with_checksum')
    except Exception as error:
        failed('migration',error,tuple(n for n in db_dependents if n!='migration'));return checks,details,diagnostics
    expected=state['phase'].lower() if state else 'source'
    try:
        check_database(project/'shpakdnd.db')
        if schema_digest(project/'shpakdnd.db')!=report[expected+'_schema']:raise ProofFailure('FAIL','live_schema_differs_from_'+expected)
        checks.update({'sqlite':item('PASS',source)})
        details['sqlite_schema']=report[expected+'_schema']
    except Exception as error:
        failed('sqlite',known(error,'sqlite'),('migration','rollback','backup_readiness','deployment_window','startup_health','real_telegram_postdeploy','lkg_promotion'))
    backup_valid=not state
    if state:
        try:
            backup=Path(state['backup'])
            if backup.is_symlink() or backup.samefile(project/'shpakdnd.db'):raise ProofFailure('FAIL','backup_alias_or_symlink')
            if store.file_hash(backup)!=state['backup_sha256']:raise ProofFailure('FAIL','backup_hash_differs')
            check_database(backup)
            if schema_digest(backup)!=report['source_schema'] or pref.logical_digest(backup)!=state['source_data']:raise ProofFailure('FAIL','backup_source_state_differs')
            checks.update({'final_backup':item('PASS',{'journal_hash':store.file_hash(state_path),'backup_hash':state['backup_sha256']})})
            backup_valid=True
        except Exception as error:
            failed('final_backup',known(error,'backup'),('migration','rollback','backup_readiness','deployment_window','startup_health','real_telegram_postdeploy','lkg_promotion'))
    if checks['sqlite']['status']=='PASS' and backup_valid:
        # SOURCE preflight migration proof and TARGET runtime journal are distinct.
        pending={'migration':item('PASS',source),'backup_readiness':item('PASS',source)}
        pending['deployment_window']=item('PASS',source) if not state or (state['phase']=='SOURCE' and not state['startup_attempted']) else item('FAIL',reason='Deployment already migrated/started; create a new preflight operation')
        checks.update(pending)
        try:
            current=completed or lkg.verify_binding(report['lkg_binding'],project)
            compat=report.get('lkg_compatibility',{})
            details.update(rollback_compatibility={'source':compat.get('source'),'target':compat.get('target')},
                           lkg_sha=current['sha'] if current else None,lkg_status='CONFIRMED' if current else 'NOT_RUN')
            if not current:raise ProofFailure('NOT_RUN','confirmed_lkg_absent')
            if compat.get('sha')!=current['sha']:raise ProofFailure('UNKNOWN','compatibility_for_another_lkg')
            if any(compat.get(k) is False for k in ('source','target')):raise ProofFailure('FAIL','lkg_incompatible')
            if not all(compat.get(k) is True for k in ('source','target')):raise ProofFailure('UNKNOWN','lkg_compatibility_unknown')
            if state:
                pref.verify_deployment(path,old=raw['old_sha'],target=sha,tools=Path(pref.__file__).parent,db=project/'shpakdnd.db',rollback=True)
            checks.update({'rollback':item('PASS',source)})
        except Exception as error:failed('rollback',known(error,'rollback'))
    else:current=None
    # Health absent stays NOT_RUN. Existing health requires all live dependencies.
    health_path=path.with_suffix('.health.json');health=None
    try:health_exists=health_path.exists()
    except Exception as error:
        health_exists=False;failed('startup_health',error,('real_telegram_postdeploy','lkg_promotion'))
    if health_exists:
        if any(checks[n]['status']!='PASS' for n in ('installed_systemd','sqlite','migration','final_backup')):
            failed('startup_health',ProofFailure('UNKNOWN','health_dependencies_unconfirmed'),('real_telegram_postdeploy','lkg_promotion'))
        else:
            try:
                health=store.read_record(health_path)
                if health.get('sha')!=sha:raise ProofFailure('STALE','health_sha_differs')
                if health.get('status')!='PASS':raise ProofFailure(health.get('status') if health.get('status') in STATUSES else 'UNKNOWN','health_not_successful')
                if (health.get('evidence_hash')!=store.file_hash(path) or health.get('deployment_state_hash')!=store.file_hash(state_path)
                        or health.get('systemd')!=binding or health.get('project')!=str(project)
                        or not state or state['phase']!='TARGET' or state.get('startup_sha')!=sha):
                    raise ProofFailure('STALE','health_deployment_binding_differs')
                if not 0<=time.time()-datetime.fromisoformat(health['finished_at']).timestamp()<=300:raise ProofFailure('STALE','health_expired')
                import semantic_smoke as automatic_smoke
                automatic_path=path.with_suffix('.automatic-smoke.json')
                automatic_smoke.validate(automatic_path,sha)
                if store.file_hash(automatic_path)!=health.get('automatic_smoke_hash'):raise ProofFailure('UNKNOWN','automatic_smoke_hash_differs')
                if health.get('sqlite',{}).get('schema')!=report['target_schema']:raise ProofFailure('FAIL','health_sqlite_schema_differs')
                units.health(binding,since=health['since'],previous=health['process'])
                checks.update({'startup_health':item('PASS',{'health_hash':store.file_hash(health_path)})})
                details['production_status']='TECHNICAL_HEALTH_PASS_SEMANTIC_UNCONFIRMED'
            except Exception as error:failed('startup_health',known(error,'health'),('real_telegram_postdeploy','lkg_promotion'))
    semantic_path=path.with_suffix('.semantic.json')
    try:semantic_exists=semantic_path.exists()
    except Exception as error:
        semantic_exists=False;failed('real_telegram_postdeploy',error,('lkg_promotion',))
    if semantic_exists:
        check,value=protected(semantic_path,sha,'REAL_TELEGRAM_POSTDEPLOY',age=1800)
        if check['status']!='PASS':failed('real_telegram_postdeploy',ProofFailure(check['status'],'semantic_evidence_'+check['status'].lower()),('lkg_promotion',))
        elif checks['startup_health']['status']!='PASS':failed('real_telegram_postdeploy',ProofFailure('UNKNOWN','technical_health_unconfirmed'),('lkg_promotion',))
        else:
            try:
                semantic.validate_for_lkg(path,project,sha,health)
                checks.update({'real_telegram_postdeploy':item('PASS',{'semantic_hash':store.file_hash(semantic_path)})})
            except Exception as error:failed('real_telegram_postdeploy',known(error,'semantic'),('lkg_promotion',))
    # Validate projection separately. It may invalidate a claim, never certify it.
    outcome_valid=True;outcome_path=path.with_suffix('.outcome.json')
    try:outcome_exists=outcome_path.exists()
    except Exception as error:
        outcome_exists=False;outcome_valid=False;failed('lkg_promotion',error)
        checks['deployment_window']=item('UNKNOWN',reason='Deployment outcome unavailable; reconcile operation before a new deployment')
    if outcome_exists:
        try:
            check,value=protected(outcome_path,sha,'DEPLOYMENT_OUTCOME',age=86400)
            if check['status']!='PASS':raise ProofFailure(check['status'],'outcome_unconfirmed')
            if value['evidence_hash']!=store.file_hash(path):raise ProofFailure('STALE','outcome_preflight_differs')
            actual=lkg.read_lkg(value['lkg_path'],project,optional=True)
            if (actual['sha'] if actual else None)!=value['lkg_sha']:raise ProofFailure('UNKNOWN','outcome_lkg_drift')
            if value['lkg_path']!=report['lkg_binding']['path']:raise ProofFailure('STALE','outcome_lkg_binding_differs')
        except Exception as error:
            outcome_valid=False;failed('lkg_promotion',error)
            checks['deployment_window']=item('UNKNOWN',reason='Deployment outcome is unconfirmed; reconcile operation before a new deployment')
    promoted=bool(completed)
    if promoted:
        if outcome_valid and all(checks[n]['status']=='PASS' for n in ('startup_health','real_telegram_postdeploy')):
            # Actual LKG, not an outcome flag, attests this completed preflight.
            try:
                if completed['completed_preflight_hash']!=store.file_hash(path):raise ProofFailure('STALE','promotion_preflight_differs')
                checks.update({'lkg_promotion':item('PASS',{'lkg_hash':store.file_hash(report['lkg_binding']['path'])})})
                details.update(promotion=True,promotion_reason='Explicit operator and technical/semantic proofs confirmed',production_status='RELEASE_CONFIRMED')
            except Exception as error:failed('lkg_promotion',error)
        elif checks['lkg_promotion']['status']=='NOT_RUN':failed('lkg_promotion',ProofFailure('UNKNOWN','promotion_dependencies_unconfirmed'))
    return checks,details,diagnostics


def generate(project,sha,*,preflight=None,staging=None,review=None,approval=None,provider=None):
    project=Path(project).resolve();store.full_sha(sha)
    checks={k:item('NOT_RUN',reason='Evidence absent') for k in ('git','ci','guard','tests','validators','automatic_smoke','systemd_ci','legacy_ci',
        'required_ci','staging','independent_review','merged_main_sha','production_decision','preflight','sqlite','migration','rollback','installed_systemd',
        'backup_readiness','deployment_window','final_backup','startup_health','real_telegram_postdeploy','lkg_promotion')}
    git_info={'sha':sha,'branch':'UNKNOWN','baseline_sha':BASELINE,'changed_files':[]}
    try:
        store.command(['git','-C',project,'cat-file','-e',sha+'^{commit}'])
        store.command(['git','-C',project,'merge-base','--is-ancestor',BASELINE,sha])
        clean=not store.command(['git','-C',project,'status','--porcelain','--untracked-files=all'])
        head=store.command(['git','-C',project,'rev-parse','HEAD'])
        git_info.update(branch=store.command(['git','-C',project,'branch','--show-current']),checkout_sha=head,clean_worktree=clean,ancestry=True,
                        changed_files=store.command(['git','-C',project,'diff','--name-only',BASELINE,sha]).splitlines())
        if clean and (head==sha or preflight):checks['git']=item('PASS',{'command_hash':store.digest(git_info)})
        else:checks['git']=item('STALE' if head!=sha else 'FAIL',reason='Checkout SHA mismatch or dirty worktree')
    except Exception:checks['git']=item('FAIL',reason='Git ancestry/SHA/clean verification failed')
    ci=(provider or github_release.collect)(sha)
    jobs=ci.get('jobs',{})
    def job(name):
        value=jobs.get(name,{})
        if value.get('checkout_sha') and value['checkout_sha']!=sha:return item('STALE',reason='CI checkout belongs to another SHA')
        if value.get('status')=='PASS' and value.get('checkout_sha')==sha and value.get('log_hash'):
            return item('PASS',{'run_id':value['run_id'],'job_id':value['job_id'],'log_hash':value['log_hash'],'checkout_sha':sha})
        return item(value.get('status','NOT_RUN') if value.get('status')!='PASS' else 'UNKNOWN',reason=value.get('reason','CI source incomplete'))
    checks['systemd_ci']=job('systemd-staging');checks['legacy_ci']=job('legacy-baseline')
    checks['ci']=job('Linux release checks')
    release=ci.get('release')
    if release and release.get('sha')==sha and release.get('status')=='PASS' and checks['ci']['status']=='PASS':
        checks['guard']=item('PASS',checks['ci']['source'])
        tests=release.get('tests',{})
        valid=(tests.get('baseline')==979 and tests.get('discovered')==tests.get('executed') and tests.get('executed',0)>=979
               and all(tests.get(k)==0 for k in ('failures','errors','skips')))
        checks['tests']=item('PASS' if valid else 'FAIL',checks['ci']['source'],None if valid else 'Test inventory/failures/skips invalid')
        try:from scripts.release_checks import CHECK_NAMES
        except ImportError:from release_checks import CHECK_NAMES
        valid_checks=set(release.get('checks',{}))==set(CHECK_NAMES) and all(v=='PASS' for v in release['checks'].values())
        checks['validators']=item('PASS' if valid_checks else 'UNKNOWN',checks['ci']['source'],'Missing validators' if not valid_checks else None)
    automatic=ci.get('automatic_smoke')
    if automatic and automatic.get('sha')==sha and automatic.get('status')=='PASS' and automatic.get('real_telegram') is False and checks['ci']['status']=='PASS':
        checks['automatic_smoke']=item('PASS',checks['ci']['source'])
    protection=ci.get('protection',{})
    checks['required_ci']=item(protection.get('status','UNKNOWN'),{'source_hash':protection.get('source_hash')} if protection.get('source_hash') else None,
                               '; '.join(protection.get('blockers',[])) or protection.get('reason'))
    checks['staging'],stage=protected(staging,sha,'REAL_TELEGRAM_STAGING')
    if stage:
        try:semantic.validate_staging(staging,sha)
        except Exception:checks['staging']=item('UNKNOWN',reason='Full real Telegram/game/recovery/staging matrix incomplete or invalid')
    for name,path,kind in (('independent_review',review,'INDEPENDENT_REVIEW'),('production_decision',approval,'PRODUCTION_DECISION')):
        checks[name],value=protected(path,sha,kind)
        if value and (value.get('operator_confirmed') is not True or value.get('source')!='explicit_operator_attestation' or not value.get('artifact_hash')):
            checks[name]=item('UNKNOWN',reason='Independent/operator proof absent')
    on_main=False
    try:
        actual=github_release.api('git/ref/heads/main')['object']['sha']
        on_main=actual==sha
        checks['merged_main_sha']=item('PASS',{'github_main_sha':actual}) if on_main else item('STALE',reason='Feature SHA is not final main SHA; repeat gate after merge/squash')
    except Exception:checks['merged_main_sha']=item('UNKNOWN',reason='Actual main SHA unavailable')
    deployment={'target_sha':sha,'production_status':'NOT_RUN','lkg_sha':None,'lkg_status':'NOT_RUN','promotion':False,'promotion_reason':'No production evidence'}
    server_checks,server_details,diagnostics=deployment_evidence(project,sha,preflight,git_info.get('checkout_sha'))
    checks.update(server_checks);deployment.update(server_details)
    result=dict(version=1,release_version='V1.4.1',sha=sha,git=git_info,pr=PR,created_at_utc=store.utc_now(),checks=checks,
                automatic=release,github_protection=protection,staging={'status':checks['staging']['status'],'real_telegram':checks['staging']['status'],
                'execution_date':stage.get('finished_at') if stage else None,'environment_hash':stage.get('environment_hash') if stage else None,
                'operator_confirmed':stage.get('operator_confirmed',False) if stage else False,
                'scenarios':{name:{k:case[k] for k in ('status','artifact_hash','observed_at') if k in case}
                             for name,case in stage.get('cases',{}).items()} if stage else {}},
                deployment=deployment,tooling_version_hash=store.digest([store.file_hash(Path(__file__)),pref.tooling_hash(Path(pref.__file__).parent)]))
    result['automatic_semantic']={k:automatic[k] for k in ('kind','sha','status','real_telegram','data_preserved','schema','probe_log_hash') if k in automatic} if automatic else {'status':'NOT_RUN','real_telegram':False}
    result['readiness']=readiness(checks,on_main=on_main)
    result['deployment_diagnostics']=diagnostics
    result['risks']={'open_error_audit':'NOT_RUN','unverified_checks':[name for name,check in checks.items() if check['status']!='PASS'],
                     'manual_confirmation_required':['staging','independent_review','production_decision','real_telegram_postdeploy'],
                     'blockers':result['readiness']['blockers']}
    return result


def markdown(report):
    lines=['# D&D Mini V1.4.1 release report','', 'SHA: `'+report['sha']+'`','', 'UTC: '+report['created_at_utc'],'',
           'Status: **'+report['readiness']['version_status']+'**','', '| Check | Status | Evidence/reason |','|---|---|---|']
    for name,value in report['checks'].items():lines.append('| '+name+' | '+value['status']+' | '+(value.get('reason') or json.dumps(value.get('source'),sort_keys=True)) .replace('|','/').replace('\n',' ')+' |')
    lines+=['','## Git and tooling','', 'Branch: `'+str(report['git']['branch'])+'`', '', 'Baseline: `'+report['git']['baseline_sha']+'`', '', 'Tooling hash: `'+report['tooling_version_hash']+'`', '', 'PR: '+report['pr'], '', 'Changed files:', '']
    lines.extend('- `'+path+'`' for path in report['git']['changed_files'])
    release=report.get('automatic') or {}
    lines+=['', '## Automatic checks', '', 'Tests: '+json.dumps(release.get('tests',{'status':'NOT_RUN'}),sort_keys=True), '', '| Command/check | Status |', '|---|---|']
    lines.extend('| '+name+' | '+status+' |' for name,status in release.get('checks',{}).items())
    lines+=['', 'Automatic semantic: '+json.dumps(report['automatic_semantic'],sort_keys=True), '', '## Staging scenarios', '', '| Scenario | Status | Evidence hash |', '|---|---|---|']
    lines.extend('| '+name+' | '+case.get('status','UNKNOWN')+' | '+case.get('artifact_hash','NOT_RUN')+' |' for name,case in report['staging']['scenarios'].items())
    if not report['staging']['scenarios']:lines+=['| All real scenarios | NOT_RUN | No operator staging evidence |']
    lines+=['', 'Open error audit: '+report['risks']['open_error_audit'], '', '## Blockers','']
    for gate,blockers in report['readiness']['blockers'].items():
        lines+=['### '+gate,'']
        lines.extend('- '+v['check']+': '+v['status']+' — '+v['reason']+'. '+v['next_action'] for v in blockers)
        if not blockers:lines.append('No blockers.')
        lines.append('')
    lines+=['## Deployment evidence diagnostics','']
    lines.extend('- '+d['check']+': '+d['status']+'; affected: '+', '.join(d['affected'])+'; '+d['reason']+'. '+d['next_action'] for d in report.get('deployment_diagnostics',[]))
    lines+=['', 'LKG: '+str(report['deployment']['lkg_sha'])+' ('+report['deployment']['lkg_status']+').',
            'Promotion: '+str(report['deployment']['promotion'])+'. '+report['deployment']['promotion_reason'],
            'Production status: '+report['deployment']['production_status'], '', 'Real Telegram is distinct from import/router/mock and systemd smoke.']
    return '\n'.join(lines)+'\n'


def legacy_bootstrap(project,sha,preflight,staging,review,approval,*,provider=None):
    """Pinned V1.4 initialization only. Never authorize a new code release.

    V1.4 has no A runner/workflow. Its fresh strict adapter proves its exact
    tests; CI instead attests the installed D tooling SHA. Real gameplay and
    explicit operator proofs remain mandatory. Full infra/LKG staging would
    be circular before the first LKG, so only GAMEPLAY scope is accepted here.
    """
    if sha!=BASELINE:raise DeployError('Legacy bootstrap only supports immutable V1.4')
    project=Path(project).resolve()
    raw=json.loads(Path(preflight).read_text(encoding='utf-8'))
    if raw.get('kind')!='PINNED_LEGACY_BASELINE' or raw.get('old_sha')!=sha:raise DeployError('Pinned legacy adapter evidence required')
    report=pref.validate_evidence(preflight,old=sha,target=sha,tools=Path(pref.__file__).parent,live_db=project/'shpakdnd.db',expected_schema='source')
    if store.command(['git','-C',project,'rev-parse','HEAD'])!=sha or store.command(['git','-C',project,'status','--porcelain','--untracked-files=all']):
        raise DeployError('Exact clean legacy checkout required')
    if pref.deployment_path(preflight).exists():raise DeployError('Legacy initialization already started; fresh operation required')
    tests=report['tests']
    if tests['count']!=979 or report['head_discovered_tests']!=979 or report['baseline_discovered_tests']!=979:
        raise DeployError('All immutable legacy tests required')
    try:from scripts.release_checks import CHECK_NAMES
    except ImportError:from release_checks import CHECK_NAMES
    if set(report.get('release_checks',{}))!=set(CHECK_NAMES) or any(v!='OK' for v in report['release_checks'].values()):
        raise DeployError('Legacy release checks incomplete')
    import systemd_state as units
    import release_lkg as lkg
    installed=report['installed_systemd'];units.verify_binding(installed,active=True)
    if lkg.verify_binding(report['lkg_binding'],project) is not None:raise DeployError('Confirmed LKG already exists; no initialization override')
    if installed['tooling_hash']!=report['tooling_hash']:raise DeployError('Installed tooling differs from verified preflight')
    tools_sha=store.full_sha(installed['source_sha'])
    ci=(provider or github_release.collect)(tools_sha)
    for name in github_release.REQUIRED_CHECKS:
        proof=ci.get('jobs',{}).get(name,{})
        if proof.get('status')!='PASS' or proof.get('checkout_sha')!=tools_sha or not proof.get('log_hash'):
            raise DeployError('Exact installed tooling CI not verified')
    if ci.get('protection',{}).get('status')!='PASS':raise DeployError('Actual mandatory CI enforcement not verified')
    if ci.get('automatic_smoke',{}).get('sha')!=tools_sha or ci['automatic_smoke'].get('status')!='PASS':
        raise DeployError('Installed D tooling smoke not verified')
    semantic.validate_staging(staging,sha,full=False)
    for path,kind in ((review,'INDEPENDENT_REVIEW'),(approval,'PRODUCTION_DECISION')):
        check,value=protected(path,sha,kind)
        if (check['status']!='PASS' or value.get('operator_confirmed') is not True
                or value.get('source')!='explicit_operator_attestation' or not value.get('artifact_hash')):
            raise DeployError('Explicit reviewed legacy bootstrap/operator decision required')
    return {'status':'PASS','kind':'PINNED_LEGACY_BOOTSTRAP_READY','sha':sha,'tooling_sha':tools_sha,
            'evidence_hash':store.file_hash(preflight),'new_release_ready':False,
            'reason':'Initialization only; startup, real postdeploy semantic and explicit promotion still required'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['report','gate','gate-staging','gate-legacy-bootstrap','attest-review','attest-production'])
    parser.add_argument('--project',default=str(ROOT));parser.add_argument('--sha',required=True)
    for name in ('preflight','staging','review','approval','output','artifact'):parser.add_argument('--'+name)
    args=parser.parse_args()
    try:
        if args.action.startswith('attest'):
            store.require_root()
            answer=input('Record explicit independent review/production decision for full SHA:\n').strip()
            review_attestation(args.project,args.sha,args.artifact,args.output,answer,'INDEPENDENT_REVIEW' if args.action=='attest-review' else 'PRODUCTION_DECISION');return 0
        if args.action in {'gate','gate-staging','gate-legacy-bootstrap'}:
            store.require_root();pref.require_deployment_lock(Path(args.project)/'shpakdnd.db')
        if args.action=='gate-legacy-bootstrap':
            print(json.dumps(legacy_bootstrap(args.project,args.sha,args.preflight,args.staging,args.review,args.approval)))
            return 0
        report=generate(args.project,args.sha,preflight=args.preflight,staging=args.staging,review=args.review,approval=args.approval)
        if args.output:
            output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            output.with_suffix('.md').write_text(markdown(report),encoding='utf-8')
        print(report['readiness']['version_status'])
        if args.action in {'gate','gate-staging','gate-legacy-bootstrap'}:
            if args.action=='gate-staging':
                import systemd_state as units
                binding=units.verify_installed(project=args.project,active=True)
                if not units.staging_identity(binding):raise DeployError('Independent staging unit/runtime required')
                required=('git','ci','guard','tests','validators','automatic_smoke','systemd_ci','legacy_ci','preflight','sqlite','migration','installed_systemd','backup_readiness','deployment_window')
                blockers=[{'check':n,'reason':'Staging prerequisite '+n+' not PASS'} for n in required if report['checks'][n]['status']!='PASS']
                for b in blockers:print(b['reason'])
                print('Staging only; production/merge NOT authorized; absent LKG means rollback unavailable')
                return 1 if blockers else 0
            import systemd_state as units
            if units.staging_identity(units.verify_installed(project=args.project,active=True)):raise DeployError('Staging runtime cannot authorize production gate')
            for blocker in report['readiness']['blockers']['deployment']:print(blocker['check']+': '+blocker['reason'])
            return 0 if report['readiness']['deployment_start_ready'] else 1
        return 0
    except Exception as error:print('Release report UNKNOWN: '+type(error).__name__,file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
