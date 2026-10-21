"""Bounded UTF-8 snapshots and cooperative atomic publication.

Lock order: separate catalog lock file -> SQLite. The replaceable CSV inode
is never locked. Cached snapshots are immutable; every access opens/fstats the
current file under its catalog lock and never falls back after an I/O error.
"""
import csv
import hashlib
import io
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType

from app.dnd_shop.currency_units import is_source_money

MAX_INTEGER = 2**63 - 1
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 25000
MAX_FIELD = 65536
COLUMNS = ('chat_id', 'thread_id', 'code', 'name', 'description', 'price', 'stock', 'active')
EXTENDED = ('buy_enabled', 'rarity', 'category', 'effect', 'master_sha256')
CODE = re.compile(r'[A-Za-z0-9_-]{1,80}')
csv.field_size_limit(MAX_FIELD)


class CatalogError(ValueError):
    pass


class CatalogBusy(CatalogError):
    """The bounded wait to acquire the catalog lock expired."""


@dataclass(frozen=True)
class Product:
    chat_id: int
    thread_id: int
    code: str
    name: str
    description: str
    price: Fraction
    stock: int
    active: bool
    buy_enabled: bool = True
    rarity: str = ''
    category: str = ''
    effect: str = ''
    master_sha256: str = ''

    @property
    def key(self):
        return self.chat_id, self.thread_id, self.code

    @property
    def monetary(self):
        return is_source_money(self.name)

    @property
    def quote(self):
        # The commercial offer, including availability configuration, is confirmed.
        return hashlib.sha256(repr((self.key, self.price, self.stock, self.active,
                                    self.buy_enabled, self.master_sha256)).encode()).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    products: tuple
    digest: str
    by_key: object
    byte_size: int


@contextmanager
def catalog_lock(path, *, exclusive=False, timeout=5):
    try:
        lock = Path(str(path) + '.lock').open('a+b')
    except OSError as error:
        raise CatalogError('Каталог недоступен: не удалось открыть lock-файл.') from error
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
        else:
            import fcntl
        deadline = time.monotonic() + timeout
        while not acquired:
            try:
                if os.name == 'nt':
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise CatalogBusy('Каталог занят. Повторите действие позже.') from error
                time.sleep(0.025)
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def bounded_bytes(path):
    try:
        with Path(path).open('rb') as source:
            before = os.fstat(source.fileno())
            if before.st_size > MAX_BYTES:
                raise CatalogError('CSV превышает безопасный предел 32 MiB.')
            data = source.read(before.st_size + 1)
            after = os.fstat(source.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                raise CatalogError('CSV изменён во время чтения; используйте атомарный publish.')
        if len(data) > MAX_BYTES:
            raise CatalogError('CSV превышает безопасный предел 32 MiB.')
        return data
    except OSError as error:
        raise CatalogError('CSV недоступен. Торговля временно закрыта.') from error


def boolean(value):
    if value not in ('true', 'false'):
        raise ValueError('Ожидалось true/false.')
    return value == 'true'


def scope_values(chat, thread):
    if not re.fullmatch(r'-?[1-9][0-9]*', chat) or not re.fullmatch(r'0|[1-9][0-9]*', thread):
        raise ValueError('Некорректная тема.')
    chat, thread = int(chat), int(thread)
    if abs(chat) > MAX_INTEGER or thread > MAX_INTEGER:
        raise ValueError('Тема вне SQLite INTEGER.')
    return chat, thread


def parse_catalog(data):
    if len(data) > MAX_BYTES:
        raise CatalogError('Снимок превышает 32 MiB.')
    try:
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''), strict=True)
        headers = reader.fieldnames
        if not headers or len(set(headers)) != len(headers) or not set(COLUMNS) <= set(headers):
            raise CatalogError('В CSV отсутствуют обязательные колонки или заголовки повторяются.')
        extended = bool(set(EXTENDED) & set(headers))
        if extended and not set(EXTENDED) <= set(headers):
            raise CatalogError('Неполная новая схема runtime CSV.')
        products, seen, names = [], set(), set()
        for line, row in enumerate(reader, 2):
            if len(products) >= MAX_ROWS:
                raise CatalogError('Слишком много товаров в CSV: максимум 25000.')
            try:
                if None in row or any(value is None or len(value) > MAX_FIELD for value in row.values()):
                    raise ValueError()
                chat, thread = scope_values(row['chat_id'], row['thread_id'])
                code, name = row['code'], row['name'].strip()
                description = row['description']
                if not CODE.fullmatch(code) or not name or len(name) > 256:
                    raise ValueError()
                raw_price = row['price']
                if len(raw_price) > 40 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', raw_price):
                    raise ValueError()
                price = Fraction(Decimal(raw_price))
                if price <= 0 or not re.fullmatch(r'0|[1-9][0-9]*', row['stock']):
                    raise ValueError()
                stock = int(row['stock'])
                if stock > MAX_INTEGER:
                    raise ValueError()
                active = boolean(row['active'].lower())
                enabled = boolean(row['buy_enabled']) if extended else True
                if is_source_money(name) and enabled:
                    raise ValueError('Денежные единицы и связки не являются товарами.')
                master = row['master_sha256'] if extended else ''
                if extended and (not re.fullmatch('[a-f0-9]{64}', master) or
                                 not row['rarity'].strip() or not row['category'].strip() or not row['effect'].strip()):
                    raise ValueError()
                product = Product(chat, thread, code, name, description, price, stock, active, enabled,
                                  row.get('rarity', ''), row.get('category', ''), row.get('effect', ''), master)
                name_key = chat, thread, name.casefold()
                if product.key in seen or name_key in names:
                    raise ValueError()
                products.append(product)
                seen.add(product.key)
                names.add(name_key)
            except (ValueError, ArithmeticError) as error:
                raise CatalogError(f'Строка {line}: неверные поля, повтор SKU или неоднозначный предмет.') from error
        return products
    except (UnicodeError, csv.Error) as error:
        raise CatalogError('CSV повреждён. Торговля временно закрыта.') from error


