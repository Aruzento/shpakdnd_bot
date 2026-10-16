"""Fresh UTF-8 catalog reads and cooperative atomic publication.

Lock order everywhere: separate catalog lock file -> SQLite BEGIN IMMEDIATE.
Linux readers use flock(LOCK_SH); Windows serializes readers with msvcrt.
Never lock the replaceable CSV inode.
"""
import csv
import io
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

MAX_INTEGER = 2**63 - 1
COLUMNS = ('chat_id', 'thread_id', 'code', 'name', 'description', 'price', 'stock', 'active')


class CatalogError(ValueError):
    pass


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

    @property
    def key(self):
        return self.chat_id, self.thread_id, self.code


@contextmanager
def catalog_lock(path, *, exclusive=False, timeout=5):
    lock_path = Path(str(path) + '.lock')
    try:
        lock = lock_path.open('a+b')
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
                    raise CatalogError('Каталог занят. Повторите действие позже.') from error
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


def read_catalog(path):
    try:
        with Path(path).open('rb') as source:
            data = source.read(2_000_001)
        if len(data) > 2_000_000:
            raise CatalogError('CSV превышает допустимый размер 2 MB.')
        text = data.decode('utf-8-sig', errors='strict')
        reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames) or not set(COLUMNS) <= set(reader.fieldnames):
            raise CatalogError('В CSV отсутствуют обязательные колонки или заголовки повторяются.')
        products = []
        seen = set()
        names = set()
        for line, row in enumerate(reader, 2):
            if len(products) >= 5000:
                raise CatalogError('Слишком много товаров в CSV.')
            if None in row or any(row.get(column) is None for column in COLUMNS):
                raise CatalogError(f'Строка {line}: неверное количество полей.')
            try:
                if not re.fullmatch(r'-?[1-9][0-9]*', row['chat_id']):
                    raise ValueError()
                if not re.fullmatch(r'0|[1-9][0-9]*', row['thread_id']):
                    raise ValueError()
                chat, thread = int(row['chat_id']), int(row['thread_id'])
                if abs(chat) > MAX_INTEGER or thread > MAX_INTEGER:
                    raise ValueError()
                code = row['code']
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code):
                    raise ValueError()
                name = row['name'].strip()
                description = row['description'].strip()
                if not name or len(name) > 100 or len(description) > 1500:
                    raise ValueError()
                raw_price = row['price']
                if len(raw_price) > 40 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', raw_price):
                    raise ValueError()
                price = Fraction(Decimal(raw_price))
                if price <= 0:
                    raise ValueError()
                if not re.fullmatch(r'0|[1-9][0-9]*', row['stock']):
                    raise ValueError()
                stock = int(row['stock'])
                if stock > MAX_INTEGER or row['active'].lower() not in ('true', 'false'):
                    raise ValueError()
                product = Product(chat, thread, code, name, description, price, stock, row['active'].lower() == 'true')
                name_key = chat, thread, name.casefold()
                if product.key in seen or name_key in names:
                    raise ValueError()
                seen.add(product.key)
                names.add(name_key)
                products.append(product)
            except (ValueError, ArithmeticError) as error:
                raise CatalogError(f'Строка {line}: неверные поля, повтор SKU или неоднозначный предмет.') from error
        return products
    except (OSError, UnicodeError, csv.Error) as error:
        raise CatalogError('CSV недоступен или повреждён. Торговля временно закрыта.') from error


def publish(source, target, validate):
    """Validate staged bytes + registry under EX lock, replace on same filesystem."""
    import tempfile
    target = Path(target)
    with Path(source).open('rb') as candidate:
        data = candidate.read(2_000_001)
    if len(data) > 2_000_000:
        raise CatalogError('CSV превышает допустимый размер 2 MB.')
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
            # Preserve explicitly provisioned read permissions.
            if target.exists():
                os.chmod(staged, target.stat().st_mode & 0o777)
            else:
                os.chmod(staged, 0o640)
            os.replace(staged, target)
            if os.name != 'nt':
                directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
