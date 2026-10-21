"""Offline ordinary D&D index/build/validate/publish/audit; never starts polling."""
import argparse
import json
import sqlite3
from fractions import Fraction
from contextlib import closing
from pathlib import Path

from app.config import BASE_DIR, DB_PATH, DND_LOOT_CSV_PATH, DND_SHOP_CSV_PATH
from app.topics import TOPIC_SETTINGS
from app.dnd_shop.builder import (MAP_COLUMNS, build, csv_bytes, index, read_map,
                                  read_master, require_scopes)
from app.dnd_shop.catalog import atomic_bytes, catalog_lock, publish, read_catalog
from app.dnd_shop.service import Shop


def scope(text):
    try:
        chat, thread = text.split(':')
        from app.dnd_shop.catalog import scope_values
        return scope_values(chat, thread)
    except ValueError as error:
        raise argparse.ArgumentTypeError('Expected chat_id:thread_id') from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('index', 'build', 'validate', 'publish', 'audit', 'rollback-check'))
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--target', type=Path, default=DND_SHOP_CSV_PATH)
    parser.add_argument('--loot', type=Path, default=DND_LOOT_CSV_PATH)
    parser.add_argument('--map', type=Path)
    parser.add_argument('--offers', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--scope', type=scope, action='append', default=[])
    parser.add_argument('--target-ref', help='Exact reviewed Git target for the read-only rollback gate')
    parser.add_argument('--db', type=Path, default=DB_PATH)
    args = parser.parse_args()
    for path in (args.target, args.map, args.offers, args.output, args.loot):
        if path and path.resolve().is_relative_to(BASE_DIR.resolve()):
            parser.error('Live sources, staging, map and runtime must be outside checkout / watcher.')
    if not args.db.is_file():
        parser.error('An initialized existing SQLite is required; no empty database will be created.')
    shop = Shop(args.db, args.target, loot_path=args.loot)
    try:
        if args.command in ('audit', 'rollback-check'):
            from app.dnd_shop.audit import audit
            with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                conn.execute('BEGIN')
                report = audit(conn)
                if args.command == 'rollback-check':
                    from app.dnd_shop.rollback import rollback_check
                    report['rollback'] = rollback_check(conn, args.target_ref, BASE_DIR)
            print(json.dumps(report, ensure_ascii=False))
            return
        with catalog_lock(args.target, exclusive=args.command == 'index'):
            if args.command == 'index':
                if not args.map:
                    parser.error('index requires --map')
                require_scopes(TOPIC_SETTINGS, args.scope)
                master = read_master(args.loot)
                with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                    conn.execute('BEGIN')
                    mapping = index(master, previous=read_map(args.map) if args.map.exists() else None,
                                    conn=conn, scopes=args.scope)
                atomic_bytes(args.map, csv_bytes(MAP_COLUMNS, [dict(code=k, name=v) for k, v in mapping.items()]))
                print(json.dumps(dict(indexed=len(mapping), master_sha256=master.digest, errors=0)))
                return
            if args.command == 'build':
                if not all((args.map, args.offers, args.output)):
                    parser.error('build requires --map, --offers, --output')
                if args.output.resolve() == args.target.resolve():
                    parser.error('Build only to a staging file; use publish for runtime.')
                with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                    blocked = {(chat,thread) for chat,thread in conn.execute(
                        "SELECT chat_id,thread_id FROM dnd_shop_settings WHERE pricing_mode='m' AND settings_version=1")}
                    if blocked.intersection(args.scope):
                        raise ValueError('Legacy -m requires a reviewed pricing migration before build.')
                    factors = {(chat, thread): Fraction(value) for chat, thread, value in conn.execute(
                        'SELECT chat_id,thread_id,factor FROM dnd_shop_settings')}
                data, report = build(read_master(args.loot), read_map(args.map), args.offers,
                    args.scope, TOPIC_SETTINGS, previous=read_catalog(args.target) if args.target.exists() else (),
                    price_factors=factors)
                atomic_bytes(args.output, data)
                shop.validate_staged(args.output)
                print(json.dumps(report, ensure_ascii=False))
                return
            if not args.source:
                parser.error('validate/publish requires a staged source')
            if args.command == 'validate':
                shop.validate_staged(args.source)
                print('OK: catalog validated; no SQLite changes')
                return
        # publish owns the exclusive catalog lock, then SQLite BEGIN IMMEDIATE.
        publish(args.source, args.target, shop.validate_staged)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Catalog rejected: {error}\n')
    print('OK: catalog atomically published; previous snapshot retained')


if __name__ == '__main__':
    main()
