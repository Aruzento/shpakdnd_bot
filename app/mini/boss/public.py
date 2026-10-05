import json

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.mini.boss.catalog import list_boss_reward_items
from app.mini.boss.service import list_participants, set_turn_message


STATUS_TEXT = {
    "announced": "🟢 Регистрация открыта",
    "ready": "🟡 Регистрация закрыта",
    "fighting": "🔴 Бой идёт",
    "cancelled": "⚫ Босс отменён",
    "defeated": "🏆 Босс побеждён",
    "failed": "💀 Бой проигран",
}


def _item_names() -> dict[str, str]:
    return {
        item["code"]: item["name"]
        for item in list_boss_reward_items(active_only=False)
    }


def reward_items(boss: dict) -> list[dict]:
    try:
        raw = json.loads(boss.get("reward_items_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    names = _item_names()
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        quantity = int(item.get("quantity", 1) or 1)
        if code and quantity > 0:
            result.append(
                {
                    "code": code,
                    "quantity": quantity,
                    "name": names.get(code, code),
                }
            )
    return result


def participant_label(row: dict) -> str:
    username = str(row.get("username") or "").strip()
    character = str(row.get("character_name") or "Игрок").strip()
    hero = str(row.get("hero_name") or "без героя").strip()
    who = username or character
    extra = ""
    if int(row.get("battle_attack") or 0) > 0:
        extra = f" • ⚔️ {int(row['battle_attack'])}"
    return f"{row['queue_position']}. {who} — {hero}{extra}"


def format_participants(participants: list[dict]) -> str:
    if not participants:
        return "Пока никто не записался."
    return "\n".join(participant_label(row) for row in participants)


def current_participant(boss: dict, participants: list[dict]) -> dict | None:
    if boss.get("status") != "fighting":
        return None
    position = int(boss.get("current_turn_position") or 0)
    for row in participants:
        if int(row["queue_position"]) == position:
            return row
    return None


def _participant_mention(row: dict | None) -> str:
    if not row:
        return "Игрок"
    username = str(row.get("username") or "").strip()
    if username:
        return username
    return str(row.get("character_name") or "Игрок").strip()


def _reward_line(boss: dict) -> str:
    percent = max(0, min(100, int(boss.get("reward_percent", 100))))
    coins = int(boss.get("reward_coins", 0)) * percent // 100
    parts = [f"{coins} 🪙"] if int(boss.get("reward_coins", 0)) > 0 else []
    for item in reward_items(boss):
        suffix = f" ×{item['quantity']}" if item["quantity"] > 1 else ""
        parts.append(f"{item['name']}{suffix}")
    return " + ".join(parts) if parts else "без предметной награды"



def boss_attack_passive_lines(reward_event: dict | None) -> list[str]:
    """Форматирует публичные сообщения пассивок, сработавших на атаке босса."""
    if not reward_event:
        return []

    lines = []
    for event in reward_event.get("passive_events", []) or []:
        message = str(event.get("message") or "").strip()
        if not message:
            continue
        who = str(
            event.get("username")
            or event.get("character_name")
            or event.get("hero_name")
            or "Игрок"
        ).strip()
        lines.append(f"{who}: {message}")
    return lines

def format_public_boss(boss: dict, participants: list[dict]) -> str:
    """Статичная карточка босса/регистрации. Текущий ход публикуется отдельно."""
    status = STATUS_TEXT.get(str(boss["status"]), str(boss["status"]))
    minimum = int(boss["min_players"])
    count = len(participants)
    shields = int(boss.get("reward_shields", 3))
    shields_max = int(boss.get("reward_shields_max", 3))
    percent = int(boss.get("reward_percent", 100))

    lines = [
        f"👹 {boss['name']}",
        "",
        str(boss.get("description") or "Без описания."),
        "",
        f"❤️ HP: {boss['current_hp']}/{boss['max_hp']}",
        f"👥 Участники: {count} • минимум {minimum}",
        f"🎁 Награда сейчас: {_reward_line(boss)}",
        f"🛡 Щиты награды: {shields}/{shields_max}",
        f"💎 Сохранность награды: {percent}%",
        status,
    ]

    if boss["status"] == "announced":
        lines.extend(["", "Запишись на бой кнопкой ниже."])
    elif boss["status"] == "ready":
        lines.extend(["", "Состав зафиксирован. Администратор может начать бой."])
    elif boss["status"] == "fighting":
        lines.extend(
            [
                "",
                f"🔄 Раунд: {boss['current_round']}",
                "⚔️ Актуальный ход публикуется отдельным сообщением внизу темы.",
            ]
        )
    elif boss["status"] == "defeated":
        if str(boss.get("battle_result") or "") == "admin_victory":
            victory_text = "🏆 Победа! Награда выдана всем зарегистрированным участникам."
        else:
            victory_text = (
                "🏆 Победа! Награда выдана тем, кто участвовал в бою "
                "или использовал фантомное участие."
            )
        lines.extend(
            [
                "",
                victory_text,
                f"🎁 Итог: {_reward_line(boss)}",
            ]
        )
    elif boss["status"] == "failed":
        shards = max(0, int(boss.get("reward_coins", 0)) // 10)
        lines.extend(
            [
                "",
                "💥 Награда уничтожена — бой проигран.",
                f"🧩 Участники с правом на награду получают {shards} осколков.",
            ]
        )
    elif boss["status"] == "cancelled":
        lines.extend(["", "Босс отменён."])

    return "\n".join(lines)[:1024]


def format_public_turn(
    boss: dict,
    participants: list[dict],
    *,
    notice: str = "",
) -> str:
    """Сообщение боя, которое всегда видно всей теме и движется вниз после хода."""
    lines = []
    notice = str(notice or "").strip()
    if notice:
        lines.extend([notice, ""])

    lines.extend(
        [
            f"👹 {boss['name']}",
            f"❤️ HP: {boss['current_hp']}/{boss['max_hp']}",
            f"🎁 Награда: {_reward_line(boss)}",
            f"🛡 Щиты: {boss.get('reward_shields', 0)}/{boss.get('reward_shields_max', 0)}",
        ]
    )

    status = str(boss.get("status") or "")
    if status == "fighting":
        current = current_participant(boss, participants)
        lines.extend(["", f"🔄 Раунд: {boss['current_round']}"])
        if current is not None:
            mention = _participant_mention(current)
            hero = str(current.get("hero_name") or "герой")
            attack = int(current.get("battle_attack") or 0)
            lines.extend(
                [
                    f"⚔️ Ход: {mention}",
                    f"🎴 {hero} • атака {attack}",
                    f"⏳ На ход: {boss['skip_after_hours']} ч.",
                ]
            )
        else:
            lines.append("⚠️ Не удалось определить текущего игрока.")
    elif status == "defeated":
        lines.extend(["", "🏆 Босс повержен!", f"🎁 Итог: {_reward_line(boss)}"])
    elif status == "failed":
        shards = max(0, int(boss.get("reward_coins", 0)) // 10)
        lines.extend(
            [
                "",
                "💀 Бой проигран: награда уничтожена.",
                f"🧩 Награда за участие: {shards} осколков.",
            ]
        )
    else:
        lines.extend(["", STATUS_TEXT.get(status, status)])

    return "\n".join(lines)[:4096]


def public_boss_menu(world_id: int, boss: dict) -> InlineKeyboardMarkup:
    rows = []
    if boss["status"] == "announced":
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚔️ Записаться",
                    callback_data=f"miniboss:join:{world_id}:{boss['id']}",
                ),
                InlineKeyboardButton(
                    text="🚪 Выйти",
                    callback_data=f"miniboss:leave:{world_id}:{boss['id']}",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="👥 Участники",
                callback_data=f"miniboss:list:{world_id}:{boss['id']}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def public_turn_menu(world_id: int, boss: dict) -> InlineKeyboardMarkup | None:
    if boss.get("status") != "fighting":
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚔️ Ударить босса",
                    callback_data=f"miniboss:hit:{world_id}:{boss['id']}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Участники",
                    callback_data=f"miniboss:list:{world_id}:{boss['id']}",
                )
            ],
        ]
    )


