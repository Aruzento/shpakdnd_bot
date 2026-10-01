import asyncio
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv


# ============================================================
# НАСТРОЙКИ
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
DB_PATH = os.path.join(BASE_DIR, "timers.db")

load_dotenv(ENV_PATH)

TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Europe/Moscow")

if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в файле .env")

try:
    TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    raise RuntimeError(
        f"Неизвестный часовой пояс: {TIMEZONE_NAME}"
    )

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Таймеры пока остаются отдельными по chat_id.
active_timers: dict[int, asyncio.Task] = {}

SUPPORTED_DICE = {
    4,
    6,
    8,
    10,
    12,
    20,
    100,
}


# ============================================================
# ЧАТЫ / ТЕМЫ / ПЕРСОНАЖИ
# ============================================================
#
# Теперь конфигурация идёт так:
#
# CHAT_ID:
#     TOPIC_ID:
#         admin
#         characters
#
# В обычном чате без тем TOPIC_ID = 0.
#
# Чтобы узнать CHAT_ID и TOPIC_ID:
# напиши /chatid прямо в нужной теме.
#
# ВАЖНО:
# для суперчата -1003376315265 я специально НЕ подставляю
# выдуманные ID тем. Сначала получи их через /chatid.
#
# Пример структуры:
#
# TOPIC_SETTINGS = {
#     CHAT_ID: {
#         TOPIC_ID: {
#             "admin": "@username",
#             "characters": {
#                 "@username": "Имя персонажа",
#             },
#         },
#     },
# }
#
# ============================================================

TOPIC_SETTINGS = {
    -1003376315265: {
        4: {
            "admin": "@arukozento",
            "characters": {
                "@arukozento": "Мастер",
                "@remark1997": "Фредо",
                "@favion_nikita": "Лазарь",
                "@sivakozov": "Громм",
                "@valeriya_2304": "Азраэль",
                "@daniil_savenko": "Ренкай",
                "@daria_osta": "Марфа",
            },
        },

        3: {
            "admin": "@favion_nikita",
            "characters": {
                "@arukozento": "Олаф",
                "@favion_nikita": "Мастер",
                "@sivakozov": "Ульф",
                "@valeriya_2304": "Дейнерис",
                "@daniil_savenko": "Кайден",
                "@kovesha_lu": "Брунгильда",
                "@romorosnya": "Зая",
            },
        },
    },

    # Обычный чат без тем.
    694384548: {
        0: {
            "admin": "@arukozento",
            "characters": {
                "@arukozento": "Мастер",
            },
        },
    },
}


# ============================================================
# КОНТЕКСТ ЧАТА / ТЕМЫ
# ============================================================

def normalize_username(username: str) -> str:
    username = username.strip().lower()

    if not username.startswith("@"):
        username = "@" + username

    return username


def get_thread_id(message: Message) -> int:
    """
    Для сообщений внутри Telegram Topic возвращает message_thread_id.
    Для обычного чата / сообщения без темы возвращает 0.
    """
    return message.message_thread_id or 0


def get_topic_settings(
    chat_id: int,
    thread_id: int,
) -> dict | None:
    return (
        TOPIC_SETTINGS
        .get(chat_id, {})
        .get(thread_id)
    )


def get_sender_username(
    message: Message,
) -> str | None:
    if not message.from_user:
        return None

    if not message.from_user.username:
        return None

    return normalize_username(
        message.from_user.username
    )


def get_inventory_admin(
    chat_id: int,
    thread_id: int,
) -> str | None:
    settings = get_topic_settings(
        chat_id,
        thread_id,
    )

    if not settings:
        return None

    admin = settings.get("admin")

    if not admin:
        return None

    return normalize_username(admin)


def get_character_name(
    chat_id: int,
    thread_id: int,
    username: str,
) -> str | None:
    settings = get_topic_settings(
        chat_id,
        thread_id,
    )

    if not settings:
        return None

    characters = settings.get(
        "characters",
        {},
    )

    username = normalize_username(
        username
    )

    normalized_characters = {
        normalize_username(user): character_name
        for user, character_name
        in characters.items()
    }

    return normalized_characters.get(
        username
    )


def get_topic_characters(
    chat_id: int,
    thread_id: int,
) -> dict[str, str]:
    settings = get_topic_settings(
        chat_id,
        thread_id,
    )

    if not settings:
        return {}

    characters = settings.get(
        "characters",
        {},
    )

    return {
        normalize_username(user): character_name
        for user, character_name
        in characters.items()
    }


