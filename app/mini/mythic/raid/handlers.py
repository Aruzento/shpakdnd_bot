from app.mini.editorial import text as editorial_text, screen as editorial_screen

"""Raid catalog, personal hero selection, public HP/ranking and admin cancel."""

import logging
import hashlib
import json

logger = logging.getLogger(__name__)
import secrets
from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.types import (
    FSInputFile,
    ForceReply,
    InlineKeyboardButton as Button,
    InlineKeyboardMarkup,
)
from app.mini.ui.context import (
    load_personal_context,
    load_extended_context,
    load_world,
    load_player,
    username_from_user,
)
from app.mini.ui.transport import send_private_text_from_callback
from app.mini.ui.limits import clip as telegram_clip
from app.mini.mythic.raid import service
from app.mini.mythic.raid.catalog import load, IMAGES
from app.mini.mythic.wallet import transaction
from app.mini.heroes import get_player_heroes
from app.mini.boss.ui import is_admin

router = Router(name="mini_mythic_raids")


def short_code(code):
    return hashlib.sha256(code.encode()).hexdigest()[:8]


def button(wid, uid, label, payload):
    data = f"mini:raid:{wid}:{uid}:{payload}"
    if len(data.encode()) > 64:
        raise ValueError("Слишком длинный callback.")
    return Button(text=label, callback_data=data)


@editorial_screen
def public_text(raid):
    snapshot = raid["snapshot"]
    lines = [
        f"👹 {snapshot['name']}",
        f"❤️ HP: {raid['current_hp']}/{raid['max_hp']}",
        f"Статус: {raid['status']}",
        "Рейтинг фактического HP-урона:",
    ]
    from app.mini.mythic.raid.mechanics import lines as mechanic_lines

    lines[3:3] = mechanic_lines(raid)
    if raid["status"] == "cancelled":
        outcome = json.loads(raid.get("result_json") or "{}")
        lines.insert(
            3,
            (
                "Отмена с частичной компенсацией — это не победа, Arise нет."
                if outcome.get("cancel_mode") == "with_reward"
                else "Отмена без новых наград."
            ),
        )
        if outcome.get("cancel_mode") == "with_reward":
            lines.insert(4, f"Компенсационный пул: {outcome['P']} мифических осколков.")
        if outcome.get("reason"):
            lines.insert(4, "Причина: " + outcome["reason"])
    for i, row in enumerate(raid["ranking"][:15], 1):
        line = f"{row.get('rank',i)}. {row['character_name']} — {row['damage']}"
        if "amount" in row:
            line += f" · +{row['amount']} миф."
        if any(r.get("success") for r in row.get("arise", [])):
            line += " · 🌑 Arise"
        lines.append(line)
    return telegram_clip("\n".join(lines), 3500)


