"""Verified per-topic pricing and currency declarations; no name guessing."""
import hashlib
import json
from fractions import Fraction

from app.dnd_shop.catalog import CatalogError

# Explicit original monetary units, not a substring classifier. Coin purses remain items.
MASTER_MONEY = {
    'Медная монета': ('Медная монета', 1),
    '10 медных монет': ('Медная монета', 10),
    '50 медных монет': ('Медная монета', 50),
    'Серебряная монета': ('Серебряная монета', 1),
    '5 серебряных монет': ('Серебряная монета', 5),
    '10 серебряных монет': ('Серебряная монета', 10),
    '50 серебряных монет': ('Серебряная монета', 50),
    'Золотая монета': ('Золотая монета', 1),
    '2 золотые монеты': ('Золотая монета', 2),
    '5 золотых монет': ('Золотая монета', 5),
    '10 золотых монет': ('Золотая монета', 10),
    '50 золотых монет': ('Золотая монета', 50),
    '100 золотых монет': ('Золотая монета', 100),
    '500 золотых монет': ('Золотая монета', 500),
    '1000 золотых монет': ('Золотая монета', 1000),
}
COPPER_FACTOR = Fraction(285, 10000)
GOLD_FACTOR = Fraction(285, 1000000)


def preflight(shop, conn, scope, mode, master):
    if mode not in ('b', 'm'):
        raise CatalogError('Использование: /shopcreate -b или /shopcreate -m.')
    topic = shop.scope(*scope)
    session = dict(chat_id=scope[0], thread_id=scope[1])
    rows, denominations = shop.currencies(conn, session)
    base = next(r for r in rows if r['is_base'])
    if denominations[base['id']] != 1:
        raise CatalogError('Базовая валюта должна иметь номинал 1.')
    declaration = topic.get('shop_currency_units')
    if not isinstance(declaration, dict) or not isinstance(declaration.get('master'), dict):
        raise CatalogError('Валюты не настроены: задайте shop_currency_units.master в TOPIC_SETTINGS, используя коды dnd_items.')
    by_code = {r['code']: r for r in rows}
    if len(by_code) != len(rows):
        raise CatalogError('Неоднозначный реестр валют.')
    required = {name.casefold() for name in MASTER_MONEY if name.casefold() in master.by_name}
    declared = {}
    if len(declaration['master']) > 32:
        raise CatalogError('Слишком много денежных единиц в конфигурации темы.')
    for name, code in declaration['master'].items():
        if not isinstance(name, str):
            raise CatalogError('Название денежной единицы должно быть строкой.')
        key = name.strip().casefold()
        if key not in master.by_name or key in declared or not isinstance(code, str) or code not in by_code:
            raise CatalogError('Денежная единица мастера не зарегистрирована активной валютой: ' + str(name))
        row = by_code[code]
        named = conn.execute('SELECT id,category FROM dnd_items WHERE chat_id=? AND thread_id=? AND name_key=?',
                             (*scope, key)).fetchone()
        if named and (named['id'] != row['id'] or named['category'] != 'currency'):
            raise CatalogError('Денежная единица конфликтует с прежним предметом: ' + name)
        declared[key] = row['id']
    if not required <= declared.keys() or len(set(declared.values())) != len(declared):
        raise CatalogError('Зарегистрируйте все денежные единицы loot.csv и их однозначные соответствия в shop_currency_units.master.')
    for name, (unit, quantity) in MASTER_MONEY.items():
        if name.casefold() not in required:
            continue
        reference = declared.get(unit.casefold())
        if not reference or denominations[declared[name.casefold()]] != quantity * denominations[reference]:
            raise CatalogError('Номинал денежной позиции не соответствует явному количеству: ' + name)
    for item in conn.execute("SELECT * FROM dnd_items WHERE chat_id=? AND thread_id=? AND category='currency'", scope):
        if item['name_key'] in master.by_name and item['id'] not in denominations:
            raise CatalogError('Валюта из каталога не имеет активного номинала: ' + item['name'])
    factor = Fraction(1)
    if mode == 'b':
        if any(not isinstance(declaration.get(key), str) for key in ('universal_copper', 'universal_gold')):
            raise CatalogError('Задайте коды universal_copper и universal_gold.')
        copper = by_code.get(declaration.get('universal_copper'))
        gold = by_code.get(declaration.get('universal_gold'))
        if not copper or not gold or copper['id'] == gold['id']:
            raise CatalogError('Для -b укажите зарегистрированные universal_copper и universal_gold.')
        if denominations[gold['id']] != 100 * denominations[copper['id']]:
            raise CatalogError('Несогласованные номиналы: 100 универсальных медных должны равняться 1 универсальной золотой.')
        if base['id'] == copper['id']:
            factor = COPPER_FACTOR
        elif base['id'] == gold['id']:
            factor = GOLD_FACTOR
        else:
            raise CatalogError('База темы не соответствует явно заданной универсальной медной или золотой.')
    signature = json.dumps(dict(declaration=declaration, currencies=[
        [r['id'], r['code'], r['name_key'], str(denominations[r['id']]), r['is_base']] for r in rows
    ]), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return dict(pricing_mode=mode, factor=str(factor), base_item_id=base['id'],
                currency_signature=hashlib.sha256(signature.encode()).hexdigest(),
                currency_units=json.dumps(declared, ensure_ascii=False, sort_keys=True),
                base_name=base['name'])