async def refresh_public_boss(bot, world: dict, boss: dict) -> bool:
    """Обновляет старую статичную карточку регистрации/босса на её месте."""
    message_id = boss.get("signup_message_id")
    if not message_id:
        return False

    participants = list_participants(boss["id"])
    text = format_public_boss(boss, participants)
    markup = public_boss_menu(world["id"], boss)

    try:
        if boss.get("signup_message_kind") == "photo":
            await bot.edit_message_caption(
                chat_id=world["chat_id"],
                message_id=int(message_id),
                caption=text,
                reply_markup=markup,
            )
        else:
            await bot.edit_message_text(
                chat_id=world["chat_id"],
                message_id=int(message_id),
                text=text,
                reply_markup=markup,
            )
        return True
    except TelegramBadRequest as error:
        text_error = str(error).lower()
        if "message is not modified" not in text_error:
            print(
                "Boss: не удалось обновить публичную карточку: "
                f"{type(error).__name__}: {error}"
            )
        return False


async def replace_public_turn(
    bot,
    world: dict,
    boss: dict,
    *,
    notice: str = "",
) -> bool:
    """
    Публикует свежий публичный ход внизу темы и удаляет предыдущий.

    Новое сообщение отправляется первым: даже если удаление старого не удалось,
    игроки не останутся без актуального хода.
    """
    participants = list_participants(boss["id"])
    text = format_public_turn(boss, participants, notice=notice)
    markup = public_turn_menu(world["id"], boss)
    old_message_id = boss.get("turn_message_id")

    try:
        sent = await bot.send_message(
            chat_id=world["chat_id"],
            message_thread_id=world["thread_id"] or None,
            text=text,
            reply_markup=markup,
        )
    except TelegramAPIError as error:
        print(
            "Boss: не удалось опубликовать публичный ход: "
            f"{type(error).__name__}: {error}"
        )
        return False

    set_turn_message(int(boss["id"]), int(sent.message_id))

    if old_message_id and int(old_message_id) != int(sent.message_id):
        try:
            await bot.delete_message(
                chat_id=world["chat_id"],
                message_id=int(old_message_id),
            )
        except TelegramAPIError as error:
            print(
                "Boss: не удалось удалить предыдущий публичный ход: "
                f"{type(error).__name__}: {error}"
            )

    boss["turn_message_id"] = int(sent.message_id)
    return True


