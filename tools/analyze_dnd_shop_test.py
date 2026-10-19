"""Analyze a fresh authorized TEST snapshot only; never writes the supplied SQLite."""
import argparse
import ast
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.dnd_shop.builder import read_master
from app.dnd_shop.diagnostics import analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Local online backup, not the live database')
    parser.add_argument('--metadata', type=Path, help='Capture provenance and TEST topics mapping, kept private')
    parser.add_argument('--scope', default='-5320347021:0')
    parser.add_argument('--output', required=True, type=Path, help='Private report outside Git')
    parser.add_argument('--loot', type=Path, default=ROOT/'data/loot.csv')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT) or args.source.resolve().is_relative_to(ROOT):
        parser.error('Private database and report must stay outside the Git checkout')
    before = hashlib.sha256(args.source.read_bytes()).hexdigest()
    scope = tuple(map(int,args.scope.split(':')))
    with closing(sqlite3.connect(args.source.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        master = read_master(args.loot)
        report = analyze(conn, scope, master=master)
        if args.metadata:
            metadata=json.loads(args.metadata.read_text(encoding='utf-8'))
            if (metadata['sha256']!=before or metadata['hostname']!='VM-338333'
                    or metadata['git_sha'] not in ('07170497d1bd375b5bd9f265112eddc7e024d08c','6ef5d8bbac29f2b938b9c21448e1466abe676dc1')
                    or metadata['master_sha256']!=master.digest or tuple(metadata['scope'])!=scope
                    or hashlib.sha256(metadata['topics_source'].encode('utf-8')).hexdigest()!=metadata['topics_sha256']):
                raise ValueError('TEST provenance mismatch')
            declarations=[]
            for node in ast.parse(metadata['topics_source']).body:
                if isinstance(node,ast.Assign) and any(
                    isinstance(target,ast.Subscript) and isinstance(target.slice,ast.Constant)
                    and target.slice.value=='shop_currency_units' for target in node.targets):
                    declarations.append(ast.literal_eval(node.value))
            if len(declarations)!=1:
                raise ValueError('Configuration shape requires separate read-only inspection')
            codes=dict(conn.execute('SELECT code,id FROM dnd_items WHERE chat_id=? AND thread_id=?',scope))
            configured={name.casefold():codes[code] for name,code in declarations[0]['master'].items()}
            if configured!=json.loads(report['settings']['currency_units']):
                raise ValueError('TEST config and saved currency mappings differ')
            report['capture']={key:metadata[key] for key in
                               ('hostname','git_sha','captured_utc','topics_sha256','runtime_sha256','master_sha256')}
            report['topic_mapping_verified']=True
    report['source_sha256'] = before
    report['source_unchanged'] = hashlib.sha256(args.source.read_bytes()).hexdigest() == before
    if not report['source_unchanged']:
        raise ValueError('Snapshot changed during analysis')
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(trades=report['trades'],currencies=len(report['monetary']),
                         wallets=len(report['wallets']),compatible=report['compatible'],
                         source_unchanged=True,report=str(args.output)),ensure_ascii=False))


if __name__=='__main__':
    main()
