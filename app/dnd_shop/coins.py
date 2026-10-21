"""Explicit registry identities for physical coins and historical bundles."""
from fractions import Fraction
from app.dnd_shop.catalog import CatalogError
from app.dnd_shop.currency_units import MASTER_MONEY, UNITS


def profile(rows, denominations, topic, mode=None):
    declaration = topic.get('shop_currency_units')
    if declaration is None:
        return None  # Legacy/custom topics keep their explicitly registered units.
    if not isinstance(declaration, dict) or not isinstance(declaration.get('master'), dict):
        raise CatalogError('Нужны явные соответствия денежных единиц темы.')
    by_code = {r['code']: r for r in rows}
    declared = {}
    for name, code in declaration['master'].items():
        if not isinstance(name,str) or not isinstance(code,str):
            raise CatalogError('Денежные соответствия должны содержать строки name/code.')
        key = name.strip().casefold()
        if key in declared or code not in by_code:
            raise CatalogError('Неоднозначное соответствие валюты и зарегистрированного code.')
        declared[key] = by_code[code]['id']
    if not all(name.casefold() in declared for name in UNITS):
        raise CatalogError('Сопоставьте три основные монеты в shop_currency_units.master.')
    ids = [declared[name.casefold()] for name in UNITS]
    if len(set(ids)) != 3:
        raise CatalogError('Основные монеты должны иметь разные item_id.')
    values = [Fraction(denominations[i]) for i in ids]
    if not (0 < values[0] < values[1] < values[2]) or any(
            (values[i+1] / values[i]).denominator != 1 for i in (0, 1)):
        raise CatalogError('Номиналы основных монет не образуют подтверждённую целочисленную шкалу.')
    if mode == 'm' and (values[1] != 100*values[0] or values[2] != 10000*values[0]):
        raise CatalogError('Несовместимая исходная шкала монет -m; номиналы не изменены.')
    # Universal money is never assigned the original 100/100 scale implicitly.
    if mode == 'b' and ((by_code[declaration['universal_copper']]['id'] if declaration.get('universal_copper') in by_code else None) != ids[0]
                        or (by_code[declaration['universal_gold']]['id'] if declaration.get('universal_gold') in by_code else None) != ids[2]
                        or values[2] != 100*values[0]):
        raise CatalogError('Универсальная шкала не совпадает с объявленными основными монетами. Требуется отдельная конфигурация темы.')
    mappings = {i: (i, 1) for i in ids}
    for name, (unit, count) in MASTER_MONEY.items():
        key = name.casefold()
        if key not in declared:
            continue
        source, target = declared[key], declared[unit.casefold()]
        if Fraction(denominations[source]) != count*Fraction(denominations[target]):
            raise CatalogError('Связка не соответствует зарегистрированному номиналу: '+name)
        if source in mappings and mappings[source] != (target, count):
            raise CatalogError('Денежные позиции используют один item_id неоднозначно.')
        mappings[source] = target, count
    return dict(ids=ids, values=[str(v) for v in values], bundles={str(i):list(pair) for i,pair in mappings.items()}, universal=mode=='b')


def output_denominations(coin_profile, denominations):
    return denominations if coin_profile is None else {i: denominations[i] for i in coin_profile['ids']}


def configured_profile(conn, scope, topic, *, validate_mode=True):
    """Admin/migration entry point, validates registry rather than names."""
    if not topic or topic.get('mini') or not topic.get('shop_currency_units'):
        return None
    from app.dnd_shop.service import Shop
    shop = Shop(settings={scope[0]: {scope[1]: topic}})
    conn.row_factory = __import__('sqlite3').Row
    rows, values = shop.currencies(conn, dict(chat_id=scope[0],thread_id=scope[1]))
    state = conn.execute('SELECT pricing_mode FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?',scope).fetchone()
    return profile(rows,values,topic,state[0] if state and validate_mode else None)
