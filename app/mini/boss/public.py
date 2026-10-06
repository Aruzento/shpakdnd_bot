import asyncio
import json

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.mini.boss.catalog import list_boss_reward_items
from app.mini.catalog import hero_image_path
from app.mini.presentation import faction_label, tag_label, TRAIT_LABELS, FEATURE_DESCRIPTIONS
from app.mini.combat import creatures
from app.mini.boss.service import get_boss, list_participants, set_turn_message


_TURN_PUBLISH_LOCKS: dict[int, asyncio.Lock] = {}


def _turn_publish_lock(boss_id: int) -> asyncio.Lock:
    """Один publisher на босса внутри процесса, чтобы сообщения хода не гонялись."""
    boss_id = int(boss_id)
    lock = _TURN_PUBLISH_LOCKS.get(boss_id)
    if lock is None:
        lock = asyncio.Lock()
        _TURN_PUBLISH_LOCKS[boss_id] = lock
    return lock


def _turn_state_key(boss: dict) -> tuple:
    """Версия видимого состояния хода без привязки к Telegram message_id."""
    return (
        str(boss.get("status") or ""),
        int(boss.get("current_round") or 0),
        int(boss.get("current_turn_position") or 0),
        int(boss.get("current_hp") or 0),
        int(boss.get("reward_shields") or 0),
        int(boss.get("reward_percent") or 0),
        str(boss.get("battle_result") or ""),
        int(boss.get("reward_temp_hp", 0)), int(boss.get("reward_corruption", 0)),
        str(boss.get("feature_state_json") or "{}"),
    )


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
    """Boss announcement with Combat v2 traits and unchanged reward amounts."""
    percent = max(0, min(100, int(boss.get("reward_percent", 100))))
    coins = int(boss.get("reward_coins", 0)) * percent // 100
    features = json.loads(boss.get("features_json") or "[]")
    feature_text = ", ".join(tag_label(tag, TRAIT_LABELS) for tag in features) or "Нет."
    feature_details = ("\n".join(FEATURE_DESCRIPTIONS[creatures.canonical_trait(tag)]
                                    for tag in features if creatures.canonical_trait(tag) in FEATURE_DESCRIPTIONS)
                       if creatures.rules_enabled(boss) else "")
    items = [
        item["name"] + (f" ×{item['quantity']}" if item["quantity"] > 1 else "")
        for item in reward_items(boss)
    ]
    lines = [
        f"👹 {boss['name']} • {faction_label(boss.get('faction', 'commoners'))}",
        "",
        str(boss.get("description") or "Без описания.")[:120],
        "",
        f"❤️ HP: {boss['current_hp']}/{boss['max_hp']}",
        f"💫 Способность: {str(boss.get('ability_text') or 'Нет особой способности.')[:200]}",
        "",
        f"‼️ Особенности: {feature_text}",
        "",
        f"🎁 Награда: {coins} монет • 🛡 Щиты: {boss.get('reward_shields', 3)}",
        f"🎁 Доп. награда: {', '.join(items) if items else 'Нет.'}",
    ]
    if int(boss.get("reward_temp_hp", 0)) or int(boss.get("reward_corruption", 0)):
        lines.append(
            f"💎 Запас награды: {creatures.effective_reward_percent(boss)}%"
            f" • Временный: +{int(boss.get('reward_temp_hp', 0))}%"
            f" • Порча: {int(boss.get('reward_corruption', 0))}%"
        )
    status = str(boss["status"])
    if status in ("announced", "ready"):
        lines.extend([
            "", f"👥 Участники: {len(participants)} • минимум {boss.get('min_players', 1)}",
            STATUS_TEXT[status],
        ])
    elif status == "defeated":
        lines.extend(["", (
            "🏆 Победа! Награда выдана всем зарегистрированным участникам."
            if boss.get("battle_result") == "admin_victory"
            else "🏆 Победа! Награда выдана участникам боя и фантомным участникам."
        )])
    elif status == "failed":
        lines.extend(["", "💥 Награда уничтожена — бой проигран.",
                      f"🧩 Награда за участие: {int(boss.get('reward_coins', 0)) // 10} осколков."])
    elif status == "cancelled":
        lines.extend(["", "Босс отменён."])
    text = "\n".join(lines)
    if feature_details:
        available = 1024 - len(text) - 2
        # Include complete descriptions only; never truncate the reward fields.
        details = []
        for detail in feature_details.splitlines():
            if len("\n".join(details + [detail])) > available:
                break
            details.append(detail)
        if details:
            text += "\n\n" + "\n".join(details)
    return text[:1024]