def read_catalog(path):
    return parse_catalog(bounded_bytes(path))


_cache_lock = threading.Lock()
_cache = None


def snapshot(path):
    global _cache
    path = str(Path(path).resolve())
    try:
        # Opening even on a cache hit verifies current read access.
        with Path(path).open('rb') as stream:
            stat = os.fstat(stream.fileno())
            signature = (path, stat.st_dev, stat.st_ino, stat.st_size,
                         stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode)
            with _cache_lock:
                if _cache is not None and _cache[0] == signature:
                    return _cache[1]
                if stat.st_size > MAX_BYTES:
                    raise CatalogError('CSV превышает безопасный предел 32 MiB.')
                data = stream.read(stat.st_size + 1)
                after = os.fstat(stream.fileno())
                if len(data) > MAX_BYTES or (after.st_mtime_ns, after.st_ctime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size):
                    raise CatalogError('CSV изменён во время чтения; используйте атомарный publish.')
                products = tuple(parse_catalog(data))
                result = Snapshot(products, hashlib.sha256(data).hexdigest(),
                                  MappingProxyType({p.key: p for p in products}), len(data))
                _cache = signature, result
                return result
    except (OSError, ValueError) as error:
        with _cache_lock:
            _cache = None
        if isinstance(error, OSError):
            raise CatalogError('CSV недоступен. Торговля временно закрыта.') from error
        raise


def fsync_directory(path):
    if os.name != 'nt':
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def atomic_bytes(target, data, *, mode=0o640):
    import tempfile
    target = Path(target)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.shop-', suffix='.csv', delete=False) as stream:
            staged = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, target.stat().st_mode & 0o777 if target.exists() else mode)
        os.replace(staged, target)
        fsync_directory(target.parent)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def publish(source, target, validate):
    """One snapshot is published; the previous bytes are retained under the same lock."""
    import tempfile
    target = Path(target)
    data = bounded_bytes(source)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.shop-', suffix='.csv', delete=False) as stream:
            staged = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        read_catalog(staged)
        with catalog_lock(target, exclusive=True):
            validate(staged)
            if target.exists():
                atomic_bytes(Path(str(target) + '.previous'), bounded_bytes(target))
            os.chmod(staged, target.stat().st_mode & 0o777 if target.exists() else 0o640)
            os.replace(staged, target)
            fsync_directory(target.parent)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
