"""Additive ordinary D&D shop schema. Caller owns the transaction."""
def migrate(conn):
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    statements = [
        """CREATE TABLE IF NOT EXISTS dnd_shop_skus (
            chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL, code TEXT NOT NULL,
            item_id INTEGER NOT NULL, original_name_key TEXT NOT NULL,
            sold INTEGER NOT NULL DEFAULT 0 CHECK(typeof(sold)='integer' AND sold>=0),
            PRIMARY KEY(chat_id,thread_id,code), UNIQUE(chat_id,thread_id,item_id),
            FOREIGN KEY(item_id,chat_id,thread_id) REFERENCES dnd_items(id,chat_id,thread_id))""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_owners (
            chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL, username TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY(chat_id,thread_id,username), UNIQUE(chat_id,thread_id,user_id))""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_sessions (
            token TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, username TEXT NOT NULL, character_key TEXT NOT NULL,
            expires INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)))""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_operations (
            token TEXT PRIMARY KEY, session_token TEXT NOT NULL REFERENCES dnd_shop_sessions(token),
            kind TEXT NOT NULL CHECK(kind IN ('buy','sell')), sku TEXT NOT NULL,
            item_id INTEGER NOT NULL REFERENCES dnd_items(id), inventory_id INTEGER,
            price TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            result TEXT, completed_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_trades (
            id INTEGER PRIMARY KEY, operation_token TEXT NOT NULL UNIQUE REFERENCES dnd_shop_operations(token),
            kind TEXT NOT NULL CHECK(kind IN ('buy','sell')), chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL,
            character_key TEXT NOT NULL, sku TEXT NOT NULL, item_id INTEGER NOT NULL REFERENCES dnd_items(id),
            price TEXT NOT NULL, value TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity=1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_money (
            trade_id INTEGER NOT NULL REFERENCES dnd_shop_trades(id),
            item_id INTEGER NOT NULL REFERENCES dnd_items(id),
            direction TEXT NOT NULL CHECK(direction IN ('debit','change','payout')),
            quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
            denomination TEXT NOT NULL, PRIMARY KEY(trade_id,item_id,direction))""",
        'CREATE INDEX IF NOT EXISTS idx_dnd_shop_operations_session ON dnd_shop_operations(session_token)',
    ]
    for statement in statements:
        conn.execute(statement)
