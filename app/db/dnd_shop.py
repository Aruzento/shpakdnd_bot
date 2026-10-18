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
    # Additive evolution: old trade/operation CHECK constraints are retained.
    columns = {r[1] for r in conn.execute('PRAGMA table_info(dnd_shop_operations)')}
    if 'quote_digest' not in columns:
        conn.execute('ALTER TABLE dnd_shop_operations ADD COLUMN quote_digest TEXT')
    extra = [
        """CREATE TABLE IF NOT EXISTS dnd_shop_catalog_versions (
            source_path TEXT PRIMARY KEY, digest TEXT NOT NULL, master_sha256 TEXT NOT NULL DEFAULT '',
            imported INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_catalog_entries (
            source_path TEXT NOT NULL, chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
            code TEXT NOT NULL, item_id INTEGER NOT NULL REFERENCES dnd_items(id),
            price TEXT NOT NULL, stock INTEGER NOT NULL CHECK(typeof(stock)='integer' AND stock>=0),
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            buy_enabled INTEGER NOT NULL CHECK(buy_enabled IN (0,1)),
            is_currency INTEGER NOT NULL CHECK(is_currency IN (0,1)),
            present INTEGER NOT NULL DEFAULT 1 CHECK(present IN (0,1)),
            PRIMARY KEY(source_path,chat_id,thread_id,code))""",
        """CREATE INDEX IF NOT EXISTS idx_dnd_shop_buy ON dnd_shop_catalog_entries
            (source_path,chat_id,thread_id,buy_enabled,active)""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_credit (
            chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, username TEXT NOT NULL,
            balance TEXT NOT NULL DEFAULT '0' CHECK(typeof(balance)='text'
                AND length(balance) BETWEEN 1 AND 1024 AND balance NOT GLOB '*[^0-9/]*'),
            PRIMARY KEY(chat_id,thread_id,user_id),
            FOREIGN KEY(chat_id,thread_id,username) REFERENCES dnd_shop_owners(chat_id,thread_id,username))""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_credit_redemptions (
            token TEXT PRIMARY KEY, session_token TEXT NOT NULL REFERENCES dnd_shop_sessions(token),
            expected TEXT NOT NULL CHECK(typeof(expected)='text' AND length(expected) BETWEEN 1 AND 1024
                AND expected!='0' AND expected NOT GLOB '*[^0-9/]*'), result TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_credit_moves (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            trade_id INTEGER REFERENCES dnd_shop_trades(id),
            redemption_token TEXT REFERENCES dnd_shop_credit_redemptions(token),
            direction TEXT NOT NULL CHECK(direction IN ('add','spend','redeem')),
            amount TEXT NOT NULL CHECK(typeof(amount)='text' AND length(amount) BETWEEN 1 AND 1024
                AND amount!='0' AND amount NOT GLOB '*[^0-9/]*'),
            balance_after TEXT NOT NULL CHECK(typeof(balance_after)='text' AND length(balance_after) BETWEEN 1 AND 1024
                AND balance_after NOT GLOB '*[^0-9/]*'),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id,thread_id,user_id) REFERENCES dnd_shop_credit(chat_id,thread_id,user_id),
            CHECK((trade_id IS NOT NULL AND redemption_token IS NULL AND direction IN ('add','spend'))
               OR (trade_id IS NULL AND redemption_token IS NOT NULL AND direction='redeem')),
            UNIQUE(trade_id,direction), UNIQUE(redemption_token))""",
        """CREATE INDEX IF NOT EXISTS idx_dnd_shop_credit_owner ON dnd_shop_credit_moves
            (chat_id,thread_id,user_id,id)""",
        """CREATE TABLE IF NOT EXISTS dnd_shop_credit_coins (
            redemption_token TEXT NOT NULL REFERENCES dnd_shop_credit_redemptions(token),
            item_id INTEGER NOT NULL REFERENCES dnd_items(id),
            quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
            denomination TEXT NOT NULL, PRIMARY KEY(redemption_token,item_id))""",
    ]
    for statement in extra:
        conn.execute(statement)

    conn.execute("""CREATE TABLE IF NOT EXISTS dnd_shop_settings (
        chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        pricing_mode TEXT CHECK(pricing_mode IN ('b','m')),
        factor TEXT NOT NULL DEFAULT '1', base_item_id INTEGER REFERENCES dnd_items(id),
        currency_signature TEXT, currency_units TEXT NOT NULL DEFAULT '{}',
        settings_version INTEGER NOT NULL DEFAULT 1 CHECK(settings_version=1),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(chat_id,thread_id))""")
    # One durable intent per runtime file; all trading fails closed until it is
    # finished by the same topic administrator. The original tables are untouched.
    conn.execute("""CREATE TABLE IF NOT EXISTS dnd_shop_publications (
        source_path TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,
        plan TEXT NOT NULL CHECK(length(plan)<=65536),
        candidate BLOB NOT NULL CHECK(typeof(candidate)='blob' AND length(candidate)<=33554432),
        digest TEXT NOT NULL CHECK(length(digest)=64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