def format_public_turn(
    boss: dict,
    participants: list[dict],
    *,
    notice: str = "",
) -> str:
    status = str(boss.get("status") or "")
    lines = [
        f"РАУНД {boss['current_round']}",
        "",
        f"👹 {boss['name']} - ❤️ HP: {boss['current_hp']}/{boss['max_hp']}",
        "",
        f"🛡 Щиты: {boss.get('reward_shields', 0)}/{boss.get('reward_shields_max', 0)}"
        f" • 💎 Состояние награды: {boss.get('reward_percent', 100)}%",
    ]
    if int(boss.get("reward_temp_hp", 0)) or int(boss.get("reward_corruption", 0)):
        lines.append(
            f"💎 Реальная награда: {int(boss['reward_coins']) * int(boss['reward_percent']) // 100} монет"
            f" • Временный запас: +{int(boss.get('reward_temp_hp', 0))}%"
            f" • Порча: {int(boss.get('reward_corruption', 0))}%"
            f" • Эффективный запас: {creatures.effective_reward_percent(boss)}%"
            f" ({int(boss['reward_coins']) * creatures.effective_reward_percent(boss) // 100} монет)"
        )
    if status == "fighting":
        current = current_participant(boss, participants)
        if current is not None:
            lines.extend([
                "",
                f"⚔️ Ход: {_participant_mention(current)} - \"{current.get('hero_name') or 'герой'}\"",
                f"⏳ На ход: {boss['skip_after_hours']} ч.",
            ])
        else:
            lines.extend(["", "⚠️ Не удалось определить текущего игрока."])
    elif status == "defeated":
        lines.extend(["", "🏆 Босс повержен!", f"🎁 Итог: {_reward_line(boss)}"])
    elif status == "failed":
        lines.extend(["", "💀 Бой проигран: награда уничтожена.",
                      f"🧩 Награда за участие: {int(boss.get('reward_coins', 0)) // 10} осколков."])
    else:
        lines.extend(["", STATUS_TEXT.get(status, status)])
    if not notice:
        saved = json.loads(boss.get("turn_notice_json") or "{}")
        if (
            saved.get("status") == status
            and saved.get("round") == int(boss["current_round"])
            and saved.get("position") == int(boss.get("current_turn_position", 0))
        ):
            notice = str(saved.get("text") or "")
    if notice.strip():
        lines.extend(["", notice.strip()])
    return "\n".join(lines)[:4096]


def _turn_image(boss: dict, participants: list[dict]):
    current = current_participant(boss, participants)
    return hero_image_path(current.get("hero_image_path", "")) if current else None


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
                    callback_data=(
                        f"miniboss:hit:{world_id}:{boss['id']}:"
                        f"{boss.get('current_round', 0)}:"
                        f"{boss.get('current_turn_position', 0)}"
                    ),
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


async def _send_public_turn_locked(
    bot,
    world: dict,
    boss: dict,
    *,
    notice: str = "",
) -> bool:
    """Отправляет новый ход. Вызывается только под lock конкретного босса."""
    participants = list_participants(boss["id"])
    text = format_public_turn(boss, participants, notice=notice)
    markup = public_turn_menu(world["id"], boss)
    old_message_id = boss.get("turn_message_id")

    # Long event notices must not silently disappear at the caption limit.
    image = _turn_image(boss, participants) if len(text) <= 1024 else None
    kind = "text"
    try:
        if image is not None:
            try:
                sent = await bot.send_photo(
                    chat_id=world["chat_id"],
                    message_thread_id=world["thread_id"] or None,
                    photo=FSInputFile(image), caption=text[:1024], reply_markup=markup,
                )
                kind = "photo"
            except TelegramBadRequest:
                sent = await bot.send_message(
                    chat_id=world["chat_id"],
                    message_thread_id=world["thread_id"] or None,
                    text=text, reply_markup=markup,
                )
        else:
            sent = await bot.send_message(
                chat_id=world["chat_id"],
                message_thread_id=world["thread_id"] or None,
                text=text, reply_markup=markup,
            )
    except TelegramAPIError as error:
        # A network failure can occur after Telegram accepted the send.
        # Never produce a second message as an uncertain fallback.
        print(f"Boss: не удалось опубликовать публичный ход: {type(error).__name__}: {error}")
        return False

    notice_json = json.dumps({
        "status": boss["status"], "round": int(boss["current_round"]),
        "position": int(boss.get("current_turn_position", 0)), "text": notice,
    }, ensure_ascii=False)
    if kind == "photo":
        set_turn_message(int(boss["id"]), int(sent.message_id), kind=kind, notice_json=notice_json)
    else:
        set_turn_message(int(boss["id"]), int(sent.message_id), notice_json=notice_json)
    boss["turn_message_kind"] = kind
    boss["turn_notice_json"] = notice_json

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


