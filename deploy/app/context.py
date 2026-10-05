import sqlite3

from aiogram.types import Message

from app.config import DB_PATH, GLOBAL_ADMIN_USERNAME
from app.topics import TOPIC_SETTINGS


def normalize_username(username: str) -> str:
    username = username.strip().lower()

    if not username.startswith("@"):
        username = "@" + username

    return username


def get_thread_id(message: Message) -> int:
    """Возвращает ID Telegram-темы или 0 для чата без темы."""
    return message.message_thread_id or 0


def get_topic_settings(
    chat_id: int,
    thread_id: int,
) -> dict | None:
    return TOPIC_SETTINGS.get(chat_id, {}).get(thread_id)


def get_sender_username(message: Message) -> str | None:
    if not message.from_user or not message.from_user.username:
        return None

    return normalize_username(message.from_user.username)


def get_topic_admin(
    chat_id: int,
    thread_id: int,
) -> str | None:
    settings = get_topic_settings(chat_id, thread_id)

    if not settings:
        return None

    admin = settings.get("admin")

    if not admin:
        return None

    return normalize_username(admin)


def get_inventory_admin(
    chat_id: int,
    thread_id: int,
) -> str | None:
    return get_topic_admin(chat_id, thread_id)


def get_character_name(
    chat_id: int,
    thread_id: int,
    username: str,
) -> str | None:
    """
    Сначала ищет персонажа, созданного через /create, в БД.
    Затем использует старую статическую конфигурацию как fallback.
    """
    username = normalize_username(username)

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT name
            FROM characters
            WHERE chat_id = ?
              AND thread_id = ?
              AND username = ?
            """,
            (
                chat_id,
                thread_id,
                username,
            ),
        ).fetchone()

    if row is not None:
        return str(row[0])

    settings = get_topic_settings(chat_id, thread_id)

    if not settings:
        return None

    characters = settings.get("characters", {})

    normalized_characters = {
        normalize_username(user): character_name
        for user, character_name in characters.items()
    }

    return normalized_characters.get(username)


def get_topic_characters(
    chat_id: int,
    thread_id: int,
) -> dict[str, str]:
    """
    Старые персонажи из TOPIC_SETTINGS + созданные через /create.
    """
    settings = get_topic_settings(chat_id, thread_id)
    characters = {}

    if settings:
        characters.update(
            {
                normalize_username(user): character_name
                for user, character_name
                in settings.get("characters", {}).items()
            }
        )

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT username, name
            FROM characters
            WHERE chat_id = ?
              AND thread_id = ?
            ORDER BY rowid
            """,
            (
                chat_id,
                thread_id,
            ),
        ).fetchall()

    for username, character_name in rows:
        characters[
            normalize_username(str(username))
        ] = str(character_name)

    return characters


def can_manage_topic(message: Message) -> bool:
    thread_id = get_thread_id(message)

    required_admin = get_topic_admin(
        message.chat.id,
        thread_id,
    )

    if required_admin is None:
        return False

    sender_username = get_sender_username(message)

    if sender_username is None:
        return False

    return sender_username == required_admin


def can_manage_inventory(message: Message) -> bool:
    return can_manage_topic(message)


def is_global_admin(message: Message) -> bool:
    sender_username = get_sender_username(message)

    if sender_username is None:
        return False

    return sender_username == normalize_username(
        GLOBAL_ADMIN_USERNAME
    )


async def check_global_admin_permission(
    message: Message,
) -> bool:
    if is_global_admin(message):
        return True

    await message.answer(
        "⛔ Эта команда доступна только глобальному администратору."
    )
    return False


async def check_topic_admin_permission(message: Message) -> bool:
    thread_id = get_thread_id(message)

    required_admin = get_topic_admin(
        message.chat.id,
        thread_id,
    )

    if required_admin is None:
        await message.answer(
            "⛔ Для этой темы не назначен администратор.\n\n"
            "Используй /chatid, чтобы узнать ID темы."
        )
        return False

    if can_manage_topic(message):
        return True

    await message.answer(
        "⛔ У тебя нет прав на изменение данных этой темы.\n"
        f"Администратор этой темы: {required_admin}"
    )
    return False


async def check_inventory_permission(message: Message) -> bool:
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
