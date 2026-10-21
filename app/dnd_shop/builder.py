"""Master loot importer and explicit, owner-maintained assortment builder.

The source master is never rewritten. Runtime output is a staging candidate.
Stable codes are allocated only by index(), never by build() or navigation.
"""
import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.dnd_shop.catalog import (CatalogError, CODE, COLUMNS, EXTENDED, MAX_FIELD,
    MAX_INTEGER, MAX_ROWS, atomic_bytes, boolean, bounded_bytes, read_catalog, scope_values)

from app.dnd_shop.currency_units import is_source_money

REFERENCE_SHA256 = '7bffd8a9b8314651318c961838c9b6b872f5d02b6cd896e968f9226c7bcdfe20'
MASTER_COLUMNS = ('Предмет', 'Эффект', 'Описание', 'Стоимость', 'Редкость', 'Категория')
MAP_COLUMNS = ('code', 'name')
OFFER_COLUMNS = ('chat_id', 'thread_id', 'code', 'buy_enabled', 'stock')


@dataclass(frozen=True)
class Loot:
    name: str
    effect: str
    description: str
    price: int
    rarity: str
    category: str


@dataclass(frozen=True)
class Master:
    rows: tuple
    by_name: object
    digest: str


def dictionaries(data, fields, delimiter=','):
    try:
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''),
                                delimiter=delimiter, strict=True)
        if reader.fieldnames != list(fields):
            raise CatalogError('Неверные колонки: ' + delimiter.join(fields))
        result = []
        for row in reader:
            if len(result) >= MAX_ROWS or None in row or any(v is None or len(v) > MAX_FIELD for v in row.values()):
                raise CatalogError('Неверная строка или превышен предел CSV.')
            result.append(row)
        return result
    except (UnicodeError, csv.Error) as error:
        raise CatalogError('Повреждён UTF-8 CSV.') from error


def read_master(path):
    data = bounded_bytes(path)
    rows, names = [], set()
    for number, row in enumerate(dictionaries(data, MASTER_COLUMNS, ';'), 2):
        name = row['Предмет'].strip()
        if not name or len(name) > 256 or name.casefold() in names or any(not v.strip() for v in row.values()):
            raise CatalogError(f'Мастер, строка {number}: пустое поле / повтор имени.')
        value = row['Стоимость']
        if not re.fullmatch(r'[1-9][0-9]{0,8}', value) or int(value) > 350_000_000:
            raise CatalogError(f'Мастер, строка {number}: цена вне 1..350000000.')
        names.add(name.casefold())
        rows.append(Loot(name, row['Эффект'], row['Описание'], int(value), row['Редкость'], row['Категория']))
    if not rows:
        raise CatalogError('Мастер-справочник пуст.')
    return Master(tuple(rows), MappingProxyType({r.name.casefold(): r for r in rows}),
                  hashlib.sha256(data).hexdigest())


def read_map(path):
    mapping, names = {}, set()
    for row in dictionaries(bounded_bytes(path), MAP_COLUMNS):
        code, name = row['code'], row['name'].strip()
        if not CODE.fullmatch(code) or not name or len(name) > 256 or code in mapping or name.casefold() in names:
            raise CatalogError('Повтор/неверный code или каноническое имя в индексе.')
        mapping[code] = name
        names.add(name.casefold())
    return mapping


