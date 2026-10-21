"""Inspect an existing SQLite snapshot read-only; never register or issue money.

The generated master mapping is a candidate for operator review, not an
authorization to activate a topic. No config, SQLite or catalog files are written.
"""
import argparse
from contextlib import closing
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.dnd_shop.currency_units import MASTER_MONEY, UNITS

VALUES = {'Медная монета': Fraction(1, 10000),
          'Серебряная монета': Fraction(1, 100), 'Золотая монета': Fraction(1)}


def inspect(conn, scope):
    conn.row_factory = sqlite3.Row
    items = conn.execute('SELECT * FROM dnd_items WHERE chat_id=? AND thread_id=? ORDER BY id', scope).fetchall()
    currencies = conn.execute('SELECT * FROM dnd_currencies WHERE chat_id=? AND thread_id=? ORDER BY item_id', scope).fetchall()
    by_id = {row['item_id']: row for row in currencies}
    errors, missing, result, candidate = [], [], [], {}
    bases = [row for row in currencies if row['is_base']]
    if len(bases) != 1:
        errors.append('Exactly one registered base currency is required')
    for name in UNITS:
        expected = VALUES[name]
        matches = [row for row in items if row['name_key'] == name.casefold()]
        if not matches:
            missing.append(name)
            continue
        if len(matches) != 1:
            errors.append('Ambiguous item: ' + name)
            continue
        item = matches[0]
        currency = by_id.get(item['id'])
        if currency is None or item['category'] != 'currency' or item['active'] != 1:
            errors.append('Not an active registered currency: ' + name)
            continue
        try:
            quantity = currency['currency_quantity']
            if type(quantity) is not int or quantity <= 0:
                raise ValueError('Invalid currency_quantity')
            value = Fraction(Decimal(currency['base_value'])) / quantity
        except (ValueError, ArithmeticError, TypeError):
            errors.append('Invalid denomination: ' + name)
            continue
        if value != expected:
            errors.append(f'Wrong denomination: {name}: {value} != {expected}')
        if currency['is_base'] != int(name == 'Золотая монета'):
            errors.append('Unexpected base currency flag: ' + name)
        code = item['code']
        if not isinstance(code, str) or not code.strip() or code in candidate.values():
            errors.append('Ambiguous code: ' + name)
        candidate[name] = code
        result.append(dict(name=name, item_id=item['id'], code=code, denomination=str(value),
                           currency_quantity=quantity, base_value=currency['base_value'],
                           expected=str(expected)))
    # Also detect orphan rows / currencies whose item belongs to another scope.
    if any(row['item_id'] not in {item['id'] for item in items} for row in currencies):
        errors.append('Cross-scope or orphan currency')
    for core in VALUES:
        if core not in candidate:
            errors.append('Required core coin missing: ' + core)
    return dict(scope=list(scope), complete=not missing and not errors, errors=errors,
                missing=missing, currencies=result,
                candidate_shop_currency_units={'master': candidate},
                registration_commands=[],
                historical_bundles=[dict(name=item['name'],item_id=item['id'],code=item['code'],
                    registered_currency=item['id'] in by_id,requires_review=True)
                    for item in items if item['name_key'] in
                    {name.casefold() for name in MASTER_MONEY if name not in UNITS}])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Existing snapshot, opened mode=ro/query_only')
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--thread-id', type=int, required=True)
    parser.add_argument('--allow-missing', action='store_true', help='Legacy flag; missing physical coins still fail closed')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        result = inspect(conn, (args.chat_id, args.thread_id))
        if [tuple(row) for row in conn.execute('PRAGMA integrity_check')] != [('ok',)]:
            result['errors'].append('SQLite integrity check failed')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            result['errors'].append('SQLite foreign key check failed')
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        result['errors'].append('Source changed during inspection; discard result')
    result['source_sha256'] = digest
    result['complete'] = not result['missing'] and not result['errors']
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result['errors']) or (bool(result['missing']) and not args.allow_missing))


if __name__ == '__main__':
    raise SystemExit(main())
