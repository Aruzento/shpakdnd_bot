"""Read-only reconciliation of exact ordinary D&D shop obligations and receipts."""
from collections import defaultdict
from fractions import Fraction


def audit(conn):
    conn.row_factory = __import__('sqlite3').Row
    if conn.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('SQLite integrity / foreign keys failed')
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
    return dict(trades=len(trades), credit_moves=len(moves), redemptions=len(redemptions), errors=0)
