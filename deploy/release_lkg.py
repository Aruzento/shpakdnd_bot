"""Confirmed last-known-good, bound to exact release, process, DB and systemd proofs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime,timezone
from deploy_helpers import DeployError,check_database
from preflight_data import schema_digest,inventory_database
import release_state as store
import systemd_state as units
import semantic_evidence as semantic
import semantic_smoke as automatic_smoke

DEFAULT_LKG='/var/lib/shpakdnd-release/last-known-good.json'


def read_lkg(path, project, *, optional=False):
    store.outside_project(Path(path).parent,project)
    if Path(path).is_symlink():raise DeployError("Unsafe LKG symlink")
    if not Path(path).exists():
        if optional:return None
        raise DeployError('Confirmed LKG is absent; OLD HEAD is not a rollback target')
    record=store.read_record(path)
    required={'version','status','sha','confirmed_at','deployment_id','repository','tooling_hash','systemd_hash',
              'release_checks','health','sqlite','previous'}
    if not required<=record.keys() or record['version'] not in {1,2} or record['status']!='CONFIRMED':raise DeployError('LKG is incomplete/unconfirmed')
    sha=store.full_sha(record['sha'])
    if record['repository']!=store.repository_id(project):raise DeployError('LKG repository mismatch')
    store.command(['git','-C',project,'cat-file','-e',sha+'^{commit}'])
    import release_preflight as pref
    sys.path.insert(0,str(pref.shared_scripts(Path(pref.__file__).parent)))
    from release_checks import CHECK_NAMES
    if set(record['release_checks'])!=set(CHECK_NAMES) or any(v!='OK' for v in record['release_checks'].values()):raise DeployError('LKG release checks missing')
    for field in ('tooling_hash','systemd_hash'):
        if not __import__('re').fullmatch('[0-9a-f]{64}',record[field]):raise DeployError('LKG hash missing')
    try:
        if datetime.fromisoformat(record['confirmed_at']).timestamp()>time.time()+5:raise ValueError('future confirmation')
    except (ValueError,TypeError):raise DeployError('LKG confirmation timestamp invalid')
    if record['health'].get('operator_confirmed') is not True:raise DeployError('LKG operator confirmation missing')
    if record['health'].get('status')!='PASS' or record['sqlite'].get('integrity')!='OK' or record['sqlite'].get('foreign_keys')!='OK':
        raise DeployError('LKG health/SQLite proof missing')
    if record['version']==2 and (record.get('semantic',{}).get('status')!='PASS' or record['semantic'].get('sha')!=sha or record['semantic'].get('deployment_id')!=record['deployment_id']):raise DeployError('LKG semantic proof missing')
    previous=record['previous']
    if previous:
        previous_path=Path(path).parent/'history'/previous['record']
        if not __import__('re').fullmatch(r'[0-9a-f]{64}\.json',previous['record']) or previous_path.name!=previous['record']:raise DeployError('Invalid LKG history path')
        old=store.read_record(previous_path)
        if store.digest(old)!=previous['hash'] or old['sha']!=previous['sha']:raise DeployError('LKG history mismatch')
    return record


def binding(path,project):
    record=read_lkg(path,project,optional=True)
    return {'path':str(Path(path).resolve()),'sha':record['sha'] if record else None,
            'record_hash':store.file_hash(path) if record else None,'repository':store.repository_id(project)}


def verify_binding(value, project):
    current=binding(value['path'],project)
    if current!=value:raise DeployError('LKG changed since preflight')
    return read_lkg(value['path'],project,optional=True)


def rollback_target(evidence, project, *, state_path=None):
    import release_preflight as pref
    report=json.loads(Path(evidence).read_text(encoding='utf-8'))
    rollback=report.get('lkg_compatibility')
    if not rollback or not report.get('lkg_binding'):raise DeployError('No LKG-specific preflight proof')
    record=verify_binding(report['lkg_binding'],project)
    if not record or rollback.get('sha')!=record['sha']:raise DeployError('Unconfirmed/mismatched LKG rollback target')
    state=pref.read_deployment(evidence,old=report['old_sha'],target=report['target_sha'],tools=Path(pref.__file__).parent)
    phase=state['phase'].lower()
    if phase not in {'source','target'} or rollback.get(phase) is not True:
        raise DeployError('LKG compatibility for actual DB stage is false or unknown')
    return record['sha']


def smoke(db):
    check_database(db)
    inventory=inventory_database(db,strict=True)
    required={'mini_players','mini_worlds','mini_wallet_transactions','mini_event_sessions'}
    if not required<=inventory.keys() or inventory['mini_worlds']['count']<1:
        raise DeployError('Postdeploy smoke: initialized world/player/wallet/events schema required')
    # Counts/hashes stay private; user rows/secrets never enter LKG.
    return {'status':'PASS','integrity':'OK','foreign_keys':'OK','schema':schema_digest(db),
            'required_tables':sorted(required)}


def startup_health(evidence, *, project, old, target, tools, before_watcher=False):
    """Fresh process and whole invocation checks also cover SOURCE/LKG recovery."""
    import release_preflight as pref
    report=pref.validate_evidence(evidence,old=old,target=target,tools=tools)
    state=pref.read_deployment(evidence,old=old,target=target,tools=tools)
    sha=state.get('startup_sha')
    if state['phase'] not in {'SOURCE','TARGET'} or not state.get('startup_attempted'):
        raise DeployError('No confirmed SOURCE/TARGET startup attempt')
    if sha==target and state['phase']=='TARGET':
        if not state['runtime_success'] or not state['preserved']:raise DeployError('Unconfirmed TARGET migration')
    else:
        record=verify_binding(report['lkg_binding'],project)
        compatibility=report.get('lkg_compatibility',{})
        if not record or sha!=record['sha'] or compatibility.get('sha')!=sha or compatibility.get(state['phase'].lower()) is not True:
            raise DeployError('Startup lacks exact stage-specific LKG proof')
    if store.command(['git','-C',project,'rev-parse','HEAD'])!=sha or store.command(['git','-C',project,'status','--porcelain','--untracked-files=all']):
        raise DeployError('Startup checkout differs from confirmed code')
    process=units.health(report['installed_systemd'],since=state['launch_requested_at'],before_watcher=before_watcher)
    if int(process['started_monotonic'])<state['launch_requested_monotonic']:
        raise DeployError('Startup process predates confirmed checkout')
    return dict(version=1,status='PASS',sha=sha,stage=state['phase'],process=process,
                evidence_hash=store.file_hash(evidence),deployment_state_hash=store.file_hash(pref.deployment_path(evidence)),
                finished_at=store.utc_now())


def postdeploy_health(evidence, *, project, db, old, target, tools, since, wait=5):
    import release_preflight as pref
    report=pref.validate_evidence(evidence,old=old,target=target,tools=tools)
    state=pref.read_deployment(evidence,old=old,target=target,tools=tools)
    if state['phase']!='TARGET' or not state['runtime_success'] or not state['preserved'] or not state['startup_attempted']:
        raise DeployError('No confirmed successful TARGET startup history')
    if state.get('startup_sha')!=target or store.command(['git','-C',project,'rev-parse','HEAD'])!=target:
        raise DeployError('Postdeploy process is not the confirmed TARGET')
    installed=report['installed_systemd']
    first=units.health(installed,since=since)
    if int(first['started_monotonic'])<state['launch_requested_monotonic']:
        raise DeployError('Running bot predates the confirmed checkout/start request')
    time.sleep(wait)
    second=units.health(installed,since=since,previous=first)
    database=smoke(db)
    if database['schema']!=report['target_schema']:raise DeployError('Postdeploy TARGET schema drift')
    if store.command(['git','-C',project,'status','--porcelain','--untracked-files=all']):raise DeployError('Postdeploy worktree dirty')
    automatic=automatic_smoke.run(project,target,db,Path(evidence).with_suffix('.automatic-smoke.json'),staging=units.staging_identity(installed))
    proof={'version':1,'status':'PASS','sha':target,'project':str(Path(project).resolve()),
           'evidence_hash':store.file_hash(evidence),'deployment_state_hash':store.file_hash(pref.deployment_path(evidence)),
           'automatic_smoke_hash':store.file_hash(Path(evidence).with_suffix('.automatic-smoke.json')),'systemd':installed,'process':second,'sqlite':database,'since':since,'finished_at':store.utc_now()}
    store.save_record(Path(evidence).with_suffix('.health.json'),proof)
    return proof


def promote(evidence, *, project, db, old, target, tools, lkg_path=DEFAULT_LKG, operator_sha=None):
    import release_preflight as pref
    store.outside_project(Path(lkg_path).parent,project)
    existing=read_lkg(lkg_path,project,optional=True)
    if existing and existing['version']==2 and existing['deployment_id']==Path(evidence).stem and existing['sha']==target:
        return existing  # Non-mutating retry of an already operator-confirmed release.
    report=pref.validate_evidence(evidence,old=old,target=target,tools=tools)
    proof=store.read_record(Path(evidence).with_suffix('.health.json'))
    state=pref.read_deployment(evidence,old=old,target=target,tools=tools)
    if (operator_sha!=target or proof.get('status')!='PASS' or proof.get('sha')!=target
            or proof.get('project')!=str(Path(project).resolve()) or proof['evidence_hash']!=store.file_hash(evidence)
            or proof['deployment_state_hash']!=store.file_hash(pref.deployment_path(evidence))
            or state['phase']!='TARGET' or state.get('startup_sha')!=target):
        raise DeployError('Operator confirmation/exact TARGET health proof missing')
    age=time.time()-datetime.fromisoformat(proof['finished_at']).timestamp()
    if not 0<=age<=300:raise DeployError('Postdeploy confirmation is stale')
    if store.command(['git','-C',project,'rev-parse','HEAD'])!=target or store.command(['git','-C',project,'status','--porcelain','--untracked-files=all']):
        raise DeployError('LKG checkout SHA/worktree mismatch')
    units.health(proof['systemd'],since=proof['since'],previous=proof['process'])
    current_db=smoke(db)
    if current_db['schema']!=proof['sqlite']['schema']:raise DeployError('LKG SQLite drift')
    automatic_path=Path(evidence).with_suffix('.automatic-smoke.json')
    automatic_smoke.validate(automatic_path,target)
    if store.file_hash(automatic_path)!=proof.get('automatic_smoke_hash'):raise DeployError('Automatic smoke proof changed')
    semantic_proof=semantic.validate_for_lkg(evidence,project,target,proof)
    store.private_directory(Path(lkg_path).parent)
    previous=read_lkg(lkg_path,project,optional=True)
    deployment_id=Path(evidence).stem
    if previous and previous['deployment_id']==deployment_id:
        if previous['sha']!=target:raise DeployError('Deployment identity collision')
        return previous
    history=None
    if previous:
        folder=Path(lkg_path).parent/'history';store.private_directory(folder)
        name=store.digest(previous)+'.json'
        store.save_record(folder/name,previous)
        history={'record':name,'hash':store.digest(previous),'sha':previous['sha']}
    record={'version':2,'status':'CONFIRMED','sha':target,'confirmed_at':store.utc_now(),
            'deployment_id':deployment_id,'repository':store.repository_id(project),
            'tooling_hash':pref.tooling_hash(tools),'systemd_hash':proof['systemd']['config_hash'],
            'release_checks':report['release_checks'],'health':{'status':'PASS','process':proof['process'],
             'operator_confirmed':True,'proof_hash':store.digest(proof)},'sqlite':current_db,'previous':history,'semantic':semantic_proof,'completed_preflight_hash':store.file_hash(evidence)}
    store.save_record(lkg_path,record)
    return record


def deployment_outcome(evidence,project,target,lkg_path=DEFAULT_LKG,*,reason=None):
    current=read_lkg(lkg_path,project,optional=True)
    promoted=bool(current and current['version']==2 and current['sha']==target and current['deployment_id']==Path(evidence).stem)
    path=Path(evidence).with_suffix('.outcome.json')
    if path.exists() and reason is None:
        old=store.read_record(path);reason=old.get('promotion_reason')
    report=json.loads(Path(evidence).read_text(encoding='utf-8'))
    compat=report.get('lkg_compatibility',{})
    technical=False;semantic_status='NOT_RUN';db_stage='UNKNOWN';actual_head='UNKNOWN'
    try:
        actual_head=store.command(['git','-C',project,'rev-parse','HEAD'])
        import release_preflight as pref
        state=pref.read_deployment(evidence,old=report['old_sha'],target=target,tools=Path(__file__).parent)
        db_stage=state['phase']
        health=store.read_record(Path(evidence).with_suffix('.health.json'))
        units.health(health['systemd'],since=health['since'],previous=health['process'])
        technical=actual_head==target and health['sha']==target and health['status']=='PASS' and db_stage=='TARGET'
        if technical:
            try:semantic.validate_for_lkg(evidence,project,target,health);semantic_status='PASS'
            except Exception:semantic_status='UNKNOWN' if Path(evidence).with_suffix('.semantic.json').exists() else 'NOT_RUN'
    except Exception:pass
    release_status='RELEASE_CONFIRMED' if promoted and technical and semantic_status=='PASS' else 'CODE_RUNNING_RELEASE_UNCONFIRMED' if technical else 'UNKNOWN'
    value=dict(version=1,kind='DEPLOYMENT_OUTCOME',status='PASS',sha=target,evidence_hash=store.file_hash(evidence),
               actual_checkout_sha=actual_head,technical_health='PASS' if technical else 'UNKNOWN',semantic_smoke=semantic_status,db_stage=db_stage,
               lkg_path=str(Path(lkg_path).resolve()),lkg_sha=current['sha'] if current else None,lkg_status='CONFIRMED' if current else 'NOT_RUN',
               promotion=promoted,promotion_reason=reason or 'No semantic/operator promotion proof',release_status=release_status,
               rollback_compatibility={'sha':compat.get('sha'),'source':compat.get('source'),'target':compat.get('target')},
               rollback_available=bool(db_stage in {'SOURCE','TARGET'} and current and compat.get('sha')==current['sha'] and compat.get(db_stage.lower()) is True),
               operator_action=None if release_status=='RELEASE_CONFIRMED' else 'Complete real Telegram smoke, refresh technical health, explicitly confirm full TARGET SHA',finished_at=store.utc_now())
    store.save_record(path,value)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['target','health','confirm','smoke','startup-health','rollback-health','status'])
    for name in ('evidence','project','db','old','target'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--lkg',default=os.environ.get('SHPAKDND_LKG',DEFAULT_LKG))
    parser.add_argument('--since');parser.add_argument('--wait',type=int,default=5)
    args=parser.parse_args(argv)
    try:
        if os.name!='posix' or os.geteuid()!=0:raise DeployError('Root Linux required')
        import release_preflight as pref
        pref.require_deployment_lock(args.db)
        common=dict(project=args.project,db=args.db,old=args.old,target=args.target,tools=Path(__file__).parent)
        if args.action=='confirm':
            existing=read_lkg(args.lkg,args.project,optional=True)
            if existing and existing['version']==2 and existing['deployment_id']==Path(args.evidence).stem and existing['sha']==args.target:
                print('LKG уже подтверждена для этого deployment; запись не изменена');return 0
        if args.action=='status':
            print(json.dumps(deployment_outcome(args.evidence,args.project,args.target,args.lkg),ensure_ascii=False));return 0
        pref.validate_evidence(args.evidence,old=args.old,target=args.target,tools=Path(__file__).parent)
        if args.action=='target':
            print(rollback_target(args.evidence,args.project))
        elif args.action in {'startup-health','rollback-health'}:
            proof=startup_health(args.evidence,project=args.project,old=args.old,target=args.target,
                                 tools=Path(__file__).parent,before_watcher=args.action=='startup-health')
            if args.action=='rollback-health':
                if proof['sha']!=rollback_target(args.evidence,args.project):raise DeployError('Recovery is not exact LKG')
                store.save_record(Path(args.evidence).with_suffix('.rollback-health.json'),proof)
            print('Fresh process/whole invocation health PASS: '+proof['sha'])
        elif args.action=='smoke':
            state=pref.read_deployment(args.evidence,old=args.old,target=args.target,tools=Path(__file__).parent)
            result=smoke(args.db)
            report=pref.validate_evidence(args.evidence,old=args.old,target=args.target,tools=Path(__file__).parent)
            if state['phase'] not in {'SOURCE','TARGET'} or result['schema']!=report[state['phase'].lower()+'_schema']:
                raise DeployError('Unconfirmed smoke DB stage')
            print('Read-only post-start SQLite smoke PASS')
        elif args.action=='health':
            if not args.since or not 1<=args.wait<=60:raise DeployError('Bounded health window and since timestamp required')
            postdeploy_health(args.evidence,since=args.since,wait=args.wait,**common)
            print('Postdeploy health/smoke PASS; LKG not yet promoted')
        else:
            if not Path(args.evidence).with_suffix('.semantic.json').exists():
                deployment_outcome(args.evidence,args.project,args.target,args.lkg,reason='Real Telegram semantic smoke NOT_RUN; previous LKG retained')
                print('Code running; technical health PASS. Real Telegram NOT_RUN. Previous LKG retained; release NOT CONFIRMED.');return 0
            try:answer=input('Подтвердить успешный релиз: введите полный TARGET SHA для LKG (Enter сохраняет прежнюю LKG):\n').strip()
            except EOFError:answer=''
            if answer!=args.target:
                deployment_outcome(args.evidence,args.project,args.target,args.lkg,reason='Operator declined/EOF; previous LKG retained')
                print('LKG не изменена: явное подтверждение TARGET не получено; новая версия НЕ release-confirmed');return 0
            promote(args.evidence,lkg_path=args.lkg,operator_sha=answer,**common)
            deployment_outcome(args.evidence,args.project,args.target,args.lkg,reason='Technical + real Telegram smoke + operator confirmed')
            print('LKG CONFIRMED: '+args.target)
        return 0
    except Exception as error:
        print('ERROR: '+(str(error) if isinstance(error,DeployError) else type(error).__name__),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
