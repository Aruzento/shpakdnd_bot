"""Read-only diagnosis; proposed nominals are never applied to any database."""
import json
from collections import defaultdict
from decimal import Decimal
from fractions import Fraction

from app.dnd_shop.pricing import MASTER_MONEY
from app.dnd_shop.catalog import MAX_INTEGER

PRIMARY_COPPER = {'Медная монета': 1, 'Серебряная монета': 100, 'Золотая монета': 10000}


def analyze(conn, scope, *, units=None, master=None):
    """Use explicit stored currency IDs, preserving historical units in reports."""
    conn.row_factory = __import__('sqlite3').Row
    if conn.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
        raise ValueError('SQLite integrity failed')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('SQLite foreign keys failed')
    state = conn.execute('SELECT * FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?', scope).fetchone()
    if units is None:
        if not state:
            raise ValueError('Explicit currency mappings are required; no name inference')
        units = json.loads(state['currency_units'])
    declared = {name.casefold(): item_id for name, item_id in units.items()}
    rows = conn.execute('''SELECT i.*,c.base_value,c.currency_quantity,c.is_base
        FROM dnd_currencies c JOIN dnd_items i ON i.id=c.item_id
        WHERE c.chat_id=? AND c.thread_id=? AND i.chat_id=c.chat_id AND i.thread_id=c.thread_id''', scope).fetchall()
    if any(r['category'] != 'currency' or not r['active'] for r in rows):
        raise ValueError('Currency registry contains inactive or contradictory entries')
    values = {}
    for r in rows:
        if type(r['currency_quantity']) is not int or r['currency_quantity'] <= 0:
            raise ValueError('Invalid currency quantity')
        value = Fraction(Decimal(str(r['base_value']))) / r['currency_quantity']
        if value <= 0:
            raise ValueError('Nonpositive currency nominal')
        values[r['id']] = value
    bases = [r for r in rows if r['is_base']]
    if len(bases) != 1 or values[bases[0]['id']] != 1:
        raise ValueError('Exactly one active base nominal 1 is required')
    missing = [name for name in MASTER_MONEY if declared.get(name.casefold()) not in values]
    if missing or len({declared.get(name.casefold()) for name in MASTER_MONEY}) != 15:
        raise ValueError('All 15 distinct explicit monetary mappings are required: ' + ', '.join(missing))
    base = bases[0]
    anchors = [quantity for name, quantity in PRIMARY_COPPER.items() if declared[name.casefold()] == base['id']]
    if len(anchors) != 1:
        raise ValueError('Migration plan needs a reviewed base-unit anchor; base is not one of the three declared source units')
    factor = Fraction(1, anchors[0])
    proposed = dict(values)
    monetary = []
    by_id = {r['id']: r for r in rows}
    for name, (primary, quantity) in MASTER_MONEY.items():
        item_id = declared[name.casefold()]
        wanted = factor * PRIMARY_COPPER[primary] * quantity
        proposed[item_id] = wanted
        monetary.append(dict(source_name=name, item_id=item_id, code=by_id[item_id]['code'],
                             current=str(values[item_id]), proposed=str(wanted),
                             compatible=values[item_id] == wanted))
    registry = defaultdict(list)
    for item in conn.execute('SELECT id,name_key FROM dnd_items WHERE chat_id=? AND thread_id=?', scope):
        registry[item['name_key']].append(item['id'])
    wallets = defaultdict(lambda: [Fraction(0), Fraction(0)])
    issues, coins = [], []
    for inv in conn.execute('SELECT * FROM inventory WHERE chat_id=? AND thread_id=?', scope):
        item_id = inv['item_id']
        candidates = registry[inv['name'].strip().casefold()]
        if item_id is None:
            if len(candidates) != 1:
                issues.append(dict(inventory_id=inv['id'], reason='ambiguous legacy item_id'))
                continue
            item_id = candidates[0]
        elif candidates != [item_id]:
            issues.append(dict(inventory_id=inv['id'], reason='inventory identity differs from registry'))
            continue
        if item_id not in values:
            continue
        quantity = inv['quantity']
        if type(quantity) is not int or quantity <= 0:
            issues.append(dict(inventory_id=inv['id'], reason='invalid coin quantity'))
            continue
        before, after = quantity * values[item_id], quantity * proposed[item_id]
        wallets[inv['username']][0] += before
        wallets[inv['username']][1] += after
        preserving = Fraction(quantity) * values[item_id] / proposed[item_id]
        coins.append(dict(inventory_id=inv['id'], username=inv['username'], item_id=item_id, quantity=quantity,
                          legacy=inv['item_id'] is None, current_value=str(before), proposed_value=str(after),
                          preserving_quantity=str(preserving),
                          preserving_quantity_safe=preserving.denominator==1 and 0<preserving<=MAX_INTEGER))
    history = []
    for trade in conn.execute('SELECT * FROM dnd_shop_trades WHERE chat_id=? AND thread_id=? ORDER BY id', scope):
        entry = dict(id=trade['id'], kind=trade['kind'], saved_price=trade['price'], saved_value=trade['value'],
                     item_id=trade['item_id'], sku=trade['sku'], created_at=trade['created_at'])
        if master:
            binding = conn.execute('SELECT original_name_key FROM dnd_shop_skus WHERE chat_id=? AND thread_id=? AND code=?',
                                   (*scope, trade['sku'])).fetchone()
            item = master.by_name.get(binding[0]) if binding else None
            if item and item.name.casefold() not in declared:
                entry['source_price'] = str(item.price)
                entry['prospective_price'] = str(item.price * factor)
                entry['prospective_value'] = str(item.price * factor * (Fraction(3,100) if trade['kind']=='sell' else 1))
        history.append(entry)
    credit = [dict(r) for r in conn.execute('SELECT * FROM dnd_shop_credit WHERE chat_id=? AND thread_id=?', scope)]
    for row in credit:
        if str(Fraction(row['balance'])) != row['balance'] or Fraction(row['balance']) < 0:
            raise ValueError('Invalid credit')
    return dict(scope=list(scope), base_item_id=base['id'], base_name=base['name'], proposed_factor=str(factor),
                settings=dict(state) if state else None, monetary=monetary,
                compatible=all(r['compatible'] for r in monetary), inventory_issues=issues, coins=coins,
                wallet_totals_complete=not issues,
                wallets=[dict(username=user, current=str(pair[0]), proposed=str(pair[1]), delta=str(pair[1]-pair[0]))
                         for user,pair in sorted(wallets.items())],
                history=history, credit=credit, trades=len(history),
                automatic_change_allowed=False, source_changes=0)