def can_manage_inventory(
    message: Message,
) -> bool:
    thread_id = get_thread_id(message)

    required_admin = get_inventory_admin(
        message.chat.id,
        thread_id,
    )

    if required_admin is None:
        return False

    sender_username = get_sender_username(
        message
    )

    if sender_username is None:
        return False

    return sender_username == required_admin


async def check_inventory_permission(
    message: Message,
) -> bool:
    thread_id = get_thread_id(message)

    required_admin = get_inventory_admin(
        message.chat.id,
        thread_id,
    )

    if required_admin is None:
        await message.answer(
            "⛔ Для этой темы не назначен "
            "администратор инвентаря.\n\n"
            "Используй /chatid, чтобы узнать ID темы."
        )
        return False

    if can_manage_inventory(message):
        return True

    await message.answer(
        "⛔ У тебя нет прав на изменение инвентаря.\n"
        f"Администратор этой темы: {required_admin}"
    )

    return False


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def create_inventory_table(
    conn: sqlite3.Connection,
):
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


def migrate_inventory_table(
    conn: sqlite3.Connection,
):
    """
    Автоматически переводит старый inventory в новый формат.

    Старые одинаковые строки объединяются:
    Меч
    Меч
    Меч

    превращаются в:
    Меч ×3
    """

    legacy_table = "inventory_legacy_migration"

    conn.execute(
        f"DROP TABLE IF EXISTS {legacy_table}"
    )

    conn.execute(
        f"ALTER TABLE inventory RENAME TO {legacy_table}"
    )

    create_inventory_table(
        conn
    )

    legacy_columns = {
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info({legacy_table})"
        ).fetchall()
    }

    thread_expr = (
        "thread_id"
        if "thread_id" in legacy_columns
        else "0"
    )

    if "name" in legacy_columns:
        name_expr = "name"
    elif "item" in legacy_columns:
        name_expr = "item"
    else:
        name_expr = "''"

    quantity_expr = (
        "quantity"
        if "quantity" in legacy_columns
        else "1"
    )

    description_expr = (
        "description"
        if "description" in legacy_columns
        else "''"
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

        username = normalize_username(
            str(username)
        )

        try:
            quantity = int(quantity)
        except (
            TypeError,
            ValueError,
        ):
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

            if (
                description
                and not merged[key]["description"]
            ):
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

    conn.execute(
        f"DROP TABLE {legacy_table}"
    )


def init_db():
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
            create_inventory_table(
                conn
            )

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

            if not required_columns.issubset(
                columns
            ):
                print(
                    "Обнаружен старый формат inventory. "
                    "Выполняю миграцию..."
                )

                migrate_inventory_table(
                    conn
                )

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

        conn.commit()


# ============================================================
# ИНВЕНТАРЬ
# ============================================================

MAX_ITEM_QUANTITY = 999999


def parse_inventory_item(
    raw_value: str,
) -> tuple[str, int, str] | None:
    """
    Поддерживаем:

    Меч
    Меч x3
    Меч ×3
    Меч х3
    Меч x2 :: Стальной клинок

    Описание отделяется через ::
    """

    raw_value = raw_value.strip()

    if not raw_value:
        return None

    if "::" in raw_value:
        item_part, description = raw_value.split(
            "::",
            maxsplit=1,
        )

        description = description.strip()

    else:
        item_part = raw_value
        description = ""

    item_part = item_part.strip()

    match = re.fullmatch(
        r"(.+?)(?:\s+[xх×](\d+))?",
        item_part,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    name = match.group(1).strip()

    if not name:
        return None

    quantity_text = match.group(2)

    quantity = (
        int(quantity_text)
        if quantity_text
        else 1
    )

    if (
        quantity <= 0
        or quantity > MAX_ITEM_QUANTITY
    ):
        return None

    return (
        name,
        quantity,
        description,
    )


def format_inventory_item(
    name: str,
    quantity: int,
    description: str,
) -> str:
    quantity_text = (
        f" ×{quantity}"
        if quantity > 1
        else ""
    )

    line = (
        f"• {name}{quantity_text}"
    )

    if description:
        line += (
            f"\n  ↳ {description}"
        )

    return line


def add_inventory_item(
    chat_id: int,
    thread_id: int,
    username: str,
    name: str,
    quantity: int,
    description: str = "",
) -> tuple[str, int, str]:
    """
    Если предмет уже есть, увеличиваем quantity.
    Если передано новое описание, обновляем его.
    """

    username = normalize_username(
        username
    )

    name = name.strip()
    description = description.strip()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                id,
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (
                chat_id,
                thread_id,
                username,
            ),
        )

        for (
            item_id,
            stored_name,
            stored_quantity,
            stored_description,
        ) in cursor.fetchall():

            if (
                stored_name.casefold()
                == name.casefold()
            ):
                new_quantity = (
                    stored_quantity
                    + quantity
                )

                new_description = (
                    description
                    if description
                    else stored_description
                )

                conn.execute(
                    """
                    UPDATE inventory
                    SET
                        quantity = ?,
                        description = ?
                    WHERE id = ?
                    """,
                    (
                        new_quantity,
                        new_description,
                        item_id,
                    ),
                )

                conn.commit()

                return (
                    stored_name,
                    new_quantity,
                    new_description,
                )

        conn.execute(
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
            (
                chat_id,
                thread_id,
                username,
                name,
                quantity,
                description,
            ),
        )

        conn.commit()

        return (
            name,
            quantity,
            description,
        )


def get_inventory(
    chat_id: int,
    thread_id: int,
    username: str,
) -> list[tuple[str, int, str]]:
    username = normalize_username(
        username
    )

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (
                chat_id,
                thread_id,
                username,
            ),
        )

        return cursor.fetchall()


