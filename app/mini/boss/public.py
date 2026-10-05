import json

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.mini.boss.catalog import list_boss_reward_items
from app.mini.boss.service import list_participants


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


def _reward_line(boss: dict) -> str:
    percent = max(0, min(100, int(boss.get("reward_percent", 100))))
    coins = int(boss.get("reward_coins", 0)) * percent // 100
    parts = [f"{coins} 🪙"] if int(boss.get("reward_coins", 0)) > 0 else []
    for item in reward_items(boss):
        suffix = f" ×{item['quantity']}" if item["quantity"] > 1 else ""
        parts.append(f"{item['name']}{suffix}")
    return " + ".join(parts) if parts else "без предметной награды"


def format_public_boss(boss: dict, participants: list[dict]) -> str:
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
        current = current_participant(boss, participants)
        lines.extend(["", f"🔄 Раунд: {boss['current_round']}"])
        if current is not None:
            who = str(current.get("username") or current.get("character_name") or "Игрок")
            hero = str(current.get("hero_name") or "герой")
            attack = int(current.get("battle_attack") or 0)
            lines.extend(
                [
                    f"⚔️ Сейчас ход: {who}",
                    f"🎴 {hero} • атака {attack}",
                    f"⏳ На ход: {boss['skip_after_hours']} ч.",
                ]
            )
    elif boss["status"] == "defeated":
        lines.extend(
            [
                "",
                "🏆 Победа! Награда выдана каждому участнику.",
                f"🎁 Итог: {_reward_line(boss)}",
            ]
        )
    elif boss["status"] == "failed":
        shards = max(0, int(boss.get("reward_coins", 0)) // 10)
        lines.extend(
            [
                "",
                "💥 Награда уничтожена — бой проигран.",
                f"🧩 Каждый участник получает {shards} осколков.",
            ]
        )
    elif boss["status"] == "cancelled":
        lines.extend(["", "Босс отменён."])

    return "\n".join(lines)[:1024]


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
    elif boss["status"] == "fighting":
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚔️ Ударить босса",
                    callback_data=f"miniboss:hit:{world_id}:{boss['id']}",
                )
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


async def refresh_public_boss(bot, world: dict, boss: dict) -> bool:
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
