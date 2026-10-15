"""Explicit operator-observed real Telegram attestations. Mocks never qualify."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
from deploy_helpers import DeployError
import release_state as store

GAME_CASES=('menu','events-rps','events-lab','events-persistence','gacha','shadow-mythic','boss','combat-v2','tower','equipment','village','duels','wallet','admin','telegram-ui')
FAULT_CASES=('double-click','concurrent-actions','stale-callback','telegram-error','boss-restart','events-restart','wallet-restart','duel-restart','tower-recovery','inventory-restart','wrong-user-topic','access-denied','db-error','duplicate-process','no-double-payout')
INFRA_CASES=('bootstrap','units','observer','preflight','preflight-failure','deployment','downtime','backup','runtime-init','startup','journal','initial-lkg','compatible-rollback','incompatible-rollback','kill-reboot-repeat')
STAGING_CASES=GAME_CASES+FAULT_CASES+INFRA_CASES
READONLY_CASES=('telegram-launcher','personal-navigation','events-both-menus','collection-equipment-progress','wallet-history-readonly','world-topic-access','ephemeral-stale-callback')
MAX_AGE=1800


def fresh(record,sha,kind,max_age=MAX_AGE):
    if record.get('sha')!=sha:raise DeployError('Semantic evidence SHA is STALE')
    if record.get('kind')!=kind or record.get('status')!='PASS' or record.get('source')!='operator_observed_real_telegram':
        raise DeployError('Real Telegram evidence is absent/failed; mock smoke cannot qualify')
    try:age=time.time()-datetime.fromisoformat(record['finished_at']).timestamp()
    except (ValueError,KeyError,TypeError):raise DeployError('Semantic timestamp UNKNOWN')
    if not 0<=age<=max_age:raise DeployError('Semantic evidence is STALE')
    if record.get('operator_confirmed') is not True or not re.fullmatch('[0-9a-f]{64}',record.get('confirmation_hash','')):
        raise DeployError('Semantic operator confirmation missing')
    if record['confirmation_hash']!=store.digest({k:v for k,v in record.items() if k!='confirmation_hash'}):
        raise DeployError('Semantic confirmation hash mismatch')
    if record.get('api',{}).get('status')!='PASS' or record['api'].get('method')!='getMe' or record['api'].get('real') is not True:
        raise DeployError('Real Telegram connectivity NOT VERIFIED')
    return record


def telegram_identity(project):
    # Read-only getMe; never polling, sendMessage, gameplay or token logging.
    from dotenv import dotenv_values
    token=dotenv_values(Path(project)/'.env').get('BOT_TOKEN')
    if not token or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token):raise DeployError('Telegram identity unavailable')
    try:
        request=urllib.request.Request('https://api.telegram.org/bot'+token+'/getMe',headers={'User-Agent':'shpakdnd-release-smoke'})
        with urllib.request.urlopen(request,timeout=15) as response:data=json.loads(response.read())
        if data.get('ok') is not True or data.get('result',{}).get('is_bot') is not True:raise ValueError('not bot')
        identity=hashlib.sha256(str(data['result']['id']).encode()).hexdigest()
    except Exception:raise DeployError('Telegram getMe failed; real smoke NOT VERIFIED') from None
    return {'status':'PASS','method':'getMe','real':True,'bot_identity_hash':identity,'checked_at':store.utc_now()}


def verify_cases(record,expected):
    cases=record.get('cases',{})
    if not set(expected)<=set(cases) or set(cases)-set(STAGING_CASES+READONLY_CASES):raise DeployError('Semantic/staging checklist incomplete')
    for name in expected:
        case=cases[name]
        if case.get('status')!='PASS' or not re.fullmatch('[0-9a-f]{64}',case.get('artifact_hash','')):
            raise DeployError('Scenario lacks successful observation/artifact')
    return record


def validate_staging(path,sha,*,full=True):
    value=fresh(store.read_record(path),sha,'REAL_TELEGRAM_STAGING',max_age=7*86400)
    verify_cases(value,STAGING_CASES if full else GAME_CASES+FAULT_CASES)
    if value.get('environment_kind')!='isolated_staging' or not value.get('environment_hash'):
        raise DeployError('Independent staging identity missing')
    return value


def validate_for_lkg(evidence,project,target,health):
    semantic_path=Path(evidence).with_suffix('.semantic.json')
    value=fresh(store.read_record(semantic_path),target,'REAL_TELEGRAM_POSTDEPLOY')
    verify_cases(value,READONLY_CASES)
    if (value.get('deployment_id')!=Path(evidence).stem or value.get('evidence_hash')!=store.file_hash(evidence)
            or value.get('process')!=health['process'] or value.get('systemd_hash')!=health['systemd']['config_hash']
            or value.get('tooling_hash')!=health['systemd']['tooling_hash']
            or value.get('project_hash')!=store.digest(str(Path(project).resolve()))):
        raise DeployError('Semantic proof belongs to another deployment/process/configuration')
    import systemd_state as units
    staged=units.staging_identity(health['systemd'])
    if value.get('environment_kind')!=('isolated_staging' if staged else 'production'):raise DeployError('Semantic environment differs from installed bot runtime')
    stage=validate_staging(value['staging_path'],target,full=False)
    if store.file_hash(value['staging_path'])!=value.get('staging_hash'):raise DeployError('Staging evidence changed')
    api=telegram_identity(project)
    if api['bot_identity_hash']!=value['api']['bot_identity_hash']:raise DeployError('Telegram bot identity changed')
    if value.get('environment_kind')=='production' and api['bot_identity_hash']==stage['api']['bot_identity_hash']:
        raise DeployError('Production and staging bot identity must differ')
    return {'status':'PASS','sha':target,'deployment_id':value['deployment_id'],'source':value['source'],
            'finished_at':value['finished_at'],'proof_hash':store.file_hash(semantic_path),
            'confirmation_hash':value['confirmation_hash'],'staging_hash':value['staging_hash']}


def attest(project,sha,scenario_file,output,*,kind,environment,operator_sha,evidence=None,staging=None):
    store.full_sha(sha);project=Path(project).resolve();store.outside_project(Path(output).parent,project)
    if operator_sha!=sha:raise DeployError('Full operator SHA confirmation required')
    if store.command(['git','-C',project,'rev-parse','HEAD'])!=sha or store.command(['git','-C',project,'status','--porcelain','--untracked-files=all']):
        raise DeployError('Attestation checkout SHA/clean state mismatch')
    if environment not in {'isolated_staging','production'}:raise DeployError('Unknown environment kind')
    if kind=='staging':
        import systemd_state as units
        installed=units.verify_installed(project=project,active=True)
        if environment!='isolated_staging' or not units.staging_identity(installed):raise DeployError('Real gameplay only on verified isolated staging runtime')
    expected=STAGING_CASES if kind=='staging' else READONLY_CASES
    store.trusted_path(scenario_file,private=True)
    raw=json.loads(Path(scenario_file).read_text(encoding='utf-8'))
    if raw.get('sha')!=sha or raw.get('environment_kind')!=environment:raise DeployError('Checklist SHA/environment mismatch')
    if raw.get('source')!='operator_observed_real_telegram' or raw.get('operator_sha')!=sha:
        raise DeployError('Explicit real observation and operator SHA required')
    if set(raw.get('cases',{}))!=set(expected):raise DeployError('Checklist NOT_RUN/incomplete')
    proof=dict(version=1,kind='REAL_TELEGRAM_STAGING' if kind=='staging' else 'REAL_TELEGRAM_POSTDEPLOY',sha=sha,
               status='UNKNOWN',source=raw['source'],environment_kind=environment,operator_confirmed=False,cases={})
    store.save_record(output,proof) # Interrupted/API failure never publishes PASS.
    try:
        for name,case in raw['cases'].items():
            if kind=='staging' and name in INFRA_CASES and case.get('status')=='NOT_RUN':
                proof['cases'][name]={'status':'NOT_RUN'};continue
            if case.get('status')!='PASS' or not all(case.get(k) for k in ('initial','action','expected','actual','date','executor')):
                raise DeployError('Incomplete observed scenario: '+name)
            age=time.time()-datetime.fromisoformat(case['date']).timestamp()
            if not 0<=age<=7*86400:raise DeployError('Scenario observation stale')
            artifact=Path(case['artifact']);store.outside_project(artifact,project);store.trusted_path(artifact,private=True)
            if not artifact.stat().st_size:raise DeployError('Empty observation artifact')
            proof['cases'][name]={'status':'PASS','artifact_hash':store.file_hash(artifact),'observed_at':case['date']}
        api=telegram_identity(project);proof['api']=api
        identity=raw.get('environment_id','')
        if not re.fullmatch('[A-Za-z0-9_-]{3,80}',identity):raise DeployError('Opaque environment identity required')
        proof['environment_hash']=store.digest([identity,api['bot_identity_hash']])
        proof['project_hash']=store.digest(str(project))
        if kind=='postdeploy':
            import release_preflight as pref
            import systemd_state as units
            report=json.loads(Path(evidence).read_text(encoding='utf-8'))
            pref.validate_evidence(evidence,old=report['old_sha'],target=sha,tools=Path(__file__).parent)
            health=store.read_record(Path(evidence).with_suffix('.health.json'))
            if health['sha']!=sha or health['status']!='PASS':raise DeployError('Technical health missing')
            units.health(health['systemd'],since=health['since'],previous=health['process'])
            validate_staging(staging,sha,full=False)
            proof.update(deployment_id=Path(evidence).stem,evidence_hash=store.file_hash(evidence),process=health['process'],
                         systemd_hash=health['systemd']['config_hash'],tooling_hash=health['systemd']['tooling_hash'],
                         staging_path=str(Path(staging).resolve()),staging_hash=store.file_hash(staging))
        proof['scope']='FULL_STAGING' if kind=='staging' and all(c.get('status')=='PASS' for c in proof['cases'].values()) else 'GAMEPLAY' if kind=='staging' else 'POSTDEPLOY_READONLY'
        proof.update(status='PASS',operator_confirmed=True,finished_at=store.utc_now())
        proof['confirmation_hash']=store.digest({k:v for k,v in proof.items() if k!='confirmation_hash'})
        store.save_record(output,proof);return proof
    except BaseException:
        proof.update(status='UNKNOWN',operator_confirmed=False,finished_at=store.utc_now());store.save_record(output,proof);raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=['staging','postdeploy'])
    for name in ('project','sha','checklist','output','environment'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--template',action='store_true')
    parser.add_argument('--evidence');parser.add_argument('--staging')
    args=parser.parse_args()
    try:
        store.require_root()
        if args.template:
            store.outside_project(Path(args.checklist).parent,args.project);store.private_directory(Path(args.checklist).parent)
            if Path(args.checklist).exists():raise DeployError('Checklist already exists; preserve observations')
            names=STAGING_CASES if args.kind=='staging' else READONLY_CASES
            template={'sha':store.full_sha(args.sha),'source':'operator_observed_real_telegram','operator_sha':'',
                      'environment_kind':args.environment,'environment_id':'SET_OPAQUE_VM_ID',
                      'cases':{name:{'status':'NOT_RUN','initial':'','action':'','expected':'','actual':'','artifact':'','date':'','executor':''} for name in names}}
            store.atomic_bytes(args.checklist,json.dumps(template,indent=2).encode())
            print('Checklist template NOT_RUN; no attestation/LKG created');return 0
        if args.kind=='postdeploy':
            import release_preflight as pref
            pref.require_deployment_lock(Path(args.project)/'shpakdnd.db')
            if not args.evidence or not args.staging or Path(args.output)!=Path(args.evidence).with_suffix('.semantic.json'):
                raise DeployError('Bound postdeploy evidence/staging/output required')
        elif args.environment!='isolated_staging':raise DeployError('Full gameplay smoke only on independent staging')
        try:answer=input('Confirm all listed real Telegram observations with full SHA:\n').strip()
        except EOFError:answer=''
        attest(args.project,args.sha,args.checklist,args.output,kind=args.kind,environment=args.environment,
               operator_sha=answer,evidence=args.evidence,staging=args.staging)
        print('Operator-observed real Telegram proof recorded; LKG unchanged');return 0
    except Exception as error:print('NOT VERIFIED: '+(str(error) if isinstance(error,DeployError) else type(error).__name__),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