def get_all_inventory(
    chat_id: int,
    thread_id: int,
) -> dict[
    str,
    list[tuple[str, int, str]],
]:
    inventory = {}

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                username,
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
            ORDER BY id
            """,
            (
                chat_id,
                thread_id,
            ),
        )

        for (
            username,
            name,
            quantity,
            description,
        ) in cursor.fetchall():

            username = normalize_username(
                username
            )

            inventory.setdefault(
                username,
                [],
            ).append(
                (
                    name,
                    quantity,
                    description,
                )
            )

    return inventory


def delete_inventory_item(
    chat_id: int,
    thread_id: int,
    username: str,
    name: str,
    quantity: int = 1,
) -> tuple[str, int, int, str] | None:
    """
    Уменьшает quantity.

    Возвращает:
    (имя предмета, удалено, осталось, описание)
    """

    username = normalize_username(
        username
    )

    wanted_name = name.strip().casefold()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                id,
                name,
                quantity,
                description
            FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            ORDER BY id
            """,
            (
                chat_id,
                thread_id,
                username,
            ),
        )

        for (
            item_id,
            stored_name,
            stored_quantity,
            description,
        ) in cursor.fetchall():

            if (
                stored_name.casefold()
                != wanted_name
            ):
                continue

            removed_quantity = min(
                quantity,
                stored_quantity,
            )

            remaining_quantity = (
                stored_quantity
                - removed_quantity
            )

            if remaining_quantity <= 0:
                conn.execute(
                    """
                    DELETE FROM inventory
                    WHERE id = ?
                    """,
                    (item_id,),
                )

            else:
                conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = ?
                    WHERE id = ?
                    """,
                    (
                        remaining_quantity,
                        item_id,
                    ),
                )

            conn.commit()

            return (
                stored_name,
                removed_quantity,
                remaining_quantity,
                description,
            )

    return None


def clean_inventory(
    chat_id: int,
    thread_id: int,
    username: str,
):
    username = normalize_username(
        username
    )

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            """,
            (
                chat_id,
                thread_id,
                username,
            ),
        )

        conn.commit()


def clean_all_inventory(
    chat_id: int,
    thread_id: int,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM inventory
            WHERE chat_id = ?
              AND thread_id = ?
            """,
            (
                chat_id,
                thread_id,
            ),
        )

        conn.commit()


# ============================================================
# ТАЙМЕРЫ — БАЗА
# ============================================================

def save_timer(
    chat_id: int,
    message_id: int,
    title: str,
    end_time: datetime,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO timers (
                chat_id,
                message_id,
                title,
                end_time
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                chat_id,
                message_id,
                title,
                end_time.isoformat(),
            ),
        )

        conn.commit()


def delete_timer(
    chat_id: int,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            DELETE FROM timers
            WHERE chat_id = ?
            """,
            (chat_id,),
        )

        conn.commit()


