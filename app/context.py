from aiogram.types import Message

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
    """
    Оставлено для совместимости со старым кодом.
    Администратор инвентаря = администратор темы.
    """
    return get_topic_admin(chat_id, thread_id)


def get_character_name(
    chat_id: int,
    thread_id: int,
    username: str,
) -> str | None:
    settings = get_topic_settings(chat_id, thread_id)

    if not settings:
        return None

    characters = settings.get("characters", {})
    username = normalize_username(username)

    normalized_characters = {
        normalize_username(user): character_name
        for user, character_name in characters.items()
    }

    return normalized_characters.get(username)


def get_topic_characters(
    chat_id: int,
    thread_id: int,
) -> dict[str, str]:
    settings = get_topic_settings(chat_id, thread_id)

    if not settings:
        return {}

    characters = settings.get("characters", {})

    return {
        normalize_username(user): character_name
        for user, character_name in characters.items()
    }


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