def csv_bytes(fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def index(master, *, previous=None, conn=None, scopes=()):
    mapping = dict(previous or {})
    by_name = {name.casefold(): code for code, name in mapping.items()}
    if len(by_name) != len(mapping) or any(not CODE.fullmatch(code) for code in mapping):
        raise CatalogError('Неоднозначный существующий индекс.')
    if conn is not None and conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_shop_skus'").fetchone():
        existing = conn.execute('''SELECT s.chat_id,s.thread_id,s.code,s.original_name_key,i.name_key
            FROM dnd_shop_skus s JOIN dnd_items i ON i.id=s.item_id''').fetchall()
        wanted = set(scopes)
        for chat, thread, code, original, canonical in existing:
            if (chat, thread) not in wanted:
                continue
            candidates = {key for key in (original, canonical) if key in master.by_name}
            if len(candidates) > 1:
                raise CatalogError('Старое переименование SKU неоднозначно; нужна рассмотренная миграция.')
            if not candidates:
                continue
            key = candidates.pop()
            if key in by_name and by_name[key] != code or code in mapping and mapping[code].casefold() != key:
                raise CatalogError('Индекс конфликтует с историческим SKU; требуется ручное разрешение.')
            mapping[code] = master.by_name[key].name
            by_name[key] = code
    serial = 1
    for row in master.rows:
        if row.name.casefold() in by_name:
            continue
        while f'loot_{serial:06d}' in mapping:
            serial += 1
        code = f'loot_{serial:06d}'
        mapping[code] = row.name
        by_name[row.name.casefold()] = code
        serial += 1
    return mapping


def require_scopes(settings, scopes):
    scopes = tuple(dict.fromkeys(scopes))
    if not scopes:
        raise CatalogError('Явно задайте хотя бы одну обычную D&D-тему.')
    for chat, thread in scopes:
        topic = settings.get(chat, {}).get(thread)
        if topic is None or topic.get('mini') or not topic.get('admin'):
            raise CatalogError('Неизвестная/Mini тема или отсутствует владелец темы.')
    return scopes


def read_offers(path, master, mapping, settings, scopes):
    result = {}
    allowed = set(require_scopes(settings, scopes))
    for row in dictionaries(bounded_bytes(path), OFFER_COLUMNS):
        try:
            scope = scope_values(row['chat_id'], row['thread_id'])
            code = row['code']
            if scope not in allowed or code not in mapping or mapping[code].casefold() not in master.by_name:
                raise ValueError()
            enabled = boolean(row['buy_enabled'])
            if enabled and is_source_money(mapping[code]):
                raise CatalogError('Денежные единицы и связки исходного каталога не являются товарами.')
            if not re.fullmatch(r'0|[1-9][0-9]*', row['stock']) or int(row['stock']) > MAX_INTEGER:
                raise ValueError()
            key = (*scope, code)
            if key in result:
                raise ValueError()
            result[key] = enabled, int(row['stock'])
        except ValueError as error:
            raise CatalogError('Неверное/повторное предложение, неизвестный code или scope.') from error
    return result


def build(master, mapping, offers_path, scopes, settings, *, previous=(), price_factors=None):
    scopes = require_scopes(settings, scopes)
    names = {name.casefold(): code for code, name in mapping.items()}
    if len(names) != len(mapping) or not set(master.by_name) <= set(names):
        raise CatalogError('Для всех мастер-позиций необходим однозначный постоянный индекс.')
    if len(scopes) * len(master.rows) > MAX_ROWS:
        raise CatalogError('Слишком много тем/позиций для одного снимка.')
    offers = read_offers(offers_path, master, mapping, settings,
                         [(chat, thread) for chat, topics in settings.items() for thread, topic in topics.items()
                          if not topic.get('mini') and topic.get('admin')]) if offers_path and Path(offers_path).exists() else {}
    from fractions import Fraction
    from app.dnd_shop.money import exact_text
    price_factors = price_factors or {}
    old = {p.key: p for p in previous}
    rows = []
    for chat, thread in scopes:
        for item in master.rows:
            if is_source_money(item.name):
                continue
            code = names[item.name.casefold()]
            key = chat, thread, code
            enabled, stock = offers.get(key, (False, old[key].stock if key in old else 0))
            # A legacy explicit active=false is never silently reactivated.
            active = old[key].active if key in old else True
            rows.append(dict(chat_id=chat, thread_id=thread, code=code, name=item.name,
                description=item.description, price=exact_text(Fraction(item.price) * price_factors.get((chat, thread), 1)), stock=stock,
                active=str(active).lower(), buy_enabled=str(enabled).lower(),
                rarity=item.rarity, category=item.category, effect=item.effect,
                master_sha256=master.digest))
    data = csv_bytes(COLUMNS + EXTENDED, rows)
    from app.dnd_shop.catalog import MAX_BYTES
    if len(data) > MAX_BYTES:
        raise CatalogError('Снимок превышает 32 MiB. Уменьшите число тем в файле.')
    return data, dict(master_sha256=master.digest, read=len(master.rows), imported=len(rows),
        per_scope=sum(not is_source_money(r.name) for r in master.rows), scopes=len(scopes), buy_disabled=sum(not r['buy_enabled'] == 'true' for r in rows),
        offers=sum(r['buy_enabled'] == 'true' for r in rows), errors=0)


def verify_snapshot(products, master, price_factors=None):
    """Prove complete membership/100% price and metadata for every published scope."""
    price_factors = price_factors or {}
    scopes = {}
    global_codes = {}
    for product in products:
        if not product.master_sha256:
            raise CatalogError('Нельзя смешивать legacy и master snapshot.')
        item = master.by_name.get(product.name.casefold())
        if product.master_sha256 != master.digest or item is None:
            raise CatalogError('Snapshot не соответствует текущему мастер-справочнику.')
        if (product.name, product.description, product.effect, product.price, product.rarity, product.category) != (
                item.name, item.description, item.effect, item.price * price_factors.get((product.chat_id, product.thread_id), 1), item.rarity, item.category):
            raise CatalogError('Цена/справочные поля snapshot отличаются от мастера.')
        if product.monetary and product.buy_enabled:
            raise CatalogError('Денежная позиция не может быть ассортиментом покупки.')
        scope = product.chat_id, product.thread_id
        scopes.setdefault(scope, set()).add(product.name.casefold())
        prior = global_codes.setdefault(product.name.casefold(), product.code)
        if prior != product.code:
            raise CatalogError('Коды одного канонического предмета отличаются по темам.')
    # New snapshots contain ordinary goods only. Complete historical snapshots
    # are accepted read-only for compatibility, but their money rows are inert.
    goods = {key for key in master.by_name if not is_source_money(key)}
    if not scopes or any(names not in (goods, set(master.by_name)) for names in scopes.values()):
        raise CatalogError('В каждой теме нужен полный мастер-справочник, без пропущенных обычных предметов.')

def encode_products(products):
    from dataclasses import asdict
    from app.dnd_shop.money import exact_text
    rows = []
    for product in products:
        row = asdict(product)
        row.update(price=exact_text(product.price), active=str(product.active).lower(),
                   buy_enabled=str(product.buy_enabled).lower())
        rows.append(row)
    return csv_bytes(COLUMNS + EXTENDED, rows)