def get_timer(
    chat_id: int,
):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                chat_id,
                message_id,
                title,
                end_time
            FROM timers
            WHERE chat_id = ?
            """,
            (chat_id,),
        )

        return cursor.fetchone()


def get_all_timers():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT
                chat_id,
                message_id,
                title,
                end_time
            FROM timers
            """
        )

        return cursor.fetchall()


# ============================================================
# ВРЕМЯ
# ============================================================

def parse_time(
    value: str,
) -> int | None:
    match = re.fullmatch(
        r"(\d+)(s|m|h)",
        value.lower(),
    )

    if not match:
        return None

    number = int(
        match.group(1)
    )

    unit = match.group(2)

    if unit == "s":
        return number

    if unit == "m":
        return number * 60

    if unit == "h":
        return number * 3600

    return None


def parse_until(
    date_value: str,
    time_value: str,
) -> datetime | None:
    try:
        target = datetime.strptime(
            f"{date_value} {time_value}",
            "%d.%m.%Y %H:%M",
        )

        return target.replace(
            tzinfo=TIMEZONE
        )

    except ValueError:
        return None


def format_time(
    seconds: int,
) -> str:
    days, remainder = divmod(
        seconds,
        86400,
    )

    hours, remainder = divmod(
        remainder,
        3600,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    if days > 0:
        return (
            f"{days} д. "
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    if hours > 0:
        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def timer_text(
    title: str,
    remaining: int,
    target_time: datetime,
) -> str:
    return (
        f"⏳ {title}\n\n"
        f"Осталось: {format_time(remaining)}\n\n"
        f"🎯 До: "
        f"{target_time.strftime('%d.%m.%Y %H:%M')}"
    )


# ============================================================
# ТАЙМЕР
# ============================================================

async def run_countdown(
    chat_id: int,
    message_id: int,
    end_time: datetime,
    title: str,
):
    try:
        while True:
            now = datetime.now(
                TIMEZONE
            )

            remaining = int(
                (
                    end_time - now
                ).total_seconds()
            )

            if remaining <= 0:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"✅ {title}\n\n"
                            f"Время вышло!"
                        ),
                    )

                except TelegramRetryAfter as error:
                    await asyncio.sleep(
                        error.retry_after + 1
                    )
                    continue

                except TelegramNetworkError:
                    await asyncio.sleep(5)
                    continue

                except TelegramBadRequest as error:
                    print(
                        "Ошибка завершения таймера:",
                        error,
                    )

                delete_timer(
                    chat_id
                )

                break

            await asyncio.sleep(
                min(
                    10,
                    remaining,
                )
            )

            now = datetime.now(
                TIMEZONE
            )

            remaining = max(
                0,
                int(
                    (
                        end_time - now
                    ).total_seconds()
                ),
            )

            if remaining <= 0:
                continue

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=timer_text(
                        title,
                        remaining,
                        end_time,
                    ),
                )

            except TelegramRetryAfter as error:
                await asyncio.sleep(
                    error.retry_after + 1
                )

            except TelegramNetworkError:
                await asyncio.sleep(5)

            except TelegramBadRequest as error:
                if (
                    "message is not modified"
                    in str(error).lower()
                ):
                    continue

                print(
                    "Ошибка таймера:",
                    error,
                )

                delete_timer(
                    chat_id
                )

                break

    except asyncio.CancelledError:
        # При systemctl restart запись в SQLite остаётся.
        raise

    finally:
        current_task = asyncio.current_task()

        if (
            active_timers.get(chat_id)
            is current_task
        ):
            active_timers.pop(
                chat_id,
                None,
            )


async def restore_timers():
    timers = get_all_timers()

    if not timers:
        print(
            "Сохранённых таймеров нет."
        )
        return

    print(
        "Сохранённых таймеров:",
        len(timers),
    )

    for (
        chat_id,
        message_id,
        title,
        end_time_string,
    ) in timers:
        try:
            end_time = datetime.fromisoformat(
                end_time_string
            )

        except ValueError:
            delete_timer(
                chat_id
            )
            continue

        task = asyncio.create_task(
            run_countdown(
                chat_id=chat_id,
                message_id=message_id,
                end_time=end_time,
                title=title,
            )
        )

        active_timers[
            chat_id
        ] = task


# ============================================================
# /START
# ============================================================

