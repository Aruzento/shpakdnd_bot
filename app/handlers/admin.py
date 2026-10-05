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
    clean_all_inventory,
    clean_inventory,
    delete_inventory_item,
)
from app.services.inventory import (
    format_inventory_item,
    parse_inventory_item,
)
from app.mini.admin_grants import (
    get_mini_player_by_username,
    grant_mini_coins,
    grant_mini_coins_all,
    grant_mini_item,
    grant_mini_item_all,
    grant_mini_hero,
    grant_mini_hero_all,
    list_mini_items,
)
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


def _parse_mini_admadd(message: Message) -> tuple[str, str, str] | None:
    """
    Mini-форматы:
    /admadd @user -c 100
    /admadd @user -i boss_coin_pouch
    /admadd ALL -c 100
    /admadd ALL -i boss_coin_pouch
    /admadd @user -p panic_dungeon_engineer
    /admadd ALL -p panic_dungeon_engineer
    """
    if not message.text:
        return None

    parts = message.text.split(maxsplit=3)
    if len(parts) != 4:
        return None

    username = parts[1].strip()
    flag = parts[2].strip().lower()
    value = parts[3].strip()

    is_all = username.upper() == "ALL"
    if (not username.startswith("@") and not is_all) or flag not in {"-c", "-i", "-p"}:
        return None

    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {'"', "'"}
    ):
        value = value[1:-1].strip()

    return username, flag, value


async def _handle_mini_admadd(message: Message) -> bool:
    parsed = _parse_mini_admadd(message)
    if parsed is None:
        return False

    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    world = get_mini_world(chat_id, thread_id)

    if world is None or not world["enabled"]:
        await message.answer(
            "❌ Этот формат /admadd работает только в теме D&D Mini."
        )
        return True

    if not await check_topic_admin_permission(message):
        return True

    target, flag, raw_value = parsed
    is_all = target.upper() == "ALL"

    admin_username = (
        "@" + message.from_user.username.lower()
        if message.from_user and message.from_user.username
        else "@admin"
    )

    if flag == "-c":
        try:
            value = int(raw_value)
        except ValueError:
            await message.answer(
                "❌ Количество монет должно быть целым числом.\n\n"
                "Примеры:\n"
                "/admadd @user -c 100\n"
                "/admadd ALL -c 100"
            )
            return True

        if value <= 0:
            await message.answer("❌ Количество монет должно быть больше 0.")
            return True

        if is_all:
            try:
                result = grant_mini_coins_all(
                    world["id"],
                    value,
                    admin_username,
                )
            except ValueError as error:
                await message.answer(f"❌ {error}")
                return True

            await message.answer(
                f"✅ Все Mini-игроки получили по {value} 🪙\n"
                f"Игроков: {result['players']}\n"
                f"Выдано всего: {result['total']} 🪙"
            )
            return True

        player = get_mini_player_by_username(
            world["id"],
            target,
        )
        if player is None:
            await message.answer(
                f"❌ Mini-игрок {normalize_username(target)} не найден в этой теме.\n"
                "Игрок должен сначала создать Mini-персонажа."
            )
            return True

        result = grant_mini_coins(
            player["id"],
            value,
            admin_username,
        )
        await message.answer(
            f"✅ {player['character_name']} ({player['username']}) получил "
            f"{value} 🪙\n"
            f"Баланс: {result['balance']} 🪙"
        )
        return True

    if flag == "-p":
        if is_all:
            try:
                result = grant_mini_hero_all(
                    world["id"],
                    raw_value,
                )
            except ValueError as error:
                await message.answer(f"❌ {error}")
                return True

            hero = result["hero"]
            text = (
                f"✅ Персонаж выдан Mini-игрокам:\n"
                f"{hero['name']} [{hero['code']}]\n"
                f"Получили: {result['granted']}"
            )
            if result.get("skipped"):
                text += f"\nУже был у игроков: {result['skipped']}"
            if result.get("auto_activated"):
                text += f"\nАвтоматически выбран активным: {result['auto_activated']}"
            await message.answer(text)
            return True

        player = get_mini_player_by_username(
            world["id"],
            target,
        )
        if player is None:
            await message.answer(
                f"❌ Mini-игрок {normalize_username(target)} не найден в этой теме.\n"
                "Игрок должен сначала создать Mini-персонажа."
            )
            return True

        try:
            hero = grant_mini_hero(
                player["id"],
                raw_value,
            )
        except ValueError as error:
            await message.answer(f"❌ {error}")
            return True

        if hero["applied"]:
            text = (
                f"✅ {player['character_name']} ({player['username']}) получил персонажа:\n"
                f"{hero['name']} [{hero['code']}]"
            )
            if hero.get("auto_activated"):
                text += "\nПерсонаж автоматически выбран активным."
        else:
            text = (
                f"ℹ️ У {player['character_name']} ({player['username']}) уже есть "
                f"{hero['name']} [{hero['code']}]. Повторно не выдавал."
            )
        await message.answer(text)
        return True

    # -i принимает как стабильный code предмета, так и старый числовой ID.
    sync_shop_catalog(world["id"])

    if is_all:
        try:
            result = grant_mini_item_all(
                world["id"],
                raw_value,
            )
        except ValueError as error:
            await message.answer(f"❌ {error}")
            return True

        item = result["item"]
        text = (
            f"✅ Все Mini-игроки получили предмет:\n"
            f"#{item['id']} — {item['name']} [{item['code']}]\n"
            f"Получили: {result['players']}"
        )
        if result.get("skipped"):
            text += f"\nПропущено (уже был нестакающийся предмет): {result['skipped']}"
        await message.answer(text)
        return True

    player = get_mini_player_by_username(
        world["id"],
        target,
    )
    if player is None:
        await message.answer(
            f"❌ Mini-игрок {normalize_username(target)} не найден в этой теме.\n"
            "Игрок должен сначала создать Mini-персонажа."
        )
        return True

    try:
        item = grant_mini_item(
            player["id"],
            raw_value,
        )
    except ValueError as error:
        await message.answer(f"❌ {error}")
        return True

    await message.answer(
        f"✅ {player['character_name']} ({player['username']}) получил предмет:\n"
        f"#{item['id']} — {item['name']} [{item['code']}]\n"
        f"Теперь в инвентаре: {item['quantity']} шт."
    )
    return True


@router.message(Command("admadd"))
async def admadd_handler(message: Message):
    if await _handle_mini_admadd(message):
        return

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
            "/admadd @user -i CODE",
            "/admadd ALL -i CODE",
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
