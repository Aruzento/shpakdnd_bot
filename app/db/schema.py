import sqlite3

from app.config import DB_PATH, LEGACY_DB_PATH
from app.context import normalize_username


def migrate_database_file():
    if DB_PATH.exists():
        return

    if not LEGACY_DB_PATH.exists():
        return

    LEGACY_DB_PATH.replace(DB_PATH)

    print(
        f"База данных переименована: "
        f"{LEGACY_DB_PATH.name} -> {DB_PATH.name}"
    )


def create_inventory_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            description TEXT NOT NULL DEFAULT ''
        )
        """
    )


def create_character_profiles_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS character_profiles (
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            username TEXT NOT NULL,
            level INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            race TEXT NOT NULL,
            PRIMARY KEY (chat_id, thread_id, username)
        )
        """
    )


def create_characters_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (chat_id, thread_id, username)
        )
        """
    )


def migrate_inventory_table(conn: sqlite3.Connection):
    legacy_table = "inventory_legacy_migration"

    conn.execute(f"DROP TABLE IF EXISTS {legacy_table}")
    conn.execute(f"ALTER TABLE inventory RENAME TO {legacy_table}")

    create_inventory_table(conn)

    legacy_columns = {
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info({legacy_table})"
        ).fetchall()
    }

    thread_expr = "thread_id" if "thread_id" in legacy_columns else "0"

    if "name" in legacy_columns:
        name_expr = "name"
    elif "item" in legacy_columns:
        name_expr = "item"
    else:
        name_expr = "''"

    quantity_expr = "quantity" if "quantity" in legacy_columns else "1"
    description_expr = (
        "description" if "description" in legacy_columns else "''"
    )

    rows = conn.execute(
        f"""
        SELECT
            chat_id,
            {thread_expr},
            username,
            {name_expr},
            {quantity_expr},
            {description_expr}
        FROM {legacy_table}
        ORDER BY id
        """
    ).fetchall()

    merged = {}

    for (
        chat_id,
        thread_id,
        username,
        name,
        quantity,
        description,
    ) in rows:
        if name is None:
            continue

        name = str(name).strip()
        if not name:
            continue

        username = normalize_username(str(username))

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1

        if quantity <= 0:
            quantity = 1

        description = (
            str(description).strip()
            if description is not None
            else ""
        )

        key = (
            int(chat_id),
            int(thread_id or 0),
            username,
            name.casefold(),
        )

        if key not in merged:
            merged[key] = {
                "chat_id": int(chat_id),
                "thread_id": int(thread_id or 0),
                "username": username,
                "name": name,
                "quantity": quantity,
                "description": description,
            }
        else:
            merged[key]["quantity"] += quantity

            if description and not merged[key]["description"]:
                merged[key]["description"] = description

    conn.executemany(
        """
        INSERT INTO inventory (
            chat_id,
            thread_id,
            username,
            name,
            quantity,
            description
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["chat_id"],
                row["thread_id"],
                row["username"],
                row["name"],
                row["quantity"],
                row["description"],
            )
            for row in merged.values()
        ],
    )

    conn.execute(f"DROP TABLE {legacy_table}")


def init_db():
    migrate_database_file()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS timers (
                chat_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                end_time TEXT NOT NULL
            )
            """
        )

        inventory_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'inventory'
            """
        ).fetchone()

        if not inventory_exists:
            create_inventory_table(conn)
        else:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(inventory)"
                ).fetchall()
            }

            required_columns = {
                "id",
                "chat_id",
                "thread_id",
                "username",
                "name",
                "quantity",
                "description",
            }

            if not required_columns.issubset(columns):
                print(
                    "Обнаружен старый формат inventory. "
                    "Выполняю миграцию..."
                )
                migrate_inventory_table(conn)
                print(
                    "Инвентарь успешно переведён "
                    "в новый формат."
                )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_inventory_scope
            ON inventory (
                chat_id,
                thread_id,
                username
            )
            """
        )

        create_character_profiles_table(conn)
        create_characters_table(conn)

        conn.commit()
