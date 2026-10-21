"""Verified per-topic pricing and currency declarations; no name guessing."""
import hashlib
import json
from fractions import Fraction

from app.dnd_shop.catalog import CatalogError

from app.dnd_shop.currency_units import MASTER_MONEY, UNITS

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
    # -m needs only physical coins. Legacy declarations may retain verified
    # bundle IDs for an explicit wallet-normalization plan, never as a requirement.
    # The existing universal-mode preflight/formula is deliberately retained.
    required = ({name.casefold() for name in UNITS} if mode == 'm' else
                {name.casefold() for name in MASTER_MONEY if name.casefold() in master.by_name})
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
    if mode == 'b' and not required <= declared.keys():
        raise CatalogError('Прежний профиль -b требует отдельного рассмотрения; справочные связки регистрировать не нужно.')
    if not required <= declared.keys() or len(set(declared.values())) != len(declared):
        raise CatalogError('Сопоставьте необходимые физические монеты в shop_currency_units.master; исторические связки регистрировать не нужно.')
    for name, (unit, quantity) in MASTER_MONEY.items():
        if name.casefold() not in declared:
            continue
        reference = declared.get(unit.casefold())
        if not reference or denominations[declared[name.casefold()]] != quantity * denominations[reference]:
            raise CatalogError('Номинал денежной позиции не соответствует явному количеству: ' + name)
    for item in conn.execute("SELECT * FROM dnd_items WHERE chat_id=? AND thread_id=? AND category='currency'", scope):
        if item['id'] in declared.values() and item['id'] not in denominations:
            raise CatalogError('Валюта из каталога не имеет активного номинала: ' + item['name'])
    factor = Fraction(1)
    if mode == 'm':
        # Original copper, silver and gold are distinct from universal units.
        # IDs are explicitly declared and their registered nominals prove scale.
        units = [declared.get(name.casefold()) for name in
                 ('Медная монета', 'Серебряная монета', 'Золотая монета')]
        if any(item_id is None for item_id in units):
            raise CatalogError('Для -m зарегистрируйте и явно сопоставьте исходные медную, серебряную и золотую валюты.')
        copper_value, silver_value, gold_value = (denominations[item_id] for item_id in units)
        if silver_value != 100 * copper_value or gold_value != 10000 * copper_value:
            raise CatalogError('Несовместимые номиналы -m: 1 серебряная = 100 медных, 1 золотая = 10000 медных. Номиналы не изменены; нужна проверка остатков и отдельная миграция.')
        if base['id'] not in units:
            raise CatalogError('Базовая валюта -m должна быть одной из трёх явно сопоставленных физических монет.')
        factor = copper_value
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
    from app.dnd_shop.coins import profile as coin_profile
    coin_profile(rows,denominations,topic,mode)  # reject unusable output units before import/activation
    signature = json.dumps(dict(declaration=declaration, currencies=[
        [r['id'], r['code'], r['name_key'], str(denominations[r['id']]), r['is_base']] for r in rows
    ]), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return dict(pricing_revision=2, pricing_mode=mode, factor=str(factor), base_item_id=base['id'],
                currency_signature=hashlib.sha256(signature.encode()).hexdigest(),
                currency_units=json.dumps(declared, ensure_ascii=False, sort_keys=True),
                base_name=base['name'])
