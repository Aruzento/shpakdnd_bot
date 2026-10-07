from app.topic_guard import allow_dnd
from app.db.inventory import get_inventory
from app.services.inventory import format_inventory
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.context import (
    check_global_admin_permission,
    check_topic_admin_permission,
    get_character_name,
    get_thread_id,
    normalize_username,
)
from app.db.characters import save_character_profile
from app.db.inventory import (
    add_inventory_item,
    clean_inventory,
    delete_inventory_item,
)
from app.services.inventory import (
    format_inventory_item,
    parse_inventory_item,
)
from app.mini.admin_grants import list_mini_items
from app.mini.shop import sync_shop_catalog
from app.mini.worlds import get_mini_world
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
    if not await allow_dnd(message,chat_id,thread_id):
        return None
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


@router.message(Command("admitems"))
async def admitems_handler(message: Message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    world = get_mini_world(chat_id, thread_id)

    if world is None or not world["enabled"]:
        await message.answer("❌ Команда /admitems работает только в теме D&D Mini.")
        return

    if not await check_topic_admin_permission(message):
        return

    sync_shop_catalog(world["id"])
    items = list_mini_items()

    if not items:
        await message.answer("В базе Mini пока нет предметов.")
        return

    lines = ["🎒 Предметы Mini в базе:", ""]
    for item in items:
        status = "✅" if item["active"] else "⛔"
        lines.append(
            f"{status} #{item['id']} — {item['name']} "
            f"[{item['code']}]"
        )

    lines.extend(
        [
            "",
            "Выдать предмет:",
            "/superadd CHAT:THEME @user -i CODE",
        ]
    )

    await message.answer("\n".join(lines))


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

    if parts[3].strip().casefold() == "all":
        clean_inventory(chat_id,thread_id,username)
        await message.answer(f"🧹 {chat_id}:{thread_id}: инвентарь {character_name} очищен.")
        return

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


@router.message(Command("adminv"))
async def adminv_handler(message: Message):
    if not await check_global_admin_permission(message):
        return
    parts=(message.text or "").split()
    if len(parts)!=3:
        await message.answer("Использование: /adminv CHAT_ID:TOPIC_ID @username")
        return
    target=await resolve_target(message,parts[1],parts[2])
    if target is None:
        return
    chat_id,thread_id,username,name=target
    await message.answer(format_inventory(name,get_inventory(chat_id,thread_id,username)))
