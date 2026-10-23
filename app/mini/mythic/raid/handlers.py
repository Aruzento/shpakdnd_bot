from app.mini.editorial import text as editorial_text, screen as editorial_screen

"""Raid catalog, personal hero selection, public HP/ranking and admin cancel."""

import logging
import hashlib

logger = logging.getLogger(__name__)
import secrets
from aiogram import F, Router
from aiogram.types import (
    FSInputFile,
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
    for i, row in enumerate(raid["ranking"][:15], 1):
        lines.append(f"{i}. {row['character_name']} — {row['damage']}")
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
def screen(world, player, user, *, pick=False, page=0, admin=False):
    wid = world["id"]
    uid = user
    pid = player["id"]
    rows = []
    b = lambda label, payload: button(wid, uid, label, payload)
    raid = service.state(wid)
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
        service.state(wid, raid_id=rid)
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
            text = (
                "Подтвердить отмену "
                + (
                    "с наградой (формула пока не утверждена)"
                    if parts[2] == "1"
                    else "без награды"
                )
                + "?"
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [b("Подтвердить", "cancel." + ".".join(parts[1:]))],
                    [b("Назад", "home")],
                ]
            )
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
                service.cancel(
                    int(parts[1]),
                    world["id"],
                    uid,
                    key,
                    with_reward=parts[2] == "1",
                    confirmed=True,
                    reason="Telegram admin confirmation",
                    actor_username=username_from_user(callback.from_user),
                    expected_revision=int(parts[3]),
                )
            elif op not in ("home", "pick"):
                raise ValueError("Неизвестное действие рейда.")
            current = service.state(world["id"])
            if current and op in ("start", "hit", "cancel"):
                await publish(callback.bot, world, current)
            text, markup = screen(
                world,
                player,
                uid,
                pick=op == "pick",
                page=int(parts[1]) if op == "pick" else 0,
                admin=is_admin(callback, world),
            )
        if receipt:
            text = receipt + "\n\n" + text
        text = telegram_clip(text, 4000)
        await callback.answer()
        await send_private_text_from_callback(callback, world, text, markup)
    except (ValueError, IndexError) as error:
        await callback.answer(str(error), show_alert=True)