@dp.message(Command("start"))
async def start_handler(
    message: Message,
):
    await message.answer(
        "🎲 Шпаковский ДНДнарь\n\n"

        "⏱ Таймер:\n"
        "/timer 5m Перерыв\n"
        "/timer until 01.10.2026 18:30 Игра\n"
        "/stop\n\n"

        "🎲 Кубики:\n"
        "/roll d4\n"
        "/roll d6\n"
        "/roll d8\n"
        "/roll d10\n"
        "/roll d12\n"
        "/roll d20\n"
        "/roll d100\n\n"

        "🎒 Инвентарь текущей темы:\n"
        "/add @username Меч\n"
        "/add @username Зелье x3\n"
        "/add @username Меч x2 :: Стальной клинок\n"
        "/del @username Меч\n"
        "/del @username Зелье x2\n"
        "/inv @username\n"
        "/inv all\n"
        "/clean @username\n"
        "/clean all\n\n"

        "ℹ️ Служебное:\n"
        "/chatid"
    )


# ============================================================
# /CHATID
# ============================================================

@dp.message(Command("chatid"))
async def chatid_handler(
    message: Message,
):
    chat_id = message.chat.id
    thread_id = get_thread_id(
        message
    )

    admin = get_inventory_admin(
        chat_id,
        thread_id,
    )

    admin_text = (
        admin
        if admin is not None
        else "не назначен"
    )

    await message.answer(
        f"ID чата:\n"
        f"{chat_id}\n\n"
        f"ID темы:\n"
        f"{thread_id}\n\n"
        f"Администратор этой темы:\n"
        f"{admin_text}"
    )


# ============================================================
# /ADD
# ============================================================

@dp.message(Command("add"))
async def add_handler(
    message: Message,
):
    if not await check_inventory_permission(
        message
    ):
        return

    parts = message.text.split(
        maxsplit=2
    )

    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "/add @username Меч\n"
            "/add @username Зелье x3\n"
            "/add @username Меч x2 :: Стальной клинок\n\n"
            "Несколько предметов можно разделять запятыми."
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(
        message
    )

    username = normalize_username(
        parts[1]
    )

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    raw_items = [
        item.strip()
        for item
        in parts[2].split(",")
        if item.strip()
    ]

    if not raw_items:
        await message.answer(
            "Не указаны предметы."
        )
        return

    parsed_items = []

    for raw_item in raw_items:
        parsed_item = parse_inventory_item(
            raw_item
        )

        if parsed_item is None:
            await message.answer(
                "❌ Не понял предмет:\n"
                f"{raw_item}\n\n"
                "Форматы:\n"
                "Меч\n"
                "Зелье x3\n"
                "Меч x2 :: Стальной клинок"
            )
            return

        parsed_items.append(
            parsed_item
        )

    added_lines = []

    for (
        name,
        quantity,
        description,
    ) in parsed_items:

        add_inventory_item(
            chat_id,
            thread_id,
            username,
            name,
            quantity,
            description,
        )

        added_lines.append(
            format_inventory_item(
                name,
                quantity,
                description,
            )
        )

    await message.answer(
        f"🎒 {character_name} получил:\n\n"
        + "\n".join(
            added_lines
        )
    )


# ============================================================
# /DEL
# ============================================================

@dp.message(Command("del"))
async def del_handler(
    message: Message,
):
    if not await check_inventory_permission(
        message
    ):
        return

    parts = message.text.split(
        maxsplit=2
    )

    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "/del @username Меч\n"
            "/del @username Зелье x3"
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(
        message
    )

    username = normalize_username(
        parts[1]
    )

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    parsed_item = parse_inventory_item(
        parts[2]
    )

    if parsed_item is None:
        await message.answer(
            "❌ Не понял предмет.\n\n"
            "Форматы:\n"
            "/del @username Меч\n"
            "/del @username Зелье x3"
        )
        return

    (
        requested_name,
        requested_quantity,
        _,
    ) = parsed_item

    deleted = delete_inventory_item(
        chat_id,
        thread_id,
        username,
        requested_name,
        requested_quantity,
    )

    if deleted is None:
        await message.answer(
            f"❌ У {character_name} "
            f"нет предмета:\n"
            f"{requested_name}"
        )
        return

    (
        stored_name,
        removed_quantity,
        remaining_quantity,
        description,
    ) = deleted

    removed_text = format_inventory_item(
        stored_name,
        removed_quantity,
        description,
    )

    if remaining_quantity > 0:
        await message.answer(
            f"🗑 {character_name} потерял:\n\n"
            f"{removed_text}\n\n"
            f"Осталось: {remaining_quantity}"
        )

    else:
        await message.answer(
            f"🗑 {character_name} потерял:\n\n"
            f"{removed_text}\n\n"
            "Предмет полностью удалён из инвентаря."
        )


