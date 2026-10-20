"""Read-only reconciliation of exact ordinary D&D shop obligations and receipts."""
import hashlib
import json
from collections import defaultdict
from fractions import Fraction


def audit(conn):
    conn.row_factory = __import__('sqlite3').Row
    if conn.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('SQLite integrity / foreign keys failed')
    settings = conn.execute('SELECT * FROM dnd_shop_settings').fetchall()
    for row in settings:
        factor = Fraction(row['factor'])
        if factor <= 0 or str(factor) != row['factor'] or row['settings_version'] not in (1, 2):
            raise ValueError('Invalid shop pricing settings')
        if row['pricing_mode'] == 'm' and row['settings_version'] == 1:
            if factor != 1 or row['enabled']:
                raise ValueError('Historical master pricing must remain quarantined')
        if row['pricing_mode'] == 'b' and factor not in (Fraction('0.0285'), Fraction('0.000285')):
            raise ValueError('Universal price formula differs')
    pending = conn.execute('SELECT * FROM dnd_shop_publications').fetchall()
    for row in pending:
        if hashlib.sha256(row['candidate']).hexdigest() != row['digest'] or not isinstance(json.loads(row['plan']), dict):
            raise ValueError('Publication journal corrupt')
    migrations = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_shop_test_migrations'").fetchone():
        migrations = conn.execute('SELECT * FROM dnd_shop_test_migrations').fetchall()
    for row in migrations:
        plan = json.loads(row['plan'])
        serialized = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(serialized).hexdigest()!=row['plan_sha256'] or plan['id']!=row['id']:
            raise ValueError('TEST migration plan hash differs')
        if row['phase']=='prepared':
            state = conn.execute('SELECT enabled FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?',
                                 (row['chat_id'],row['thread_id'])).fetchone()
            if not state or state[0] or not any(json.loads(p['plan']).get('test_migration_id')==row['id'] for p in pending):
                raise ValueError('Prepared TEST migration must have a stopped store and durable publication')
        else:
            result=json.loads(row['result'])
            if result['id']!=row['id'] or result['plan_sha256']!=row['plan_sha256']:
                raise ValueError('TEST migration receipt differs')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_coin_migrations'").fetchone():
        for row in conn.execute('SELECT * FROM dnd_coin_migrations'):
            plan=json.loads(row['plan'])
            serialized=json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            result=json.loads(row['result'])
            if (hashlib.sha256(serialized).hexdigest()!=row['plan_sha256']
                    or result['plan_sha256']!=row['plan_sha256'] or result['id']!=row['id']
                    or any(Fraction(w['before_value'])!=Fraction(w['after_value']) for w in plan['wallets'])):
                raise ValueError('Coin normalization journal differs')
    balances = defaultdict(Fraction)
    moves = conn.execute('SELECT * FROM dnd_shop_credit_moves ORDER BY id').fetchall()
    for move in moves:
        key = move['chat_id'], move['thread_id'], move['user_id']
        amount = Fraction(move['amount'])
        if amount <= 0 or str(amount) != move['amount']:
            raise ValueError('Invalid journal amount')
        balances[key] += amount if move['direction'] == 'add' else -amount
        if balances[key] < 0 or str(balances[key]) != move['balance_after']:
            raise ValueError('Credit journal does not reconcile')
    for row in conn.execute('SELECT * FROM dnd_shop_credit'):
        key = row['chat_id'], row['thread_id'], row['user_id']
        if str(balances.pop(key, Fraction(0))) != row['balance']:
            raise ValueError('Credit balance does not reconcile')
    if balances:
        raise ValueError('Journal owner missing')
    trades = conn.execute('SELECT * FROM dnd_shop_trades ORDER BY id').fetchall()
    for trade in trades:
        value = Fraction(trade['value'])
        if value != Fraction(trade['price']) * (Fraction(3, 100) if trade['kind'] == 'sell' else 1):
            raise ValueError('Trade price invariant failed')
        net = Fraction(0)
        for money in conn.execute('SELECT * FROM dnd_shop_money WHERE trade_id=?', (trade['id'],)):
            amount = money['quantity'] * Fraction(money['denomination'])
            net += amount if money['direction'] == 'debit' else -amount
        adjustments = conn.execute('SELECT * FROM dnd_shop_credit_moves WHERE trade_id=?', (trade['id'],)).fetchall()
        for move in adjustments:
            if (move['chat_id'], move['thread_id'], move['user_id']) != (trade['chat_id'], trade['thread_id'], trade['user_id']):
                raise ValueError('Trade/credit owner mismatch')
            net += Fraction(move['amount']) if move['direction'] == 'spend' else -Fraction(move['amount'])
        if net != (value if trade['kind'] == 'buy' else -value):
            raise ValueError('Coin + credit conservation failed')
        operation = conn.execute('SELECT result FROM dnd_shop_operations WHERE token=?', (trade['operation_token'],)).fetchone()
        if not operation or not operation[0]:
            raise ValueError('Completed trade has no receipt')
    redemptions = conn.execute("SELECT * FROM dnd_shop_credit_redemptions WHERE result IS NOT NULL").fetchall()
    for row in redemptions:
        move = conn.execute('SELECT * FROM dnd_shop_credit_moves WHERE redemption_token=?', (row['token'],)).fetchone()
        coins = conn.execute('SELECT * FROM dnd_shop_credit_coins WHERE redemption_token=?', (row['token'],)).fetchall()
        if not move or Fraction(move['amount']) != Fraction(row['expected']) or sum(
                (r['quantity'] * Fraction(r['denomination']) for r in coins), Fraction(0)) != Fraction(row['expected']):
            raise ValueError('Redemption conservation failed')
    return dict(trades=len(trades), credit_moves=len(moves), redemptions=len(redemptions),
                shops=len(settings), pending_publications=len(pending),
                blocked_master_migrations=sum(r['pricing_mode']=='m' and r['settings_version']==1 for r in settings),
                test_migrations=len(migrations),
                pending_test_migrations=sum(r['phase']=='prepared' for r in migrations), errors=0)
