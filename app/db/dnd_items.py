"""Scoped ordinary D&D item registry. It never touches Mini economy."""
from contextlib import closing
import hashlib
import re
import shlex
import sqlite3
from decimal import Decimal, InvalidOperation, Inexact, localcontext
from app.config import DB_PATH
from app.topics import TOPIC_SETTINGS


def migrate(conn):
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    conn.execute('''CREATE TABLE IF NOT EXISTS dnd_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL DEFAULT 0,
        code TEXT NOT NULL CHECK(length(trim(code))>0),
        name TEXT NOT NULL CHECK(length(trim(name))>0), name_key TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'item' CHECK(length(trim(category))>0),
        description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(chat_id,thread_id,code), UNIQUE(chat_id,thread_id,name_key), UNIQUE(id,chat_id,thread_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS dnd_currencies (
        item_id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
        base_value TEXT NOT NULL CHECK(typeof(base_value)='text'
            AND base_value NOT GLOB '*[^0-9.]*'
            AND length(base_value)-length(replace(base_value,'.',''))<=1
            AND length(replace(replace(base_value,'.',''),'0',''))>0),
        is_base INTEGER NOT NULL DEFAULT 0 CHECK(is_base IN (0,1)),
        currency_quantity INTEGER NOT NULL DEFAULT 1 CHECK(typeof(currency_quantity)='integer' AND currency_quantity>0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(item_id,chat_id,thread_id) REFERENCES dnd_items(id,chat_id,thread_id),
        CHECK(is_base=0 OR (base_value='1' AND currency_quantity=1))
    )''')
    if 'currency_quantity' not in {r[1] for r in conn.execute('PRAGMA table_info(dnd_currencies)')}:
        conn.execute('ALTER TABLE dnd_currencies ADD COLUMN currency_quantity INTEGER NOT NULL DEFAULT 1 CHECK(currency_quantity>0)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_dnd_base_currency ON dnd_currencies(chat_id,thread_id) WHERE is_base=1')
    for operation in ('INSERT','UPDATE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS dnd_currency_category_{operation.lower()}
            BEFORE {operation} ON dnd_currencies
            WHEN NOT EXISTS(SELECT 1 FROM dnd_items WHERE id=NEW.item_id AND category='currency')
            BEGIN SELECT RAISE(ABORT,'Currency item category required'); END''')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS dnd_currency_keep_category BEFORE UPDATE OF category ON dnd_items
        WHEN NEW.category!='currency' AND EXISTS(SELECT 1 FROM dnd_currencies WHERE item_id=NEW.id)
        BEGIN SELECT RAISE(ABORT,'Currency item category required'); END''')
    columns={r[1] for r in conn.execute('PRAGMA table_info(inventory)')}
    if columns and 'item_id' not in columns:
        conn.execute('ALTER TABLE inventory ADD COLUMN item_id INTEGER REFERENCES dnd_items(id)')
    if columns:
        conn.execute('CREATE INDEX IF NOT EXISTS idx_inventory_item ON inventory(item_id)')
        for operation in ('INSERT','UPDATE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS inventory_item_scope_{operation.lower()}
                BEFORE {operation} ON inventory WHEN NEW.item_id IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM dnd_items WHERE id=NEW.item_id
                    AND chat_id=NEW.chat_id AND thread_id=NEW.thread_id)
                BEGIN SELECT RAISE(ABORT,'Item belongs to another D&D scope'); END''')


def require_dnd_scope(chat_id, thread_id):
    topic=TOPIC_SETTINGS.get(chat_id,{}).get(thread_id)
    if topic is None or topic.get('mini'):
        raise ValueError('Валюта доступна только в настроенной теме обычного D&D.')


def register_in_transaction(conn, chat_id, thread_id, name, description='', category='item', *, strict=False):
    if not conn.in_transaction: raise RuntimeError('Item registration requires a transaction.')
    name=name.strip()
    if not name: raise ValueError('Название предмета не может быть пустым.')
    key=name.casefold()
    old=conn.execute('SELECT id FROM dnd_items WHERE chat_id=? AND thread_id=? AND name_key=?', (chat_id,thread_id,key)).fetchone()
    if old:
        if strict: raise ValueError('Предмет с таким названием уже существует в этой теме.')
        return old[0]
    code='dnd_'+hashlib.sha256(key.encode('utf-8')).hexdigest()
    return conn.execute('''INSERT INTO dnd_items(chat_id,thread_id,code,name,name_key,category,description)
        VALUES(?,?,?,?,?,?,?)''',(chat_id,thread_id,code,name,key,category,description)).lastrowid


def decimal_value(text):
    if not isinstance(text,str) or not re.fullmatch(r'\+?\d+(?:\.\d+)?',text):
        raise ValueError('Номинал должен быть положительным конечным десятичным числом.')
    try: value=Decimal(text)
    except InvalidOperation as error: raise ValueError('Некорректный номинал.') from error
    if not value.is_finite() or value<=0: raise ValueError('Номинал должен быть больше нуля.')
    return value


def denomination(quantity, base_value):
    if type(quantity) is not int or not 0<quantity<=2**63-1:
        raise ValueError('Количество должно быть положительным целым в пределах SQLite INTEGER.')
    value=decimal_value(base_value)
    with localcontext() as ctx:
        ctx.prec=max(100,len(value.as_tuple().digits)+len(str(quantity))+20)
        ctx.traps[Inexact]=True
        try: result=value/Decimal(quantity)
        except Inexact:
            return f'{format(value,"f")}/{quantity}'
    return format(result,'f').rstrip('0').rstrip('.') if '.' in format(result,'f') else format(result,'f')


def create_currency(chat_id,thread_id,name,quantity,base_value,*,is_base=False,db_path=DB_PATH):
    require_dnd_scope(chat_id,thread_id)
    value=denomination(quantity,base_value)
    if is_base and (quantity!=1 or value!='1'): raise ValueError('Базовая валюта должна иметь номинал 1.')
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        base=conn.execute('SELECT 1 FROM dnd_currencies WHERE chat_id=? AND thread_id=? AND is_base=1',(chat_id,thread_id)).fetchone()
        if bool(base)==bool(is_base):
            raise ValueError('Базовая валюта уже существует.' if is_base else 'Сначала создайте базовую валюту.')
        item_id=register_in_transaction(conn,chat_id,thread_id,name,category='currency',strict=True)
        # Terminating quotients are stored directly. Repeating quotients retain
        # the exact input ratio; no finite Decimal approximation is persisted.
        numerator=format(decimal_value(base_value),'f') if '/' in value else value
        denominator=quantity if '/' in value else 1
        conn.execute('INSERT INTO dnd_currencies(item_id,chat_id,thread_id,base_value,is_base,currency_quantity) VALUES(?,?,?,?,?,?)', (item_id,chat_id,thread_id,numerator,int(is_base),denominator))
        return {'item_id':item_id,'name':name.strip(),'base_value':value,'is_base':is_base}


def parse_currency_command(text, *, secondary=False):
    try: parts=shlex.split(text)
    except ValueError as error: raise ValueError('Проверьте кавычки в названии валюты.') from error
    if len(parts)!=(6 if secondary else 4) or (secondary and parts[4]!='='):
        raise ValueError('Использование: /admscoin CHATID:THEMEID "Имя" 1 = 0.01' if secondary else 'Использование: /admcoin CHATID:THEMEID "Имя" 1')
    if not parts[2].strip(): raise ValueError('Название не может быть пустым.')
    if not re.fullmatch(r'[1-9]\d*',parts[3]): raise ValueError('Количество должно быть положительным целым.')
    quantity=int(parts[3]);value=parts[5] if secondary else parts[3]
    if not secondary and quantity!=1: raise ValueError('Базовый номинал должен быть 1.')
    denomination(quantity,value)
    return parts[1],parts[2],quantity,value