# ============================================================
# /INV
# ============================================================

@dp.message(Command("inv"))
async def inventory_handler(
    message: Message,
):
    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/inv @username\n"
            "/inv all"
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(
        message
    )

    target = parts[1].strip()

    # --------------------------------------------------------
    # INV ALL
    # --------------------------------------------------------

    if target.lower() == "all":
        characters = get_topic_characters(
            chat_id,
            thread_id,
        )

        if not characters:
            await message.answer(
                "❌ Для этой темы не настроены персонажи.\n\n"
                "Используй /chatid, чтобы узнать ID темы."
            )
            return

        all_inventory = get_all_inventory(
            chat_id,
            thread_id,
        )

        blocks = []

        for (
            username,
            character_name,
        ) in characters.items():

            items = all_inventory.get(
                username,
                [],
            )

            if not items:
                continue

            item_text = "\n".join(
                format_inventory_item(
                    name,
                    quantity,
                    description,
                )
                for (
                    name,
                    quantity,
                    description,
                ) in items
            )

            blocks.append(
                f"{character_name}:\n"
                f"{item_text}"
            )

        if not blocks:
            await message.answer(
                "🎒 Ни у одного персонажа "
                "в этой теме нет предметов."
            )
            return

        result = (
            "🎒 Инвентарь\n\n"
            + "\n\n".join(
                blocks
            )
        )

        await message.answer(
            result
        )
        return

    # --------------------------------------------------------
    # INV USER
    # --------------------------------------------------------

    username = normalize_username(
        target
    )

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    items = get_inventory(
        chat_id,
        thread_id,
        username,
    )

    if not items:
        await message.answer(
            f"🎒 {character_name}:\n\n"
            "Инвентарь пуст."
        )
        return

    item_text = "\n".join(
        format_inventory_item(
            name,
            quantity,
            description,
        )
        for (
            name,
            quantity,
            description,
        ) in items
    )

    await message.answer(
        f"🎒 {character_name}:\n\n"
        f"{item_text}"
    )


# ============================================================
# /CLEAN
# ============================================================

@dp.message(Command("clean"))
async def clean_handler(
    message: Message,
):
    if not await check_inventory_permission(
        message
    ):
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "/clean @username\n"
            "/clean all"
        )
        return

    chat_id = message.chat.id
    thread_id = get_thread_id(
        message
    )
    target = parts[1].strip()

    if target.lower() == "all":
        clean_all_inventory(
            chat_id,
            thread_id,
        )

        await message.answer(
            "💨 Все персонажи этой темы потеряли вещи."
        )
        return

    username = normalize_username(
        target
    )

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} "
            "не найден персонаж в этой теме."
        )
        return

    clean_inventory(
        chat_id,
        thread_id,
        username,
    )

    await message.answer(
        f"🧹 Инвентарь "
        f"{character_name} очищен."
    )


# ============================================================
# /ROLL
# ============================================================

@dp.message(Command("roll"))
async def roll_handler(
    message: Message,
):
    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "🎲 Доступно:\n"
            "d4, d6, d8, d10, d12, d20, d100"
        )
        return

    match = re.fullmatch(
        r"d(\d+)",
        parts[1].lower(),
    )

    if not match:
        await message.answer(
            "🎲 Не понял кубик."
        )
        return

    sides = int(
        match.group(1)
    )

    if sides not in SUPPORTED_DICE:
        await message.answer(
            "🎲 Доступно:\n"
            "d4, d6, d8, d10, d12, d20, d100"
        )
        return

    result = (
        secrets.randbelow(sides)
        + 1
    )

    if sides == 20 and result == 1:
        text = (
            "🎲 d20: 1\n\n"
            "💀 ЕДИНИЦА, "
            "КРИТИЧЕСКАЯ НЕУДАЧА!"
        )

    elif sides == 20 and result == 20:
        text = (
            "🎲 d20: 20\n\n"
            "🔥 О БОЖЕ МОЙ, "
            "КРИТИЧЕСКАЯ УДАЧА!"
        )

    else:
        text = (
            f"🎲 d{sides}: {result}"
        )

    await message.answer(
        text
    )