async def replace_public_turn(
    bot,
    world: dict,
    boss: dict,
    *,
    notice: str = "",
) -> bool:
    """
    Публикует свежий публичный ход и удаляет предыдущий.

    Защищено от гонки: watcher, callback удара и открытие boss-меню могут
    почти одновременно попросить обновление одного и того же хода. Раньше
    каждый из них успевал отправить своё сообщение до записи turn_message_id.
    Теперь publisher сериализован, а устаревший запрос не создаёт дубль.
    """
    boss_id = int(boss["id"])
    expected_message_id = boss.get("turn_message_id")
    expected_state = _turn_state_key(boss)

    async with _turn_publish_lock(boss_id):
        fresh = get_boss(boss_id)
        if fresh is None:
            return False

        # Пока этот coroutine ждал lock, бой мог уже перейти ещё на один ход.
        # Старое уведомление нельзя публиковать поверх более нового состояния.
        if _turn_state_key(fresh) != expected_state:
            fresh_message_id = fresh.get("turn_message_id")
            boss["turn_message_id"] = (
                int(fresh_message_id) if fresh_message_id is not None else None
            )
            return True

        current_message_id = fresh.get("turn_message_id")
        expected_normalized = (
            int(expected_message_id) if expected_message_id is not None else None
        )
        current_normalized = (
            int(current_message_id) if current_message_id is not None else None
        )

        # Если ID уже изменился после снимка, другой publisher успел
        # сформировать этот переход. Не публикуем второе сообщение.
        if current_normalized != expected_normalized:
            boss["turn_message_id"] = current_normalized
            saved = json.loads(fresh.get("turn_notice_json") or "{}")
            # Recovery can publish the new state before the callback arrives.
            # Attach its ability notice to that message, without another send.
            if current_normalized is not None and notice.strip() and (
                saved.get("status") == fresh["status"]
                and saved.get("round") == int(fresh["current_round"])
                and saved.get("position") == int(fresh.get("current_turn_position", 0))
                and not saved.get("text")
            ):
                participants = list_participants(boss_id)
                text = format_public_turn(fresh, participants, notice=notice)
                kind = fresh.get("turn_message_kind", "text")
                if kind == "photo" and len(text) > 1024:
                    applied = await _send_public_turn_locked(bot, world, fresh, notice=notice)
                    if applied:
                        boss["turn_message_id"] = fresh.get("turn_message_id")
                        boss["turn_message_kind"] = fresh.get("turn_message_kind", "text")
                    return applied
                try:
                    kwargs = {
                        "chat_id": world["chat_id"], "message_id": current_normalized,
                        "reply_markup": public_turn_menu(world["id"], fresh),
                    }
                    if kind == "photo":
                        await bot.edit_message_caption(**kwargs, caption=text)
                    else:
                        await bot.edit_message_text(**kwargs, text=text)
                except TelegramBadRequest as error:
                    if "message is not modified" not in str(error).lower():
                        return False
                except TelegramAPIError:
                    return False
                saved["text"] = notice
                set_turn_message(boss_id, current_normalized, kind=kind,
                                 notice_json=json.dumps(saved, ensure_ascii=False))
            return True

        applied = await _send_public_turn_locked(
            bot,
            world,
            fresh,
            notice=notice,
        )
        if applied:
            boss["turn_message_id"] = fresh.get("turn_message_id")
            boss["turn_message_kind"] = fresh.get("turn_message_kind", "text")
        return applied


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
    """Recover one current turn message, preserving photo/text and dedup locks."""
    if boss.get("status") != "fighting":
        return False
    boss_id = int(boss["id"])
    async with _turn_publish_lock(boss_id):
        fresh = get_boss(boss_id)
        if fresh is None or fresh.get("status") != "fighting":
            return False
        participants = list_participants(boss_id)
        text = format_public_turn(fresh, participants)
        markup = public_turn_menu(world["id"], fresh)
        message_id = fresh.get("turn_message_id")
        kind = fresh.get("turn_message_kind", "text")
        wants_photo = _turn_image(fresh, participants) is not None and len(text) <= 1024
        # The old text message is upgraded to a hero photo once on recovery.
        if message_id and not (kind == "text" and wants_photo) and not (kind == "photo" and len(text) > 1024):
            try:
                if kind == "photo":
                    await bot.edit_message_caption(
                        chat_id=world["chat_id"], message_id=int(message_id),
                        caption=text[:1024], reply_markup=markup,
                    )
                else:
                    await bot.edit_message_text(
                        chat_id=world["chat_id"], message_id=int(message_id),
                        text=text, reply_markup=markup,
                    )
                boss["turn_message_id"] = int(message_id)
                boss["turn_message_kind"] = kind
                return True
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    boss["turn_message_id"] = int(message_id)
                    boss["turn_message_kind"] = kind
                    return True
            except TelegramAPIError as error:
                print(f"Boss: ошибка проверки хода: {type(error).__name__}: {error}")
                return False

        saved = json.loads(fresh.get("turn_notice_json") or "{}")
        recovered_notice = str(saved.get("text") or "") if (
            saved.get("status") == fresh["status"]
            and saved.get("round") == int(fresh["current_round"])
            and saved.get("position") == int(fresh.get("current_turn_position", 0))
        ) else ""
        applied = await _send_public_turn_locked(bot, world, fresh, notice=recovered_notice)
        if applied:
            boss["turn_message_id"] = fresh.get("turn_message_id")
            boss["turn_message_kind"] = fresh.get("turn_message_kind", "text")
        return applied
