"""Prepare an immutable TEST option-B plan; --apply is guarded to VM-338333 only."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('BOT_TOKEN','123456:offline-test-b-migrator')

from app.dnd_shop.service import Shop
from app.dnd_shop.test_migration import (DB, RUNTIME, LOOT, apply, canonical, digest, prepare)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true',help='Default; source SQLite/catalogs remain unchanged')
    mode.add_argument('--apply',action='store_true',help='Only after independent review and separate TEST authorization')
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--metadata',type=Path,required=True)
    parser.add_argument('--runtime-snapshot',type=Path,required=True)
    parser.add_argument('--loot',type=Path,default=ROOT/'data/loot.csv',help='Dry-run original master')
    parser.add_argument('--output',type=Path,help='Private plan JSON outside Git; dry-run only')
    parser.add_argument('--plan',type=Path,help='Approved plan JSON; required for apply')
    parser.add_argument('--plan-sha256',help='Explicit reviewed SHA printed by dry-run; required for apply')
    args=parser.parse_args()
    for path in (args.backup,args.metadata,args.runtime_snapshot,args.output,args.plan):
        if path and path.resolve().is_relative_to(ROOT):
            parser.error('Backups, private metadata and plans must stay outside Git.')
    try:
        if args.apply:
            if not args.plan or not args.plan_sha256 or args.output:
                parser.error('--apply requires --plan and --plan-sha256; --output is dry-run only')
            raw_plan=args.plan.read_bytes()
            if digest(raw_plan)!=args.plan_sha256:
                parser.error('Plan file SHA-256 differs from explicit approval')
            plan=json.loads(raw_plan)
            result=apply(Shop(DB,RUNTIME,loot_path=LOOT),args.backup,args.metadata,
                         args.runtime_snapshot,plan,args.plan_sha256)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        else:
            if args.plan or args.plan_sha256:
                parser.error('Use --output for dry-run; --plan applies only with --apply')
            plan,_=prepare(args.backup,args.metadata,args.runtime_snapshot,args.loot)
            if args.output:
                descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'wb') as output:
                    output.write(canonical(plan).encode())
                    output.flush()
                    os.fsync(output.fileno())
            print(json.dumps(dict(dry_run=True,plan_sha256=digest(canonical(plan).encode()),plan=plan),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError,OSError,__import__('sqlite3').Error) as error:
        parser.exit(1,'TEST migration rejected: '+str(error)+'\n')


if __name__=='__main__':
    main()