async def publish_admin_victory(
    bot,
    world: dict,
    boss: dict,
    *,
    old_turn_message_id: int | None = None,
) -> bool:
    """Публикует аварийную победу и убирает старую карточку текущего хода."""
    text = (
        "⚡ Сами боги услышали клич, и раскат грома ударил по боссу. "
        "Босс пал.\n\n"
        "🏆 Поздравляю, вы победили!\n\n"
        "🎁 Награда выдана всем, кто зарегистрировался на бой."
    )

    try:
        await bot.send_message(
            chat_id=world["chat_id"],
            message_thread_id=world["thread_id"] or None,
            text=text,
        )
    except TelegramAPIError as error:
        print(
            "Boss: не удалось опубликовать аварийную победу: "
            f"{type(error).__name__}: {error}"
        )
        return False

    message_id = old_turn_message_id
    if message_id is None:
        message_id = boss.get("turn_message_id")

    if message_id:
        try:
            await bot.delete_message(
                chat_id=world["chat_id"],
                message_id=int(message_id),
            )
        except TelegramAPIError as error:
            print(
                "Boss: не удалось удалить ход после аварийной победы: "
                f"{type(error).__name__}: {error}"
            )

    set_turn_message(int(boss["id"]), None)
    boss["turn_message_id"] = None
    return True


async def ensure_public_turn(bot, world: dict, boss: dict) -> bool:
    """Восстанавливает публичное сообщение текущего хода после рестарта бота."""
    if boss.get("status") != "fighting":
        return False

    participants = list_participants(boss["id"])
    text = format_public_turn(boss, participants)
    markup = public_turn_menu(world["id"], boss)
    message_id = boss.get("turn_message_id")

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=world["chat_id"],
                message_id=int(message_id),
                text=text,
                reply_markup=markup,
            )
            return True
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
                return True
        except TelegramAPIError:
            pass

    return await replace_public_turn(bot, world, boss)