# ============================================================
# /TIMER
# ============================================================

@dp.message(Command("timer"))
async def timer_handler(
    message: Message,
):
    chat_id = message.chat.id

    existing_task = active_timers.get(
        chat_id
    )

    if (
        existing_task
        and not existing_task.done()
    ):
        await message.answer(
            "⚠️ В этом чате уже работает таймер.\n"
            "Используй /stop."
        )
        return

    saved_timer = get_timer(
        chat_id
    )

    if saved_timer:
        try:
            saved_end_time = datetime.fromisoformat(
                saved_timer[3]
            )

            if (
                saved_end_time
                > datetime.now(TIMEZONE)
            ):
                await message.answer(
                    "⚠️ В этом чате уже "
                    "работает таймер.\n"
                    "Используй /stop."
                )
                return

        except ValueError:
            pass

        delete_timer(
            chat_id
        )

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) < 2:
        await message.answer(
            "Например:\n"
            "/timer 5m Перерыв\n\n"
            "/timer until "
            "01.10.2026 18:30 Игра"
        )
        return

    args = parts[1].strip()

    if args.lower().startswith(
        "until "
    ):
        until_parts = args.split(
            maxsplit=3
        )

        if len(until_parts) < 3:
            await message.answer(
                "Формат:\n"
                "/timer until "
                "01.10.2026 18:30 Игра"
            )
            return

        target_time = parse_until(
            until_parts[1],
            until_parts[2],
        )

        if target_time is None:
            await message.answer(
                "Неверная дата.\n"
                "Формат: ДД.ММ.ГГГГ ЧЧ:ММ"
            )
            return

        if (
            target_time
            <= datetime.now(TIMEZONE)
        ):
            await message.answer(
                "Эта дата уже наступила."
            )
            return

        if len(until_parts) == 4:
            title = (
                until_parts[3].strip()
                or "Таймер"
            )
        else:
            title = "Таймер"

        end_time = target_time

    else:
        timer_parts = args.split(
            maxsplit=1
        )

        duration = parse_time(
            timer_parts[0]
        )

        if duration is None:
            await message.answer(
                "Пример:\n"
                "/timer 5m Перерыв"
            )
            return

        if duration < 10:
            await message.answer(
                "Минимальный таймер — 10 секунд."
            )
            return

        if len(timer_parts) == 2:
            title = (
                timer_parts[1].strip()
                or "Таймер"
            )
        else:
            title = "Таймер"

        end_time = (
            datetime.now(TIMEZONE)
            + timedelta(
                seconds=duration
            )
        )

    remaining = max(
        0,
        int(
            (
                end_time
                - datetime.now(TIMEZONE)
            ).total_seconds()
        ),
    )

    timer_message = await message.answer(
        timer_text(
            title,
            remaining,
            end_time,
        )
    )

    save_timer(
        chat_id,
        timer_message.message_id,
        title,
        end_time,
    )

    task = asyncio.create_task(
        run_countdown(
            chat_id,
            timer_message.message_id,
            end_time,
            title,
        )
    )

    active_timers[
        chat_id
    ] = task


# ============================================================
# /STOP
# ============================================================

@dp.message(Command("stop"))
async def stop_handler(
    message: Message,
):
    chat_id = message.chat.id

    saved_timer = get_timer(
        chat_id
    )

    task = active_timers.get(
        chat_id
    )

    if (
        saved_timer is None
        and (
            task is None
            or task.done()
        )
    ):
        await message.answer(
            "Сейчас активного таймера нет."
        )
        return

    if (
        task
        and not task.done()
    ):
        task.cancel()

        try:
            await task

        except asyncio.CancelledError:
            pass

    if saved_timer:
        (
            _,
            message_id,
            title,
            _,
        ) = saved_timer

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"❌ {title}\n\n"
                    f"Таймер остановлен."
                ),
            )

        except (
            TelegramBadRequest,
            TelegramNetworkError,
        ):
            pass

    delete_timer(
        chat_id
    )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    init_db()

    print(
        f"База данных: {DB_PATH}"
    )

    print(
        f"Часовой пояс: {TIMEZONE_NAME}"
    )

    await restore_timers()

    print(
        "Бот запущен."
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