async def publish(bot, world, raid):
    rid = raid["id"]
    image = (
        IMAGES / raid["snapshot"]["image"] if raid["snapshot"].get("image") else None
    )
    medium = "photo" if image and image.is_file() else "text"
    caption = telegram_clip("\n".join(public_text(raid).splitlines()[:9]), 1024)
    # Claim delivery before sending. Uncertain sends are never automatically repeated.
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mini_mythic_raid_publications(raid_id) VALUES(?)",
            (rid,),
        )
        publication = dict(
            conn.execute(
                "SELECT * FROM mini_mythic_raid_publications WHERE raid_id=?", (rid,)
            ).fetchone()
        )
        if publication["status"] == "pending":
            conn.execute(
                "UPDATE mini_mythic_raid_publications SET status='sending',media_type=? WHERE raid_id=?",
                (medium, rid),
            )
            send = True
        else:
            send = False
    markup = (
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    Button(
                        text="Войти / выбрать героя",
                        callback_data=f'mini:raidopen:{world["id"]}:{rid}',
                    )
                ]
            ]
        )
        if raid["status"] == "active"
        else None
    )
    if publication.get("message_id"):
        try:
            if publication["media_type"] == "photo":
                await bot.edit_message_caption(
                    chat_id=world["chat_id"],
                    message_id=publication["message_id"],
                    caption=caption,
                    reply_markup=markup,
                    parse_mode=None,
                )
            else:
                await bot.edit_message_text(
                    chat_id=world["chat_id"],
                    message_id=publication["message_id"],
                    text=public_text(raid),
                    reply_markup=markup,
                    parse_mode=None,
                )
        except Exception:
            logger.exception("Cannot refresh raid publication %s", rid)
    elif send:
        try:
            if medium == "photo":
                message = await bot.send_photo(
                    chat_id=world["chat_id"],
                    message_thread_id=world["thread_id"] or None,
                    photo=FSInputFile(image),
                    caption=caption,
                    reply_markup=markup,
                    parse_mode=None,
                )
            else:
                message = await bot.send_message(
                    chat_id=world["chat_id"],
                    message_thread_id=world["thread_id"] or None,
                    text=public_text(raid),
                    reply_markup=markup,
                    parse_mode=None,
                )
        except BaseException:
            with transaction() as conn:
                conn.execute(
                    "UPDATE mini_mythic_raid_publications SET status='uncertain' WHERE raid_id=?",
                    (rid,),
                )
            raise
        else:
            with transaction() as conn:
                conn.execute(
                    "UPDATE mini_mythic_raid_publications SET status='sent',message_id=? WHERE raid_id=?",
                    (message.message_id, rid),
                )


