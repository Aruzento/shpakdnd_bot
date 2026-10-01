from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.context import (
    check_global_admin_permission,
    get_character_name,
    normalize_username,
)
from app.db.characters import save_character_profile
from app.db.inventory import (
    add_inventory_item,
    clean_all_inventory,
    clean_inventory,
    delete_inventory_item,
)
from app.services.inventory import (
    format_inventory_item,
    parse_inventory_item,
)
from app.topics import TOPIC_SETTINGS


router = Router(name="admin")


def parse_target_scope(
    raw_scope: str,
) -> tuple[int, int] | None:
    """
    Форматы:
    CHAT_ID
    CHAT_ID:TOPIC_ID

    Только CHAT_ID разрешён, если у этого чата
    в конфигурации ровно одна тема.
    """
    raw_scope = raw_scope.strip()

    if ":" in raw_scope:
        chat_text, thread_text = raw_scope.rsplit(
            ":",
            maxsplit=1,
        )

        try:
            chat_id = int(chat_text)
            thread_id = int(thread_text)
        except ValueError:
            return None

        if (
            chat_id not in TOPIC_SETTINGS
            or thread_id not in TOPIC_SETTINGS[chat_id]
        ):
            return None

        return chat_id, thread_id

    try:
        chat_id = int(raw_scope)
    except ValueError:
        return None

    topics = TOPIC_SETTINGS.get(chat_id)

    if not topics:
        return None

    if len(topics) != 1:
        return None

    thread_id = next(iter(topics))

    return chat_id, thread_id


def target_scope_help() -> str:
    return (
        "Для обычного чата или чата с одной настроенной темой:\n"
        "CHAT_ID\n\n"
        "Если в чате несколько тем:\n"
        "CHAT_ID:TOPIC_ID\n\n"
        "Пример:\n"
        "-1003376315265:4"
    )


async def resolve_target(
    message: Message,
    raw_scope: str,
    raw_username: str,
) -> tuple[int, int, str, str] | None:
    scope = parse_target_scope(raw_scope)

    if scope is None:
        await message.answer(
            "❌ Не удалось определить чат/тему.\n\n"
            + target_scope_help()
        )
        return None

    chat_id, thread_id = scope
    username = normalize_username(raw_username)

    character_name = get_character_name(
        chat_id,
        thread_id,
        username,
    )

    if character_name is None:
        await message.answer(
            f"❌ Для {username} не найден персонаж "
            f"в чате/теме {chat_id}:{thread_id}."
        )
        return None

    return (
        chat_id,
        thread_id,
        username,
        character_name,
    )


@router.message(Command("admadd"))
async def admadd_handler(message: Message):
    if not await check_global_admin_permission(message):
        return

    parts = message.text.split(maxsplit=3)

    if len(parts) < 4:
        await message.answer(
            "Использование:\n"
            "/admadd CHAT_ID[:TOPIC_ID] @username предмет\n\n"
            "Пример:\n"
            "/admadd -1003376315265:4 "
            "@daniil_savenko Зелье лечения x3"
        )
        return

    target = await resolve_target(
        message,
        parts[1],
        parts[2],
    )

    if target is None:
        return

    chat_id, thread_id, username, character_name = target

    raw_items = [
        item.strip()
        for item in parts[3].split(",")
        if item.strip()
    ]

    if not raw_items:
        await message.answer("❌ Не указаны предметы.")
        return

    parsed_items = []

    for raw_item in raw_items:
        parsed_item = parse_inventory_item(raw_item)

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

        parsed_items.append(parsed_item)

    added_lines = []

    for name, quantity, description in parsed_items:
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
        f"✅ {chat_id}:{thread_id}\n"
        f"🎒 {character_name} получил:\n\n"
        + "\n".join(added_lines)
    )


