"""Normalize TEST wallets only: default read-only dry-run; no option-B rerun."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('BOT_TOKEN','123456:offline-coins')
from app.dnd_shop import coin_evidence as evidence
from app.dnd_shop.coin_migration import canonical,digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run',action='store_true')
    modes.add_argument('--apply',action='store_true')
    for name in ('backup','metadata','runtime-snapshot'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--loot',type=Path,default=ROOT/'data/loot.csv')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--plan-sha256')
    args=parser.parse_args()
    for path in (args.backup,args.metadata,args.runtime_snapshot,args.output,args.plan):
        if path and path.resolve().is_relative_to(ROOT):
            parser.error('Приватные данные и планы должны храниться вне Git.')
    try:
        if args.apply:
            if not args.plan or not args.plan_sha256 or args.output:
                parser.error('--apply требует --plan/--plan-sha256; --output только для dry-run')
            raw=args.plan.read_bytes()
            if digest(raw)!=args.plan_sha256:
                parser.error('SHA файла плана не совпадает с разрешённым.')
            result=evidence.apply(args.backup,args.metadata,args.runtime_snapshot,json.loads(raw),args.plan_sha256)
        else:
            if args.plan or args.plan_sha256:
                parser.error('Dry-run использует --output, без --plan.')
            plan,_=evidence.prepare(args.backup,args.metadata,args.runtime_snapshot,args.loot)
            raw=canonical(plan).encode()
            if args.output:
                fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
            result=dict(dry_run=True,plan_sha256=digest(raw),plan=plan)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as error:
        parser.exit(1,'Нормализация отклонена: '+str(error)+'\n')


if __name__=='__main__':
    main()