@editorial_screen
def screen(
    world,
    player,
    user,
    *,
    pick=False,
    page=0,
    admin=False,
    rank_page=None,
    history_page=None,
    raid_id=None,
):
    wid = world["id"]
    uid = user
    pid = player["id"]
    rows = []
    b = lambda label, payload: button(wid, uid, label, payload)
    if history_page is not None:
        entries = service.history(wid, history_page)
        rows = [
            [
                b(
                    f"#{v['id']} · {json.loads(v['snapshot_json'])['name']} · {v['status']}",
                    f"past.{v['id']}.0",
                )
            ]
            for v in entries[:10]
        ]
        if history_page:
            rows.append([b("⬅️", f"history.{history_page-1}")])
        if len(entries) > 10:
            rows.append([b("➡️", f"history.{history_page+1}")])
        rows.append([b("Назад", "home")])
        return "История мифических рейдов", InlineKeyboardMarkup(inline_keyboard=rows)
    raid = service.state(wid, raid_id=raid_id)
    if raid and rank_page is not None:
        rank_prefix = f"past.{raid_id}." if raid_id is not None else "rank."
        ranking = raid["ranking"]
        size = 10
        rank_page = max(0, min(rank_page, max(0, (len(ranking) - 1) // size)))
        view = {**raid, "ranking": ranking[rank_page * size : (rank_page + 1) * size]}
        for i, row in enumerate(view["ranking"], rank_page * size + 1):
            row.setdefault("rank", i)
        text = (
            public_text(view)
            + f"\nСтраница {rank_page+1}/{max(1,(len(ranking)+size-1)//size)}"
        )
        if rank_page:
            rows.append([b("⬅️", rank_prefix + str(rank_page - 1))])
        if (rank_page + 1) * size < len(ranking):
            rows.append([b("➡️", rank_prefix + str(rank_page + 1))])
        rows.append([b("Назад", "home")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)
    if raid:
        rows.append([b("Рейтинг / награды", "rank.0")])
    if raid and raid["status"] == "active":
        with transaction() as conn:
            from app.mini.mythic.pets import day, timestamp

            used = conn.execute(
                "SELECT COUNT(*) FROM mini_mythic_raid_attacks WHERE player_id=? AND game_day=?",
                (pid, day(timestamp())),
            ).fetchone()[0]
        text = public_text(raid) + f"\nОсталось атак сегодня: {max(0,10-used)}/10"
        if pick:
            heroes = get_player_heroes(pid)
            size = 8
            page = max(0, min(page, max(0, (len(heroes) - 1) // size)))
            for hero in heroes[page * size : (page + 1) * size]:
                rows.append(
                    [
                        b(
                            hero["name"],
                            f'hitask.{raid["id"]}.{hero["id"]}.{secrets.token_hex(3)}',
                        )
                    ]
                )
            if page:
                rows.append([b("⬅️", f"pick.{page-1}")])
            if (page + 1) * size < len(heroes):
                rows.append([b("➡️", f"pick.{page+1}")])
            text += (
                "\nНеописанное raid-свойство не срабатывает; базовая атака разрешена."
            )
        else:
            rows.append([b("Выбрать своего героя", "pick.0")])
        if admin:
            rows.append(
                [
                    b(
                        "Отменить без награды",
                        f'cancelask.{raid["id"]}.0.{raid["revision"]}',
                    )
                ]
            )
            rows.append(
                [
                    b(
                        "Отменить с наградой",
                        f'cancelask.{raid["id"]}.1.{raid["revision"]}',
                    )
                ]
            )
    else:
        entries = load()
        text = (
            editorial_text("v15.raid.title")
            + "\n\n"
            + (
                "Выбери реального босса."
                if entries
                else "Каталог четырёх мифических боссов ещё не заполнен. Рейды недоступны до добавления реальных JSON/PNG."
            )
        )
        for boss in entries:
            rows.append(
                [
                    b(
                        boss["name"],
                        f'start.{short_code(boss["code"])}.{secrets.token_hex(3)}',
                    )
                ]
            )
        if raid:
            text += "\n\n" + public_text(raid)
    rows.append([b("История рейдов", "history.0")])
    rows.append(
        [Button(text="Мифическая эра", callback_data=f"mini:mythic:{wid}:{uid}")]
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("mini:raids:"))
async def home(callback):
    context = await load_personal_context(callback)
    if not context:
        return
    world, player = context
    try:
        text, markup = screen(
            world, player, callback.from_user.id, admin=is_admin(callback, world)
        )
        await callback.answer()
        await send_private_text_from_callback(callback, world, text, markup)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("mini:raidopen:"))
async def public_open(callback):
    try:
        _, _, wid, rid = callback.data.split(":")
        wid, rid = int(wid), int(rid)
        world = load_world(wid, callback)
        player = load_player(wid, callback)
        if not world or not player:
            raise ValueError("Сначала зарегистрируйся в Mini этой темы.")
        requested = service.state(wid, raid_id=rid)
        if requested["status"] != "active":
            raise ValueError("Этот рейд завершён. Открой актуальное меню.")
        text, markup = screen(
            world,
            player,
            callback.from_user.id,
            pick=True,
            admin=is_admin(callback, world),
        )
        await callback.answer()
        await send_private_text_from_callback(callback, world, text, markup)
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)


@router.callback_query(F.data.startswith("mini:raid:"))
async def action(callback):
    context = await load_extended_context(callback, "raid")
    if not context:
        return
    world, player, payload = context
    parts = payload.split(".")
    op = parts[0]
    uid = callback.from_user.id
    b = lambda label, payload: button(world["id"], uid, label, payload)
    key = f"raid-ui:{world['id']}:{uid}:{payload}"
    receipt = ""
    try:
        if op == "hitask":
            rid, hid = int(parts[1]), int(parts[2])
            text = "Подтвердить рейдовую атаку выбранным героем? Расходуется одна из 10 суточных попыток."
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [b("Атаковать", "hit." + ".".join(parts[1:]))],
                    [b("Отмена", "pick.0")],
                ]
            )
        elif op == "cancelask":
            if parts[2] not in ("0", "1"):
                raise ValueError("Недопустимый режим отмены.")
            if not is_admin(callback, world):
                raise ValueError("Нет прав администратора темы.")
            request = service.request_cancel(
                int(parts[1]),
                world["id"],
                uid,
                parts[2] == "1",
                int(parts[3]),
                actor_username=username_from_user(callback.from_user),
            )
            text, markup = cancel_preview(world["id"], uid, request)
        elif op == "cancelpage":
            request = service.cancel_request(
                parts[1],
                world["id"],
                uid,
                actor_username=username_from_user(callback.from_user),
            )
            text, markup = cancel_preview(
                world["id"], uid, request, page=max(0, int(parts[2]))
            )
        elif op == "cancelreason":
            request = service.cancel_request(
                parts[1],
                world["id"],
                uid,
                actor_username=username_from_user(callback.from_user),
            )
            prompt = await callback.bot.send_message(
                chat_id=uid,
                text="Ответь на это сообщение причиной отмены (1–500 символов).",
                reply_markup=ForceReply(selective=True),
                parse_mode=None,
            )
            service.cancel_request(
                parts[1],
                world["id"],
                uid,
                actor_username=username_from_user(callback.from_user),
                prompt=(uid, 0, prompt.message_id),
            )
            await callback.answer("Введи причину в личном чате.")
            return

        else:
            if op == "start":
                matches = [x for x in load() if short_code(x["code"]) == parts[1]]
                if len(matches) != 1:
                    raise ValueError("Каталог изменился, обнови меню.")
                service.start(player["id"], matches[0]["code"], key)
            elif op == "hit":
                outcome = service.attack(
                    player["id"], int(parts[1]), int(parts[2]), key
                )
                receipt = f"Снято HP: {outcome['actual_hp_damage']}. Осталось атак: {outcome['attempts_left']}/10."
                from app.mini.mythic.raid.mechanics import event_lines

                details = event_lines(outcome.get("audit", []))
                if details:
                    receipt += "\n" + "\n".join(details)
                if outcome.get("egg"):
                    from app.mini.mythic.handlers import NAMES

                    receipt += f"\nПолучено первое яйцо: {NAMES[outcome['egg']['element']]}. Оно в инвентаре зверей."
                for reward in outcome.get("final", {}).get("rewards", []):
                    if reward["player_id"] == player["id"]:
                        receipt += (
                            f"\nНаграда: +{reward['amount']} мифических осколков."
                        )
                        if any(r["success"] for r in reward.get("arise", [])):
                            receipt += "\nArise: получена тень!"

            elif op == "cancel":
                raise ValueError("Кнопка устарела. Открой новое подтверждение отмены.")
            elif op == "cancelconfirm":
                outcome = service.confirm_cancel(
                    parts[1],
                    world["id"],
                    uid,
                    key,
                    actor_username=username_from_user(callback.from_user),
                )
                result = outcome["result"]
                receipt = "Рейд отменён. Это не победа; Arise нет."
                if result["cancel_mode"] == "with_reward":
                    receipt += f" Выплачено {result['P']} мифических осколков."
            elif op not in ("home", "pick", "rank", "history", "past"):
                raise ValueError("Неизвестное действие рейда.")
            current = service.state(world["id"])
            if current and op in ("start", "hit", "cancelconfirm"):
                await publish(callback.bot, world, current)
            text, markup = screen(
                world,
                player,
                uid,
                pick=op == "pick",
                page=int(parts[1]) if op == "pick" else 0,
                admin=is_admin(callback, world),
                rank_page=(
                    int(parts[1])
                    if op == "rank"
                    else (int(parts[2]) if op == "past" else None)
                ),
                history_page=max(0, int(parts[1])) if op == "history" else None,
                raid_id=int(parts[1]) if op == "past" else None,
            )
        if receipt:
            text = receipt + "\n\n" + text
        text = telegram_clip(text, 4000)
        await callback.answer()
        await send_private_text_from_callback(callback, world, text, markup)
    except (ValueError, IndexError) as error:
        await callback.answer(str(error), show_alert=True)


async def recover_pending(bot):
    with transaction() as conn:
        targets = [
            dict(r)
            for r in conn.execute(
                "SELECT r.id,r.world_id,w.chat_id,w.thread_id FROM mini_mythic_raids r JOIN mini_mythic_raid_publications p ON p.raid_id=r.id JOIN mini_worlds w ON w.id=r.world_id WHERE p.status='pending' AND w.enabled=1"
            )
        ]
    for row in targets:
        try:
            await publish(
                bot,
                {**row, "id": row["world_id"]},
                service.state(row["world_id"], raid_id=row["id"]),
            )
        except Exception:
            logger.exception("Raid pending publication recovery failed: %s", row["id"])


def cancel_preview(world_id, user_id, request, *, page=0):
    plan = request["preview"]
    rows = []
    text = "Причина: " + request["reason"] + "\n"
    if request["with_reward"]:
        text += f"Частичная компенсация: пул {plan['P']} из {plan['F']}. Фактический вклад {plan['D']}; исходный HP {plan['M']}.\nЭто не победа, Arise нет. Нет +1/топ-3/лунных бонусов."
        page = min(page, max(0, (len(plan["rewards"]) - 1) // 10))
        for item in plan["rewards"][page * 10 : (page + 1) * 10]:
            text += f"\nИгрок #{item['player_id']}: вклад {item['damage']}, +{item['amount']} миф."
        if page:
            rows.append(
                [
                    button(
                        world_id,
                        user_id,
                        "⬅️",
                        f"cancelpage.{request['token']}.{page-1}",
                    )
                ]
            )
        if (page + 1) * 10 < len(plan["rewards"]):
            rows.append(
                [
                    button(
                        world_id,
                        user_id,
                        "➡️",
                        f"cancelpage.{request['token']}.{page+1}",
                    )
                ]
            )
    else:
        text += (
            "Отменить без новых наград? Первое яйцо и использованные атаки сохраняются."
        )
    if not request["with_reward"] or plan["P"] > 0:
        rows.append(
            [
                button(
                    world_id,
                    user_id,
                    "Подтвердить",
                    f"cancelconfirm.{request['token']}",
                )
            ]
        )
    else:
        text += "\nПул равен нулю: отмена с наградой недоступна."
    rows.append(
        [
            button(
                world_id,
                user_id,
                "Изменить причину",
                f"cancelreason.{request['token']}",
            )
        ]
    )
    rows.append([button(world_id, user_id, "Назад", "home")])
    return telegram_clip(text, 4000), InlineKeyboardMarkup(inline_keyboard=rows)


class PendingCancelReason(Filter):
    async def __call__(self, message):
        if (
            not message.from_user
            or not message.text
            or message.text.startswith("/")
            or not message.reply_to_message
        ):
            return False
        with transaction() as conn:
            row = conn.execute(
                "SELECT q.* FROM mini_mythic_raid_cancel_requests q JOIN mini_mythic_raids r ON r.id=q.raid_id WHERE q.actor_id=? AND q.input_chat_id=? AND COALESCE(q.input_thread_id,0)=? AND q.prompt_message_id=? AND r.status='active' ORDER BY q.created_at DESC LIMIT 1",
                (
                    message.from_user.id,
                    message.chat.id,
                    message.message_thread_id or 0,
                    message.reply_to_message.message_id,
                ),
            ).fetchone()
        return {"cancel_prompt": dict(row)} if row else False


@router.message(PendingCancelReason())
async def cancel_reason(message, cancel_prompt):
    try:
        request = service.cancel_request(
            cancel_prompt["token"],
            cancel_prompt["world_id"],
            message.from_user.id,
            actor_username=username_from_user(message.from_user),
            reason=message.text,
        )
        text, markup = cancel_preview(
            cancel_prompt["world_id"], message.from_user.id, request
        )
        await message.answer(text, reply_markup=markup, parse_mode=None)
    except ValueError as error:
        await message.answer(str(error))