@router.message(Command("admdel"))
async def admdel_handler(message: Message):
    if not await check_global_admin_permission(message):
        return

    parts = message.text.split(maxsplit=3)

    if len(parts) < 4:
        await message.answer(
            "Использование:\n"
            "/admdel CHAT_ID[:TOPIC_ID] @username предмет\n\n"
            "Пример:\n"
            "/admdel -1003376315265:4 "
            "@daniil_savenko Зелье лечения x2"
        )
        return

    target = await resolve_target(
        message,
        parts[1],
        parts[2],
    )

    if target is None:
        return

    chat_id, thread_id, username, character_name = target

    parsed_item = parse_inventory_item(parts[3])

    if parsed_item is None:
        await message.answer(
            "❌ Не понял предмет.\n\n"
            "Пример:\n"
            "/admdel -1003376315265:4 "
            "@daniil_savenko Зелье лечения x2"
        )
        return

    requested_name, requested_quantity, _ = parsed_item

    deleted = delete_inventory_item(
        chat_id,
        thread_id,
        username,
        requested_name,
        requested_quantity,
    )

    if deleted is None:
        await message.answer(
            f"❌ У {character_name} нет предмета:\n"
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

    response = (
        f"✅ {chat_id}:{thread_id}\n"
        f"🗑 {character_name} потерял:\n\n"
        f"{removed_text}"
    )

    if remaining_quantity > 0:
        response += f"\n\nОсталось: {remaining_quantity}"
    else:
        response += "\n\nПредмет полностью удалён из инвентаря."

    await message.answer(response)


@router.message(Command("admclean"))
async def admclean_handler(message: Message):
    if not await check_global_admin_permission(message):
        return

    parts = message.text.split(maxsplit=2)

    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            "/admclean CHAT_ID[:TOPIC_ID] @username\n"
            "/admclean CHAT_ID[:TOPIC_ID] all"
        )
        return

    scope = parse_target_scope(parts[1])

    if scope is None:
        await message.answer(
            "❌ Не удалось определить чат/тему.\n\n"
            + target_scope_help()
        )
        return

    chat_id, thread_id = scope
    target_name = parts[2].strip()

    if target_name.lower() == "all":
        clean_all_inventory(
            chat_id,
            thread_id,
        )

        await message.answer(
            f"✅ {chat_id}:{thread_id}\n"
            "🧹 Инвентарь всех персонажей очищен."
        )
        return

    target = await resolve_target(
        message,
        parts[1],
        target_name,
    )

    if target is None:
        return

    chat_id, thread_id, username, character_name = target

    clean_inventory(
        chat_id,
        thread_id,
        username,
    )

    await message.answer(
        f"✅ {chat_id}:{thread_id}\n"
        f"🧹 Инвентарь {character_name} очищен."
    )


@router.message(Command("admcharset"))
async def admcharset_handler(message: Message):
    if not await check_global_admin_permission(message):
        return

    parts = message.text.split(maxsplit=3)

    if len(parts) < 4:
        await message.answer(
            "Использование:\n"
            "/admcharset CHAT_ID[:TOPIC_ID] @username "
            "уровень | Класс | Раса\n\n"
            "Пример:\n"
            "/admcharset -1003376315265:4 "
            "@daniil_savenko 5 | Монах | Человек"
        )
        return

    target = await resolve_target(
        message,
        parts[1],
        parts[2],
    )

    if target is None:
        return

    chat_id, thread_id, username, character_name = target

    profile_parts = [
        value.strip()
        for value in parts[3].split("|")
    ]

    if len(profile_parts) != 3:
        await message.answer(
            "❌ Формат:\n"
            "уровень | Класс | Раса"
        )
        return

    level_text, class_name, race = profile_parts

    try:
        level = int(level_text)
    except ValueError:
        await message.answer(
            "❌ Уровень должен быть целым числом."
        )
        return

    if level < 1:
        await message.answer(
            "❌ Уровень должен быть больше 0."
        )
        return

    if not class_name:
        await message.answer("❌ Не указан класс.")
        return

    if not race:
        await message.answer("❌ Не указана раса.")
        return

    save_character_profile(
        chat_id,
        thread_id,
        username,
        level,
        class_name,
        race,
    )

    await message.answer(
        f"✅ {chat_id}:{thread_id}\n"
        f"Данные {character_name} сохранены:\n\n"
        f"уровень: {level}\n"
        f"Класс: {class_name}\n"
        f"Раса: {race}"
    )
